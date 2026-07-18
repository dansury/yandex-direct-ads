#!/usr/bin/env python3
"""Offline unit tests — no token, no network. Run: python -m unittest -v

    cd yandex-direct/scripts && python -m unittest discover -s ../tests -v
"""
import os
import sys
import tempfile
import unittest
import json

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import ads as ads_mod
import audit as audit_mod
import creative_provider as cp
import creative_to_image_manifest as ctim
import optimize as opt
from direct_api import (CostGuardError, collect_add_results, cost_guard)
from state import State

RULES = {
    "bid_step_pct": 15, "min_bid": 30, "max_bid": 5000,
    "cpa_pause_multiplier": 1.5, "cpa_lower_bid_multiplier": 1.3,
    "cpa_raise_bid_multiplier": 0.7, "min_clicks_before_pause": 20,
    "min_spend_for_minus_word": 500,
}


class TestAdValidation(unittest.TestCase):
    def test_good_ad_passes(self):
        ad = {"title": "Купить диван", "text": "Каталог 500+ моделей. Скидки 30%."}
        self.assertEqual(ads_mod.validate_ad(ad, 0), [])

    def test_title_too_long(self):
        ad = {"title": "x" * 57, "text": "ok"}
        errs = ads_mod.validate_ad(ad, 0)
        self.assertTrue(any("title" in e for e in errs))

    def test_double_exclamation(self):
        ad = {"title": "Срочно!!", "text": "ok"}
        self.assertTrue(any("!" in e for e in ads_mod.validate_ad(ad, 0)))

    def test_missing_required(self):
        self.assertTrue(any("title" in e for e in ads_mod.validate_ad({"text": "x"}, 0)))


class TestOptimizeEngine(unittest.TestCase):
    def _row(self, **kw):
        base = {"CriteriaId": "1", "Criteria": "k", "Clicks": "0",
                "Cost": "0", "Conversions": "0", "AvgCpc": "50"}
        base.update({k: str(v) for k, v in kw.items()})
        return base

    def test_waste_paused(self):
        rows = [self._row(CriteriaId="1", Clicks=40, Cost=800, Conversions=0)]
        acts = opt.decide(rows, RULES, target_cpa=1500)
        self.assertEqual(acts[0]["action"], "pause")

    def test_expensive_cpa_paused(self):
        rows = [self._row(CriteriaId="2", Clicks=50, Cost=6000, Conversions=2, AvgCpc=120)]
        acts = opt.decide(rows, RULES, target_cpa=1500)  # CPA 3000 > 1.5x
        self.assertEqual(acts[0]["action"], "pause")

    def test_cheap_cpa_raises_bid(self):
        rows = [self._row(CriteriaId="4", Clicks=60, Cost=900, Conversions=3, AvgCpc=50)]
        acts = opt.decide(rows, RULES, target_cpa=1500)  # CPA 300 < 0.7x
        self.assertEqual(acts[0]["action"], "bid")
        self.assertGreater(acts[0]["bid"], 50)

    def test_bid_clamped_to_max(self):
        rows = [self._row(CriteriaId="5", Clicks=60, Cost=10, Conversions=3, AvgCpc=9000)]
        acts = opt.decide(rows, RULES, target_cpa=1500)
        self.assertLessEqual(acts[0]["bid"], RULES["max_bid"])


class TestBatchResults(unittest.TestCase):
    def test_mixed_results(self):
        resp = {"AddResults": [
            {"Id": 101},
            {"Errors": [{"Code": 5005, "Message": "bad", "Details": "x"}]},
            {"Id": 103, "Warnings": [{"Code": 1, "Message": "w"}]},
        ]}
        ids, errors, warnings = collect_add_results(resp)
        self.assertEqual(ids, [101, None, 103])
        self.assertEqual(len(errors), 1)
        self.assertEqual(len(warnings), 1)


class TestCostGuard(unittest.TestCase):
    def test_sandbox_never_blocks(self):
        cost_guard({"sandbox": True, "kpi": {"max_daily_total": 100}}, 999999)

    def test_production_blocks_over_cap(self):
        with self.assertRaises(CostGuardError):
            cost_guard({"sandbox": False, "kpi": {"max_daily_total": 1000}}, 5000)

    def test_production_allows_under_cap(self):
        cost_guard({"sandbox": False, "kpi": {"max_daily_total": 1000}}, 500)


class TestState(unittest.TestCase):
    def test_idempotent_mapping(self):
        d = tempfile.mkdtemp()
        st = State(path=os.path.join(d, "s.json"),
                   changelog=os.path.join(d, "c.jsonl"))
        self.assertIsNone(st.get("campaign", "X"))
        st.put("campaign", "X", 42)
        st.put("campaign", "X", 42)  # re-run: same key, no dup
        st2 = State(path=os.path.join(d, "s.json"),
                    changelog=os.path.join(d, "c.jsonl"))
        self.assertEqual(st2.get("campaign", "X"), 42)

    def test_changelog_appends(self):
        d = tempfile.mkdtemp()
        st = State(path=os.path.join(d, "s.json"),
                   changelog=os.path.join(d, "c.jsonl"))
        st.log("create_campaign", {"name": "X"}, {"ids": [1]})
        self.assertEqual(len(st.history()), 1)


