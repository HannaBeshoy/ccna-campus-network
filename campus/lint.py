"""lint.py - the security policy of SRWE modules 10 and 11 and ITN 16.4, checked on any IOS configuration file.

Works on the generated configs and on a running-config saved from a real switch, so it also
finds what someone changed by hand on the lab day.
"""
import re

from .iosconf import interfaces, parse


def lint_text(text):
    s = parse(text)
    g = s[""]
    issues = []
    is_switch = any(l.startswith("spanning-tree") or l.startswith("vlan ") for l in g) or any(
        "switchport" in l for body in s.values() for l in body)

    def has(prefix):
        return any(l.startswith(prefix) for l in g)

    # device access (module 1 and 10)
    if not has("service password-encryption"):
        issues.append("service password-encryption is missing")
    if not has("enable secret"):
        issues.append("no enable secret")
    if has("enable password"):
        issues.append("enable password is stored with weak encryption: use enable secret only")
    if not has("ip ssh version 2"):
        issues.append("SSH version 2 is not forced")
    vty = s.get("line vty", [])
    tin = [l for l in vty if l.startswith("transport input")]
    if not tin or any(re.search(r"\b(telnet|all)\b", l) for l in tin):
        issues.append("line vty: allow SSH only (transport input ssh)")
    if "login local" not in vty:
        issues.append("line vty: login local is missing")
    if any(l.startswith("username") and " password " in l for l in g):
        issues.append("a username uses 'password' instead of 'secret'")
    # ITN 16.4: password length, login blocking, idle timeout
    m = [l for l in g if l.startswith("security passwords min-length")]
    if not m or int(m[0].split()[-1]) < 8:
        issues.append("security passwords min-length is missing or below 8")
    if not any(l.startswith("login block-for") for l in g):
        issues.append("login block-for is missing (no protection against password guessing)")
    for sec in ("line con 0", "line vty"):
        t = [l for l in s.get(sec, []) if l.startswith("exec-timeout")]
        if not t or t[0].split()[1:] == ["0", "0"] or (len(t[0].split()) > 1 and int(t[0].split()[1]) > 30):
            issues.append(f"{sec}: exec-timeout is missing, 0 0 or longer than 30 minutes")

    if is_switch:
        if not any(l.startswith("spanning-tree mode rapid-pvst") for l in g):
            issues.append("spanning tree is not Rapid PVST+")
        user_ports = 0
        for name, body in interfaces(s).items():
            if name.startswith(("vlan", "port-channel", "loopback")):
                continue
            mode_access = "switchport mode access" in body
            mode_trunk = "switchport mode trunk" in body
            shut = "shutdown" in body
            routed = "no switchport" in body
            if routed:
                continue
            if not mode_access and not mode_trunk and not shut:
                issues.append(f"{name}: no switchport mode (DTP would negotiate: set access or trunk)")
            if mode_access and shut:
                vl = [l for l in body if l.startswith("switchport access vlan")]
                if not vl or vl[0].split()[-1] == "1":
                    issues.append(f"{name}: unused port left in VLAN 1 (move it to the parking VLAN)")
            if mode_access and not shut:
                user_ports += 1
                if not any(l.startswith("switchport access vlan") for l in body):
                    issues.append(f"{name}: access port in VLAN 1")
                if "switchport port-security" not in body:
                    issues.append(f"{name}: port security is off")
                if "spanning-tree portfast" not in body:
                    issues.append(f"{name}: PortFast is off")
                if "spanning-tree bpduguard enable" not in body:
                    issues.append(f"{name}: BPDU guard is off")
                if "ip dhcp snooping trust" in body:
                    issues.append(f"{name}: an access port must not be trusted for DHCP snooping")
            if mode_trunk:
                native = [l for l in body if l.startswith("switchport trunk native vlan")]
                if not native or native[0].split()[-1] == "1":
                    issues.append(f"{name}: trunk with native VLAN 1")
                if "switchport nonegotiate" not in body:
                    issues.append(f"{name}: trunk still sends DTP (switchport nonegotiate)")
                if not any(l.startswith("switchport trunk allowed vlan") for l in body):
                    issues.append(f"{name}: trunk allows every VLAN")
        if user_ports:
            if not has("ip dhcp snooping vlan"):
                issues.append("DHCP snooping is not enabled on the user VLANs")
            if not any(l.startswith("ip arp inspection vlan") for l in g):
                issues.append("Dynamic ARP Inspection is not enabled on the user VLANs")
    return issues


def lint_file(path):
    with open(path) as f:
        return lint_text(f.read())
