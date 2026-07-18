#!/usr/bin/env python3
"""Reports service — pull performance stats as parsed rows (list of dicts).

Examples:
    python reports.py campaigns --days 14
    python reports.py adgroups --campaign 111 --days 7
    python reports.py keywords --campaign 111 --days 30
    python reports.py ads --campaign 111 --days 14

Reusable from other scripts:
    from reports import run_report
    rows = run_report(client, report_type="CAMPAIGN_PERFORMANCE_REPORT",
                      fields=["CampaignId","Cost","Clicks","Impressions","Conversions"],
                      days=14)
"""

import argparse
import csv
import datetime
import io
import sys

from direct_api import DirectClient, load_config

PRESETS = {
    "campaigns": ("CAMPAIGN_PERFORMANCE_REPORT",
                  ["CampaignId", "CampaignName", "Impressions", "Clicks", "Ctr",
                   "Cost", "AvgCpc", "Conversions", "CostPerConversion"]),
    "adgroups": ("ADGROUP_PERFORMANCE_REPORT",
                 ["CampaignId", "AdGroupId", "AdGroupName", "Impressions", "Clicks",
                  "Ctr", "Cost", "Conversions", "CostPerConversion"]),
    "keywords": ("CRITERIA_PERFORMANCE_REPORT",
                 ["CampaignId", "AdGroupId", "CriteriaId", "Criteria", "Impressions",
                  "Clicks", "Ctr", "Cost", "AvgCpc", "Conversions", "CostPerConversion"]),
    "ads": ("AD_PERFORMANCE_REPORT",
            ["CampaignId", "AdGroupId", "AdId", "Impressions", "Clicks", "Ctr",
             "Cost", "Conversions", "CostPerConversion"]),
}


def run_report(client, report_type, fields, days=14, campaign_id=None,
               goals=None, attribution="LSC"):
    """Run a report, return list of dicts (str values). Dates: last N days."""
    today = datetime.date.today()
    date_from = (today - datetime.timedelta(days=days)).isoformat()
    date_to = today.isoformat()

    selection = {"DateFrom": date_from, "DateTo": date_to, "Filter": []}
    if campaign_id:
        selection["Filter"].append(
            {"Field": "CampaignId", "Operator": "EQUALS", "Values": [str(campaign_id)]}
        )

    report_def = {
        "SelectionCriteria": selection,
        "FieldNames": fields,
        "ReportName": f"{report_type}_{date_from}_{date_to}_{campaign_id or 'all'}",
        "ReportType": report_type,
        "DateRangeType": "CUSTOM_DATE",
        "Format": "TSV",
        "IncludeVAT": "YES",
    }
    if goals:
        report_def["Goals"] = [str(g) for g in goals]
        report_def["AttributionModels"] = [attribution]

    tsv = client.report(report_def, skip_report_header=True,
                        skip_column_header=False, skip_report_summary=True)
    reader = csv.DictReader(io.StringIO(tsv), delimiter="\t")
    return [row for row in reader]


def main(argv):
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("preset", choices=list(PRESETS.keys()))
    p.add_argument("--days", type=int, default=14)
    p.add_argument("--campaign", help="filter by campaign id")
    p.add_argument("--goals", nargs="*", help="Metrica GoalIds for conversion columns")
    args = p.parse_args(argv)

    client = DirectClient(load_config())
    report_type, fields = PRESETS[args.preset]
    rows = run_report(client, report_type, fields, days=args.days,
                      campaign_id=args.campaign, goals=args.goals)

    if not rows:
        print("(no data)")
        return 0
    cols = rows[0].keys()
    print("\t".join(cols))
    for r in rows:
        print("\t".join(str(r.get(c, "")) for c in cols))
    print(f"\n# {len(rows)} rows | units: {client.last_units}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
