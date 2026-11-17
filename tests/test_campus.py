"""Tests: the model checks, the generated configurations, the policy lint, the running-config diff,
the spanning-tree prediction and the lab-day verifier."""
import copy
import os
import shutil

import pytest

from campus import model
from campus.diff import diff_configs
from campus.generate import render_all
from campus.iosconf import expand_range, parse
from campus.lint import lint_text
from campus.routing import Lab, round_trip
from campus.stp import predict
from campus.verify import parse_etherchannel, parse_routes, parse_spanning_tree, verify_folder

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIX = os.path.join(ROOT, "tests", "fixtures", "lab")


@pytest.fixture
def net():
    return model.load(os.path.join(ROOT, "network.yaml"))


@pytest.fixture
def cfg(net):
    return render_all(net)


def changed(net, edit):
    raw = copy.deepcopy(net.raw)
    edit(raw)
    return model.load_from(raw)


# --- the model -------------------------------------------------------------------------------
def test_the_design_is_consistent(net):
    assert model.validate(net) == []


def test_port_ranges():
    assert model.expand_ports("Fa0/1-3") == ["Fa0/1", "Fa0/2", "Fa0/3"]
    assert model.expand_ports("G1/0/24") == ["G1/0/24"]
    assert expand_range("FastEthernet0/21 - 22") == ["FastEthernet0/21", "FastEthernet0/22"]
    with pytest.raises(model.ModelError):
        model.expand_ports("Fa0/5-1")


@pytest.mark.parametrize("edit, expected", [
    (lambda r: r["vlans"][20].update(v4="10.20.10.128/25"), "overlaps"),
    (lambda r: r["vlans"][30].update(primary="A1"), "not a distribution switch"),
    (lambda r: r["vlans"][40].update(v6="2001:db8:acad:40::/80"), "must be a /64"),
    (lambda r: r["switches"]["A1"]["access_ports"]["Fa0/9-16"].update(vlan=60), "does not exist"),
    (lambda r: r["switches"]["A1"]["unused"].append("Fa0/1"), "listed as unused"),
    (lambda r: r["switches"]["A2"]["unused"].remove("Fa0/20"), "not assigned"),
    (lambda r: r["etherchannels"][1].update(b_ports="Fa0/21-23"), "different numbers of members"),
    (lambda r: r["etherchannels"].pop(2), "must have an uplink to each distribution switch"),
    (lambda r: r["stp"].update(root_priority=30000), "not a multiple of 4096"),
    (lambda r: r["switches"]["D1"]["uplink"].update(v4="10.20.255.9/30"), "not in the same subnet"),
    (lambda r: r["dhcp_reservations"][0].update(ip="10.20.30.5"), "excluded range"),
    (lambda r: r["dhcp_reservations"][0].update(ip="10.20.20.50"), "is not in VLAN 30"),
    (lambda r: r["isp"]["uplink"].update(v4="203.0.113.5/30"), "not in the same IPv4 subnet"),
    (lambda r: r["isp"]["server"].update(v4="64.100.1.1"), "not a usable host"),
    (lambda r: r["isp"]["server_lan"].update(v4="10.20.100.1/24"), "overlaps the campus"),
    (lambda r: r["routers"]["R1"]["default_route"].update(v4="203.0.113.3"), "does not point at the ISP"),
    (lambda r: r["switches"]["D1"]["uplink"].update(lla="fe80::1"), "both ends of the link use fe80::1"),
    (lambda r: r["routers"]["R1"]["interfaces"]["G0/1"].update(lla="2001:db8::1"), "not a link-local address"),
    (lambda r: r["routers"]["R1"]["host_routes"][0].update(dest6="2001:db8:beef::20"), "not in a campus VLAN"),
    (lambda r: r["vlans"][20].update(dhcpv6="managed"), "must be stateless or stateful"),
    (lambda r: r["switches"]["A1"]["access_ports"]["Fa0/1-8"].update(voice=10), "must differ from the data VLAN"),
    (lambda r: r["switches"]["A1"]["access_ports"]["Fa0/1-8"].update(voice=999), "voice VLAN 999 does not exist"),
    (lambda r: r["switches"]["A1"]["access_ports"]["Fa0/1-8"].update(max_mac=1), "maximum 2 or more"),
    (lambda r: r["site"]["hardening"].update(min_password_length=6), "minimum password length below 8"),
    (lambda r: r["site"]["hardening"].update(exec_timeout=0), "exec-timeout should be 1 to 30"),
    (lambda r: r["vlans"].update({1: {"name": "default", "v4": "10.20.1.0/24", "v6": "2001:db8:acad:1::/64", "primary": "D1"}}),
     "VLAN 1 must not carry traffic"),
])
def test_validation_catches_mistakes(net, edit, expected):
    problems = model.validate(changed(net, edit))
    assert any(expected in p for p in problems), problems


