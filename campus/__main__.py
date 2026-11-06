"""python -m campus {check|generate|verify|stp|lint} - see README."""
import argparse
import os
import sys

from . import model


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m campus")
    ap.add_argument("--model", default="network.yaml")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check", help="validate network.yaml")
    g = sub.add_parser("generate", help="write one configuration per device")
    g.add_argument("--out", default="configs")
    g.add_argument("--secrets", default="secrets.yaml", help="fills the passwords; placeholders if missing")
    l = sub.add_parser("lint", help="check configuration files against the security policy")
    l.add_argument("files", nargs="+")
    d = sub.add_parser("diff", help="compare a device's running-config with the generated one")
    d.add_argument("device")
    d.add_argument("running_config")
    sub.add_parser("stp", help="predict every port's spanning-tree role per VLAN")
    sub.add_parser("plan", help="print the addressing plan (Markdown)")
    v = sub.add_parser("verify", help="check saved show-command output from the lab against the design")
    v.add_argument("captures", help="folder with <device>_<command>.txt files")
    a = ap.parse_args(argv)

    net = model.load(a.model)
    problems = model.validate(net)
    if a.cmd == "check" or problems:
        for p in problems:
            print("PROBLEM:", p)
        print("network.yaml is consistent" if not problems else f"{len(problems)} problem(s)")
        return 1 if problems else 0
    if a.cmd == "generate":
        from .generate import render_all
        os.makedirs(a.out, exist_ok=True)
        for name, text in render_all(net, a.secrets).items():
            with open(os.path.join(a.out, f"{name}.cfg"), "w") as f:
                f.write(text)
            print(f"{a.out}/{name}.cfg  {len(text.splitlines())} lines")
        return 0
    if a.cmd == "lint":
        from .lint import lint_file
        bad = 0
        for f in a.files:
            issues = lint_file(f)
            for i in issues:
                print(f"{f}: {i}")
            bad += len(issues)
        print("policy OK" if not bad else f"{bad} policy violation(s)")
        return 1 if bad else 0
    if a.cmd == "diff":
        from .diff import diff_configs
        from .generate import render_all
        intended = render_all(net)[a.device]
        with open(a.running_config) as f:
            running = f.read()
        missing = diff_configs(intended, running)
        for section, lines in missing.items():
            print(section or "(global)")
            for line in lines:
                print("   missing:", line)
        print("running-config has everything the design asks for" if not missing else f"{sum(map(len, missing.values()))} line(s) missing")
        return 1 if missing else 0
    if a.cmd == "stp":
        from .stp import predict, report
        print(report(predict(net)))
        return 0
    if a.cmd == "plan":
        from .plan import plan
        print(plan(net), end="")
        return 0
    if a.cmd == "verify":
        from .verify import verify_folder
        ok, lines = verify_folder(net, a.captures)
        print("\n".join(lines))
        return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
