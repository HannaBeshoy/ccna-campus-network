"""routing.py - follow a packet hop by hop through the generated configurations.

Each router and Layer 3 switch gets a routing table built the way IOS builds one (modules 14
and 15): a connected route for every interface that is up, and every static route whose next
hop is reachable over an interface that is up. A static route whose next hop is not reachable
stays out of the table, and that is what lets a floating static route (higher administrative
distance) take its place. The forwarding decision is the longest match, then the lowest
administrative distance.

Links or whole devices can be taken down to see what happens on the lab day before it
happens: "R1:G0/1" shuts that interface and the interface at the other end of the /30, "D1"
switches the whole device off (HSRP then moves its VLANs' gateway to the other switch).

Hosts are the VLANs (a host in VLAN 10 sends to its HSRP gateway, which is the VLAN's primary
switch while it is up) and the dual-stack server behind the ISP.
"""
import ipaddress
import re

from .generate import render_all
from .iosconf import ifname, parse

MAX_HOPS = 16


class Device:
    def __init__(self, name, text):
        self.name = name
        self.ifaces = {}      # interface -> {"v4": [IPv4Interface], "v6": [IPv6Interface], "up": bool}
        self.static = []      # (prefix, next hop or None, exit interface or None, AD)
        s = parse(text)
        for key, body in s.items():
            if not key.startswith("interface "):
                continue
            name_ = key[len("interface "):]
            info = {"v4": [], "v6": [], "up": "shutdown" not in body}
            for line in body:
                m = re.match(r"ip address (\S+) (\S+)$", line)
                if m:
                    info["v4"].append(ipaddress.ip_interface(f"{m.group(1)}/{m.group(2)}"))
                m = re.match(r"ipv6 address (\S+/\d+)$", line)
                if m:
                    info["v6"].append(ipaddress.ip_interface(m.group(1)))
            if info["v4"] or info["v6"]:
                self.ifaces[name_] = info
        for line in s[""]:
            m = re.match(r"ip route (\S+) (\S+) null0(?: (\d+))?$", line, re.I)
            if m:                                    # discard route: matches, then drops
                self.static.append((ipaddress.ip_network(f"{m.group(1)}/{m.group(2)}"), None, "null0",
                                    int(m.group(3) or 1)))
                continue
            m = re.match(r"ip route (\S+) (\S+) (\S+)(?: (\d+))?$", line)
            if m:
                self.static.append((ipaddress.ip_network(f"{m.group(1)}/{m.group(2)}"),
                                    ipaddress.ip_address(m.group(3)), None, int(m.group(4) or 1)))
            if line.startswith("ipv6 route "):
                t = line.split()[2:]                 # prefix [exit interface] next hop [AD]
                ad = int(t.pop()) if t[-1].isdigit() else 1
                if t[-1].lower() == "null0":
                    self.static.append((ipaddress.ip_network(t[0]), None, "null0", ad))
                    continue
                exit_if = ifname(t[1]).lower() if len(t) == 3 else None
                self.static.append((ipaddress.ip_network(t[0]), ipaddress.ip_address(t[-1]), exit_if, ad))

    def connected(self):
        """[(network, interface)] of the interfaces that are up."""
        out = []
        for n, i in self.ifaces.items():
            if i["up"]:
                out += [(a.network, n) for a in i["v4"] + i["v6"]]
        return out

    def owns(self, ip):
        return any(i["up"] and any(a.ip == ip for a in i["v4"] + i["v6"]) for i in self.ifaces.values())

    def table(self):
        """[(prefix, AD, next hop, interface, kind)] - what "show ip route" would list
        (kind C connected, S static, N static to Null0)."""
        conn = self.connected()
        rows = [(n, 0, None, i, "C") for n, i in conn]
        for prefix, hop, exit_if, ad in self.static:
            if exit_if == "null0":
                rows.append((prefix, ad, None, "Null0", "N"))
                continue
            if hop.is_link_local:
                ok = exit_if is not None and self.ifaces.get(exit_if, {}).get("up", False)
                via_if = exit_if
            else:
                via_if = next((i for n, i in conn if hop.version == n.version and hop in n), None)
                ok = via_if is not None and (exit_if is None or exit_if == via_if)
            if ok:
                rows.append((prefix, ad, hop, via_if, "S"))
        best = {}
        for r in rows:                         # per prefix, only the lowest AD is installed
            if r[0] not in best or r[1] < best[r[0]][1]:
                best[r[0]] = r
        return list(best.values())

    def lookup(self, ip):
        cands = [r for r in self.table() if r[0].version == ip.version and ip in r[0]]
        if not cands:
            return None
        return max(cands, key=lambda r: (r[0].prefixlen, -r[1]))


