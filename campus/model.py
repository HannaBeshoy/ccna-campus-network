"""model.py - load network.yaml, expand it into everything the templates need, and check it.

The checks catch the mistakes that are easy to make by hand and hard to find in a running
network: overlapping subnets, a gateway outside its subnet, an access port in a VLAN that does
not exist, a port used twice, an EtherChannel with different member counts on its two ends,
and an STP root that is not the HSRP active router (traffic would take a detour through the
other distribution switch).
"""
import ipaddress
import re
from dataclasses import dataclass, field

import yaml


class ModelError(Exception):
    pass


def expand_ports(spec):
    """'Fa0/1-8' -> ['Fa0/1', ..., 'Fa0/8']; 'G1/0/21-22' -> two ports; a list -> each expanded."""
    if isinstance(spec, list):
        return [p for s in spec for p in expand_ports(s)]
    m = re.fullmatch(r"([A-Za-z]+)((?:\d+/)*)(\d+)(?:-(\d+))?", spec.strip())
    if not m:
        raise ModelError(f"bad port '{spec}'")
    kind, prefix, first, last = m.group(1), m.group(2), int(m.group(3)), m.group(4)
    last = int(last) if last else first
    if last < first:
        raise ModelError(f"bad port range '{spec}'")
    return [f"{kind}{prefix}{n}" for n in range(first, last + 1)]


def range_text(spec):
    """'Fa0/1-8' -> 'range FastEthernet0/1 - 8'; one port -> 'FastEthernet0/20' (after "interface ")."""
    ports = expand_ports(spec) if isinstance(spec, str) else spec
    if len(ports) == 1:
        return long_name(ports[0])
    return f"range {long_name(ports[0])} - {ports[-1].split('/')[-1]}"


IOS_NAMES = {"Fa": "FastEthernet", "G": "GigabitEthernet", "Gi": "GigabitEthernet"}


def long_name(port):
    m = re.match(r"([A-Za-z]+)(.*)", port)
    return IOS_NAMES.get(m.group(1), m.group(1)) + m.group(2)


@dataclass
class Vlan:
    id: int
    name: str
    v4: ipaddress.IPv4Network = None
    v6: ipaddress.IPv6Network = None
    primary: str = None
    dhcp: bool = False
    dhcpv6_stateless: bool = False

    def host(self, n):
        return self.v4.network_address + n

    def host6(self, n):
        return self.v6.network_address + n


@dataclass
class Network:
    raw: dict
    vlans: dict = field(default_factory=dict)
    routed_vlans: list = field(default_factory=list)

    @property
    def parking(self):
        return next(v.id for v in self.vlans.values() if v.v4 is None)

    def dist(self):
        return sorted(n for n, s in self.raw["switches"].items() if s["role"] == "distribution")

    def access(self):
        return sorted(n for n, s in self.raw["switches"].items() if s["role"] == "access")

    def channels_of(self, dev):
        """[(channel id, local ports, peer, peer ports)] of one device."""
        out = []
        for ec in self.raw["etherchannels"]:
            if ec["a"] == dev:
                out.append((ec["id"], expand_ports(ec["a_ports"]), ec["b"], expand_ports(ec["b_ports"])))
            elif ec["b"] == dev:
                out.append((ec["id"], expand_ports(ec["b_ports"]), ec["a"], expand_ports(ec["a_ports"])))
        return out


def load(path="network.yaml"):
    with open(path) as f:
        return load_from(yaml.safe_load(f))


def load_from(raw):
    net = Network(raw=raw)
    for vid, v in raw["vlans"].items():
        net.vlans[int(vid)] = Vlan(
            id=int(vid), name=v["name"],
            v4=ipaddress.ip_network(v["v4"]) if "v4" in v else None,
            v6=ipaddress.ip_network(v["v6"]) if "v6" in v else None,
            primary=v.get("primary"), dhcp=v.get("dhcp", False),
            dhcpv6_stateless=v.get("dhcpv6_stateless", False))
    net.routed_vlans = [v for v in net.vlans.values() if v.v4 is not None]
    return net


