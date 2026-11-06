"""generate.py - one IOS configuration per device from the model.

Secrets (enable secret, admin password) come from secrets.yaml when it exists; otherwise the
configs get placeholders like <ENABLE_SECRET>, which is what the repository stores.
"""
import ipaddress
import os

import jinja2
import yaml

from .model import expand_ports, long_name, range_text

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLACEHOLDERS = {"enable": "<ENABLE_SECRET>", "admin": "<ADMIN_SECRET>"}

# physical ports of each model, and whether it still needs "switchport trunk encapsulation dot1q"
MODELS = {
    "C3650-24PS": {"ports": ["G1/0/1-24"], "trunk_encapsulation": False},
    "C3560-24PS": {"ports": ["Fa0/1-24", "G0/1-2"], "trunk_encapsulation": True},
    "WS-C2960-24TT-L": {"ports": ["Fa0/1-24", "G0/1-2"], "trunk_encapsulation": False},
}


def vlan_list(ids):
    return ",".join(str(i) for i in sorted(ids))


def compress(ports):
    """['G1/0/5','G1/0/6','G1/0/7','G1/0/9'] -> runs of consecutive ports, as "interface" arguments."""
    out, run = [], []

    def flush():
        if run:
            out.append(range_text(list(run)))

    for p in ports:
        if run and p.rsplit("/", 1)[0] == run[-1].rsplit("/", 1)[0] and int(p.rsplit("/", 1)[1]) == int(run[-1].rsplit("/", 1)[1]) + 1:
            run.append(p)
        else:
            flush()
            run = [p]
    flush()
    return out


def load_secrets(path):
    if path and os.path.exists(path):
        with open(path) as f:
            s = yaml.safe_load(f) or {}
        return {k: s.get(k, v) for k, v in PLACEHOLDERS.items()}
    return dict(PLACEHOLDERS)


def env():
    return jinja2.Environment(loader=jinja2.FileSystemLoader(os.path.join(ROOT, "templates")),
                              undefined=jinja2.StrictUndefined, keep_trailing_newline=True,
                              trim_blocks=False, lstrip_blocks=False)


def common(net, name, model, secrets, vty_last):
    return {"name": name, "model": model, "site": net.raw["site"], "secrets": secrets, "vty_last": vty_last}


def switch_common(net, name):
    s = net.raw["switches"][name]
    m = MODELS[s["model"]]
    dists = net.dist()
    root = [v.id for v in net.routed_vlans if v.primary == name]
    second = [v.id for v in net.routed_vlans if v.primary != name] if name in dists else []
    channels = [{"id": cid, "range": range_text(ports), "peer": peer, "ports": ports}
                for cid, ports, peer, _ in sorted(net.channels_of(name))]
    used = {p for c in channels for p in c["ports"]}
    if s["role"] == "access":
        for spec in s["access_ports"]:
            used |= set(expand_ports(spec))
        unused = [range_text(u) for u in s["unused"]]
    else:
        used.add(s["uplink"]["port"])
        w = net.raw.get("wlc")
        if w and w["switch"] == name:
            used.add(w["port"])
        allports = expand_ports(m["ports"])
        unused = compress([p for p in allports if p not in used])
    return {
        "role": s["role"], "stp": net.raw["stp"],
        "root_vlans": vlan_list(root) if root else "", "secondary_vlans": vlan_list(second) if second else "",
        "vlans": [{"id": v.id, "name": v.name} for v in sorted(net.vlans.values(), key=lambda v: v.id)],
        "channels": channels, "parking": net.parking,
        "trunk_vlans": vlan_list(v.id for v in net.routed_vlans),
        "trunk_encapsulation": m["trunk_encapsulation"], "unused": unused,
    }


def distribution_ctx(net, name, secrets):
    s = net.raw["switches"][name]
    a = net.raw["addressing"]
    r1 = net.raw["routers"]["R1"]
    peer_if = next(i for i in r1["interfaces"].values() if i.get("peer") == name)
    my_host = a["d1_host"] if name == net.dist()[0] else a["d2_host"]
    svis = []
    for v in net.routed_vlans:
        prio = 110 if v.primary == name else 100
        svis.append({
            "id": v.id, "name": v.name, "primary": v.primary,
            "ip": v.host(my_host), "mask": v.v4.netmask, "vip": v.host(a["gateway_host"]),
            "ip6": v.host6(my_host), "lla": f"fe80::{my_host}", "g6": 1000 + v.id, "priority": prio,
            "dhcp": v.dhcp, "dhcpv6_stateless": v.dhcpv6_stateless,
        })
    w = net.raw.get("wlc")
    wctx = None
    if w and w["switch"] == name:
        mg = next(v.id for v in net.routed_vlans if ipaddress.ip_interface(w["mgmt"]).ip in v.v4)
        wctx = {"name": w["name"], "long": long_name(w["port"]), "vlans": vlan_list({w["wlan"]["vlan"], mg})}
    ctx = common(net, name, s["model"], secrets, 15)
    ctx.update(switch_common(net, name))
    ctx.update({
        "uplink": {"long": long_name(s["uplink"]["port"]), "v4": ipaddress.ip_interface(s["uplink"]["v4"]),
                   "v6": s["uplink"]["v6"]},
        "svis": svis, "wlc": wctx,
        "dhcp_server": ipaddress.ip_interface(r1["loopback"]["v4"]).ip,
        "dhcp_server6": ipaddress.ip_interface(r1["loopback"]["v6"]).ip,
        "r1_v4": ipaddress.ip_interface(peer_if["v4"]).ip,
        "r1_v6": ipaddress.ip_interface(peer_if["v6"]).ip,
    })
    return ctx