class Lab:
    """The routed devices of the design, with some links or devices taken down."""

    def __init__(self, net, down=()):
        self.net = net
        cfgs = render_all(net)
        self.devices = {n: Device(n, t) for n, t in cfgs.items()
                        if n in net.raw["routers"] or n in net.dist() or n == net.raw.get("isp", {}).get("name")}
        self.dead = set()
        for item in down:
            self.take_down(item)

    def take_down(self, item):
        if ":" not in item:
            if item not in self.devices:
                raise ValueError(f"unknown device {item}")
            self.dead.add(item)
            for n, i in self.devices[item].ifaces.items():
                if not n.startswith("vlan") and not n.startswith("loopback") and i["up"]:
                    self._down(item, n)              # the links to its neighbours go down with it
                i["up"] = False
            return
        dev, port = item.split(":", 1)
        if dev not in self.devices:
            raise ValueError(f"unknown device {dev}")
        name = ifname(port).lower()
        if name not in self.devices[dev].ifaces:
            raise ValueError(f"{dev} has no routed interface {port}")
        self._down(dev, name)

    def _down(self, dev, name):
        d = self.devices[dev]
        d.ifaces[name]["up"] = False
        nets = {a.network for a in d.ifaces[name]["v4"] + d.ifaces[name]["v6"]}
        for other in self.devices.values():          # the other end of a point-to-point link goes down too
            for n, i in other.ifaces.items():
                if not n.startswith("vlan") and {a.network for a in i["v4"] + i["v6"]} & nets:
                    i["up"] = False

    # --- hosts ------------------------------------------------------------------------------
    def gateway_of(self, vlan_id):
        v = self.net.vlans[vlan_id]
        if v.primary not in self.dead:
            return v.primary
        alive = [d for d in self.net.dist() if d not in self.dead]
        return alive[0] if alive else None

    def host_address(self, vlan_id, family):
        v = self.net.vlans[vlan_id]
        return v.host(50) if family == 4 else v.host6(0x50)

    def server(self, family):
        s = self.net.raw["isp"]["server"]
        return ipaddress.ip_address(s["v4"] if family == 4 else s["v6"])

    def endpoint(self, spec, family):
        """'vlan10' / 'server' / an address -> (address, first device, label)."""
        if spec == "server":
            return self.server(family), self.net.raw["isp"]["name"], self.net.raw["isp"]["server"]["name"]
        m = re.fullmatch(r"vlan(\d+)", spec, re.I)
        if m:
            vid = int(m.group(1))
            return self.host_address(vid, family), self.gateway_of(vid), f"host in VLAN {vid}"
        ip = ipaddress.ip_address(spec)
        for v in self.net.routed_vlans:
            if ip in (v.v4 if ip.version == 4 else v.v6):
                return ip, self.gateway_of(v.id), f"host in VLAN {v.id}"
        if ip == self.server(ip.version):
            return ip, self.net.raw["isp"]["name"], self.net.raw["isp"]["server"]["name"]
        raise ValueError(f"{spec}: not a VLAN host or the server")

    def is_host(self, ip):
        if self.net.raw.get("isp") and ip == self.server(ip.version):
            return True
        return any(ip in (v.v4 if ip.version == 4 else v.v6) for v in self.net.routed_vlans)

    # --- forwarding --------------------------------------------------------------------------
    def trace(self, src, dst, family=4):
        """(delivered, [hop lines]) for a packet from src to dst ('vlan10', 'server' or an address).
        Afterwards self.path lists (device, address the packet arrived on) for every hop."""
        src_ip, first, label = self.endpoint(src, family)
        dst_ip = self.endpoint(dst, family)[0] if not _is_ip(dst) else ipaddress.ip_address(dst)
        hops = [f"{label} -> gateway {first}" if first else f"{label}: no gateway is up"]
        self.path = []
        if first is None:
            return False, hops
        return self._forward(first, self._address_on(first, src_ip), dst_ip, hops)

    def ping(self, device, source, dst):
        """Extended ping from a router or switch with a source address: (ok, there, back)."""
        src_ip, dst_ip = ipaddress.ip_address(source), ipaddress.ip_address(dst)
        if not self.devices[device].owns(src_ip):
            raise ValueError(f"{device} has no interface with {src_ip}")
        ok1, there = self._forward(device, None, dst_ip, [f"{device} (source {src_ip}) -> {dst_ip}"])
        back_from = self._first_hop_of(dst_ip)
        if back_from is None:
            return False, there, [f"{dst_ip}: no device answers"]
        ok2, back = self._forward(back_from, None, src_ip, [f"{dst_ip} -> {src_ip}"])
        return ok1 and ok2, there, back

    def _first_hop_of(self, ip):
        """The device that sends a reply from ip: its owner, the VLAN's gateway or the server's router."""
        owner = next((n for n, d in self.devices.items() if d.owns(ip)), None)
        if owner:
            return owner
        return self.endpoint(str(ip), ip.version)[1]

    def _address_on(self, dev, ip):
        """dev's own address in the subnet of ip (the interface a packet from ip arrives on)."""
        for i in self.devices[dev].ifaces.values():
            for a in i["v4"] + i["v6"]:
                if a.version == ip.version and ip in a.network and i["up"]:
                    return a.ip
        return None

    def _forward(self, here, came_in, dst_ip, hops):
        seen = set()
        self.path = [(here, came_in)]
        while len(hops) < MAX_HOPS:
            d = self.devices[here]
            if d.owns(dst_ip):
                hops.append(f"{here}: the destination is this device")
                return True, hops
            if here in seen:
                hops.append(f"{here}: routing loop")
                return False, hops
            seen.add(here)
            r = d.lookup(dst_ip)
            if r is None:
                hops.append(f"{here}: no route to {dst_ip} - dropped")
                return False, hops
            prefix, ad, hop, iface, kind = r
            if kind == "N":
                hops.append(f"{here}: {prefix} [{ad}/0] to Null0 - discarded (nothing in the site has {dst_ip})")
                return False, hops
            if kind == "C":
                owner = next((n for n, o in self.devices.items() if n != here and o.owns(dst_ip)), None)
                if owner:
                    hops.append(f"{here}: {prefix} connected ({iface}) -> {owner}")
                    self.path.append((owner, dst_ip))
                    here = owner
                    continue
                if self.is_host(dst_ip):
                    hops.append(f"{here}: {prefix} connected ({iface}) -> delivered to {dst_ip}")
                    return True, hops
                hops.append(f"{here}: {prefix} connected, but nothing answers at {dst_ip}")
                return False, hops
            nxt = next((n for n, o in self.devices.items() if n != here and o.owns(hop)), None)
            arrive = hop
            if hop.is_link_local:
                nxt = self._link_local_neighbour(here, iface)
                mine = self.devices[here].ifaces[iface]["v6"]
                arrive = self._address_on(nxt, mine[0].ip) if nxt and mine else None
            hops.append(f"{here}: {prefix} [{ad}/0] via {hop} ({iface}) -> {nxt}")
            if nxt is None or nxt in self.dead:
                hops.append(f"{here}: next hop {hop} does not answer - dropped")
                return False, hops
            self.path.append((nxt, arrive))
            here = nxt
        return False, hops + ["too many hops"]

    def _link_local_neighbour(self, here, iface):
        """The device at the other end of a point-to-point link."""
        mine = self.devices[here].ifaces[iface]
        nets = {a.network for a in mine["v4"] + mine["v6"]}
        for n, o in self.devices.items():
            if n == here:
                continue
            for i in o.ifaces.values():
                if i["up"] and {a.network for a in i["v4"] + i["v6"]} & nets:
                    return n
        return None


