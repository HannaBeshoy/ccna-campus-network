"""iosconf.py - read an IOS configuration (generated or a saved running-config) into sections.

A section is a line that starts in column 0 ("interface Vlan10", "line vty 0 15") with its
indented lines. "interface range X - Y" is expanded into one section per interface, because a
running-config always lists interfaces one by one. Lines are normalised so that the same
command written two ways compares equal: case, extra spaces, interface abbreviations, and the
few commands newer IOS versions spell differently.
"""
import re

ABBREV = [(r"^(?:gi|gig|gigabitethernet|g)(?=\d)", "GigabitEthernet"),
          (r"^(?:fa|fastethernet|f)(?=\d)", "FastEthernet"),
          (r"^(?:po|port-channel)(?=\d)", "Port-channel"),
          (r"^(?:vl|vlan)(?=\d)", "Vlan"),
          (r"^(?:lo|loopback)(?=\d)", "Loopback")]
RENAMES = {"no ip domain lookup": "no ip domain-lookup", "ip domain name": "ip domain-name"}


def ifname(token):
    t = token.strip()
    for pat, full in ABBREV:
        if re.match(pat, t, re.I):
            return re.sub(pat, full, t, flags=re.I)
    return t


def norm(line):
    s = re.sub(r"\s+", " ", line.strip())
    for old, new in RENAMES.items():
        if s.lower().startswith(old):
            s = new + s[len(old):]
    return s.lower()


def expand_range(arg):
    """'range FastEthernet0/1 - 8, Fa0/10' -> ['FastEthernet0/1', ..., 'FastEthernet0/8', 'FastEthernet0/10']."""
    out = []
    for part in arg.split(","):
        part = part.strip()
        m = re.fullmatch(r"(\S+?)(\d+)\s*-\s*(\d+)", part)
        if m:
            base = ifname(m.group(1) + m.group(2))
            prefix = base[: len(base) - len(m.group(2))]
            out += [f"{prefix}{n}" for n in range(int(m.group(2)), int(m.group(3)) + 1)]
        else:
            out.append(ifname(part))
    return out


def parse(text):
    """{section header (normalised): [body lines (normalised)]}; global lines under ''."""
    sections = {"": []}
    current = [""]
    in_banner = None
    for raw in text.splitlines():
        line = raw.rstrip()
        if in_banner:                          # skip multi-line banners up to the delimiter
            if in_banner in line:
                in_banner = None
            continue
        if not line.strip() or line.strip() in ("!", "end") or line.lstrip().startswith("!"):
            if not line.startswith(" "):
                current = [""]
            continue
        if not line.startswith(" "):
            m = re.match(r"banner \w+ (\S)", line)
            if m:
                delim = m.group(1) if m.group(1) != "^" else "^"
                rest = line[m.end():]
                if line.startswith("banner") and delim not in rest and "^C" not in rest:
                    in_banner = "^C" if line.rstrip().endswith("^C") else delim
                sections[""].append("banner")
                current = [""]
                continue
            m = re.match(r"interface range (.+)", line, re.I)
            if m:
                current = ["interface " + n.lower() for n in expand_range(m.group(1))]
            elif re.match(r"interface \S+", line, re.I):
                current = ["interface " + ifname(line.split(None, 1)[1]).lower()]
            elif re.match(r"line vty (\d+) (\d+)", line):
                current = ["line vty"]               # 0 15 may be saved as 0 4 + 5 15
            else:
                # any other top-level line: a global command, and the header of what is indented under it
                sections[""].append(norm(line))
                current = [norm(line)]
            for c in current:
                sections.setdefault(c, [])
            continue
        for c in current:
            sections.setdefault(c, []).append(norm(line))
    return sections


def interfaces(sections):
    return {k[len("interface "):]: v for k, v in sections.items() if k.startswith("interface ")}