def access_ctx(net, name, secrets):
    s = net.raw["switches"][name]
    mgmt = ipaddress.ip_interface(s["mgmt"])
    mvlan = next(v for v in net.routed_vlans if mgmt.ip in v.v4)
    ports = []
    for spec, p in s["access_ports"].items():
        ports.append({"range": range_text(spec), "desc": p["desc"], "vlan": p["vlan"],
                      "max_mac": p.get("max_mac", 2), "violation": p.get("violation", "restrict"),
                      "snoop": net.vlans[p["vlan"]].dhcp})
    ctx = common(net, name, s["model"], secrets, 15)
    ctx.update(switch_common(net, name))
    ctx.update({"mgmt": mgmt, "mgmt_vlan": mvlan.id, "mgmt_gw": mvlan.host(net.raw["addressing"]["gateway_host"]),
                "user_vlans": vlan_list(v.id for v in net.routed_vlans if v.dhcp), "ports": ports})
    return ctx


def router_ctx(net, name, secrets):
    r = net.raw["routers"][name]
    a = net.raw["addressing"]
    sw = net.raw["switches"]
    backup = next(d for d in net.dist() if d != r["primary_path"])

    def hop(d, fam):
        return ipaddress.ip_interface(sw[d]["uplink"][fam]).ip

    pools = []
    for v in net.routed_vlans:
        if v.dhcp:
            pools.append({"name": v.name, "network": v.v4.network_address, "mask": v.v4.netmask,
                          "gw": v.host(a["gateway_host"]), "excl_first": v.host(1), "excl_last": v.host(a["dhcp_excluded_last"])})
    res = []
    for x in net.raw.get("dhcp_reservations", []):
        v = net.vlans[x["vlan"]]
        res.append({"name": x["name"], "ip": x["ip"], "mask": v.v4.netmask, "mac": x["mac"], "gw": v.host(a["gateway_host"])})
    ctx = common(net, name, r["model"], secrets, 4)
    ctx.update({
        "lo": {"v4": ipaddress.ip_interface(r["loopback"]["v4"]), "v6": r["loopback"]["v6"]},
        "interfaces": [{"long": long_name(k), "desc": i["desc"], "v4": ipaddress.ip_interface(i["v4"]),
                        "v6": i["v6"], "peer": i.get("peer")} for k, i in r["interfaces"].items()],
        "pools": pools, "reservations": res, "dns": " ".join(a["dns"]), "dns6": " ".join(a["dns_v6"]),
        "dhcpv6_pool": "IOT-STATELESS",
        "default_v4": r["default_route"]["v4"], "default_v6": r["default_route"]["v6"],
        "summary": ipaddress.ip_network(r["campus_summary"]["v4"]), "summary6": r["campus_summary"]["v6"],
        "primary_hop": hop(r["primary_path"], "v4"), "backup_hop": hop(backup, "v4"),
        "primary_hop6": hop(r["primary_path"], "v6"), "backup_hop6": hop(backup, "v6"),
        "floating_ad": r["floating_ad"],
        "host_routes": [{"dest": h["dest"], "hop": hop(h["via"], "v4")} for h in r.get("host_routes", [])],
    })
    return ctx


def render_all(net, secrets_path=None):
    secrets = load_secrets(secrets_path)
    e = env()
    out = {}
    for name in net.raw["routers"]:
        out[name] = e.get_template("router.j2").render(**router_ctx(net, name, secrets))
    for name in net.dist():
        out[name] = e.get_template("distribution.j2").render(**distribution_ctx(net, name, secrets))
    for name in net.access():
        out[name] = e.get_template("access.j2").render(**access_ctx(net, name, secrets))
    clean = {}
    for k, v in out.items():
        lines = [line.rstrip() for line in v.splitlines()]
        kept = [line for i, line in enumerate(lines) if line or (i and lines[i - 1])]   # no double blank lines
        clean[k] = "\n".join(kept).strip("\n") + "\n"
    return clean