# --- generated configurations -------------------------------------------------------------------
def test_one_config_per_device_with_placeholders_not_passwords(cfg):
    assert set(cfg) == {"R1", "ISP", "D1", "D2", "A1", "A2"}
    for text in cfg.values():
        assert "enable secret <ENABLE_SECRET>" in text and "secret <ADMIN_SECRET>" in text


def test_secrets_file_fills_the_placeholders(net, tmp_path):
    p = tmp_path / "secrets.yaml"
    p.write_text("enable: Lab-Enable-1\nadmin: Lab-Admin-1\n")
    text = render_all(net, str(p))["A1"]
    assert "enable secret Lab-Enable-1" in text and "<ENABLE_SECRET>" not in text


def test_hsrp_and_stp_root_follow_the_primary(cfg):
    d1, d2 = parse(cfg["D1"]), parse(cfg["D2"])
    assert "standby 10 priority 110" in d1["interface vlan10"] and "standby 10 ip 10.20.10.1" in d1["interface vlan10"]
    assert not any("priority" in l for l in d2["interface vlan10"] if l.startswith("standby 10 "))
    assert "standby 30 priority 110" in d2["interface vlan30"]
    assert "spanning-tree vlan 10,20,99 priority 24576" in d1[""]
    assert "spanning-tree vlan 30,40,50 priority 24576" in d2[""]
    assert "spanning-tree vlan 10,20,99 priority 28672" in d2[""]


def test_relays_point_at_the_dhcp_server(cfg):
    d1 = parse(cfg["D1"])
    for v in (10, 20, 30, 40):
        assert "ip helper-address 10.20.254.1" in d1[f"interface vlan{v}"]
    assert not any("helper" in l for l in d1["interface vlan99"])
    assert "ipv6 dhcp relay destination 2001:db8:acad:254::1" in d1["interface vlan30"]
    assert "ipv6 nd other-config-flag" in d1["interface vlan30"]


def test_router_routes_and_pools(cfg):
    r1 = parse(cfg["R1"])
    g = r1[""]
    assert "ip route 0.0.0.0 0.0.0.0 203.0.113.1" in g
    assert "ip route 10.20.0.0 255.255.128.0 10.20.255.2" in g
    assert "ip route 10.20.0.0 255.255.128.0 10.20.255.6 10" in g        # floating static
    assert "ip route 10.20.99.20 255.255.255.255 10.20.255.6" in g       # host route
    assert "ipv6 route 2001:db8:acad::/56 gigabitethernet0/2 fe80::3 10" in g   # fully specified
    assert "ip dhcp excluded-address 10.20.30.1 10.20.30.20" in g
    assert r1["ip dhcp pool iot"] == ["network 10.20.30.0 255.255.255.0", "default-router 10.20.30.1",
                                      "dns-server 10.20.99.53", "domain-name hu01.test"]
    assert "hardware-address 0000.0000.0001" in r1["ip dhcp pool esp32-node-1"]


def test_isp_routes_back_to_the_campus(cfg):
    isp = parse(cfg["ISP"])
    assert "ip route 10.20.0.0 255.255.0.0 203.0.113.2" in isp[""]          # the whole site block
    assert "ipv6 route 2001:db8:acad::/48 gigabitethernet0/0 fe80::1" in isp[""]
    assert "ip address 64.100.1.1 255.255.255.0" in isp["interface gigabitethernet0/1"]
    assert "ipv6 address 2001:db8:100:1::1/64" in isp["interface gigabitethernet0/1"]


def test_r1_discards_the_unused_part_of_the_site_block(cfg):
    r1 = parse(cfg["R1"])
    assert "ip route 10.20.0.0 255.255.0.0 null0" in r1[""]
    assert "ipv6 route 2001:db8:acad::/48 null0" in r1[""]


def test_unused_site_address_is_discarded_not_looped(net):
    ok, hops = Lab(net).trace("server", "10.20.200.1")
    assert not ok and "Null0" in hops[-1] and "loop" not in hops[-1]
    ok, hops = Lab(net, ["D1", "D2"]).trace("server", "10.20.10.50")        # no way into the campus
    assert not ok and hops[-1].startswith("R1:") and "Null0" in hops[-1]


def test_r1_loopback_reaches_the_server_with_extended_ping(net):
    lab = Lab(net)
    assert lab.ping("R1", "10.20.254.1", "64.100.1.10")[0]
    assert lab.ping("R1", "2001:db8:acad:254::1", "2001:db8:100:1::a")[0]


def test_loopback_outside_the_site_block_is_refused(net):
    bad = changed(net, lambda r: r["routers"]["R1"].update(loopback={"v4": "10.30.0.1/32", "v6": "2001:db8:acad:254::1/128"}))
    assert any("Loopback0" in x and "site block" in x for x in model.validate(bad))


def test_etherchannel_members_match_on_both_ends(cfg, net):
    for ec in net.raw["etherchannels"]:
        for dev, ports in ((ec["a"], ec["a_ports"]), (ec["b"], ec["b_ports"])):
            s = parse(cfg[dev])
            for p in model.expand_ports(ports):
                body = s["interface " + model.long_name(p).lower()]
                assert f"channel-group {ec['id']} mode active" in body
            assert f"interface port-channel{ec['id']}" in s


