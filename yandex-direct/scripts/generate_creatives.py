#!/usr/bin/env python3
"""Generate ad creatives through a configured API or MCP bridge.

Examples:
    python generate_creatives.py --brief brief.json
    python generate_creatives.py --brief brief.json --out ./generated

brief.json example:
{
  "offer": "Диваны от производителя",
  "audience": "семьи и владельцы квартир в Москве",
  "geo": "Москва",
  "cta": "Оставьте заявку",
  "style": "premium furniture ad",
  "size": "1200x628",
  "asset_types": ["banner", "photo"],
  "variants": 2,
  "required_elements": ["диван в интерьере", "мягкий дневной свет"],
  "forbidden_elements": ["водяные знаки", "лишний текст"]
}
"""

import argparse
import json
import os
import sys

from creative_provider import CreativeProviderError, generate_assets
from direct_api import load_config


def _default_output_dir(brief_path):
    base = os.path.splitext(os.path.basename(brief_path))[0]
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "generated", base)


def main(argv):
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--brief", required=True, help="path to creative brief json")
    p.add_argument("--out", help="output directory for generated files")
    args = p.parse_args(argv)

    with open(args.brief, "r", encoding="utf-8") as fh:
        brief = json.load(fh)

    cfg = load_config()
    out_dir = args.out or _default_output_dir(args.brief)

    try:
        result = generate_assets(cfg, brief, out_dir)
    except CreativeProviderError as e:
        print(str(e), file=sys.stderr)
        return 1

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
