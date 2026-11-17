"""roas.py - the router-on-a-stick variant of the design (SRWE module 4).

One router and one Layer 2 switch, with the VLANs, subnets, DHCP pools and IPv6 address methods of
network.yaml. The router has one subinterface per VLAN on a single trunk (encapsulation dot1Q N,
the gateway address .1), the native VLAN on a subinterface without addresses, and it is its own
DHCP server, so no relay is needed. The switch has a block of access ports per data VLAN; the
staff ports also carry the voice VLAN.

It is a side lab for module 4, and the fallback for a lab without a Layer 3 switch. The trade-off
it shows: every packet between two VLANs crosses the one trunk twice, and the router is a single
point of failure (no HSRP).
"""
import ipaddress

from .generate import MODELS, common, env, load_secrets, vlan_list
from .model import expand_ports, long_name, range_text


def roas_ctx(net, secrets):
    cfg = net.raw["router_on_a_stick"]
    a = net.raw["addressing"]
    r, s = cfg["router"], cfg["switch"]
    voice = {p["voice"] for sw in net.access() for p in net.raw["switches"][sw]["access_ports"].values() if "voice" in p}
    vlans = sorted(net.routed_vlans, key=lambda v: v.id)
    subifs = [{"long": f"{long_name(r['trunk'])}.{v.id}", "id": v.id, "name": v.name,
               "ip": v.host(a["gateway_host"]), "mask": v.v4.netmask, "ip6": v.host6(a["gateway_host"]),
               "dhcpv6": v.dhcpv6, "pool6": f"ROAS-{v.name}" if v.dhcpv6 == "stateful" else "ROAS-STATELESS"}
              for v in vlans]
    pools = [{"name": f"ROAS-{v.name}", "network": v.v4.network_address, "mask": v.v4.netmask,
              "gw": v.host(a["gateway_host"]), "excl_first": v.host(1), "excl_last": v.host(a["dhcp_excluded_last"])}
             for v in vlans if v.dhcp]
    # switch: a block of access ports per data VLAN (the voice VLAN rides on the staff ports)
    ports = expand_ports(MODELS[s["model"]]["ports"])
    access = [p for p in ports if p.startswith("Fa")]
    trunk = s["trunk"]
    blocks, i, n = [], 0, s["ports_per_vlan"]
    staff = next((sw_p for sw in net.access() for sw_p in net.raw["switches"][sw]["access_ports"].values()
                  if "voice" in sw_p), None)
    for v in vlans:
        if v.id in voice:
            continue
        chunk = access[i:i + n]
        i += n
        vv = staff["voice"] if staff and staff["vlan"] == v.id else None
        blocks.append({"range": range_text(chunk), "vlan": v.id, "name": v.name, "voice": vv,
                       "max_mac": 3 if vv else 2, "snoop": v.dhcp})
    used = set(access[:i]) | {trunk}
    unused = [range_text(list(g)) for g in _runs([p for p in ports if p not in used])]
    mg = net.vlans[99] if 99 in net.vlans else next(v for v in vlans if not v.dhcp)
    rctx = common(net, r["name"], r["model"], secrets, 4)
    rctx.update({"trunk": long_name(r["trunk"]), "peer": s["name"], "peer_port": s["trunk"],
                 "subifs": subifs, "pools": pools, "native": net.parking,
                 "dns": " ".join(a["dns"]), "dns6": " ".join(a["dns_v6"]),
                 "stateful6": [{"name": f"ROAS-{v.name}", "prefix": v.v6} for v in vlans if v.dhcpv6 == "stateful"],
                 "stateless6": any(v.dhcpv6 == "stateless" for v in vlans)})
    sctx = common(net, s["name"], s["model"], secrets, 15)
    sctx.update({"stp": net.raw["stp"], "vlans": [{"id": v.id, "name": v.name} for v in sorted(net.vlans.values(), key=lambda v: v.id)],
                 "trunk": long_name(trunk), "peer": r["name"], "peer_port": r["trunk"], "native": net.parking,
                 "trunk_vlans": vlan_list(v.id for v in vlans), "blocks": blocks, "unused": unused,
                 "mgmt_vlan": mg.id, "mgmt_ip": mg.host(s["mgmt_host"]), "mgmt_mask": mg.v4.netmask,
                 "mgmt_gw": mg.host(a["gateway_host"]),
                 "user_vlans": vlan_list(v.id for v in vlans if v.dhcp), "qos": any(b["voice"] for b in blocks)})
    return rctx, sctx


def _runs(ports):
    out, run = [], []
    for p in ports:
        base, num = p.rsplit("/", 1)
        if run and run[-1].rsplit("/", 1)[0] == base and int(run[-1].rsplit("/", 1)[1]) + 1 == int(num):
            run.append(p)
        else:
            if run:
                out.append(run)
            run = [p]
    if run:
        out.append(run)
    return out


