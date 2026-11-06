"""verify.py - read the show commands saved on the lab day and compare them with the design.

Save each command's output as <device>_<command>.txt, spaces as underscores, for example
D1_show_standby_brief.txt. Supported commands:

  show vlan brief                 every VLAN exists with its name; access ports in the right VLAN
  show etherchannel summary       every Port-channel is in use (SU), LACP, all members bundled (P)
  show standby brief              HSRP: the primary switch of each VLAN is Active, the other Standby
  show spanning-tree              root bridge and every Port-channel's role, as stp.py predicts
  show ip route                   R1 has the default, campus and host routes, through the right next hop

Files that are not there are skipped, so a partial capture can be checked too.
"""
import os
import re

from .model import expand_ports
from .stp import predict


def short(port):
    p = port.replace("FastEthernet", "Fa").replace("GigabitEthernet", "Gi").replace("Port-channel", "Po")
    return re.sub(r"^G(?=\d)", "Gi", p)


def parse_vlan_brief(text):
    """{vlan id: (name, [ports])}"""
    vlans, last = {}, None
    for line in text.splitlines():
        m = re.match(r"^(\d+)\s+(\S+)\s+(active|act/unsup|suspended)\s*(.*)$", line)
        if m:
            last = int(m.group(1))
            vlans[last] = (m.group(2), [p.strip() for p in m.group(4).split(",") if p.strip()])
        elif last is not None and re.match(r"^\s{20,}\S", line):
            vlans[last][1].extend(p.strip() for p in line.split(",") if p.strip())
    return vlans


def parse_etherchannel(text):
    """{id: {"flags": "SU", "protocol": "LACP", "members": {port: flag}}}"""
    out = {}
    for line in text.splitlines():
        m = re.match(r"^(\d+)\s+Po(\d+)\((\w+)\)\s+(\S+)\s*(.*)$", line)
        if m:
            members = dict(re.findall(r"(\S+?)\((\w+)\)", m.group(5)))
            out[int(m.group(2))] = {"flags": m.group(3), "protocol": m.group(4), "members": members}
    return out


def parse_standby(text):
    """{(vlan, group): state}"""
    out = {}
    for line in text.splitlines():
        m = re.match(r"^Vl(\d+)\s+(\d+)\s+(\d+)\s+P?\s*(Active|Standby|Listen|Speak|Init)\b", line)
        if m:
            out[(int(m.group(1)), int(m.group(2)))] = m.group(4)
    return out


def parse_spanning_tree(text):
    """{vlan: {"root": bool, "ports": {port: role}}}"""
    out, cur = {}, None
    for line in text.splitlines():
        m = re.match(r"^VLAN0*(\d+)", line)
        if m:
            cur = int(m.group(1))
            out[cur] = {"root": False, "ports": {}}
            continue
        if cur is None:
            continue
        if "This bridge is the root" in line:
            out[cur]["root"] = True
        m = re.match(r"^(\S+)\s+(Root|Desg|Altn|Back|Mstr)\s+(FWD|BLK|LRN|LIS|DIS|BKN)", line)
        if m:
            out[cur]["ports"][short(m.group(1))] = m.group(2)
    return out


def parse_routes(text):
    """[(code, prefix, next hop or interface)]"""
    out = []
    for line in text.splitlines():
        m = re.match(r"^(S\*|S|C|L|D|O)\s+(\S+/\d+)(?:\s+\[\d+/\d+\])?(?:\s+via\s+([0-9A-Fa-f.:]+)|\s+is directly connected, (\S+))", line.strip())
        if m:
            out.append((m.group(1), m.group(2), m.group(3) or m.group(4)))
    return out


def verify_folder(net, folder):
    lines, ok = [], True

    def result(good, text):
        nonlocal ok
        ok = ok and good
        lines.append(("PASS " if good else "FAIL ") + text)

    def read(dev, cmd):
        p = os.path.join(folder, f"{dev}_{cmd.replace(' ', '_')}.txt")
        return open(p).read() if os.path.exists(p) else None

    switches = net.dist() + net.access()
    for dev in switches:
        t = read(dev, "show vlan brief")
        if t:
            vl = parse_vlan_brief(t)
            for v in net.vlans.values():
                result(v.id in vl and vl[v.id][0] == v.name, f"{dev}: VLAN {v.id} {v.name} exists")
            sw = net.raw["switches"][dev]
            for spec, cfg in sw.get("access_ports", {}).items():
                ports = [short(p) for p in expand_ports(spec)]
                got = set(vl.get(cfg["vlan"], ("", []))[1])
                result(set(ports) <= got, f"{dev}: {spec} in VLAN {cfg['vlan']}")
        t = read(dev, "show etherchannel summary")
        if t:
            ec = parse_etherchannel(t)
            for cid, ports, peer, _ in net.channels_of(dev):
                e = ec.get(cid)
                good = bool(e) and e["flags"] == "SU" and e["protocol"] == "LACP" and \
                    {short(p) for p in ports} == set(e["members"]) and all(f == "P" for f in e["members"].values())
                result(good, f"{dev}: Port-channel{cid} to {peer} is up, LACP, {len(ports)} members bundled")
    for dev in net.dist():
        t = read(dev, "show standby brief")
        if t:
            sb = parse_standby(t)
            for v in net.routed_vlans:
                want = "Active" if v.primary == dev else "Standby"
                result(sb.get((v.id, v.id)) == want, f"{dev}: HSRP VLAN {v.id} IPv4 is {want}")
                result(sb.get((v.id, 1000 + v.id)) == want, f"{dev}: HSRP VLAN {v.id} IPv6 is {want}")
    pred = predict(net)
    for dev in switches:
        t = read(dev, "show spanning-tree")
        if t:
            st = parse_spanning_tree(t)
            for vid, p in pred.items():
                got = st.get(vid)
                if got is None:
                    result(False, f"{dev}: VLAN {vid} missing from show spanning-tree")
                    continue
                result(got["root"] == (p["root"] == dev), f"{dev}: VLAN {vid} root bridge is {p['root']}")
                for d, port, role, _ in p["ports"]:
                    if d == dev:
                        result(got["ports"].get(port) == role, f"{dev}: VLAN {vid} {port} is {role}")
    t = read("R1", "show ip route")
    if t:
        routes = parse_routes(t)
        r1 = net.raw["routers"]["R1"]
        sw = net.raw["switches"]
        summ = r1["campus_summary"]["v4"]
        hop = sw[r1["primary_path"]]["uplink"]["v4"].split("/")[0]
        result(any(c == "S*" and p == "0.0.0.0/0" and h == r1["default_route"]["v4"] for c, p, h in routes),
               f"R1: default route via {r1['default_route']['v4']}")
        backup = next(d for d in net.dist() if d != r1["primary_path"])
        bhop = sw[backup]["uplink"]["v4"].split("/")[0]
        result(any(c == "S" and p == summ and h == hop for c, p, h in routes) and
               not any(p == summ and h == bhop for c, p, h in routes),
               f"R1: campus route {summ} via {r1['primary_path']} ({hop}), the floating route via {backup} not installed")
        for h in r1.get("host_routes", []):
            via = sw[h["via"]]["uplink"]["v4"].split("/")[0]
            result(any(p == f"{h['dest']}/32" and n == via for c, p, n in routes), f"R1: host route {h['dest']}/32 via {h['via']}")
    if not lines:
        lines.append(f"no capture files found in {folder}")
        ok = False
    lines.append(f"{sum(l.startswith('PASS') for l in lines)} passed, {sum(l.startswith('FAIL') for l in lines)} failed")
    return ok, lines
