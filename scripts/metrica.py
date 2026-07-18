#!/usr/bin/env python3
"""Yandex.Metrica Reporting API — pull leads/conversions by goal.

Uses the same OAuth token if it was issued with the Metrica scope
(metrika:read). counter_id and goal ids come from config.json -> "metrica".

Examples:
    python metrica.py goals                       # list goals on the counter
    python metrica.py conversions --days 14       # leads by Direct campaign
    python metrica.py summary --days 7

Reusable:
    from metrica import conversions_by_campaign
    data = conversions_by_campaign(cfg, days=14)  # {direct_campaign_id: leads}
"""

import argparse
import json
import sys
import urllib.parse
import urllib.request

from direct_api import load_config

METRICA_BASE = "https://api-metrika.yandex.net"


def _get(token, path, params):
    url = METRICA_BASE + path + "?" + urllib.parse.urlencode(params, doseq=True)
    req = urllib.request.Request(url, headers={"Authorization": f"OAuth {token}"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8"))


def list_goals(cfg):
    token = cfg["token"]
    counter = cfg["metrica"]["counter_id"]
    data = _get(token, f"/management/v1/counter/{counter}/goals", {})
    return data.get("goals", [])


def conversions_by_campaign(cfg, days=14, goal_id=None):
    """Returns {direct_campaign_id(str): goal_reaches(float)} from Metrica.

    Best-effort cross-check. The PRIMARY source of leads-per-campaign is the
    Direct report itself (reports.py with --goals): it is server-side joined and
    robust. Use this only when you need Metrica-side numbers (e.g. goals Direct
    can't see). Groups by the ym:ad:directCampaignID dimension.
    """
    token = cfg["token"]
    counter = cfg["metrica"]["counter_id"]
    goal_id = goal_id or cfg["metrica"].get("primary_goal_id")
    metric = f"ym:ad:goal{goal_id}reaches" if goal_id else "ym:ad:goalreaches"
    params = {
        "ids": counter,
        "metrics": metric,
        "dimensions": "ym:ad:directCampaignID",
        "date1": f"{days}daysAgo",
        "date2": "today",
        "filters": "ym:ad:directCampaignID!=0",
        "limit": 10000,
        "accuracy": "full",
    }
    data = _get(token, "/stat/v1/data", params)
    out = {}
    for row in data.get("data", []):
        dims = row.get("dimensions", [])
        direct_id = str(dims[0].get("id") or dims[0].get("name")) if dims else None
        reaches = row.get("metrics", [0])[0]
        if direct_id and direct_id != "0":
            out[direct_id] = out.get(direct_id, 0) + reaches
    return out


def summary(cfg, days=7):
    token = cfg["token"]
    counter = cfg["metrica"]["counter_id"]
    goal_id = cfg["metrica"].get("primary_goal_id")
    metrics = ["ym:s:visits", "ym:s:users"]
    if goal_id:
        metrics.append(f"ym:s:goal{goal_id}reaches")
        metrics.append(f"ym:s:goal{goal_id}conversionRate")
    params = {
        "ids": counter,
        "metrics": ",".join(metrics),
        "date1": f"{days}daysAgo",
        "date2": "today",
        "filters": "ym:s:lastDirectClickOrder!n",
        "accuracy": "full",
    }
    data = _get(token, "/stat/v1/data", params)
    return data.get("totals", [])


def main(argv):
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("goals")
    c = sub.add_parser("conversions")
    c.add_argument("--days", type=int, default=14)
    c.add_argument("--goal", type=int)
    s = sub.add_parser("summary")
    s.add_argument("--days", type=int, default=7)

    args = p.parse_args(argv)
    cfg = load_config()
    if not cfg.get("metrica", {}).get("counter_id"):
        print("Set metrica.counter_id in config.json (see references/setup-guide.md)",
              file=sys.stderr)
        return 1

    if args.cmd == "goals":
        for g in list_goals(cfg):
            print(f"{g.get('id'):>10}  {g.get('name')}")
        return 0
    if args.cmd == "conversions":
        data = conversions_by_campaign(cfg, days=args.days, goal_id=args.goal)
        print("# direct_campaign_id\tleads")
        for cid, leads in sorted(data.items(), key=lambda x: -x[1]):
            print(f"{cid}\t{leads:.0f}")
        return 0
    if args.cmd == "summary":
        print(json.dumps(summary(cfg, days=args.days), ensure_ascii=False, indent=2))
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
