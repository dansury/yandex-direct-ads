#!/usr/bin/env python3
"""Blueprint orchestrator — build a whole campaign from one JSON, idempotently.

Reads a blueprint (see assets/campaign_blueprint.example.json), then creates
campaign -> ad groups -> keywords -> ads in order. Uses state.json so a re-run
does NOT duplicate anything already created. Validates ad copy before upload.
Honors the cost guard in production.

Examples:
    python build.py --blueprint campaign.json              # dry-run (default)
    python build.py --blueprint campaign.json --apply

Dry-run prints the plan and what already exists vs what would be created.
"""

import argparse
import json
import sys

import ads as ads_mod
from direct_api import (CostGuardError, DirectClient, collect_add_results,
                        cost_guard, load_config)
from state import State


def _campaign_key(bp):
    return bp["campaign"]["name"]


def _report(label, ids, errors, warnings):
    ok = [i for i in ids if i is not None]
    print(f"  {label}: created {len(ok)}", end="")
    if warnings:
        print(f", warnings {len(warnings)}", end="")
    if errors:
        print(f", ERRORS {len(errors)}")
        for e in errors:
            print(f"     ! item[{e['index']}] {e['code']}: {e['message']} {e['details']}")
    else:
        print()
    return ok


def build(client, cfg, bp, apply, st):
    sandbox = cfg.get("sandbox", True)
    ck = _campaign_key(bp)

    # cost guard preflight (production only)
    projected = bp["campaign"].get("daily_budget", 0)
    try:
        cost_guard(cfg, projected)
    except CostGuardError as e:
        print(str(e), file=sys.stderr)
        return 2

    # 1) campaign --------------------------------------------------------
    camp_id = st.get("campaign", ck)
    if camp_id:
        print(f"campaign exists: {ck} -> {camp_id}")
    elif not apply:
        print(f"[dry] would CREATE campaign: {ck} (budget {projected})")
        camp_id = "<new>"
    else:
        import campaigns as camp_mod
        ns = argparse.Namespace(
            name=ck, daily=projected,
            strategy=bp["campaign"].get("strategy", "manual"),
            goal=bp["campaign"].get("goal_id"), cpa=bp["campaign"].get("target_cpa"),
            roi=bp["campaign"].get("target_roi"), start=bp["campaign"].get("start"))
        res = camp_mod.create_campaign(client, ns)
        ids, errs, warns = collect_add_results(res)
        ok = _report("campaign", ids, errs, warns)
        if not ok:
            return 1
        camp_id = ok[0]
        st.put("campaign", ck, camp_id)
        st.log("create_campaign", {"name": ck}, {"ids": [camp_id]}, sandbox)

    # 2) ad groups + keywords + ads -------------------------------------
    for g in bp.get("adgroups", []):
        gk = f"{ck}/{g['name']}"
        grp_id = st.get("group", gk)
        if grp_id:
            print(f"  group exists: {g['name']} -> {grp_id}")
        elif not apply:
            print(f"  [dry] would CREATE group: {g['name']} "
                  f"({len(g.get('keywords', []))} kw, {len(g.get('ads', []))} ads)")
            grp_id = "<new>"
        else:
            import adgroups as ag_mod
            res = ag_mod.create_group(client, camp_id, g["name"],
                                      g.get("region_ids", cfg["defaults"]["region_ids"]))
            ids, errs, warns = collect_add_results(res)
            ok = _report(f"group {g['name']}", ids, errs, warns)
            if not ok:
                continue
            grp_id = ok[0]
            st.put("group", gk, grp_id)
            st.log("create_group", {"name": g["name"]}, {"ids": [grp_id]}, sandbox)

        # keywords
        kws = g.get("keywords", [])
        if kws and apply and grp_id not in (None, "<new>"):
            import adgroups as ag_mod
            for chunk in ag_mod.add_keywords(client, grp_id, kws):
                ids, errs, warns = collect_add_results(chunk)
                _report("    keywords", ids, errs, warns)
        if g.get("minus_words") and apply and grp_id not in (None, "<new>"):
            import adgroups as ag_mod
            ag_mod.set_minus_words(client, grp_id, g["minus_words"])

        # ads (validate first, always)
        g_ads = g.get("ads", [])
        if g_ads:
            errs = []
            for i, a in enumerate(g_ads):
                errs.extend(ads_mod.validate_ad(a, i))
            if errs:
                print(f"  ad copy INVALID in group {g['name']}:")
                for e in errs:
                    print("     -", e)
            elif apply and grp_id not in (None, "<new>"):
                _camp_for_utm = camp_id
                res = ads_mod.create_ads(client, grp_id, g_ads)
                ids, e2, w2 = collect_add_results(res)
                ok = _report("    ads", ids, e2, w2)
                st.log("create_ads", {"group": g["name"], "n": len(g_ads)},
                       {"ids": ok}, sandbox)
            else:
                print(f"    [dry] {len(g_ads)} ads valid, would upload")

    return 0


def main(argv):
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--blueprint", required=True)
    p.add_argument("--apply", action="store_true")
    args = p.parse_args(argv)

    with open(args.blueprint, "r", encoding="utf-8") as fh:
        bp = json.load(fh)

    cfg = load_config()
    client = DirectClient(cfg)
    st = State()
    mode = "APPLY" if args.apply else "DRY-RUN"
    env = "SANDBOX" if cfg.get("sandbox", True) else "PRODUCTION"
    print(f"[{mode} / {env}] building blueprint: {bp['campaign']['name']}\n")
    rc = build(client, cfg, bp, args.apply, st)
    if not args.apply:
        print("\n(dry-run — nothing created; add --apply to build)")
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
