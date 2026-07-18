#!/usr/bin/env python3
"""A/B test ads — find the statistically better creative, retire the loser.

Compares ads in a group by conversion rate (or CTR if no conversions yet) using
a two-proportion z-test. Only calls a winner when the difference is significant
AND has enough volume — no killing an ad on 3 clicks.

Examples:
    python abtest.py eval --group 222 --days 14
    python abtest.py eval --group 222 --days 14 --metric ctr
    python abtest.py eval --group 222 --apply        # pause losers (keep winner)
"""

import argparse
import math
import sys

from direct_api import DirectClient, load_config
from reports import run_report

Z_95 = 1.96
MIN_CLICKS = 50  # per ad before we trust the test


def _z_two_prop(c1, n1, c2, n2):
    """z statistic for two conversion proportions."""
    if n1 == 0 or n2 == 0:
        return 0.0
    p1, p2 = c1 / n1, c2 / n2
    p = (c1 + c2) / (n1 + n2)
    se = math.sqrt(p * (1 - p) * (1 / n1 + 1 / n2))
    if se == 0:
        return 0.0
    return (p1 - p2) / se


def evaluate(client, group_id, days, metric="conversions"):
    # Request AdGroupId so we can filter to this group client-side.
    rows = run_report(client, report_type="AD_PERFORMANCE_REPORT",
                      fields=["AdGroupId", "AdId", "Impressions", "Clicks", "Conversions"],
                      days=days)
    ads = [r for r in rows if str(r.get("AdGroupId")) == str(group_id)]
    parsed = []
    for r in ads:
        clicks = float(r.get("Clicks") or 0)
        conv = float(r.get("Conversions") or 0)
        imp = float(r.get("Impressions") or 0)
        num = conv if metric == "conversions" else clicks
        den = clicks if metric == "conversions" else imp
        parsed.append({"id": r["AdId"], "num": num, "den": den, "clicks": clicks,
                       "rate": (num / den) if den else 0})
    return parsed


def pick_winner(parsed):
    eligible = [a for a in parsed if a["clicks"] >= MIN_CLICKS]
    if len(eligible) < 2:
        return None, "not enough data (need >=2 ads with %d+ clicks)" % MIN_CLICKS
    eligible.sort(key=lambda a: -a["rate"])
    best, second = eligible[0], eligible[1]
    z = abs(_z_two_prop(best["num"], best["den"], second["num"], second["den"]))
    if z >= Z_95:
        losers = [a["id"] for a in eligible[1:]]
        return {"winner": best["id"], "z": z, "losers": losers}, "significant"
    return None, f"no significant winner yet (z={z:.2f} < {Z_95})"


def main(argv):
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("eval")
    e.add_argument("--group", required=True)
    e.add_argument("--days", type=int, default=14)
    e.add_argument("--metric", choices=["conversions", "ctr"], default="conversions")
    e.add_argument("--apply", action="store_true", help="pause loser ads")
    args = p.parse_args(argv)

    client = DirectClient(load_config())
    parsed = evaluate(client, args.group, args.days, args.metric)
    for a in parsed:
        print(f"  ad {a['id']:>10}  rate {a['rate']:.3f}  clicks {a['clicks']:.0f}")
    result, note = pick_winner(parsed)
    print(f"\n=> {note}")
    if result:
        print(f"   winner: {result['winner']}  losers: {result['losers']}")
        if args.apply:
            res = client.call("ads", "suspend",
                              {"SelectionCriteria": {"Ids": result["losers"]}})
            print("   paused losers:", res)
        else:
            print("   (dry-run — add --apply to pause losers)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
