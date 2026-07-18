#!/usr/bin/env python3
"""Bid adjustments (BidModifiers) — mobile, demographics, dayparting hooks.

Adjustments multiply your bid for a segment, often the cheapest CPA win there is:
bid less where it doesn't convert, more where it does. BidModifier is a percent
(e.g. 50 = bid x0.5, 120 = +20%). Range per Direct: typically 0–1300.

Examples:
    # bid 30% less on mobile for a campaign
    python adjustments.py mobile --campaign 111 --pct 70
    # bid 50% more on women 25-34
    python adjustments.py demographics --campaign 111 --gender GENDER_FEMALE \
        --age AGE_25_34 --pct 150
    python adjustments.py list --campaign 111
    python adjustments.py toggle --id 555 --enabled true
"""

import argparse
import json
import sys

from direct_api import DirectClient, collect_add_results, load_config

AGES = ["AGE_0_17", "AGE_18_24", "AGE_25_34", "AGE_35_44",
        "AGE_45", "AGE_45_54", "AGE_55"]
GENDERS = ["GENDER_MALE", "GENDER_FEMALE"]


def _target(campaign=None, adgroup=None):
    t = {}
    if campaign:
        t["CampaignId"] = int(campaign)
    if adgroup:
        t["AdGroupId"] = int(adgroup)
    if not t:
        raise SystemExit("need --campaign or --group")
    return t


def add_mobile(client, pct, campaign=None, adgroup=None, os_type=None):
    item = _target(campaign, adgroup)
    mobile = {"BidModifier": int(pct)}
    if os_type:
        mobile["OperatingSystemType"] = os_type  # IOS | ANDROID
    item["MobileAdjustment"] = mobile
    return client.call("bidmodifiers", "add", {"BidModifiers": [item]})


def add_demographics(client, pct, gender=None, age=None, campaign=None, adgroup=None):
    item = _target(campaign, adgroup)
    demo = {"BidModifier": int(pct)}
    if gender:
        demo["Gender"] = gender
    if age:
        demo["Age"] = age
    item["DemographicsAdjustment"] = demo
    return client.call("bidmodifiers", "add", {"BidModifiers": [item]})


def list_modifiers(client, campaign=None, adgroup=None):
    sel = {}
    if campaign:
        sel["CampaignIds"] = [int(campaign)]
    if adgroup:
        sel["AdGroupIds"] = [int(adgroup)]
    res = client.call("bidmodifiers", "get", {
        "SelectionCriteria": sel,
        "FieldNames": ["Id", "CampaignId", "AdGroupId", "Type", "Level"],
        "MobileAdjustmentFieldNames": ["BidModifier", "OperatingSystemType"],
        "DemographicsAdjustmentFieldNames": ["BidModifier", "Age", "Gender"],
    })
    return res.get("BidModifiers", [])


def set_enabled(client, modifier_id, enabled):
    method = "toggle"
    return client.call("bidmodifiers", "toggle", {
        "BidModifierToggleItems": [
            {"Id": int(modifier_id), "Enabled": "YES" if enabled else "NO"}
        ]
    })


def main(argv):
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    m = sub.add_parser("mobile")
    m.add_argument("--campaign"); m.add_argument("--group")
    m.add_argument("--pct", type=int, required=True)
    m.add_argument("--os", choices=["IOS", "ANDROID"])

    d = sub.add_parser("demographics")
    d.add_argument("--campaign"); d.add_argument("--group")
    d.add_argument("--gender", choices=GENDERS)
    d.add_argument("--age", choices=AGES)
    d.add_argument("--pct", type=int, required=True)

    l = sub.add_parser("list")
    l.add_argument("--campaign"); l.add_argument("--group")

    t = sub.add_parser("toggle")
    t.add_argument("--id", required=True)
    t.add_argument("--enabled", choices=["true", "false"], required=True)

    args = p.parse_args(argv)
    client = DirectClient(load_config())

    if args.cmd == "mobile":
        res = add_mobile(client, args.pct, args.campaign, args.group, args.os)
        ids, errs, _ = collect_add_results(res)
        print(json.dumps({"ids": ids, "errors": errs}, ensure_ascii=False, indent=2))
        return 1 if errs else 0
    if args.cmd == "demographics":
        res = add_demographics(client, args.pct, args.gender, args.age,
                               args.campaign, args.group)
        ids, errs, _ = collect_add_results(res)
        print(json.dumps({"ids": ids, "errors": errs}, ensure_ascii=False, indent=2))
        return 1 if errs else 0
    if args.cmd == "list":
        for bm in list_modifiers(client, args.campaign, args.group):
            print(json.dumps(bm, ensure_ascii=False))
        return 0
    if args.cmd == "toggle":
        print(json.dumps(set_enabled(client, args.id, args.enabled == "true"),
                         ensure_ascii=False, indent=2))
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
