#!/usr/bin/env python3
"""Weekly autopilot — one command runs the full maintenance loop.

Sequence per campaign:
  1. mine wasteful queries -> suggest minus-words
  2. mine winning queries  -> hints for ad copy refresh
  3. optimize bids/pauses  (dry-run unless --apply)
  4. anomaly scan
  5. print a compact report (spend, leads, CPA vs target)

Designed to be driven by /schedule for hands-off weekly runs.

Examples:
    python autopilot.py --campaign 111            # dry-run report + plan
    python autopilot.py --campaign 111 --apply    # also applies optimizer changes
"""

import argparse
import sys

import anomalies
import keywords as kw
import optimize as opt
from direct_api import DirectClient, load_config
from reports import run_report


def run(client, cfg, campaign_id, days, apply):
    target_cpa = cfg["kpi"]["target_cpa"]
    print(f"=== AUTOPILOT campaign {campaign_id} | {days}d | target CPA {target_cpa} ===\n")

    # 1. waste -> minus-word candidates
    waste = kw.mine_queries(client, campaign_id, days,
                            cfg["optimization_rules"]["min_spend_for_minus_word"])
    print(f"[1] wasteful queries (minus-word candidates): {len(waste)}")
    for q, cost in waste[:10]:
        print(f"      {cost:>7.0f}  {q}")

    # 2. winners -> copy refresh hints
    winners = kw.mine_winners(client, campaign_id, days, min_conversions=1)
    print(f"\n[2] top converting queries (use in ad copy): {len(winners)}")
    for q, conv, cost in winners[:10]:
        print(f"      {conv:>4.0f} conv  {q}")

    # 3. optimizer
    rules = cfg["optimization_rules"]
    goals = [cfg["metrica"]["primary_goal_id"]] if cfg.get("metrica", {}).get("primary_goal_id") else None
    rows = run_report(client, report_type="CRITERIA_PERFORMANCE_REPORT",
                      fields=["CampaignId", "AdGroupId", "CriteriaId", "Criteria",
                              "Impressions", "Clicks", "Cost", "AvgCpc", "Conversions"],
                      days=days, campaign_id=campaign_id, goals=goals)
    actions = opt.decide(rows, rules, target_cpa)
    print(f"\n[3] optimizer actions: {len(actions)}")
    for a in actions[:20]:
        bid = f"->{a['bid']}" if a["action"] == "bid" else ""
        print(f"      {a['action'].upper():<6}{bid:<8} {a['crit'][:40]}  # {a['why']}")
    if apply and actions:
        res = opt.apply_actions(client, actions)
        print("      applied.")

    # 4. anomalies
    by_camp = anomalies._daily(client, campaign_id, days)
    alerts = anomalies.detect(by_camp, spike=1.5, target_cpa=target_cpa)
    print(f"\n[4] anomalies: {len(alerts)}")
    for cid, name, kind, detail in alerts:
        print(f"      [{kind}] {name}: {detail}")

    # 5. summary
    tot_cost = sum(float(r.get("Cost") or 0) for r in rows)
    tot_conv = sum(float(r.get("Conversions") or 0) for r in rows)
    cpa = (tot_cost / tot_conv) if tot_conv else None
    print(f"\n[5] SUMMARY {days}d: spend {tot_cost:.0f}, leads {tot_conv:.0f}, "
          f"CPA {('%.0f' % cpa) if cpa else 'n/a'} (target {target_cpa})")
    if not apply:
        print("\n(dry-run — add --apply to enact optimizer changes)")
    return 0


def main(argv):
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--campaign", required=True)
    p.add_argument("--days", type=int, default=7)
    p.add_argument("--apply", action="store_true")
    args = p.parse_args(argv)

    cfg = load_config()
    client = DirectClient(cfg)
    return run(client, cfg, args.campaign, args.days, args.apply)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