def test_every_physical_port_is_configured(cfg):
    s = parse(cfg["D1"])
    ports = [k for k in s if k.startswith("interface gigabitethernet1/0/")]
    assert len(ports) == 24
    a2 = parse(cfg["A2"])
    assert len([k for k in a2 if k.startswith("interface fastethernet0/")]) == 24


# --- policy lint ---------------------------------------------------------------------------------
def test_generated_configs_pass_the_policy(cfg):
    for name, text in cfg.items():
        assert lint_text(text) == [], name


@pytest.mark.parametrize("old, new, expected", [
    (" spanning-tree bpduguard enable\n", "", "BPDU guard is off"),
    (" switchport port-security\n", "", "port security is off"),
    (" switchport trunk native vlan 999\n", "", "native VLAN 1"),
    (" switchport nonegotiate\n channel-group 11", " channel-group 11", "still sends DTP"),
    (" transport input ssh", " transport input ssh telnet", "SSH only"),
    ("ip ssh version 2\n", "", "SSH version 2"),
    ("ip dhcp snooping vlan 10,20,30,40,50\n", "", "DHCP snooping"),
    (" switchport access vlan 999\n shutdown", " shutdown", "unused port left in VLAN 1"),
])
def test_lint_finds_a_weakened_switch(cfg, old, new, expected):
    text = cfg["A1"].replace(old, new, 1)
    assert text != cfg["A1"]
    assert any(expected in i for i in lint_text(text)), lint_text(text)


@pytest.mark.parametrize("old, new, expected", [
    ("security passwords min-length 10\n", "", "min-length is missing"),
    ("security passwords min-length 10", "security passwords min-length 6", "below 8"),
    ("login block-for 120 attempts 3 within 60\n", "", "login block-for is missing"),
    (" exec-timeout 10 0\nline vty", " exec-timeout 0 0\nline vty", "line con 0: exec-timeout"),
])
def test_lint_finds_weak_device_access(cfg, old, new, expected):
    text = cfg["R1"].replace(old, new, 1)
    assert text != cfg["R1"]
    assert any(expected in i for i in lint_text(text)), lint_text(text)


def test_hardening_on_every_device(cfg):
    for name, text in cfg.items():
        g = parse(text)[""]
        assert "security passwords min-length 10" in g, name
        assert "login block-for 120 attempts 3 within 60" in g, name


def test_short_lab_password_is_refused(net, tmp_path):
    from campus.generate import SecretsError
    p = tmp_path / "secrets.yaml"
    p.write_text("enable: cisco123\nadmin: Lab-Admin-Pass\n")
    with pytest.raises(SecretsError, match="enable shorter than 10"):
        render_all(net, str(p))


def test_lint_flags_enable_password():
    assert any("enable password" in i for i in lint_text("hostname X\nenable password cisco\n"))


# --- running-config diff ----------------------------------------------------------------------------
def test_diff_ignores_how_ios_prints_a_running_config(cfg):
    """A running-config lists interfaces one by one, in capitals, hides secrets and 'no shutdown'."""
    run = []
    for line in cfg["A1"].splitlines():
        if line.startswith("interface range FastEthernet0/1 - 8"):
            for n in range(1, 9):
                run.append(f"interface FastEthernet0/{n}")
                run += [l for l in BODY_A1_1_8]
            continue
        run.append(line)
    text = "\n".join(run).replace("enable secret <ENABLE_SECRET>", "enable secret 9 $9$abc").replace(
        "no ip domain-lookup", "no ip domain lookup").replace(" no shutdown\n", "\n")
    missing = diff_configs(cfg["A1"], text)
    assert missing == {}, missing


BODY_A1_1_8 = [" description staff desk, IP phone", " switchport access vlan 10", " switchport mode access",
               " switchport voice vlan 50", " mls qos trust cos",
               " switchport nonegotiate", " switchport port-security maximum 3", " switchport port-security",
               " switchport port-security violation restrict", " switchport port-security mac-address sticky",
               " switchport port-security mac-address sticky 0011.2233.4455 vlan 10",
               " spanning-tree portfast", " spanning-tree bpduguard enable", " ip dhcp snooping limit rate 10"]


def test_diff_reports_what_was_removed(cfg):
    text = cfg["D1"].replace(" standby 10 preempt\n", "").replace("ip route 0.0.0.0 0.0.0.0 10.20.255.1\n", "")
    missing = diff_configs(cfg["D1"], text)
    assert missing["interface vlan10"] == ["standby 10 preempt"]
    assert "ip route 0.0.0.0 0.0.0.0 10.20.255.1" in missing[""]


