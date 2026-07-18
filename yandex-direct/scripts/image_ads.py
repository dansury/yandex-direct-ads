#!/usr/bin/env python3
"""Image ads for РСЯ (Yandex Ad Network) — upload an image, create an image ad.

Flow: AdImages.add (base64 of a JPG/PNG) -> get AdImageHash -> Ads.add with an
ImageAd referencing that hash. Image ads run in the network (РСЯ), not search.

Image rules (Direct): JPG/PNG/GIF, 1080x607 (≈16:9) or 1x1/3x4 etc., <=10 MB.

Examples:
    python image_ads.py upload --file banner.jpg
    python image_ads.py create --group 222 --image-hash <hash> \
        --title "Диваны от фабрики" --text "Скидки до 50%" --href https://x.com
"""

import argparse
import base64
import json
import os
import sys

from direct_api import DirectClient, collect_add_results, load_config


def upload_image(client, path):
    with open(path, "rb") as fh:
        data = base64.b64encode(fh.read()).decode("ascii")
    name = path.rsplit("/", 1)[-1][:255]
    res = client.call("adimages", "add", {"AdImages": [{"ImageData": data, "Name": name}]})
    hashes, errs, _ = collect_add_results_images(res)
    return hashes, errs


def collect_add_results_images(res):
    """AdImages.add returns AdImageHash, not Id."""
    rows = res.get("AddResults", [])
    hashes, errors = [], []
    for i, row in enumerate(rows):
        if row.get("AdImageHash"):
            hashes.append(row["AdImageHash"])
        else:
            hashes.append(None)
        for e in row.get("Errors", []) or []:
            errors.append({"index": i, "code": e.get("Code"), "message": e.get("Message")})
    return hashes, errors


def create_image_ad(client, group_id, image_hash, title, text, href):
    ad = {
        "AdGroupId": int(group_id),
        "TextImageAd": {"AdImageHash": image_hash, "Href": href},
    }
    # Some account types use TextAd+AdImageHash for image-in-search; РСЯ image ad
    # uses ImageAd/TextImageAd. TextImageAd needs an associated creative; the
    # simplest portable path is the image creative referenced by hash.
    return client.call("ads", "add", {"Ads": [ad]})


def upload_directory(client, directory):
    out = []
    for name in sorted(os.listdir(directory)):
        path = os.path.join(directory, name)
        if not os.path.isfile(path):
            continue
        if not name.lower().endswith((".jpg", ".jpeg", ".png", ".gif")):
            continue
        hashes, errs = upload_image(client, path)
        out.append({"file": path, "image_hashes": hashes, "errors": errs})
    return out


def create_image_ads_bulk(client, group_id, manifest):
    results = []
    for item in manifest:
        res = create_image_ad(
            client,
            group_id=group_id,
            image_hash=item["image_hash"],
            title=item.get("title") or "Рекламное предложение",
            text=item.get("text") or "Подробнее на сайте",
            href=item["href"],
        )
        ids, errs, _ = collect_add_results(res)
        results.append({
            "image_hash": item["image_hash"],
            "ids": ids,
            "errors": errs,
            "title": item.get("title") or "",
        })
    return results


def main(argv):
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    u = sub.add_parser("upload")
    u.add_argument("--file", required=True)

    c = sub.add_parser("create")
    c.add_argument("--group", required=True)
    c.add_argument("--image-hash", required=True)
    c.add_argument("--title", required=True)
    c.add_argument("--text", required=True)
    c.add_argument("--href", required=True)

    ud = sub.add_parser("upload-dir")
    ud.add_argument("--dir", required=True)

    cb = sub.add_parser("create-bulk")
    cb.add_argument("--group", required=True)
    cb.add_argument("--manifest", required=True,
                    help="json list with image_hash/title/text/href items")

    args = p.parse_args(argv)
    client = DirectClient(load_config())

    if args.cmd == "upload":
        hashes, errs = upload_image(client, args.file)
        print(json.dumps({"image_hashes": hashes, "errors": errs}, ensure_ascii=False))
        return 1 if errs else 0
    if args.cmd == "create":
        res = create_image_ad(client, args.group, args.image_hash,
                              args.title, args.text, args.href)
        ids, errs, _ = collect_add_results(res)
        print(json.dumps({"ids": ids, "errors": errs}, ensure_ascii=False, indent=2))
        return 1 if errs else 0
    if args.cmd == "upload-dir":
        print(json.dumps(upload_directory(client, args.dir), ensure_ascii=False, indent=2))
        return 0
    if args.cmd == "create-bulk":
        with open(args.manifest, "r", encoding="utf-8") as fh:
            manifest = json.load(fh)
        print(json.dumps(create_image_ads_bulk(client, args.group, manifest),
                         ensure_ascii=False, indent=2))
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