def validate(net):
    """Return a list of problems (empty list = the model is consistent)."""
    p = []
    raw = net.raw
    dists, accs = net.dist(), net.access()
    a = raw["addressing"]

    # VLANs
    for v in net.vlans.values():
        if not 1 <= v.id <= 4094 or v.id in (1002, 1003, 1004, 1005):
            p.append(f"VLAN {v.id}: not a usable VLAN number")
        if v.id == 1:
            p.append("VLAN 1 must not carry traffic (leave it unused)")
        if v.v4 is not None and v.primary not in dists:
            p.append(f"VLAN {v.id}: primary '{v.primary}' is not a distribution switch")
        if v.v6 is not None and v.v6.prefixlen != 64:
            p.append(f"VLAN {v.id}: IPv6 subnet {v.v6} must be a /64 (SLAAC needs 64-bit interface IDs)")
    if sum(1 for v in net.vlans.values() if v.v4 is None) != 1:
        p.append("exactly one VLAN without addresses is needed: the parking / native VLAN")

    # subnets do not overlap (VLANs, transit links, loopback)
    nets4 = [(f"VLAN {v.id}", v.v4) for v in net.routed_vlans]
    nets6 = [(f"VLAN {v.id}", v.v6) for v in net.routed_vlans if v.v6]
    for rname, r in raw["routers"].items():
        for ifn, i in r["interfaces"].items():
            nets4.append((f"{rname} {ifn}", ipaddress.ip_interface(i["v4"]).network))
            nets6.append((f"{rname} {ifn}", ipaddress.ip_interface(i["v6"]).network))
        nets4.append((f"{rname} Lo0", ipaddress.ip_interface(r["loopback"]["v4"]).network))
    for group in (nets4, nets6):
        for i, (n1, s1) in enumerate(group):
            for n2, s2 in group[i + 1:]:
                if s1.overlaps(s2):
                    p.append(f"{n1} {s1} overlaps {n2} {s2}")

    # host numbers fit, DHCP range leaves room
    for v in net.routed_vlans:
        for what in ("gateway_host", "d1_host", "d2_host"):
            if a[what] >= v.v4.num_addresses - 1:
                p.append(f"VLAN {v.id}: {what} .{a[what]} is outside {v.v4}")
        if v.dhcp and a["dhcp_excluded_last"] >= v.v4.num_addresses - 2:
            p.append(f"VLAN {v.id}: the excluded range leaves no addresses for DHCP")

    # the router's links to the distribution switches match the switches' uplinks
    r1 = raw["routers"]["R1"]
    for ifn, i in r1["interfaces"].items():
        if "peer" in i:
            up = raw["switches"][i["peer"]]["uplink"]
            if ipaddress.ip_interface(up["v4"]).network != ipaddress.ip_interface(i["v4"]).network:
                p.append(f"R1 {ifn} and {i['peer']} {up['port']} are not in the same subnet")
            if ipaddress.ip_interface(up["v4"]).ip == ipaddress.ip_interface(i["v4"]).ip:
                p.append(f"R1 {ifn} and {i['peer']} have the same address")
    summ = ipaddress.ip_network(r1["campus_summary"]["v4"])
    for v in net.routed_vlans:
        if not v.v4.subnet_of(summ):
            p.append(f"VLAN {v.id} {v.v4} is outside R1's campus route {summ}")

    # ports: every access port in an existing VLAN, no port used twice
    for sw in accs:
        s = raw["switches"][sw]
        used = {}
        for spec, cfg in s["access_ports"].items():
            if cfg["vlan"] not in net.vlans or net.vlans[cfg["vlan"]].v4 is None:
                p.append(f"{sw} {spec}: VLAN {cfg['vlan']} does not exist or is the parking VLAN")
            for port in expand_ports(spec):
                if port in used:
                    p.append(f"{sw} {port} is used twice")
                used[port] = spec
        for port in expand_ports(s["unused"]):
            if port in used:
                p.append(f"{sw} {port} is listed as unused and as {used[port]}")
            used[port] = "unused"
        for cid, ports, peer, _ in net.channels_of(sw):
            for port in ports:
                if port in used:
                    p.append(f"{sw} {port} is in Port-channel {cid} and also {used[port]}")
                used[port] = f"Po{cid}"
        from .generate import MODELS
        if s["model"] in MODELS:
            missing = [x for x in expand_ports(MODELS[s["model"]]["ports"]) if x not in used]
            extra = [x for x in used if x not in expand_ports(MODELS[s["model"]]["ports"])]
            if missing:
                p.append(f"{sw}: ports {', '.join(missing)} are not assigned (list them as unused)")
            if extra:
                p.append(f"{sw}: {', '.join(extra)} do not exist on a {s['model']}")
        else:
            p.append(f"{sw}: unknown model {s['model']}")

    # EtherChannels: same number of members on both sides, ids unique, a link between every pair
    ids = [ec["id"] for ec in raw["etherchannels"]]
    if len(ids) != len(set(ids)):
        p.append("EtherChannel ids must be unique")
    for ec in raw["etherchannels"]:
        if len(expand_ports(ec["a_ports"])) != len(expand_ports(ec["b_ports"])):
            p.append(f"Port-channel {ec['id']}: {ec['a']} and {ec['b']} have different numbers of members")
        if len(expand_ports(ec["a_ports"])) > 8:
            p.append(f"Port-channel {ec['id']}: more than 8 active members")
    for sw in accs:
        peers = {peer for _, _, peer, _ in net.channels_of(sw)}
        if peers != set(dists):
            p.append(f"{sw} must have an uplink to each distribution switch (has {sorted(peers)})")
    if not any({ec["a"], ec["b"]} == set(dists) for ec in raw["etherchannels"]):
        p.append("the two distribution switches need a link between them")

    # STP root and HSRP active must be the same switch per VLAN: that is what 'primary' sets for both
    st = raw["stp"]
    if not st["root_priority"] < st["secondary_priority"] < 32768:
        p.append("STP: root priority < secondary priority < 32768 (the default) is required")
    for prio in (st["root_priority"], st["secondary_priority"]):
        if prio % 4096:
            p.append(f"STP priority {prio} is not a multiple of 4096")

    # DHCP reservations inside their VLAN and outside the pool's excluded range
    for res in raw.get("dhcp_reservations", []):
        v = net.vlans.get(res["vlan"])
        ip = ipaddress.ip_address(res["ip"])
        if v is None or ip not in v.v4:
            p.append(f"reservation {res['name']}: {ip} is not in VLAN {res['vlan']}")
        elif int(ip) - int(v.v4.network_address) <= a["dhcp_excluded_last"]:
            p.append(f"reservation {res['name']}: {ip} is in the excluded range")
        if not re.fullmatch(r"[0-9a-f]{4}\.[0-9a-f]{4}\.[0-9a-f]{4}", res["mac"]):
            p.append(f"reservation {res['name']}: MAC must be written as xxxx.xxxx.xxxx")

    w = raw.get("wlc")
    if w:
        if w["switch"] not in dists:
            p.append("the WLC must connect to a distribution switch")
        if w["wlan"]["vlan"] not in net.vlans:
            p.append(f"WLAN VLAN {w['wlan']['vlan']} does not exist")
    return p