# --- spanning tree ---------------------------------------------------------------------------------
def test_stp_prediction(net):
    p = predict(net)
    assert p[10]["root"] == "D1" and p[30]["root"] == "D2"
    roles = {(d, port): r for d, port, r, _ in p[10]["ports"]}
    assert roles[("A1", "Po11")] == "Root" and roles[("A1", "Po12")] == "Altn"
    assert roles[("D2", "Po1")] == "Root" and roles[("D2", "Po12")] == "Desg"
    # one blocked port per access switch per VLAN, none on the distribution switches
    for vid, r in p.items():
        blocked = [(d, port) for d, port, role, _ in r["ports"] if role == "Altn"]
        assert sorted(d for d, _ in blocked) == ["A1", "A2"], (vid, blocked)
        assert r["ties"] == []


def test_stp_reports_a_mac_tie(net):
    n = changed(net, lambda r: r["stp"].update(secondary_priority=24576 + 4096))
    n.raw["stp"]["root_priority"] = 28672
    assert any("MAC" in t for t in predict(n)[10]["ties"])


# --- lab-day verification --------------------------------------------------------------------------
def test_parsers():
    ec = parse_etherchannel(open(os.path.join(FIX, "A1_show_etherchannel_summary.txt")).read())
    assert ec[11] == {"flags": "SU", "protocol": "LACP", "members": {"Fa0/21": "P", "Fa0/22": "P"}}
    st = parse_spanning_tree(open(os.path.join(FIX, "A1_show_spanning-tree.txt")).read())
    assert st[30]["ports"] == {"Fa0/17": "Desg", "Po11": "Altn", "Po12": "Root"}
    routes = parse_routes(open(os.path.join(FIX, "R1_show_ip_route.txt")).read())
    assert ("S*", "0.0.0.0/0", "203.0.113.1") in routes and ("S", "10.20.99.20/32", "10.20.255.6") in routes


def test_verify_passes_on_a_good_capture(net):
    ok, lines = verify_folder(net, FIX)
    assert ok, [l for l in lines if l.startswith("FAIL")]
    assert lines[-1] == "51 passed, 0 failed"


@pytest.mark.parametrize("file, old, new, expected", [
    ("A1_show_etherchannel_summary.txt", "Fa0/24(P)", "Fa0/24(s)", "Port-channel12 to D2"),
    ("D1_show_standby_brief.txt", "Vl30        30   100 P Standby", "Vl30        30   100 P Active ", "HSRP VLAN 30 IPv4 is Standby"),
    ("A1_show_spanning-tree.txt", "Po12                Altn BLK", "Po12                Root FWD", "VLAN 10 Po12 is Altn"),
    ("R1_show_ip_route.txt", "S        10.20.0.0/17 [1/0] via 10.20.255.2", "S        10.20.0.0/17 [10/0] via 10.20.255.6",
     "floating route via D2 not installed"),
    ("A1_show_vlan_brief.txt", "30   IOT  ", "30   IOT2 ", "VLAN 30 IOT exists"),
])
def test_verify_finds_a_problem(net, tmp_path, file, old, new, expected):
    d = tmp_path / "cap"
    shutil.copytree(FIX, d)
    p = d / file
    text = p.read_text()
    assert old in text
    p.write_text(text.replace(old, new, 1))
    ok, lines = verify_folder(net, str(d))
    assert not ok
    assert any(l.startswith("FAIL") and expected in l for l in lines), [l for l in lines if l.startswith("FAIL")]


# --- routing tables and end-to-end paths --------------------------------------------------------
def test_routing_table_of_r1(net):
    t = {(str(p), ad, str(h) if h else None) for p, ad, h, _, _ in Lab(net).devices["R1"].table()}
    assert ("0.0.0.0/0", 1, "203.0.113.1") in t
    assert ("10.20.0.0/17", 1, "10.20.255.2") in t
    assert not any(p == "10.20.0.0/17" and ad == 10 for p, ad, _ in t)       # the floating route waits
    assert ("2001:db8:acad::/56", 1, "fe80::2") in t


def test_floating_route_replaces_the_primary_when_its_link_goes_down(net):
    t = {(str(p), ad, str(h) if h else None) for p, ad, h, _, _ in Lab(net, ["R1:G0/1"]).devices["R1"].table()}
    assert ("10.20.0.0/17", 10, "10.20.255.6") in t
    assert ("2001:db8:acad::/56", 10, "fe80::3") in t
    assert not any(p == "10.20.255.0/30" for p, _, _ in t)                   # the connected route is gone too


def test_longest_match_beats_the_summary(net):
    ok, hops = Lab(net).trace("server", "10.20.99.20")
    assert "R1: 10.20.99.20/32 [1/0] via 10.20.255.6" in " ".join(hops)


@pytest.mark.parametrize("vlan", [10, 20, 30, 40, 99])
@pytest.mark.parametrize("family", [4, 6])
def test_every_vlan_reaches_the_server_and_back(net, vlan, family):
    ok1, ok2, h1, h2 = round_trip(net, vlan, family)
    assert ok1 and ok2, (h1, h2)
    assert h1[1].startswith(net.vlans[vlan].primary)                         # leaves through its HSRP gateway


