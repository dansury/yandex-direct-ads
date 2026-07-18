#!/usr/bin/env python3
"""KeywordBids + search-query mining for minus-words.

Examples:
    python keywords.py set-bid --group 222 --bid 45         # bid in currency units
    python keywords.py set-bid --id 333 --bid 60            # single keyword
    python keywords.py list --group 222
    python keywords.py mine-queries --campaign 111 --days 14 --min-spend 500
        -> prints search queries that spent money with zero conversions
           (candidates for minus-words). Pair with adgroups.py minus.
"""

import argparse
import json
import sys

from direct_api import DirectClient, load_config


def list_keywords(client, group_id):
    res = client.call("keywords", "get", {
        "SelectionCriteria": {"AdGroupIds": [int(group_id)]},
        "FieldNames": ["Id", "Keyword", "Bid", "State", "Status"],
    })
    return res.get("Keywords", [])


def set_bid(client, ids, bid):
    """bid in currency units -> micros. KeywordBids.set targets keyword Ids."""
    items = [{"KeywordId": int(i), "SearchBid": int(bid) * 1_000_000} for i in ids]
    return client.call("keywordbids", "set", {"KeywordBids": items})


def set_group_bid(client, group_id, bid):
    kws = list_keywords(client, group_id)
    ids = [k["Id"] for k in kws]
    if not ids:
        return {"info": "no keywords in group"}
    return set_bid(client, ids, bid)


def auction(client, group_id):
    """Read live auction (AuctionBids) per keyword: what bid buys what traffic.

    Lets you set a competitive bid grounded in the current auction instead of
    guessing. Returns list of (keyword_id, keyword, [(traffic_volume, bid), ...]).
    """
    res = client.call("keywords", "get", {
        "SelectionCriteria": {"AdGroupIds": [int(group_id)]},
        "FieldNames": ["Id", "Keyword"],
        "AuctionBidFieldNames": ["AuctionBids"],
    })
    out = []
    for k in res.get("Keywords", []):
        bids = []
        for ab in (k.get("AuctionBids") or {}).get("Search", {}).get("AuctionBidItems", []):
            bids.append((ab.get("TrafficVolume"), (ab.get("Bid") or 0) / 1_000_000))
        out.append((k["Id"], k.get("Keyword"), bids))
    return out


def mine_winners(client, campaign_id, days, min_conversions=1):
    """Top converting search queries — feed these back into ad copy (RSA relevance)."""
    from reports import run_report
    rows = run_report(client, report_type="SEARCH_QUERY_PERFORMANCE_REPORT",
                      fields=["Query", "Cost", "Clicks", "Conversions"],
                      days=days, campaign_id=campaign_id)
    winners = []
    for r in rows:
        conv = float(r.get("Conversions") or 0)
        if conv >= min_conversions:
            winners.append((r.get("Query"), conv, float(r.get("Cost") or 0)))
    winners.sort(key=lambda x: -x[1])
    return winners


def mine_queries(client, campaign_id, days, min_spend):
    """Use Reports SEARCH_QUERY_PERFORMANCE_REPORT to find spend-no-conversion queries."""
    from reports import run_report  # local import to avoid cycle at import time
    report_def = {
        "SelectionCriteria": {"Filter": [
            {"Field": "CampaignId", "Operator": "EQUALS", "Values": [str(campaign_id)]},
        ]},
        "FieldNames": ["Query", "Cost", "Clicks", "Conversions"],
        "ReportName": f"queries_{campaign_id}_{days}d",
        "ReportType": "SEARCH_QUERY_PERFORMANCE_REPORT",
        "DateRangeType": "LAST_N_DAYS" if False else "CUSTOM_DATE",
        "Format": "TSV",
        "IncludeVAT": "YES",
    }
    rows = run_report(client, campaign_id=campaign_id, days=days,
                      report_type="SEARCH_QUERY_PERFORMANCE_REPORT",
                      fields=["Query", "Cost", "Clicks", "Conversions"])
    waste = []
    for r in rows:
        cost = float(r.get("Cost") or 0)
        conv = float(r.get("Conversions") or 0)
        if cost >= min_spend and conv == 0:
            waste.append((r.get("Query"), cost))
    waste.sort(key=lambda x: -x[1])
    return waste


def main(argv):
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("set-bid")
    s.add_argument("--group", help="apply to all keywords in group")
    s.add_argument("--id", action="append", help="keyword id (repeatable)")
    s.add_argument("--bid", type=int, required=True)

    l = sub.add_parser("list")
    l.add_argument("--group", required=True)

    m = sub.add_parser("mine-queries")
    m.add_argument("--campaign", required=True)
    m.add_argument("--days", type=int, default=14)
    m.add_argument("--min-spend", type=float, default=500)

    a = sub.add_parser("auction")
    a.add_argument("--group", required=True)

    w = sub.add_parser("mine-winners")
    w.add_argument("--campaign", required=True)
    w.add_argument("--days", type=int, default=30)
    w.add_argument("--min-conversions", type=float, default=1)

    args = p.parse_args(argv)
    client = DirectClient(load_config())

    if args.cmd == "set-bid":
        if args.group:
            print(json.dumps(set_group_bid(client, args.group, args.bid),
                             ensure_ascii=False, indent=2))
        elif args.id:
            print(json.dumps(set_bid(client, args.id, args.bid),
                             ensure_ascii=False, indent=2))
        else:
            print("need --group or --id", file=sys.stderr)
            return 1
        return 0
    if args.cmd == "list":
        for k in list_keywords(client, args.group):
            bid = (k.get("Bid") or 0) / 1_000_000
            print(f"{k['Id']:>10} {bid:>8.0f}  {k.get('Keyword')}")
        return 0
    if args.cmd == "mine-queries":
        waste = mine_queries(client, args.campaign, args.days, args.min_spend)
        if not waste:
            print("no wasteful queries found")
            return 0
        print("# query\tcost (spend, no conversions)")
        for q, cost in waste:
            print(f"{q}\t{cost:.0f}")
        return 0
    if args.cmd == "auction":
        for kid, kw, bids in auction(client, args.group):
            tail = "  ".join(f"{v}%:{b:.0f}" for v, b in bids[:5])
            print(f"{kid:>10}  {kw[:30]:<30}  {tail}")
        return 0
    if args.cmd == "mine-winners":
        winners = mine_winners(client, args.campaign, args.days, args.min_conversions)
        if not winners:
            print("no converting queries found")
            return 0
        print("# query\tconversions\tcost")
        for q, conv, cost in winners:
            print(f"{q}\t{conv:.0f}\t{cost:.0f}")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
