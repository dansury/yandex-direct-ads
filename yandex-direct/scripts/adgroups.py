#!/usr/bin/env python3
"""AdGroups + Keywords + minus-words.

Examples:
    python adgroups.py list --campaign 111
    python adgroups.py create --campaign 111 --name "Group A" --regions 225
    python adgroups.py add-keywords --group 222 --keywords keywords.txt
    python adgroups.py add-keywords --group 222 --kw "купить диван" --kw "диван недорого"
    python adgroups.py minus --group 222 --words "бесплатно,своими руками,б/у"

keywords.txt: one keyword phrase per line; use leading '-' tokens inside the
phrase for phrase-level minus words, e.g.  купить диван -бесплатно
"""

import argparse
import json
import sys

from direct_api import DirectClient, load_config

GROUP_FIELDS = ["Id", "Name", "CampaignId", "RegionIds", "Status"]


def list_groups(client, campaign_id):
    res = client.call("adgroups", "get", {
        "SelectionCriteria": {"CampaignIds": [int(campaign_id)]},
        "FieldNames": GROUP_FIELDS,
    })
    return res.get("AdGroups", [])


def create_group(client, campaign_id, name, regions):
    group = {
        "Name": name,
        "CampaignId": int(campaign_id),
        "RegionIds": [int(r) for r in regions],
    }
    return client.call("adgroups", "add", {"AdGroups": [group]})


def _load_keywords(args):
    words = list(args.kw or [])
    if args.keywords:
        with open(args.keywords, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line and not line.startswith("#"):
                    words.append(line)
    # Direct keyword phrase limit: 4096 chars, <=7 words excluding operators.
    cleaned = []
    for w in words:
        if len(w) > 4096:
            print(f"  skip (too long): {w[:40]}...", file=sys.stderr)
            continue
        cleaned.append(w)
    return cleaned


def add_keywords(client, group_id, keywords):
    items = [{"AdGroupId": int(group_id), "Keyword": kw} for kw in keywords]
    # Keywords.add accepts up to 10000 per call; chunk to be safe.
    out = []
    for i in range(0, len(items), 1000):
        res = client.call("keywords", "add", {"Keywords": items[i:i + 1000]})
        out.append(res)
    return out


def set_minus_words(client, group_id, words):
    return client.call("adgroups", "update", {
        "AdGroups": [{
            "Id": int(group_id),
            "NegativeKeywords": {"Items": words},
        }],
    })


def main(argv):
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    l = sub.add_parser("list")
    l.add_argument("--campaign", required=True)

    c = sub.add_parser("create")
    c.add_argument("--campaign", required=True)
    c.add_argument("--name", required=True)
    c.add_argument("--regions", nargs="+", default=["225"])

    k = sub.add_parser("add-keywords")
    k.add_argument("--group", required=True)
    k.add_argument("--keywords", help="path to file, one phrase per line")
    k.add_argument("--kw", action="append", help="inline keyword (repeatable)")

    m = sub.add_parser("minus")
    m.add_argument("--group", required=True)
    m.add_argument("--words", required=True, help="comma-separated minus words")

    args = p.parse_args(argv)
    client = DirectClient(load_config())

    if args.cmd == "list":
        for g in list_groups(client, args.campaign):
            print(f"{g['Id']:>10}  {g.get('Status'):<10} {g['Name']}")
        return 0
    if args.cmd == "create":
        print(json.dumps(create_group(client, args.campaign, args.name, args.regions),
                         ensure_ascii=False, indent=2))
        return 0
    if args.cmd == "add-keywords":
        kws = _load_keywords(args)
        if not kws:
            print("no keywords given", file=sys.stderr)
            return 1
        print(f"adding {len(kws)} keywords...", file=sys.stderr)
        print(json.dumps(add_keywords(client, args.group, kws), ensure_ascii=False, indent=2))
        return 0
    if args.cmd == "minus":
        words = [w.strip() for w in args.words.split(",") if w.strip()]
        print(json.dumps(set_minus_words(client, args.group, words),
                         ensure_ascii=False, indent=2))
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