@pytest.mark.parametrize("dead", ["D1", "D2"])
def test_a_distribution_switch_can_fail(net, dead):
    for v in net.routed_vlans:
        for family in (4, 6):
            ok1, ok2, h1, h2 = round_trip(net, v.id, family, [dead])
            assert ok1 and ok2, (dead, v.id, family, h1, h2)
            assert dead not in " ".join(h1 + h2)


def test_trace_reports_a_missing_route(net):
    lab = Lab(net, ["ISP:G0/1"])
    ok, hops = lab.trace("vlan10", "server")
    assert not ok
    assert hops[-1] == "ISP: no route to 64.100.1.10 - dropped"


@pytest.mark.parametrize("link, owner", [("R1:G0/1", "D1"), ("R1:G0/2", "D2")])
def test_an_uplink_can_fail(net, link, owner):
    """Found by the simulator: without the floating default route a switch that loses its uplink
    stays HSRP active and drops its VLANs' traffic to the outside."""
    for v in net.routed_vlans:
        for family in (4, 6):
            ok1, ok2, h1, h2 = round_trip(net, v.id, family, [link])
            assert ok1 and ok2, (link, v.id, family, h1, h2)
    ok1, _, h1, _ = round_trip(net, 10 if owner == "D1" else 30, 4, [link])
    assert "[10/0] via 10.20.99." in " ".join(h1)


def test_without_the_backup_default_an_uplink_failure_blackholes(net):
    n = changed(net, lambda r: r.pop("distribution"))
    ok1, ok2, h1, _ = round_trip(n, 10, 4, ["R1:G0/1"])
    assert not ok1 and ok2 and h1[-1] == "D1: no route to 64.100.1.10 - dropped"


def test_backup_default_route_in_the_configs(cfg):
    assert "ip route 0.0.0.0 0.0.0.0 10.20.99.3 10" in parse(cfg["D1"])[""]
    assert "ipv6 route ::/0 2001:db8:acad:99::2 10" in parse(cfg["D2"])[""]


# --- IPv6 link-local addresses and fully specified routes -----------------------------------------
def test_link_local_addresses_on_every_routed_link(cfg):
    r1, d1, d2, isp = (parse(cfg[n]) for n in ("R1", "D1", "D2", "ISP"))
    for port in ("gigabitethernet0/0", "gigabitethernet0/1", "gigabitethernet0/2"):
        assert "ipv6 address fe80::1 link-local" in r1["interface " + port]
    assert "ipv6 address fe80::2 link-local" in d1["interface gigabitethernet1/0/24"]
    assert "ipv6 address fe80::3 link-local" in d2["interface gigabitethernet1/0/24"]
    assert "ipv6 address fe80::a link-local" in isp["interface gigabitethernet0/0"]


def test_every_ipv6_route_to_a_link_local_hop_names_its_interface(cfg):
    for name, text in cfg.items():
        for line in parse(text)[""]:
            if line.startswith("ipv6 route") and "fe80::" in line:
                assert len(line.split()) >= 5 and line.split()[3].startswith("gigabitethernet"), (name, line)


def test_ipv6_host_route_to_the_wlc(cfg, net):
    assert "ipv6 route 2001:db8:acad:99::20/128 gigabitethernet0/2 fe80::3" in parse(cfg["R1"])[""]
    ok, hops = Lab(net).trace("server", "2001:db8:acad:99::20", 6)
    assert ok and "2001:db8:acad:99::20/128 [1/0] via fe80::3 (gigabitethernet0/2) -> D2" in " ".join(hops)


def test_link_local_route_needs_its_interface_up(net):
    t = {(str(p), ad, str(h)) for p, ad, h, _, k in Lab(net, ["R1:G0/1"]).devices["R1"].table() if k == "S"}
    assert ("2001:db8:acad::/56", 10, "fe80::3") in t and ("2001:db8:acad::/56", 1, "fe80::2") not in t


# --- the three IPv6 address methods (SRWE module 8) ---------------------------------------------
@pytest.mark.parametrize("vlan, method", [(10, None), (20, "stateful"), (30, "stateless"), (40, None), (99, None)])
def test_ipv6_address_method_per_vlan(cfg, vlan, method):
    for dev in ("D1", "D2"):
        body = parse(cfg[dev])[f"interface vlan{vlan}"]
        m_flag, o_flag = "ipv6 nd managed-config-flag" in body, "ipv6 nd other-config-flag" in body
        no_a = "ipv6 nd prefix default no-autoconfig" in body
        relay = "ipv6 dhcp relay destination 2001:db8:acad:254::1" in body
        assert (m_flag, o_flag, no_a, relay) == {None: (False, False, False, False),
                                                  "stateless": (False, True, False, True),
                                                  "stateful": (True, False, True, True)}[method], (dev, vlan)


