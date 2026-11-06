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
    (lambda r: r["switches"]["A1"]["access_ports"]["Fa0/9-16"].update(vlan=50), "does not exist"),
    (lambda r: r["switches"]["A1"]["unused"].append("Fa0/1"), "listed as unused"),
    (lambda r: r["switches"]["A2"]["unused"].remove("Fa0/20"), "not assigned"),
    (lambda r: r["etherchannels"][1].update(b_ports="Fa0/21-23"), "different numbers of members"),
    (lambda r: r["etherchannels"].pop(2), "must have an uplink to each distribution switch"),
    (lambda r: r["stp"].update(root_priority=30000), "not a multiple of 4096"),
    (lambda r: r["switches"]["D1"]["uplink"].update(v4="10.20.255.9/30"), "not in the same subnet"),
    (lambda r: r["dhcp_reservations"][0].update(ip="10.20.30.5"), "excluded range"),
    (lambda r: r["dhcp_reservations"][0].update(ip="10.20.20.50"), "is not in VLAN 30"),
    (lambda r: r["vlans"].update({1: {"name": "default", "v4": "10.20.1.0/24", "v6": "2001:db8:acad:1::/64", "primary": "D1"}}),
     "VLAN 1 must not carry traffic"),
])
def test_validation_catches_mistakes(net, edit, expected):
    problems = model.validate(changed(net, edit))
    assert any(expected in p for p in problems), problems


# --- generated configurations -------------------------------------------------------------------
def test_one_config_per_device_with_placeholders_not_passwords(cfg):
    assert set(cfg) == {"R1", "D1", "D2", "A1", "A2"}
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
    assert "spanning-tree vlan 30,40 priority 24576" in d2[""]
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
    assert "ipv6 route 2001:db8:acad::/56 2001:db8:acad:ff02::2 10" in g
    assert "ip dhcp excluded-address 10.20.30.1 10.20.30.20" in g
    assert r1["ip dhcp pool iot"] == ["network 10.20.30.0 255.255.255.0", "default-router 10.20.30.1",
                                      "dns-server 10.20.99.53", "domain-name hu01.test"]
    assert "hardware-address 0000.0000.0001" in r1["ip dhcp pool esp32-node-1"]


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
    ("ip dhcp snooping vlan 10,20,30,40\n", "", "DHCP snooping"),
    (" switchport access vlan 999\n shutdown", " shutdown", "unused port left in VLAN 1"),
])
def test_lint_finds_a_weakened_switch(cfg, old, new, expected):
    text = cfg["A1"].replace(old, new, 1)
    assert text != cfg["A1"]
    assert any(expected in i for i in lint_text(text)), lint_text(text)


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


BODY_A1_1_8 = [" description staff desk", " switchport access vlan 10", " switchport mode access",
               " switchport nonegotiate", " switchport port-security maximum 2", " switchport port-security",
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
    assert lines[-1] == "40 passed, 0 failed"


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
