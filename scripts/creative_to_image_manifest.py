#!/usr/bin/env python3
"""Convert generated creative metadata into an image-ad creation manifest.

Takes:
  - generate_creatives.py result (API mode), or
  - upload-dir output enriched with image hashes

Produces:
  list of {image_hash,title,text,href}
"""

import argparse
import json
import sys


def _load(path):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def from_generated(result, href, default_title, default_text):
    items = []
    for asset in result.get("assets", []):
        image_hash = asset.get("image_hash")
        if not image_hash:
            continue
        items.append({
            "image_hash": image_hash,
            "title": asset.get("title") or default_title,
            "text": asset.get("text") or default_text,
            "href": asset.get("href") or href,
        })
    return items


def from_uploads(result, href, default_title, default_text):
    items = []
    for row in result:
        hashes = [h for h in row.get("image_hashes", []) if h]
        for image_hash in hashes:
            items.append({
                "image_hash": image_hash,
                "title": default_title,
                "text": default_text,
                "href": href,
            })
    return items


def main(argv):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--input", required=True)
    p.add_argument("--mode", required=True, choices=["generated", "uploads"])
    p.add_argument("--href", required=True)
    p.add_argument("--title", default="Рекламное предложение")
    p.add_argument("--text", default="Подробнее на сайте")
    args = p.parse_args(argv)

    src = _load(args.input)
    if args.mode == "generated":
        out = from_generated(src, args.href, args.title, args.text)
    else:
        out = from_uploads(src, args.href, args.title, args.text)
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