def test_dhcpv6_pool_serves_stateful_and_stateless_clients(cfg):
    r1 = parse(cfg["R1"])
    pool = r1["ipv6 dhcp pool campus-dhcpv6"]
    assert "address prefix 2001:db8:acad:20::/64 lifetime 172800 86400" in pool
    assert "link-address 2001:db8:acad:20::/64" in pool
    assert "dns-server 2001:db8:acad:99::53" in pool
    for port in ("gigabitethernet0/1", "gigabitethernet0/2"):          # the relayed requests arrive here
        assert "ipv6 dhcp server campus-dhcpv6" in r1["interface " + port]


def test_no_dhcpv6_pool_when_every_vlan_uses_slaac(net):
    n = changed(net, lambda r: [v.pop("dhcpv6", None) for v in r["vlans"].values()])
    r1 = render_all(n)["R1"]
    assert "ipv6 dhcp" not in r1


# --- voice VLAN (SRWE 3.2.6 - 3.3.7) -------------------------------------------------------------
def test_staff_ports_carry_data_and_voice(cfg):
    a1, a2 = parse(cfg["A1"]), parse(cfg["A2"])
    for sw, ports in ((a1, range(1, 9)), (a2, (17, 18))):
        for n in ports:
            body = sw[f"interface fastethernet0/{n}"]
            assert "switchport access vlan 10" in body and "switchport voice vlan 50" in body
            assert "mls qos trust cos" in body and "switchport port-security maximum 3" in body
    assert "mls qos" in a1[""] and "mls qos" in a2[""]
    assert "switchport voice vlan 50" not in a1["interface fastethernet0/9"]          # test stations: no phone
    assert "switchport port-security maximum 2" in a1["interface fastethernet0/9"]


def test_voice_vlan_is_routed_and_served(cfg):
    assert "ip dhcp pool voice" in parse(cfg["R1"])
    d2 = parse(cfg["D2"])
    assert "standby 50 priority 110" in d2["interface vlan50"]
    assert "ip helper-address 10.20.254.1" in d2["interface vlan50"]
    assert "switchport trunk allowed vlan 10,20,30,40,50,99" in d2["interface port-channel12"]
    assert "ip dhcp snooping vlan 10,20,30,40,50" in parse(cfg["A1"])[""]


def test_no_qos_without_phones(net):
    n = changed(net, lambda r: [p.pop("voice", None) for sw in ("A1", "A2")
                                for p in r["switches"][sw]["access_ports"].values()])
    assert "mls qos" not in render_all(n)["A1"]


def test_phones_reach_the_server(net):
    for family in (4, 6):
        ok1, ok2, h1, h2 = round_trip(net, 50, family)
        assert ok1 and ok2 and h1[0].endswith("gateway D2")


# --- VLSM (ITN 11) ----------------------------------------------------------------------------
from campus.vlsm import allocate, plan_vlsm, prefix_for, report, usable  # noqa: E402


@pytest.mark.parametrize("needed,prefix", [(2, 30), (6, 29), (7, 28), (30, 27), (62, 26), (63, 25),
                                           (126, 25), (127, 24), (254, 24), (255, 23)])
def test_smallest_prefix_that_holds_the_hosts(needed, prefix):
    assert prefix_for(needed) == prefix
    assert usable(prefix) >= needed
    if prefix < 30:
        assert usable(prefix + 1) < needed


def test_a_31_holds_a_point_to_point_link():
    assert prefix_for(2, allow_31=True) == 31
    assert usable(31) == 2 and usable(30) == 2 and usable(32) == 1


def test_vlsm_allocates_biggest_first_without_overlap():
    import ipaddress
    out = allocate(ipaddress.ip_network("192.168.1.0/24"), [("a", 64), ("b", 128), ("c", 4), ("d", 32)])
    assert [l for l, _ in out] == ["b", "a", "d", "c"]
    assert [str(s) for _, s in out] == ["192.168.1.0/25", "192.168.1.128/26", "192.168.1.192/27", "192.168.1.224/30"]


def test_vlsm_refuses_what_does_not_fit():
    import ipaddress
    with pytest.raises(ValueError):
        allocate(ipaddress.ip_network("192.168.1.0/24"), [("a", 128), ("b", 128), ("c", 4)])


def test_vlsm_plan_of_the_design(net):
    p = plan_vlsm(net)
    by_id = {r[0].id: r for r in p["vlans"]}
    assert by_id[30][3] == 24 and by_id[10][3] == 25 and by_id[99][3] == 27
    subs = [r[4] for r in p["vlans"]] + [s for _, s in p["links"]] + [p["loopback"]]
    for i, a in enumerate(subs):
        assert a.subnet_of(p["block"])
        assert not any(a.overlaps(b) for b in subs[i + 1:])
    assert all(s.prefixlen == 30 for _, s in p["links"])
    assert all(s.prefixlen == 31 for _, s in plan_vlsm(net, allow_31=True)["links"])


def test_vlsm_report_is_committed(net):
    with open(os.path.join(ROOT, "docs", "VLSM.md")) as f:
        assert f.read() == report(net)


def test_too_many_hosts_for_the_dhcp_pool(net):
    bad = changed(net, lambda r: r["vlans"][30].update(hosts=200))     # 200 + 50% = 300 > 234
    assert any("VLAN 30" in x and "room for 234" in x for x in model.validate(bad))


