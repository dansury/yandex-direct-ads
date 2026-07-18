#!/usr/bin/env python3
"""Ad extensions — callouts (уточнения) via the AdExtensions service.

Callouts are created once with AdExtensions.add (type Callout, field CalloutText),
then attached to individual ads through the ad's CalloutSetting at upload time
(handled in ads.py). They go through moderation like ads.

Examples:
    python extensions.py add-callouts --text "Гарантия 18 мес" --text "Рассрочка 0%"
    python extensions.py list
"""

import argparse
import json
import sys

from direct_api import DirectClient, collect_add_results, load_config

CALLOUT_LIMIT = 25  # chars


def add_callouts(client, texts):
    """Create callout extensions. Returns list of AdExtensionIds (None for failed)."""
    items = []
    for t in texts:
        if len(t) > CALLOUT_LIMIT:
            print(f"  skip callout >{CALLOUT_LIMIT} chars: {t!r}", file=sys.stderr)
            continue
        items.append({"Callout": {"CalloutText": t}})
    if not items:
        return [], []
    res = client.call("adextensions", "add", {"AdExtensions": items})
    ids, errors, _ = collect_add_results(res)
    return ids, errors


def list_callouts(client):
    res = client.call("adextensions", "get", {
        "SelectionCriteria": {"Types": ["CALLOUT"]},
        "FieldNames": ["Id", "Type", "State", "Status"],
        "CalloutFieldNames": ["CalloutText"],
    })
    return res.get("AdExtensions", [])


def ensure_callouts(client, texts):
    """Create callouts and return the list of usable ids (drops failures)."""
    ids, errors = add_callouts(client, texts)
    if errors:
        for e in errors:
            print(f"  callout error {e['code']}: {e['message']}", file=sys.stderr)
    return [i for i in ids if i is not None]


def main(argv):
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("add-callouts")
    a.add_argument("--text", action="append", required=True)
    sub.add_parser("list")
    args = p.parse_args(argv)

    client = DirectClient(load_config())
    if args.cmd == "add-callouts":
        ids = ensure_callouts(client, args.text)
        print(json.dumps({"callout_ids": ids}, ensure_ascii=False))
        return 0
    if args.cmd == "list":
        for c in list_callouts(client):
            txt = c.get("Callout", {}).get("CalloutText", "")
            print(f"{c['Id']:>10} {c.get('Status'):<10} {txt}")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