class TestCreativeProvider(unittest.TestCase):
    def test_build_prompt_contains_offer_and_type(self):
        prompt = cp.build_prompt(
            {"offer": "Диваны от производителя", "cta": "Оставьте заявку"},
            "banner",
            index=2,
        )
        self.assertIn("Диваны от производителя", prompt)
        self.assertIn("banner", prompt)
        self.assertIn("Variant #2", prompt)

    def test_mcp_manifest_written(self):
        d = tempfile.mkdtemp()
        cfg = {
            "creative_provider": {
                "mode": "mcp",
                "service_name": "creative-mcp",
                "mcp_tool": "generate_banner"
            }
        }
        brief = {"offer": "Цветы", "asset_types": ["banner"], "variants": 2}
        result = cp.generate_assets(cfg, brief, d)
        self.assertEqual(result["mode"], "mcp")
        self.assertTrue(os.path.isfile(result["manifest_path"]))
        with open(result["manifest_path"], "r", encoding="utf-8") as fh:
            manifest = json.load(fh)
        self.assertEqual(len(manifest["requests"]), 2)


class TestCreativeManifestConversion(unittest.TestCase):
    def test_from_uploads(self):
        rows = [{"image_hashes": ["hash1", None, "hash2"]}]
        out = ctim.from_uploads(rows, "https://example.com", "Title", "Text")
        self.assertEqual(len(out), 2)
        self.assertEqual(out[0]["image_hash"], "hash1")

    def test_from_generated(self):
        result = {
            "assets": [
                {"image_hash": "h1", "title": "A", "text": "B", "href": "https://x"},
                {"path": "/tmp/nohash.png"},
            ]
        }
        out = ctim.from_generated(result, "https://fallback", "T", "X")
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["title"], "A")


class TestAudit(unittest.TestCase):
    CFG = {
        "kpi": {"target_cpa": 1000, "avg_order_value": 20000, "margin": 0.3, "site_cvr": 2},
        "metrica": {"primary_goal_id": 555},
        "optimization_rules": {"min_spend_for_minus_word": 500, "max_bid": 300},
        "audit_rules": {"weekly_budget_multiplier": 10, "high_ticket_threshold": 5000,
                        "min_group_keywords": 1, "max_group_keywords": 15},
    }

    def _campaign(self, **overrides):
        c = {
            "Id": 1, "Name": "search_msk_divany", "Status": "ACCEPTED", "State": "ON",
            "Type": "TEXT_CAMPAIGN", "DailyBudget": {"Amount": 2_000_000},
            "NegativeKeywords": ["бесплатно", "скачать", "вакансии", "форум", "б/у"],
            "TextCampaign": {
                "BiddingStrategy": {
                    "Search": {"BiddingStrategyType": "AVERAGE_CPA",
                              "AverageCpa": {"GoalId": 555}},
                    "Network": {"BiddingStrategyType": "SERVING_OFF"},
                },
                "CounterIds": [12345],
                "PriorityGoals": [{"GoalId": 555}],
            },
        }
        c.update(overrides)
        return c

    def test_healthy_campaign_scores_high(self):
        campaigns = [self._campaign()]
        adgroups = [{"Id": 10, "Name": "g1", "CampaignId": 1, "RegionIds": [213],
                    "Status": "ACCEPTED"}]
        keywords = [{"Id": 100, "Keyword": '"купить диван"', "AdGroupId": 10,
                    "CampaignId": 1, "Status": "ACCEPTED", "State": "ON"}]
        ads = [{"Id": 1000, "AdGroupId": 10, "CampaignId": 1, "Status": "ACCEPTED",
               "TextAd": {"Href": "https://x.ru/?utm_source=direct", "SitelinkSetId": 1,
                         "AdExtensionIds": [1], "VCardId": 1}}] * 2
        checks = audit_mod.run_checks(campaigns, adgroups, keywords, ads, [{"Id": 1}],
                                      [{"CampaignId": 1, "Type": "MOBILE_ADJUSTMENT",
                                        "MobileAdjustment": {"BidModifier": 50}}],
                                      {}, None, self.CFG)
        pts = audit_mod.score(checks)
        self.assertIsNotNone(pts)
        self.assertGreater(pts, 60)
        by_id = {c.id: c for c in checks}
        self.assertEqual(by_id["YD56"].status, "PASS")
        self.assertEqual(by_id["YD59"].status, "PASS")
        self.assertEqual(by_id["YD62"].status, "PASS")

    def test_max_clicks_strategy_fails_budget_leak_checks(self):
        campaigns = [self._campaign(TextCampaign={
            "BiddingStrategy": {
                "Search": {"BiddingStrategyType": "WB_MAXIMUM_CLICKS"},
                "Network": {"BiddingStrategyType": "NETWORK_DEFAULT"},
            },
            "CounterIds": [],
        }, NegativeKeywords=[])]
        checks = audit_mod.run_checks(campaigns, [], [], [], [], [], {}, None, self.CFG)
        by_id = {c.id: c for c in checks}
        self.assertEqual(by_id["YD56"].status, "FAIL")
        self.assertEqual(by_id["YD59"].status, "FAIL")
        self.assertEqual(by_id["YD01"].status, "FAIL")
        pts = audit_mod.score(checks)
        self.assertLess(pts, 60)

    def test_grade_boundaries(self):
        self.assertEqual(audit_mod.grade(95), "A")
        self.assertEqual(audit_mod.grade(80), "B")
        self.assertEqual(audit_mod.grade(65), "C")
        self.assertEqual(audit_mod.grade(45), "D")
        self.assertEqual(audit_mod.grade(10), "F")
        self.assertEqual(audit_mod.grade(None), "?")

    def test_underfunded_campaign_flags_yd63(self):
        campaigns = [self._campaign(DailyBudget={"Amount": 100_000})]  # 0.1 RUB/day
        checks = audit_mod.run_checks(campaigns, [], [], [], [], [], {}, None, self.CFG)
        by_id = {c.id: c for c in checks}
        self.assertTrue(by_id["YD63"].id.startswith("YD63"))
        self.assertEqual(by_id["YD63"].status, "FAIL")


if __name__ == "__main__":
    unittest.main(verbosity=2)