def test_hosts_check_counts_the_growth_margin(net):
    ok = changed(net, lambda r: (r["vlans"][30].update(hosts=200), r["addressing"].update(growth_percent=10)))
    assert model.validate(ok) == []


# --- connectivity tests (ITN 13, 17.4) ---------------------------------------------------------
from campus.connectivity import expected_tracert, extended_pings, hsrp6_link_local  # noqa: E402
from campus.connectivity import report as connectivity_report  # noqa: E402
from campus.verify import parse_ping, parse_tracert  # noqa: E402


def test_tracert_expected_hops_everything_up(net):
    hops = expected_tracert(net, 10, 4)
    assert [str(a) for _, a in hops] == ["10.20.10.2", "10.20.255.1", "203.0.113.1", "64.100.1.10"]
    hops6 = expected_tracert(net, 30, 6)
    assert [d for d, _ in hops6] == ["D2", "R1", "ISP", "Dual Stack Server"]
    assert str(hops6[1][1]) == "2001:db8:acad:ff02::1"         # the global address, not the link-local next hop


def test_tracert_with_d1_uplink_down_goes_through_d2(net):
    hops = expected_tracert(net, 10, 4, ("R1:G0/1",))
    assert [d for d, _ in hops] == ["D1", "D2", "R1", "ISP", "Dual Stack Server"]
    assert str(hops[1][1]) == "10.20.99.3"                      # arrives on D2 over VLAN 99


def test_every_extended_ping_gets_replies(net):
    pings = extended_pings(net)
    assert len(pings) == 6 and all(ok for *_, ok in pings)


def test_hsrp_ipv6_virtual_link_local():
    assert str(hsrp6_link_local(1010)) == "fe80::205:73ff:fea0:3f2"


def test_connectivity_report_is_committed(net):
    with open(os.path.join(ROOT, "docs", "CONNECTIVITY.md")) as f:
        assert f.read() == connectivity_report(net)


def test_parse_ios_traceroute_with_a_timeout():
    text = """R1#traceroute 64.100.1.10
Type escape sequence to abort.
Tracing the route to 64.100.1.10
VRF info: (vrf in name/id, vrf out name/id)
  1 203.0.113.1 0 msec 1 msec 0 msec
  2 *  *  *
  3 64.100.1.10 1 msec 1 msec 1 msec
"""
    assert [str(h) if h else None for h in parse_tracert(text)] == ["203.0.113.1", None, "64.100.1.10"]


def test_parse_tracert_with_names():
    text = "  1    <1 ms    <1 ms    <1 ms  d1.hu01.test [10.20.10.2]\n  2     *        *        *     Request timed out.\n"
    assert [str(h) if h else None for h in parse_tracert(text)] == ["10.20.10.2", None]


@pytest.mark.parametrize("text,result", [
    ("Packets: Sent = 4, Received = 0, Lost = 4 (100% loss),", (4, 0)),
    ("Success rate is 80 percent (4/5), round-trip min/avg/max = 1/1/2 ms", (5, 4)),
    ("4 packets transmitted, 4 received, 0% packet loss, time 3004ms", (4, 4)),
    ("nothing here", None)])
def test_parse_ping(text, result):
    assert parse_ping(text) == result


def test_verify_catches_a_tracert_through_the_wrong_switch(net, tmp_path):
    (tmp_path / "vlan10_tracert_server.txt").write_text(
        "  1  <1 ms  <1 ms  <1 ms  10.20.10.3\n  2  1 ms  1 ms  1 ms  10.20.255.5\n"
        "  3  1 ms  1 ms  1 ms  203.0.113.1\n  4  2 ms  2 ms  2 ms  64.100.1.10\n")
    (tmp_path / "vlan20_ping6_server.txt").write_text("Packets: Sent = 4, Received = 0, Lost = 4 (100% loss),")
    ok, lines = verify_folder(net, str(tmp_path))
    assert not ok
    assert any(l.startswith("FAIL vlan10: tracert") and "got D2 > R1 > ISP" in l for l in lines)
    assert any(l.startswith("FAIL vlan20_ping6_server") for l in lines)


# --- router-on-a-stick variant (SRWE 4) ------------------------------------------------------
from campus.roas import render_roas, report as roas_report  # noqa: E402
from campus.routing import Device  # noqa: E402


@pytest.fixture
def roas(net):
    return render_roas(net)


def test_roas_subinterface_per_vlan(roas):
    r = parse(roas["R1-ROAS"])
    sub = r["interface gigabitethernet0/1.20"]
    assert "encapsulation dot1q 20" in sub and "ip address 10.20.20.1 255.255.255.0" in sub
    assert "ipv6 nd managed-config-flag" in sub and "ipv6 dhcp server roas-production" in sub
    assert "ipv6 nd other-config-flag" in r["interface gigabitethernet0/1.30"]
    assert "encapsulation dot1q 999 native" in r["interface gigabitethernet0/1.999"]
    assert not any(l.startswith("ip address") for l in r["interface gigabitethernet0/1.999"])
    assert not any(l.startswith("ip address") for l in r["interface gigabitethernet0/1"])


