#!/usr/bin/env python3
"""Shared negative-keyword sets — one junk list for the whole account.

Instead of pasting the same minus-words into every group, keep a shared set
(NegativeKeywordSharedSets) and attach it to many campaigns. Update once,
applies everywhere.

Examples:
    python negatives.py create --name "Global junk" --words "бесплатно,скачать,вакансии,б/у"
    python negatives.py list
    python negatives.py attach --set 777 --campaign 111 --campaign 222
    python negatives.py update --set 777 --words "бесплатно,скачать,реферат,форум"
"""

import argparse
import json
import sys

from direct_api import DirectClient, collect_add_results, load_config


def create_set(client, name, words):
    res = client.call("negativekeywordsharedsets", "add", {
        "NegativeKeywordSharedSets": [{"Name": name, "NegativeKeywords": {"Items": words}}],
    })
    ids, errs, _ = collect_add_results(res)
    return ids, errs


def update_set(client, set_id, words):
    return client.call("negativekeywordsharedsets", "update", {
        "NegativeKeywordSharedSets": [
            {"Id": int(set_id), "NegativeKeywords": {"Items": words}}
        ],
    })


def list_sets(client):
    res = client.call("negativekeywordsharedsets", "get", {
        "SelectionCriteria": {},
        "FieldNames": ["Id", "Name", "NegativeKeywords"],
    })
    return res.get("NegativeKeywordSharedSets", [])


def attach(client, set_id, campaign_ids):
    """Attach a shared set to campaigns via Campaigns.update."""
    updates = [{"Id": int(c), "NegativeKeywordSharedSetIds": {"Items": [int(set_id)]}}
               for c in campaign_ids]
    return client.call("campaigns", "update", {"Campaigns": updates})


def _words(s):
    return [w.strip() for w in s.split(",") if w.strip()]


def main(argv):
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("create")
    c.add_argument("--name", required=True)
    c.add_argument("--words", required=True)

    u = sub.add_parser("update")
    u.add_argument("--set", required=True)
    u.add_argument("--words", required=True)

    sub.add_parser("list")

    a = sub.add_parser("attach")
    a.add_argument("--set", required=True)
    a.add_argument("--campaign", action="append", required=True)

    args = p.parse_args(argv)
    client = DirectClient(load_config())

    if args.cmd == "create":
        ids, errs = create_set(client, args.name, _words(args.words))
        print(json.dumps({"ids": ids, "errors": errs}, ensure_ascii=False, indent=2))
        return 1 if errs else 0
    if args.cmd == "update":
        print(json.dumps(update_set(client, args.set, _words(args.words)),
                         ensure_ascii=False, indent=2))
        return 0
    if args.cmd == "list":
        for s in list_sets(client):
            n = len((s.get("NegativeKeywords") or {}).get("Items", []))
            print(f"{s['Id']:>10}  {n:>4} words  {s.get('Name')}")
        return 0
    if args.cmd == "attach":
        print(json.dumps(attach(client, args.set, args.campaign),
                         ensure_ascii=False, indent=2))
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
