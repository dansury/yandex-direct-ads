#!/usr/bin/env python3
"""Text ads — validate char limits, create, list, moderate.

The model (Claude) writes the copy following references/ad-copy-rules.md,
then hands a JSON file to this script. This script is the last safety gate:
it hard-validates Direct character limits and rejects anything over.

ads.json format (list of ad objects):
[
  {
    "title": "Купить диван в Москве",
    "title2": "Доставка за 1 день",
    "text": "Каталог 500+ моделей. Скидки до 30%. Бесплатный замер. Звоните!",
    "href": "https://example.com/divany",
    "display_path": "divany",
    "sitelinks": [
      {"title": "Угловые", "href": "https://example.com/uglovye", "description": "от 19900"},
      {"title": "Прямые",  "href": "https://example.com/pryamye"}
    ],
    "callouts": ["Гарантия 18 мес", "Рассрочка 0%"]
  }
]

Examples:
    python ads.py validate --file ads.json
    python ads.py create --group 222 --file ads.json
    python ads.py list --group 222
    python ads.py moderate --group 222     # send drafts to moderation
"""

import argparse
import json
import sys

from direct_api import DirectClient, load_config

# Hard limits enforced by Yandex.Direct (chars).
LIMITS = {
    "title": 56,
    "title2": 30,
    "text": 81,
    "display_path": 20,
    "sitelink_title": 30,
    "sitelink_description": 60,
    "callout": 25,
}
MAX_SITELINKS = 8
MAX_CALLOUTS = 4
# A '!' is allowed but only one exclamation mark per title/text; no CAPS-LOCK words.


def validate_ad(ad, idx):
    errs = []
    for field in ("title", "title2", "text", "display_path"):
        val = ad.get(field)
        if val is None:
            if field in ("title", "text"):
                errs.append(f"ad[{idx}]: missing required '{field}'")
            continue
        if len(val) > LIMITS[field]:
            errs.append(f"ad[{idx}].{field}: {len(val)}>{LIMITS[field]} chars: {val!r}")
        if val.count("!") > 1:
            errs.append(f"ad[{idx}].{field}: more than one '!'")
    for i, sl in enumerate(ad.get("sitelinks", [])[:MAX_SITELINKS]):
        if len(sl.get("title", "")) > LIMITS["sitelink_title"]:
            errs.append(f"ad[{idx}].sitelink[{i}].title too long")
        if len(sl.get("description", "")) > LIMITS["sitelink_description"]:
            errs.append(f"ad[{idx}].sitelink[{i}].description too long")
    if len(ad.get("sitelinks", [])) > MAX_SITELINKS:
        errs.append(f"ad[{idx}]: >{MAX_SITELINKS} sitelinks")
    for i, co in enumerate(ad.get("callouts", [])[:MAX_CALLOUTS]):
        if len(co) > LIMITS["callout"]:
            errs.append(f"ad[{idx}].callout[{i}] too long")
    if len(ad.get("callouts", [])) > MAX_CALLOUTS:
        errs.append(f"ad[{idx}]: >{MAX_CALLOUTS} callouts")
    return errs


def validate_file(path):
    with open(path, "r", encoding="utf-8") as fh:
        ads = json.load(fh)
    all_errs = []
    for i, ad in enumerate(ads):
        all_errs.extend(validate_ad(ad, i))
    return ads, all_errs


def _sitelinks_set(client, sitelinks):
    """Create a sitelinks set, return SitelinksSetId."""
    items = []
    for sl in sitelinks[:MAX_SITELINKS]:
        item = {"Title": sl["title"], "Href": sl["href"]}
        if sl.get("description"):
            item["Description"] = sl["description"]
        items.append(item)
    res = client.call("sitelinks", "add", {"SitelinksSets": [{"Sitelinks": items}]})
    ids = res.get("AddResults", [])
    return ids[0].get("Id") if ids else None


def build_ad_payload(client, group_id, ad):
    text_ad = {
        "Title": ad["title"],
        "Text": ad["text"],
        "Href": ad.get("href"),
        "Mobile": "NO",
    }
    if ad.get("title2"):
        text_ad["Title2"] = ad["title2"]
    if ad.get("display_path"):
        text_ad["DisplayUrlPath"] = ad["display_path"]
    if ad.get("callouts"):
        # Create callout extensions, then attach their ids via CalloutSetting.
        from extensions import ensure_callouts
        callout_ids = ensure_callouts(client, ad["callouts"][:MAX_CALLOUTS])
        if callout_ids:
            text_ad["CalloutSetting"] = {
                "Additions": [{"AdExtensionId": cid, "Operation": "ADD"}
                              for cid in callout_ids]
            }
    if ad.get("sitelinks"):
        sl_id = _sitelinks_set(client, ad["sitelinks"])
        if sl_id:
            text_ad["SitelinksSetId"] = sl_id
    return {"AdGroupId": int(group_id), "TextAd": text_ad}


def create_ads(client, group_id, ads):
    payload = [build_ad_payload(client, group_id, ad) for ad in ads]
    return client.call("ads", "add", {"Ads": payload})


def list_ads(client, group_id):
    res = client.call("ads", "get", {
        "SelectionCriteria": {"AdGroupIds": [int(group_id)]},
        "FieldNames": ["Id", "State", "Status", "StatusClarification"],
        "TextAdFieldNames": ["Title", "Title2", "Text", "Href"],
    })
    return res.get("Ads", [])


def moderate(client, group_id):
    ads = list_ads(client, group_id)
    draft_ids = [a["Id"] for a in ads if a.get("Status") == "DRAFT"]
    if not draft_ids:
        return {"info": "no DRAFT ads to submit"}
    return client.call("ads", "moderate", {"SelectionCriteria": {"Ids": draft_ids}})


def main(argv):
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    v = sub.add_parser("validate")
    v.add_argument("--file", required=True)

    c = sub.add_parser("create")
    c.add_argument("--group", required=True)
    c.add_argument("--file", required=True)

    l = sub.add_parser("list")
    l.add_argument("--group", required=True)

    m = sub.add_parser("moderate")
    m.add_argument("--group", required=True)

    args = p.parse_args(argv)

    if args.cmd == "validate":
        _, errs = validate_file(args.file)
        if errs:
            print("INVALID:")
            for e in errs:
                print("  -", e)
            return 1
        print("OK: all ads within Direct limits")
        return 0

    client = DirectClient(load_config())

    if args.cmd == "create":
        ads, errs = validate_file(args.file)
        if errs:
            print("Refusing to upload — fix these first:", file=sys.stderr)
            for e in errs:
                print("  -", e, file=sys.stderr)
            return 1
        print(json.dumps(create_ads(client, args.group, ads), ensure_ascii=False, indent=2))
        return 0
    if args.cmd == "list":
        for a in list_ads(client, args.group):
            ta = a.get("TextAd", {})
            print(f"{a['Id']:>10} {a.get('Status'):<10} {ta.get('Title','')}")
        return 0
    if args.cmd == "moderate":
        print(json.dumps(moderate(client, args.group), ensure_ascii=False, indent=2))
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