def test_roas_router_serves_dhcp_itself(roas):
    text = roas["R1-ROAS"]
    assert "ip dhcp pool ROAS-STAFF" in text and "default-router 10.20.10.1" in text
    assert "ip helper-address" not in text and "ipv6 dhcp relay" not in text


def test_roas_router_routes_between_all_vlans(net, roas):
    d = Device("R1-ROAS", roas["R1-ROAS"])
    for v in net.routed_vlans:
        r = d.lookup(v.host(50))
        assert r[4] == "C" and r[3] == f"gigabitethernet0/1.{v.id}"


def test_roas_switch_trunk_and_ports(roas):
    s = parse(roas["S1-ROAS"])
    t = s["interface gigabitethernet0/1"]
    assert "switchport trunk native vlan 999" in t and "switchport trunk allowed vlan 10,20,30,40,50,99" in t
    assert "ip dhcp snooping trust" in t
    staff = s["interface fastethernet0/4"]
    assert "switchport access vlan 10" in staff and "switchport voice vlan 50" in staff
    assert "switchport access vlan 20" in s["interface fastethernet0/5"]
    assert "shutdown" in s["interface fastethernet0/21"] and "shutdown" in s["interface gigabitethernet0/2"]


def test_roas_configs_pass_the_policy(roas):
    for name, text in roas.items():
        assert lint_text(text) == [], name


def test_roas_committed(net, roas):
    for name, text in roas.items():
        with open(os.path.join(ROOT, "configs", "roas", f"{name}.cfg")) as f:
            assert f.read() == text
    with open(os.path.join(ROOT, "docs", "ROUTER_ON_A_STICK.md")) as f:
        assert f.read() == roas_report(net)


def test_roas_too_many_ports_per_vlan(net):
    bad = changed(net, lambda r: r["router_on_a_stick"]["switch"].update(ports_per_vlan=6))   # 5 x 6 > 24
    assert any("router-on-a-stick" in x and "do not fit" in x for x in model.validate(bad))


def test_roas_management_address_clash(net):
    bad = changed(net, lambda r: r["router_on_a_stick"]["switch"].update(mgmt_host=11))      # A1's address
    assert any("already an access switch" in x for x in model.validate(bad))


# --- R1 as a DHCP client on the ISP link (SRWE 7.3.4) ------------------------------------------
@pytest.fixture
def wan_dhcp(net):
    return changed(net, lambda r: r["routers"]["R1"]["interfaces"]["G0/0"].update(dhcp_client=True))


def test_static_outside_address_is_the_default(cfg):
    r1 = parse(cfg["R1"])
    assert "ip address 203.0.113.2 255.255.255.252" in r1["interface gigabitethernet0/0"]
    assert "ip route 0.0.0.0 0.0.0.0 203.0.113.1" in r1[""]
    assert "ip dhcp pool customer-hu01" not in parse(cfg["ISP"])


def test_wan_dhcp_client_configuration(wan_dhcp):
    assert model.validate(wan_dhcp) == []
    c = render_all(wan_dhcp)
    r1, isp = parse(c["R1"]), parse(c["ISP"])
    assert "ip address dhcp" in r1["interface gigabitethernet0/0"]
    assert not any(l.startswith("ip route 0.0.0.0 0.0.0.0") for l in r1[""])      # it comes from the offer
    assert "ip route 10.20.0.0 255.255.128.0 10.20.255.2" in r1[""]                 # the rest stays static
    assert "network 203.0.113.0 255.255.255.252" in isp["ip dhcp pool customer-hu01"]
    assert "default-router 203.0.113.1" in isp["ip dhcp pool customer-hu01"]
    assert "ip dhcp excluded-address 203.0.113.1" in isp[""]


def test_wan_dhcp_lease_and_default_route_in_the_simulator(wan_dhcp):
    import ipaddress
    lab = Lab(wan_dhcp)
    assert [str(a) for a in lab.devices["R1"].ifaces["gigabitethernet0/0"]["v4"]] == ["203.0.113.2/30"]
    prefix, ad, hop, iface, kind = lab.devices["R1"].lookup(ipaddress.ip_address("64.100.1.10"))
    assert (str(prefix), ad, str(hop)) == ("0.0.0.0/0", 254, "203.0.113.1")
    for v in wan_dhcp.routed_vlans:
        ok1, ok2, _, _ = round_trip(wan_dhcp, v.id, 4)
        assert ok1 and ok2


def test_wan_dhcp_needs_a_30(net):
    def edit(r):
        r["routers"]["R1"]["interfaces"]["G0/0"].update(dhcp_client=True, v4="203.0.113.2/29")
        r["isp"]["uplink"]["v4"] = "203.0.113.1/29"
    assert any("use a /30" in x for x in model.validate(changed(net, edit)))
