"""diff.py - what the generated configuration asks for that a running-config does not have.

Only missing lines are reported: a running-config always has extra default lines. Lines that
cannot match by design are skipped: secrets (hashed in the running-config), the RSA key
command (not saved in the config), the banner text, and "no shutdown" (never printed).
"""
from .iosconf import parse

SKIP = ("enable secret", "username ", "crypto key generate", "banner",
        "no shutdown")   # a running-config shows only "shutdown"; up is the default and is not printed


def diff_configs(intended, running):
    want, have = parse(intended), parse(running)
    missing = {}
    for section, lines in want.items():
        if section.startswith(SKIP) or (section and not lines and not section.startswith("interface ")):
            continue                     # a global line without a body is checked in the '' section
        got = set(have.get(section, []))
        gone = [l for l in lines if l not in got and not l.startswith(SKIP)]
        if section and section not in have:
            gone = ["(the whole section is missing)"] + gone
        if gone:
            missing[section] = gone
    return missing
