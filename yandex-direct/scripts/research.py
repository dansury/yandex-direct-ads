#!/usr/bin/env python3
"""Keyword research — expand seeds into phrases and check which have demand.

Two stages:
  1) expand: model/you give seeds + modifiers -> cartesian phrases (local, free).
  2) hasSearchVolume: KeywordsResearch.hasSearchVolume filters out phrases that
     get (almost) no searches in a region, so you don't waste structure on dead
     keywords. (Exact volumes come from Wordstat in the UI; the API answers the
     cheaper yes/no "does this get searched".)

Examples:
    python research.py expand --seed "диван" --seed "диваны" \
        --mod "купить" --mod "цена" --mod "в москве" --mod "недорого"
    python research.py expand ... | python research.py demand --region 213
    python research.py demand --region 213 --file phrases.txt
"""

import argparse
import itertools
import sys

from direct_api import DirectClient, load_config


def expand(seeds, mods, include_bare=True):
    """Cartesian product seed x modifier, both orders, deduped."""
    out = set()
    if include_bare:
        out.update(seeds)
    for s, m in itertools.product(seeds, mods):
        out.add(f"{m} {s}".strip())
        out.add(f"{s} {m}".strip())
    return sorted(out)


def has_search_volume(client, phrases, region_id):
    """KeywordsResearch.hasSearchVolume -> dict {phrase: bool}.

    The service answers whether each phrase has search volume in the region.
    Chunks to stay within request limits.
    """
    result = {}
    for i in range(0, len(phrases), 1000):
        chunk = phrases[i:i + 1000]
        res = client.call("keywordsresearch", "hasSearchVolume", {
            "SelectionCriteria": {
                "Keywords": chunk,
                "RegionIds": [int(region_id)],
            },
            "FieldNames": ["Keyword", "HasSearchVolume"],
        })
        for item in res.get("HasSearchVolumeResults", []):
            result[item.get("Keyword")] = bool(item.get("HasSearchVolume"))
    return result


def _read_phrases(args):
    phrases = []
    if args.file:
        with open(args.file, "r", encoding="utf-8") as fh:
            phrases = [l.strip() for l in fh if l.strip()]
    elif not sys.stdin.isatty():
        phrases = [l.strip() for l in sys.stdin if l.strip()]
    return phrases


def main(argv):
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    e = sub.add_parser("expand")
    e.add_argument("--seed", action="append", required=True)
    e.add_argument("--mod", action="append", default=[])
    e.add_argument("--no-bare", action="store_true")

    d = sub.add_parser("demand")
    d.add_argument("--region", default="225")
    d.add_argument("--file", help="phrases file; else reads stdin")
    d.add_argument("--only-live", action="store_true",
                   help="print only phrases that have search volume")

    args = p.parse_args(argv)

    if args.cmd == "expand":
        for ph in expand(args.seed, args.mod, include_bare=not args.no_bare):
            print(ph)
        return 0

    if args.cmd == "demand":
        phrases = _read_phrases(args)
        if not phrases:
            print("no phrases (use --file or pipe stdin)", file=sys.stderr)
            return 1
        client = DirectClient(load_config())
        vol = has_search_volume(client, phrases, args.region)
        for ph in phrases:
            live = vol.get(ph, False)
            if args.only_live:
                if live:
                    print(ph)
            else:
                print(f"{'LIVE' if live else 'dead'}\t{ph}")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