def _is_ip(s):
    try:
        ipaddress.ip_address(s)
        return True
    except ValueError:
        return False


def round_trip(net, vlan_id, family, down=()):
    """(outbound ok, return ok, outbound hops, return hops) between a VLAN host and the server."""
    lab = Lab(net, down)
    ok1, h1 = lab.trace(f"vlan{vlan_id}", "server", family)
    ok2, h2 = lab.trace("server", str(lab.host_address(vlan_id, family)), family)
    return ok1, ok2, h1, h2


SCENARIOS = [((), "everything up"), (("R1:G0/1",), "D1's uplink down"), (("R1:G0/2",), "D2's uplink down"),
             (("D1",), "D1 off"), (("D2",), "D2 off")]


def paths_report(net):
    """Markdown: every VLAN to the server and back, IPv4 and IPv6, in each failure scenario."""
    vlans = [v.id for v in net.routed_vlans]
    out = ["# Paths to the dual-stack server", "",
           "Generated by `python -m campus paths` from the generated configurations: each router and Layer 3",
           "switch builds its routing table (connected routes of the interfaces that are up, static routes",
           "whose next hop is reachable, the lowest administrative distance per prefix) and forwards by the",
           "longest match. A host in a VLAN sends to its HSRP gateway. \"ok\" means the packet reaches the",
           "server and the reply reaches the host.", "",
           "| Scenario | " + " | ".join(f"VLAN {v}" for v in vlans) + " |",
           "| --- | " + " | ".join("---" for _ in vlans) + " |"]
    failures = []
    for down, label in SCENARIOS:
        cells = []
        for v in vlans:
            res = []
            for fam in (4, 6):
                ok1, ok2, h1, h2 = round_trip(net, v, fam, down)
                res.append("ok" if ok1 and ok2 else ("no way out" if not ok1 else "no way back"))
                if not (ok1 and ok2):
                    failures.append((label, v, fam, h1 if not ok1 else h2))
            cells.append(f"v4 {res[0]}, v6 {res[1]}")
        out.append(f"| {label} | " + " | ".join(cells) + " |")
    out += ["", "## Hop by hop, VLAN 10, IPv4", ""]
    for down, label in SCENARIOS:
        ok1, ok2, h1, h2 = round_trip(net, 10, 4, down)
        out += [f"**{label}**", "", "```text"] + h1 + [""] + h2 + ["```", ""]
    if failures:
        out += ["## Failures", ""]
        for label, v, fam, hops in failures:
            out.append(f"- {label}, VLAN {v}, IPv{fam}: {hops[-1]}")
    return "\n".join(out).rstrip() + "\n"
