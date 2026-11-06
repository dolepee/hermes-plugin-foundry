"""Small terminal entry point for the Plugin Foundry spike."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from foundry_core import build_and_test, install_plugin, list_generated, parse_request, run_tests


def main(argv=None):
    parser = argparse.ArgumentParser(prog="foundry")
    sub = parser.add_subparsers(dest="cmd", required=True)

    plan = sub.add_parser("plan")
    plan.add_argument("request")

    build = sub.add_parser("build")
    build.add_argument("request")
    build.add_argument("--output")
    build.add_argument("--install", action="store_true")

    test = sub.add_parser("test")
    test.add_argument("path")

    catalog = sub.add_parser("catalog")
    catalog.add_argument("path")

    poll = sub.add_parser("poll")
    poll.add_argument("path")

    sub.add_parser("list")
    args = parser.parse_args(argv)

    if args.cmd == "plan":
        print(json.dumps(parse_request(args.request), indent=2))
        return 0
    if args.cmd == "build":
        result = build_and_test(args.request, output_dir=args.output)
        spec = result["build"]["spec"]
        print(f"Generated: {spec['slug']}")
        print("Files: " + "  ".join(result["build"]["files"]))
        print(f"Tests: {'passed' if result['tests']['ok'] else 'failed'}")
        print(f"Static scan: {'clean' if result['scan']['ok'] else 'findings'}")
        if args.install:
            print("Installed: " + install_plugin(spec["path"], spec["slug"]))
        return 0 if result["ok"] else 1
    if args.cmd == "test":
        result = run_tests(args.path)
        sys.stdout.write(result["stderr"])
        return 0 if result["ok"] else 1
    if args.cmd == "catalog":
        path = Path(args.path).expanduser() / "CATALOG.md"
        if not path.exists():
            print(f"No catalog draft at {path}", file=sys.stderr)
            return 1
        sys.stdout.write(path.read_text())
        return 0
    if args.cmd == "poll":
        import importlib.util
        path = Path(args.path).expanduser() / "plugin.py"
        spec = importlib.util.spec_from_file_location("foundry_generated", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        handler = getattr(module, "_poll", None) or getattr(module, "_scan")
        print(json.dumps(handler(), indent=2))
        return 0
    if args.cmd == "list":
        print(json.dumps(list_generated(), indent=2))
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
