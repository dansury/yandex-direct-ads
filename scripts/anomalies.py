#!/usr/bin/env python3
"""Spend & performance anomaly watch — catch a blown budget before it hurts.

Compares the most recent day against the trailing baseline per campaign and
flags: spend spike with no matching conversion lift, conversions collapse, or
CPA blowout. Read-only — prints alerts; pair with /schedule for daily runs.

Examples:
    python anomalies.py --days 14
    python anomalies.py --days 14 --spike 1.5 --campaign 111
"""

import argparse
import datetime
import sys

from direct_api import DirectClient, load_config
from reports import run_report


def _daily(client, campaign_id, days):
    rows = run_report(
        client, report_type="CAMPAIGN_PERFORMANCE_REPORT",
        fields=["Date", "CampaignId", "CampaignName", "Cost", "Clicks", "Conversions"],
        days=days, campaign_id=campaign_id)
    # group by campaign -> date -> metrics
    by_camp = {}
    for r in rows:
        cid = r.get("CampaignId")
        d = r.get("Date")
        by_camp.setdefault(cid, {"name": r.get("CampaignName"), "days": {}})
        by_camp[cid]["days"][d] = {
            "cost": float(r.get("Cost") or 0),
            "conv": float(r.get("Conversions") or 0),
        }
    return by_camp


def detect(by_camp, spike=1.5, target_cpa=None):
    alerts = []
    for cid, info in by_camp.items():
        days = sorted(info["days"].keys())
        if len(days) < 4:
            continue
        last = info["days"][days[-1]]
        base_days = days[:-1]
        base_cost = sum(info["days"][d]["cost"] for d in base_days) / len(base_days)
        base_conv = sum(info["days"][d]["conv"] for d in base_days) / len(base_days)

        if base_cost > 0 and last["cost"] > base_cost * spike:
            if last["conv"] <= base_conv:  # spend up, conversions not up
                alerts.append((cid, info["name"], "SPEND SPIKE",
                               f"cost {last['cost']:.0f} vs avg {base_cost:.0f}, "
                               f"conv {last['conv']:.0f} vs avg {base_conv:.1f}"))
        if base_conv >= 1 and last["conv"] == 0:
            alerts.append((cid, info["name"], "CONVERSIONS DROP",
                           f"0 conversions today vs avg {base_conv:.1f}"))
        if target_cpa and last["conv"] > 0:
            cpa = last["cost"] / last["conv"]
            if cpa > target_cpa * 2:
                alerts.append((cid, info["name"], "CPA BLOWOUT",
                               f"CPA {cpa:.0f} vs target {target_cpa}"))
    return alerts


def main(argv):
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--days", type=int, default=14)
    p.add_argument("--spike", type=float, default=1.5, help="spend multiple vs baseline")
    p.add_argument("--campaign")
    args = p.parse_args(argv)

    cfg = load_config()
    client = DirectClient(cfg)
    target_cpa = cfg.get("kpi", {}).get("target_cpa")
    by_camp = _daily(client, args.campaign, args.days)
    alerts = detect(by_camp, spike=args.spike, target_cpa=target_cpa)
    if not alerts:
        print("OK — no anomalies in the last day.")
        return 0
    print(f"{len(alerts)} ALERT(S):")
    for cid, name, kind, detail in alerts:
        print(f"  [{kind}] campaign {cid} {name}: {detail}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
