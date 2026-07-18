#!/usr/bin/env python3
"""Campaigns service — create / list / update / suspend / resume / strategy.

Examples:
    python campaigns.py list
    python campaigns.py create --name "Search RU" --daily 1500 --strategy manual
    python campaigns.py create --name "Conv" --strategy auto_cpa --goal 12345 --cpa 1200
    python campaigns.py suspend --id 111
    python campaigns.py resume  --id 111
    python campaigns.py set-budget --id 111 --daily 2000
"""

import argparse
import json
import sys

from direct_api import DirectClient, load_config

CAMPAIGN_FIELDS = ["Id", "Name", "Status", "State", "Type", "DailyBudget", "Funds"]


def list_campaigns(client):
    res = client.call("campaigns", "get", {
        "SelectionCriteria": {},
        "FieldNames": CAMPAIGN_FIELDS,
    })
    return res.get("Campaigns", [])


def _strategy_block(args):
    """Build Search+Network strategy. Sandbox accepts the same shapes as prod."""
    if args.strategy == "manual":
        search = {"BiddingStrategyType": "HIGHEST_POSITION"}
        network = {"BiddingStrategyType": "MAXIMUM_COVERAGE"}
    elif args.strategy == "auto_cpa":
        if not args.goal:
            raise SystemExit("auto_cpa strategy requires --goal <GoalId>")
        avg_cpa = int(args.cpa or 0)
        search = {
            "BiddingStrategyType": "AVERAGE_CPA",
            "AverageCpa": {
                "AverageCpa": avg_cpa * 1_000_000,  # money in micros for strategy
                "GoalId": int(args.goal),
            },
        }
        network = {"BiddingStrategyType": "NETWORK_DEFAULT"}
    elif args.strategy == "auto_roi":
        if not args.goal:
            raise SystemExit("auto_roi strategy requires --goal <GoalId>")
        search = {
            "BiddingStrategyType": "AVERAGE_ROI",
            "AverageRoi": {
                "ReserveReturn": 100,
                "Roi": int((args.roi or 20) * 1000),  # 20% -> 20000 (millis)
                "GoalId": int(args.goal),
            },
        }
        network = {"BiddingStrategyType": "NETWORK_DEFAULT"}
    else:
        raise SystemExit(f"unknown strategy: {args.strategy}")
    return {"Search": search, "Network": network}


def create_campaign(client, args):
    text_params = {
        "BiddingStrategy": _strategy_block(args),
    }
    if args.daily:
        text_params["DailyBudget"] = {"Amount": int(args.daily) * 1_000_000, "Mode": "STANDARD"}

    campaign = {
        "Name": args.name,
        "TextCampaign": text_params,
    }
    if args.start:
        campaign["StartDate"] = args.start  # YYYY-MM-DD

    res = client.call("campaigns", "add", {"Campaigns": [campaign]})
    return res


def _toggle(client, method, campaign_id):
    return client.call("campaigns", method, {
        "SelectionCriteria": {"Ids": [int(campaign_id)]},
    })


def set_budget(client, campaign_id, daily):
    return client.call("campaigns", "update", {
        "Campaigns": [{
            "Id": int(campaign_id),
            "TextCampaign": {
                "DailyBudget": {"Amount": int(daily) * 1_000_000, "Mode": "STANDARD"},
            },
        }],
    })


def main(argv):
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list")

    c = sub.add_parser("create")
    c.add_argument("--name", required=True)
    c.add_argument("--daily", type=int, help="daily budget in currency units")
    c.add_argument("--strategy", default="manual",
                   choices=["manual", "auto_cpa", "auto_roi"])
    c.add_argument("--goal", help="Metrica GoalId for auto strategies")
    c.add_argument("--cpa", type=int, help="target CPA for auto_cpa")
    c.add_argument("--roi", type=float, help="target ROI %% for auto_roi")
    c.add_argument("--start", help="StartDate YYYY-MM-DD")

    for name in ("suspend", "resume", "archive"):
        s = sub.add_parser(name)
        s.add_argument("--id", required=True)

    b = sub.add_parser("set-budget")
    b.add_argument("--id", required=True)
    b.add_argument("--daily", type=int, required=True)

    args = p.parse_args(argv)
    client = DirectClient(load_config())

    if args.cmd == "list":
        for c in list_campaigns(client):
            print(f"{c['Id']:>10}  {c.get('State'):<9} {c.get('Status'):<10} {c['Name']}")
        return 0
    if args.cmd == "create":
        res = create_campaign(client, args)
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return 0
    if args.cmd in ("suspend", "resume", "archive"):
        res = _toggle(client, args.cmd, args.id)
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return 0
    if args.cmd == "set-budget":
        res = set_budget(client, args.id, args.daily)
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
