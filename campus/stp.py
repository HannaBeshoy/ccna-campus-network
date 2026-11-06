"""stp.py - predict the spanning-tree result of the design, per VLAN, before any cable is plugged in.

The four steps of module 5: elect the root bridge (lowest bridge ID = priority + VLAN number,
then MAC address), find each switch's root port (lowest root path cost, then lowest sender
bridge ID), elect one designated port per link (lower root path cost, then lower bridge ID),
and every other port is an alternate (blocking). Costs are Cisco's short method; an
EtherChannel counts as one link with the cost of its total bandwidth.

MAC addresses are not known before the lab day, so the switch name stands in for the MAC as
the last tie-breaker; any decision that needed it is reported, because on real hardware it
could go the other way. With this design, none does.
"""
import heapq

from .model import expand_ports

COST = {10: 100, 100: 19, 200: 12, 400: 9, 1000: 4, 2000: 3, 4000: 2, 10000: 2}


def link_cost(speed_mbps, members):
    bw = speed_mbps * members
    best = min(COST, key=lambda k: abs(k - bw))
    return COST[best]


def predict(net):
    """{vlan: {"root": name, "ports": [(device, port, role, root_cost)], "ties": [...]}}."""
    st = net.raw["stp"]
    links = []
    for ec in net.raw["etherchannels"]:
        n = len(expand_ports(ec["a_ports"]))
        links.append((ec["a"], ec["b"], f"Po{ec['id']}", link_cost(ec["speed"], n)))
    bridges = net.dist() + net.access()
    out = {}
    for v in net.routed_vlans:
        ties = []
        prio = {}
        for b in bridges:
            if b in net.dist():
                prio[b] = (st["root_priority"] if v.primary == b else st["secondary_priority"]) + v.id
            else:
                prio[b] = 32768 + v.id
        bid = {b: (prio[b], b) for b in bridges}
        root = min(bridges, key=lambda b: bid[b])
        if sum(1 for b in bridges if prio[b] == prio[root]) > 1:
            ties.append(f"VLAN {v.id}: root election decided by MAC address")
        # root path cost (Dijkstra)
        cost = {root: 0}
        pq = [(0, root)]
        while pq:
            c, u = heapq.heappop(pq)
            if c > cost.get(u, 1e9):
                continue
            for a, b, _, w in links:
                for x, y in ((a, b), (b, a)):
                    if x == u and c + w < cost.get(y, 1e9):
                        cost[y] = c + w
                        heapq.heappush(pq, (c + w, y))
        roles = {}
        # root ports
        for b in bridges:
            if b == root:
                continue
            cands = []
            for a, c, name, w in links:
                if b in (a, c):
                    peer = c if b == a else a
                    cands.append(((cost[peer] + w, bid[peer], name), name))
            cands.sort()
            if len(cands) > 1 and cands[0][0][0] == cands[1][0][0] and cands[0][0][1][0] == cands[1][0][1][0]:
                ties.append(f"VLAN {v.id}: {b}'s root port decided by MAC address")
            roles[(b, cands[0][1])] = "Root"
        # designated / alternate per link
        for a, c, name, w in links:
            ka, kc = (cost[a], bid[a]), (cost[c], bid[c])
            if ka[0] == kc[0] and ka[1][0] == kc[1][0]:
                ties.append(f"VLAN {v.id}: designated port on {name} decided by MAC address")
            desig, other = (a, c) if ka < kc else (c, a)
            roles[(desig, name)] = "Desg"
            if (other, name) not in roles:
                roles[(other, name)] = "Altn"
        ports = sorted((d, p, r, cost[d]) for (d, p), r in roles.items())
        out[v.id] = {"root": root, "ports": ports, "ties": ties}
    return out


def report(pred):
    lines = []
    for vid, r in sorted(pred.items()):
        lines.append(f"VLAN {vid}: root bridge {r['root']}")
        for d, p, role, c in r["ports"]:
            state = "BLK" if role == "Altn" else "FWD"
            lines.append(f"  {d:3} {p:5} {role} {state}  (root path cost of {d}: {c})")
        for t in r["ties"]:
            lines.append(f"  NOTE {t}")
    return "\n".join(lines)
