#!/usr/bin/env python3
"""Dictionaries service — reference data (regions, currencies, constants...).

Examples:
    python dictionaries.py get --names GeoRegions
    python dictionaries.py get --names Currencies TimeZones
    python dictionaries.py list-names
"""

import argparse
import json
import sys

from direct_api import DirectClient, load_config

DICTIONARY_NAMES = [
    "Currencies", "GeoRegions", "TimeZones", "Constants", "AdCategories",
    "OperationSystemVersions", "TrackingSystems", "InterfaceLanguages",
    "GoalsIDs",
]


def get_dictionaries(client, names):
    return client.call("dictionaries", "get", {"DictionaryNames": names})


def main(argv):
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    g = sub.add_parser("get")
    g.add_argument("--names", nargs="+", required=True)

    sub.add_parser("list-names")

    args = p.parse_args(argv)

    if args.cmd == "list-names":
        print("\n".join(DICTIONARY_NAMES))
        return 0

    client = DirectClient(load_config())
    res = get_dictionaries(client, args.names)
    print(json.dumps(res, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