def render_roas(net, secrets_path=None):
    """{device name: configuration} of the router-on-a-stick variant."""
    secrets = load_secrets(secrets_path, net.raw["site"].get("hardening", {}).get("min_password_length", 0))
    rctx, sctx = roas_ctx(net, secrets)
    e = env()
    out = {rctx["name"]: e.get_template("roas_router.j2").render(**rctx),
           sctx["name"]: e.get_template("roas_switch.j2").render(**sctx)}
    clean = {}
    for k, v in out.items():
        lines = [line.rstrip() for line in v.splitlines()]
        kept = [line for i, line in enumerate(lines) if line or (i and lines[i - 1])]
        clean[k] = "\n".join(kept).strip("\n") + "\n"
    return clean


def report(net):
    rctx, sctx = roas_ctx(net, {"enable": "", "admin": ""})
    out = ["# Router-on-a-stick variant (SRWE 4)", "",
           "Generated by `python -m campus roas`, configurations in [configs/roas/](../configs/roas/). One router and",
           "one Layer 2 switch carry the VLANs, subnets, DHCP pools and IPv6 address methods of the main design.",
           "Use it as the module 4 side lab, or on the lab day if no Layer 3 switch is available for D1/D2.", "",
           f"```text\n{rctx['name']} {rctx['trunk']} (subinterfaces) ==== 802.1Q trunk ==== {sctx['trunk']} {sctx['name']} --- PCs\n```", "",
           "## The router's subinterfaces", "",
           "| Subinterface | VLAN | IPv4 gateway | IPv6 gateway | IPv6 method |", "| --- | ---: | --- | --- | --- |"]
    for s in rctx["subifs"]:
        m = {None: "SLAAC", "stateless": "SLAAC + stateless DHCPv6", "stateful": "stateful DHCPv6"}[s["dhcpv6"]]
        out.append(f"| {s['long']} | {s['id']} {s['name']} | {s['ip']} | {s['ip6']}, FE80::1 | {m} |")
    out.append(f"| {rctx['trunk']}.{rctx['native']} | {rctx['native']} (native) | - | - | `encapsulation dot1Q {rctx['native']} native`, no addresses |")
    out += ["", "## The switch's ports", "", "| Ports | VLAN | Voice VLAN |", "| --- | --- | --- |"]
    for b in sctx["blocks"]:
        out.append(f"| {b['range'].replace('range ', '')} | {b['vlan']} {b['name']} | {b['voice'] or '-'} |")
    out += [f"| {sctx['trunk']} | trunk to {sctx['peer']}, native {sctx['native']}, VLANs {sctx['trunk_vlans']} | |",
            f"| {', '.join(u.replace('range ', '') for u in sctx['unused'])} | unused, VLAN {sctx['native']}, shut down | |",
            "", f"Management: Vlan{sctx['mgmt_vlan']} {sctx['mgmt_ip']}, default gateway {sctx['mgmt_gw']} (the router's subinterface).",
            f"VLAN {sctx['mgmt_vlan']} has no DHCP pool, so a PC on its ports needs a static address (for example "
            f"{net.vlans[sctx['mgmt_vlan']].host(50)}/{net.vlans[sctx['mgmt_vlan']].v4.prefixlen}, gateway {sctx['mgmt_gw']}).", "",
            "## Differences from the main design", "",
            "| | Main design | Router-on-a-stick |", "| --- | --- | --- |",
            "| inter-VLAN routing | SVIs on D1 and D2 (Layer 3 switches) | subinterfaces on one router |",
            "| gateway | HSRP virtual address .1, two switches | the subinterface's own .1, one router (single point of failure) |",
            "| traffic between VLANs | stays in the distribution switch | crosses the trunk to the router and back: the trunk's bandwidth is shared by all VLANs |",
            "| DHCP | R1, through relays on the SVIs (`ip helper-address`) | the router itself, directly on each subnet: no relay |",
            "| native VLAN | `switchport trunk native vlan` on both ends | the router needs `encapsulation dot1Q N native` on a subinterface to match |", "",
            "## Tests", "",
            "| Test | Expected |", "| --- | --- |",
            f"| `show interfaces trunk` on {sctx['name']} | {sctx['trunk']} trunking, native {sctx['native']}, VLANs {sctx['trunk_vlans']} allowed and forwarding |",
            f"| `show vlans` on {rctx['name']} | one subinterface per VLAN with its dot1Q tag and packet counters |",
            f"| `show ip interface brief` on {rctx['name']} | every subinterface up/up |",
            "| a PC in each VLAN | an address from the router's pool; `ping` its own gateway and a PC in another VLAN |",
            "| `tracert` from a VLAN 10 PC to a VLAN 20 PC | one hop: the router's VLAN 10 subinterface, then the PC |",
            f"| `show ip dhcp binding` on {rctx['name']} | one binding per PC |",
            "", "The lint (`python -m campus lint configs/roas/*.cfg`) applies the same security policy as to the main design."]
    return "\n".join(out) + "\n"
