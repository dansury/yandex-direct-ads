#!/usr/bin/env python3
"""Optimization engine — read stats, decide bid/budget/pause/minus changes.

Default is --dry-run: prints the plan, changes nothing. Add --apply to push.

Rules (thresholds from config.json -> optimization_rules), per keyword:
  * spent >= min_spend_for_minus_word with 0 conversions      -> pause keyword
  * conversions>0 and CPA  > target * cpa_pause_multiplier     -> pause keyword
  * conversions>0 and CPA  > target * cpa_lower_bid_multiplier -> lower bid by step
  * conversions>0 and CPA  < target * cpa_raise_bid_multiplier -> raise bid by step
  * clicks >= min_clicks_before_pause and 0 conversions        -> pause keyword
Bids clamped to [min_bid, max_bid].

Examples:
    python optimize.py --campaign 111                 # dry run, default
    python optimize.py --campaign 111 --apply
    python optimize.py --campaign 111 --days 30 --apply
"""

import argparse
import sys

from direct_api import DirectClient, load_config
from reports import run_report


def _f(row, key, default=0.0):
    v = row.get(key)
    if v in (None, "", "--"):
        return default
    try:
        return float(v)
    except ValueError:
        return default


def decide(rows, rules, target_cpa):
    """Return list of actions: dicts {keyword_id, criteria, action, ...}."""
    actions = []
    step = rules["bid_step_pct"] / 100.0
    for r in rows:
        kid = r.get("CriteriaId")
        if not kid:
            continue
        crit = r.get("Criteria", "")
        clicks = _f(r, "Clicks")
        cost = _f(r, "Cost")
        conv = _f(r, "Conversions")
        cur_bid = _f(r, "AvgCpc") or rules["min_bid"]
        cpa = (cost / conv) if conv else None

        if conv == 0 and cost >= rules["min_spend_for_minus_word"]:
            actions.append({"id": kid, "crit": crit, "action": "pause",
                            "why": f"spent {cost:.0f}, 0 conversions"})
            continue
        if conv == 0 and clicks >= rules["min_clicks_before_pause"]:
            actions.append({"id": kid, "crit": crit, "action": "pause",
                            "why": f"{clicks:.0f} clicks, 0 conversions"})
            continue
        if conv > 0 and cpa is not None:
            if cpa > target_cpa * rules["cpa_pause_multiplier"]:
                actions.append({"id": kid, "crit": crit, "action": "pause",
                                "why": f"CPA {cpa:.0f} >> target {target_cpa}"})
            elif cpa > target_cpa * rules["cpa_lower_bid_multiplier"]:
                new = max(rules["min_bid"], round(cur_bid * (1 - step)))
                actions.append({"id": kid, "crit": crit, "action": "bid", "bid": new,
                                "why": f"CPA {cpa:.0f} high -> lower bid to {new}"})
            elif cpa < target_cpa * rules["cpa_raise_bid_multiplier"]:
                new = min(rules["max_bid"], round(cur_bid * (1 + step)) or rules["min_bid"])
                actions.append({"id": kid, "crit": crit, "action": "bid", "bid": new,
                                "why": f"CPA {cpa:.0f} low -> raise bid to {new}"})
    return actions


def apply_actions(client, actions):
    pause_ids = [a["id"] for a in actions if a["action"] == "pause"]
    bid_items = [{"KeywordId": int(a["id"]), "SearchBid": int(a["bid"]) * 1_000_000}
                 for a in actions if a["action"] == "bid"]
    results = {}
    if pause_ids:
        results["suspend"] = client.call("keywords", "suspend",
                                         {"SelectionCriteria": {"Ids": [int(i) for i in pause_ids]}})
    if bid_items:
        results["bids"] = client.call("keywordbids", "set", {"KeywordBids": bid_items})
    return results


def main(argv):
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--campaign", required=True)
    p.add_argument("--days", type=int)
    p.add_argument("--apply", action="store_true", help="push changes (default: dry-run)")
    args = p.parse_args(argv)

    cfg = load_config()
    rules = cfg["optimization_rules"]
    target_cpa = cfg["kpi"]["target_cpa"]
    days = args.days or rules.get("lookback_days", 14)
    goals = []
    g = cfg.get("metrica", {}).get("primary_goal_id")
    if g:
        goals = [g]

    client = DirectClient(cfg)
    rows = run_report(
        client,
        report_type="CRITERIA_PERFORMANCE_REPORT",
        fields=["CampaignId", "AdGroupId", "CriteriaId", "Criteria",
                "Impressions", "Clicks", "Cost", "AvgCpc", "Conversions"],
        days=days, campaign_id=args.campaign, goals=goals or None,
    )

    actions = decide(rows, rules, target_cpa)
    if not actions:
        print(f"No changes. Reviewed {len(rows)} keywords over {days}d. CPA target {target_cpa}.")
        return 0

    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"[{mode}] target CPA {target_cpa}, {days}d, {len(actions)} actions:\n")
    for a in actions:
        tag = a["action"].upper()
        bid = f" -> {a['bid']}" if a["action"] == "bid" else ""
        print(f"  {tag:<6}{bid:<8} kw {a['id']:>10}  {a['crit'][:40]:<40}  # {a['why']}")

    if args.apply:
        res = apply_actions(client, actions)
        print("\napplied:", res.get("suspend", {}).get("SuspendResults", "-"),
              "| bids:", "ok" if "bids" in res else "-")
    else:
        print("\n(dry-run — nothing changed; add --apply to push)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
