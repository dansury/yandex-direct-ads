#!/usr/bin/env python3
"""Account audit — 65 checks (YD01-YD65), weighted score 0-100, grade A-F.

Merges the general Yandex.Direct account checklist (YD01-YD55, structure /
keywords / ads / settings) with the "7 hidden settings that burn budget"
checklist distilled from practitioner articles (YD56-YD65, see
references/budget-leak-checklist.md and references/unit-economics.md).
The budget-leak checks are printed first — they are the highest-value,
fastest-to-fix items — then the full weighted report follows.

Some checks need data this skill does not fetch (Metrica goal wiring,
RSY placement reports, ad relevance judged by a human) — those come back
N/A with a note on how to check manually. N/A is excluded from scoring,
never silently counted as a pass.

Examples:
    python audit.py                          # whole account, last 14 days
    python audit.py --campaign 111 --days 30
    python audit.py --format json
"""

import argparse
import json
import re
import sys
from collections import defaultdict

from direct_api import DirectClient, load_config
from reports import run_report

SEVERITY_WEIGHT = {"Critical": 5.0, "High": 3.0, "Medium": 1.5, "Low": 0.5}
STATUS_SCORE = {"PASS": 1.0, "WARNING": 0.5, "FAIL": 0.0}  # N/A excluded

CATEGORY_WEIGHT = {
    "Конверсии и Метрика": 0.25,
    "Слив бюджета / минус-слова": 0.20,
    "Структура аккаунта": 0.15,
    "Ключевые слова и качество": 0.15,
    "Объявления и расширения": 0.15,
    "Настройки и таргетинг": 0.10,
}

GOAL_STRATEGIES = {"AVERAGE_CPA", "AVERAGE_ROI", "AVERAGE_CRR", "CPA_OPTIMIZATION",
                    "PAY_FOR_CONVERSION"}
CLICK_ONLY_STRATEGIES = {"WB_MAXIMUM_CLICKS", "HIGHEST_POSITION", "AVERAGE_CLICK_COST"}


class Check:
    __slots__ = ("id", "name", "category", "severity", "status", "detail")

    def __init__(self, id, name, category, severity, status, detail):
        self.id, self.name, self.category = id, name, category
        self.severity, self.status, self.detail = severity, status, detail

    def as_dict(self):
        return {"id": self.id, "name": self.name, "category": self.category,
                "severity": self.severity, "status": self.status, "detail": self.detail}


# ---------------------------------------------------------------------------
# Data fetch
# ---------------------------------------------------------------------------
def fetch_account(client, campaign_id=None):
    sel = {"Ids": [int(campaign_id)]} if campaign_id else {}
    campaigns = client.call("campaigns", "get", {
        "SelectionCriteria": sel,
        "FieldNames": ["Id", "Name", "Status", "State", "Type", "DailyBudget",
                       "NegativeKeywords", "NegativeKeywordSharedSetIds",
                       "TimeTargeting", "ExcludedSites"],
        "TextCampaignFieldNames": ["BiddingStrategy", "Settings", "CounterIds",
                                    "RelevantKeywords", "PriorityGoals"],
    }).get("Campaigns", [])

    cids = [c["Id"] for c in campaigns]
    if not cids:
        return campaigns, [], [], [], [], [], []

    adgroups = client.call("adgroups", "get", {
        "SelectionCriteria": {"CampaignIds": cids},
        "FieldNames": ["Id", "Name", "CampaignId", "RegionIds", "Status",
                       "NegativeKeywords"],
    }).get("AdGroups", [])

    keywords = client.call("keywords", "get", {
        "SelectionCriteria": {"CampaignIds": cids},
        "FieldNames": ["Id", "Keyword", "AdGroupId", "CampaignId", "Status", "State"],
    }).get("Keywords", [])

    ads = client.call("ads", "get", {
        "SelectionCriteria": {"CampaignIds": cids},
        "FieldNames": ["Id", "AdGroupId", "CampaignId", "Status", "State", "Type"],
        "TextAdFieldNames": ["Title", "Title2", "Text", "Href", "Mobile",
                             "SitelinkSetId", "AdExtensionIds", "VCardId",
                             "DisplayUrlPath"],
    }).get("Ads", [])

    negative_sets = client.call("negativekeywordsharedsets", "get", {
        "SelectionCriteria": {},
        "FieldNames": ["Id", "Name", "NegativeKeywords"],
    }).get("NegativeKeywordSharedSets", [])

    bidmodifiers = client.call("bidmodifiers", "get", {
        "SelectionCriteria": {"CampaignIds": cids},
        "FieldNames": ["Id", "CampaignId", "AdGroupId", "Type", "Level"],
        "MobileAdjustmentFieldNames": ["BidModifier", "OperatingSystemType"],
        "DemographicsAdjustmentFieldNames": ["BidModifier", "Age", "Gender"],
    }).get("BidModifiers", [])

    return campaigns, adgroups, keywords, ads, negative_sets, bidmodifiers, cids


def fetch_campaign_perf(client, cids, days):
    if not cids:
        return {}
    rows = run_report(
        client, report_type="CAMPAIGN_PERFORMANCE_REPORT",
        fields=["CampaignId", "Impressions", "Clicks", "Ctr", "Cost", "Conversions",
               "CostPerConversion"],
        days=days,
    )
    by_id = {}
    for r in rows:
        cid = r.get("CampaignId")
        if cid:
            by_id[int(cid)] = r
    return by_id


def fetch_keyword_perf(client, campaign_id, days):
    """Only pulled when a single --campaign is given (report units are not free)."""
    if not campaign_id:
        return None
    rows = run_report(
        client, report_type="CRITERIA_PERFORMANCE_REPORT",
        fields=["CampaignId", "AdGroupId", "CriteriaId", "Criteria", "Impressions",
               "Clicks", "Ctr", "Cost", "Conversions"],
        days=days, campaign_id=campaign_id,
    )
    return rows


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _f(row, key, default=0.0):
    v = row.get(key)
    if v in (None, "", "--"):
        return default
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _network_active(campaign):
    """True if this TEXT_CAMPAIGN still serves YAN/RSY traffic alongside Search."""
    strat = (campaign.get("TextCampaign") or {}).get("BiddingStrategy") or {}
    network = strat.get("Network") or {}
    return network.get("BiddingStrategyType") not in (None, "SERVING_OFF")


def _search_strategy_type(campaign):
    strat = (campaign.get("TextCampaign") or {}).get("BiddingStrategy") or {}
    return (strat.get("Search") or {}).get("BiddingStrategyType")


def _setting(campaign, needle):
    """Find a Settings[{Option,Value}] entry whose Option contains `needle`."""
    for s in (campaign.get("TextCampaign") or {}).get("Settings", []) or []:
        if needle in (s.get("Option") or ""):
            return s.get("Value")
    return None


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------
def run_checks(campaigns, adgroups, keywords, ads, negative_sets, bidmodifiers,
                perf_by_campaign, keyword_perf, cfg):
    checks = []
    C = "Конверсии и Метрика"
    B = "Слив бюджета / минус-слова"
    S = "Структура аккаунта"
    K = "Ключевые слова и качество"
    A = "Объявления и расширения"
    T = "Настройки и таргетинг"

    def add(id, name, category, severity, status, detail):
        checks.append(Check(id, name, category, severity, status, detail))

    active_text = [c for c in campaigns if c.get("Type") == "TEXT_CAMPAIGN"]
    rules = cfg.get("optimization_rules", {})
    audit_rules = cfg.get("audit_rules", {})
    kpi = cfg.get("kpi", {})
    target_cpa = kpi.get("target_cpa") or 0
    primary_goal = (cfg.get("metrica") or {}).get("primary_goal_id")

    kw_by_group = defaultdict(list)
    for kw in keywords:
        kw_by_group[kw.get("AdGroupId")].append(kw)
    ads_by_group = defaultdict(list)
    for ad in ads:
        ads_by_group[ad.get("AdGroupId")].append(ad)

    # -- Category 1: Конверсии и Метрика --------------------------------
    no_counter = [c["Name"] for c in active_text
                 if not (c.get("TextCampaign") or {}).get("CounterIds")]
    add("YD01", "Метрика привязана", C, "Critical",
        "PASS" if not no_counter else "FAIL",
        "все кампании привязаны" if not no_counter
        else f"без счётчика: {', '.join(no_counter[:5])}")

    no_goals = [c["Name"] for c in active_text
               if not (c.get("TextCampaign") or {}).get("PriorityGoals")]
    add("YD02", "Конверсионные цели настроены", C, "Critical",
        "WARNING" if no_goals else "PASS",
        "PriorityGoals не заданы через API — проверьте цели в Метрике вручную "
        f"для: {', '.join(no_goals[:5])}" if no_goals else "цели заданы")

    # YD03-YD09 need the Metrica Reporting API, which this account-data pull
    # does not call — see references/setup-guide.md; report them as N/A.
    add("YD03-YD09", "E-commerce/атрибуция/офлайн-конверсии/CRM", C, "Medium", "N/A",
        "нужен Metrica Reporting API — прогоните scripts/metrica.py и сверьте руками")

    goal_ok, goal_bad = [], []
    for c in active_text:
        strat_type = _search_strategy_type(c)
        if strat_type in CLICK_ONLY_STRATEGIES:
            goal_bad.append((c["Name"], "стратегия по кликам, без цели"))
        elif strat_type in GOAL_STRATEGIES and not primary_goal:
            goal_bad.append((c["Name"], "цель конверсии не сверена с config.metrica"))
        else:
            goal_ok.append(c["Name"])
    add("YD62", "Цель ≠ «Визит»", C, "Critical",
        "PASS" if not goal_bad else "FAIL",
        "оптимизация идёт по целевому действию" if not goal_bad
        else "; ".join(f"{n}: {w}" for n, w in goal_bad[:5]))

    # -- Category 2: Слив бюджета / минус-слова --------------------------
    no_neg = [c["Name"] for c in active_text if not c.get("NegativeKeywords")]
    add("YD10", "Минус-слова на уровне кампании", B, "Critical",
        "PASS" if not no_neg else "FAIL",
        "заданы у всех" if not no_neg else f"пусто у: {', '.join(no_neg[:5])}")

    add("YD11", "Общие наборы минус-слов", B, "High",
        "PASS" if negative_sets else "WARNING",
        f"{len(negative_sets)} общих наборов" if negative_sets
        else "нет общих наборов — заведите через negatives.py create")

    add("YD12", "Регулярный анализ поисковых запросов", B, "Critical", "N/A",
        "нельзя проверить через API — запускайте keywords.py mine-queries "
        "не реже раза в неделю (references/optimization-playbook.md)")

    seen, dup = defaultdict(set), set()
    for kw in keywords:
        norm = re.sub(r"[\"\+\!\[\]\(\)-]", "", (kw.get("Keyword") or "")).strip().lower()
        if not norm:
            continue
        camp = kw.get("CampaignId")
        if norm in seen[camp] and kw.get("AdGroupId") not in seen[camp]:
            dup.add(norm)
        seen[camp].add(norm)
    add("YD18", "Дубли ключевых слов между группами", K, "Medium",
        "PASS" if not dup else "WARNING",
        "дублей не найдено" if not dup else f"{len(dup)} повторяющихся фраз в разных группах")

    if keyword_perf is not None:
        min_spend = rules.get("min_spend_for_minus_word", 500)
        wasters = [r for r in keyword_perf
                  if _f(r, "Conversions") == 0 and _f(r, "Cost") >= min_spend]
        add("YD17", "Zero-conversion ключи (слив)", B, "High",
            "PASS" if not wasters else "FAIL",
            "нет ключей-сливов" if not wasters
            else f"{len(wasters)} ключей потратили ≥{min_spend} без конверсий — "
                 f"пауза через optimize.py")
    else:
        add("YD17", "Zero-conversion ключи (слив)", B, "High", "N/A",
            "передайте --campaign <id>, чтобы включить отчёт по ключам")

    add("YD13-YD16", "Кросс-минусовка / площадки РСЯ / бренд / инфозапросы", B,
        "High", "N/A", "нужен отчёт по площадкам и семантический разбор — см. use-cases.md")

    autotarget_flag = []
    for c in active_text:
        rk = (c.get("TextCampaign") or {}).get("RelevantKeywords")
        if rk is not None:
            autotarget_flag.append(c["Name"])
    add("YD57", "Автотаргетинг под контролем", B, "High",
        "WARNING" if autotarget_flag else "N/A",
        f"автотаргетинг активен: {', '.join(autotarget_flag[:5])} — сверьте с "
        "references/budget-leak-checklist.md п.2" if autotarget_flag
        else "API не вернул RelevantKeywords — проверьте чек-бокс «Автотаргетинг» "
             "в интерфейсе кампании вручную")

    stop_words_setting = [(_setting(c, "STOP_WORD") or _setting(c, "IGNORE")) for c in active_text]
    niche_neg_ok = [c for c in active_text if len(c.get("NegativeKeywords", [])) >= 5]
    add("YD61", "Минус-слова: гигиена + игнор стоп-слов", B, "Critical",
        "PASS" if len(niche_neg_ok) == len(active_text) and active_text else "WARNING",
        f"{len(niche_neg_ok)}/{len(active_text)} кампаний с ≥5 нишевых минус-слов; "
        "чек-бокс «Игнорировать стоп-слова» API не отдаёт — сверьте вручную")

    # -- Category 3: Структура аккаунта ----------------------------------
    mixed = [c["Name"] for c in active_text if _network_active(c)]
    add("YD19", "Разделение Поиск/РСЯ", S, "Critical",
        "PASS" if not mixed else "WARNING",
        "поиск и РСЯ разделены" if not mixed
        else f"РСЯ не выключена в: {', '.join(mixed[:5])}")

    add("YD59", "РСЯ не подмешана в «поисковую» кампанию", B, "Critical",
        "PASS" if not mixed else "FAIL",
        "Network.BiddingStrategyType=SERVING_OFF везде, где ожидается чистый поиск"
        if not mixed else
        f"единая стратегия тянет РСЯ в {len(mixed)} кампани(ях) — "
        "budget-leak-checklist.md п.4")

    bad_names = [c["Name"] for c in active_text
                if not re.search(r"[_\-|]", c["Name"])]
    add("YD21", "Именование кампаний (тип_гео_продукт)", S, "Medium",
        "PASS" if not bad_names else "WARNING",
        "эвристика по разделителям в названии — "
        f"без структуры: {', '.join(bad_names[:5])}" if bad_names else "ок")

    big_groups = []
    for gid, kws in kw_by_group.items():
        n = len(kws)
        mn = audit_rules.get("min_group_keywords", 1)
        mx = audit_rules.get("max_group_keywords", 15)
        if n > mx or n < mn:
            big_groups.append((gid, n))
    add("YD22", "Размер групп (1-15 ключей)", S, "Medium",
        "PASS" if not big_groups else "WARNING",
        "в пределах нормы" if not big_groups
        else f"{len(big_groups)} групп вне диапазона (напр. группа {big_groups[0][0]}: "
             f"{big_groups[0][1]} ключей)")

    zombies = [c["Name"] for c in campaigns
              if c.get("State") in ("OFF", "SUSPENDED") and c.get("Status") == "ACCEPTED"]
    add("YD23", "Нет кампаний-зомби", S, "Medium",
        "WARNING" if zombies else "PASS",
        f"давно остановлены, но не в архиве: {', '.join(zombies[:5])}" if zombies
        else "нет зомби-кампаний")

    mobile_mod_camps = {bm.get("CampaignId") for bm in bidmodifiers
                        if bm.get("Type") == "MOBILE_ADJUSTMENT"}
    no_mobile = [c["Name"] for c in active_text if c["Id"] not in mobile_mod_camps]
    add("YD24", "Мобильные корректировки настроены", S, "High",
        "PASS" if not no_mobile else "WARNING",
        "заданы везде" if not no_mobile
        else f"без корректировки на мобильные: {', '.join(no_mobile[:5])}")

    region_by_group = [set(g.get("RegionIds", [])) for g in adgroups]
    mixed_geo = [g["Name"] for g in adgroups
                if len(g.get("RegionIds", [])) > 1 and 225 in g.get("RegionIds", [])]
    add("YD25", "Гео разделено (МСК/СПб/остальные регионы)", S, "Medium",
        "WARNING" if mixed_geo else "PASS",
        f"«вся Россия» смешана с точечными регионами в {len(mixed_geo)} группах — "
        "разделите МСК/СПб/остальные отдельными кампаниями" if mixed_geo
        else "гео сегментировано")

    has_time_targeting = [c["Name"] for c in active_text if c.get("TimeTargeting")]
    add("YD26", "Временной таргетинг настроен", S, "Medium",
        "PASS" if has_time_targeting else "N/A",
        f"{len(has_time_targeting)}/{len(active_text)} с расписанием — "
        "ночью снижайте ставку, а не выключайте показ целиком (если заявки "
        "не теряют смысл ночью)")

    add("YD65", "Кампания не заброшена (ведётся регулярно)", S, "Medium", "N/A",
        "API не даёт журнал правок напрямую — используйте changes.checkCampaigns "
        "или сверяйте state.py log / changelog.jsonl за последние 14 дней")

    add("YD20", "Логичная структура по продуктам/гео", S, "High", "N/A", "требует ручной оценки")

    # -- Category 4: Ключевые слова и качество ---------------------------
    with_ops = sum(1 for kw in keywords
                  if re.search(r'["!+\[\]]', kw.get("Keyword") or ""))
    add("YD27", "Операторы соответствия используются", K, "High",
        "PASS" if keywords and with_ops / max(len(keywords), 1) > 0.1 else "WARNING",
        f"{with_ops}/{len(keywords)} ключей с операторами (\", !, +)")

    add("YD28-YD33", "Broad+автостратегия / релевантность / показатель качества",
        K, "Medium", "N/A", "нужны данные показателя качества и статус групп — сверьте в UI")

    if keyword_perf is not None:
        low_ctr = [r for r in keyword_perf if _f(r, "Impressions") > 100 and _f(r, "Ctr") < 1.0]
        add("YD34", "CTR ключей ≥ 1% (поиск, >100 показов)", K, "High",
            "PASS" if not low_ctr else "WARNING",
            "ок" if not low_ctr else f"{len(low_ctr)} ключей с CTR < 1%")
    else:
        add("YD34", "CTR ключей ≥ 1%", K, "High", "N/A",
            "передайте --campaign <id> для отчёта по ключам")

    # -- Category 5: Объявления и расширения ------------------------------
    not_accepted = [a["Id"] for a in ads if a.get("Status") != "ACCEPTED"]
    add("YD35", "Все объявления прошли модерацию", A, "Critical",
        "PASS" if not not_accepted else "WARNING",
        "все ACCEPTED" if not not_accepted else f"{len(not_accepted)} не в статусе ACCEPTED")

    few_ads_groups = [gid for gid, lst in ads_by_group.items() if len(lst) < 2]
    add("YD36", "≥2 объявления на группу", A, "High",
        "PASS" if not few_ads_groups else "WARNING",
        "ок" if not few_ads_groups else f"{len(few_ads_groups)} групп с < 2 объявлениями")

    no_sitelinks = [a["Id"] for a in ads if not (a.get("TextAd") or {}).get("SitelinkSetId")]
    add("YD39", "Быстрые ссылки добавлены", A, "High",
        "PASS" if len(no_sitelinks) < len(ads) * 0.2 else "WARNING",
        f"{len(ads) - len(no_sitelinks)}/{len(ads)} объявлений со sitelinks")

    no_callouts = [a["Id"] for a in ads if not (a.get("TextAd") or {}).get("AdExtensionIds")]
    add("YD40", "Уточнения (callouts) добавлены", A, "Medium",
        "PASS" if len(no_callouts) < len(ads) * 0.2 else "WARNING",
        f"{len(ads) - len(no_callouts)}/{len(ads)} объявлений с уточнениями")

    no_vcard = [a["Id"] for a in ads if not (a.get("TextAd") or {}).get("VCardId")]
    add("YD42", "Визитка/vCard заполнена", A, "Medium",
        "PASS" if len(no_vcard) < len(ads) * 0.2 else "WARNING",
        f"{len(ads) - len(no_vcard)}/{len(ads)} объявлений с визиткой")

    no_utm = [a["Id"] for a in ads
             if "utm_" not in ((a.get("TextAd") or {}).get("Href") or "")]
    add("YD43", "UTM-метки на всех ссылках", A, "High",
        "PASS" if not no_utm else "FAIL",
        "везде есть utm_" if not no_utm else f"{len(no_utm)} объявлений без utm_ в Href")

    add("YD37-YD46 (остальные)", "Заголовки/УТП/отображаемая ссылка/РСЯ-медиа/A-B",
        A, "Medium", "N/A", "текстовая релевантность и медиа — сверьте руками "
        "или abtest.py для A/B")

    # -- Category 6: Настройки и таргетинг --------------------------------
    no_region = [g["Name"] for g in adgroups if not g.get("RegionIds")]
    add("YD47", "Гео-таргетинг задан", T, "Critical",
        "PASS" if not no_region else "FAIL",
        "везде задан" if not no_region else f"без региона: {', '.join(no_region[:5])}")

    strat_bad = [c["Name"] for c in active_text
                if _search_strategy_type(c) in CLICK_ONLY_STRATEGIES]
    add("YD56", "Стратегия ≠ «Максимум кликов»", T, "Critical",
        "PASS" if not strat_bad else "FAIL",
        "все на конверсионных стратегиях" if not strat_bad
        else f"на «максимум кликов»: {', '.join(strat_bad[:5])} — "
             "budget-leak-checklist.md п.1")

    add("YD49", "Стратегия соответствует целям", T, "High",
        "PASS" if not strat_bad and not goal_bad else "WARNING",
        "см. YD56/YD62 выше")

    low_budget = [c["Name"] for c in active_text
                 if _f(c.get("DailyBudget") or {}, "Amount") == 0]
    add("YD50", "Дневной бюджет задан", T, "High",
        "PASS" if not low_budget else "FAIL",
        "у всех задан" if not low_budget else f"без бюджета: {', '.join(low_budget[:5])}")

    mult = audit_rules.get("weekly_budget_multiplier", 10)
    starved = []
    for c in active_text:
        daily = _f(c.get("DailyBudget") or {}, "Amount") / 1_000_000
        if target_cpa and daily * 7 < target_cpa * mult:
            starved.append((c["Name"], daily))
    add("YD63", f"Недельный бюджет ≥ {mult}× target_cpa (обучение алгоритма)", T, "High",
        "PASS" if not starved else "FAIL",
        "бюджета хватает на обучение" if not starved
        else "; ".join(f"{n}: {d:.0f}₽/день" for n, d in starved[:5]) +
             f" — нужно ≥{target_cpa * mult / 7:.0f}₽/день "
             "(references/unit-economics.md)")

    demo_mod = {bm.get("CampaignId") for bm in bidmodifiers
               if bm.get("Type") == "DEMOGRAPHICS_ADJUSTMENT"}
    add("YD58", "Демография не «все подряд»", T, "Medium",
        "WARNING" if not demo_mod else "PASS",
        "нет корректировок по полу/возрасту — исключите неизвестный "
        "пол/возраст или сузьте сегмент" if not demo_mod
        else f"{len(demo_mod)} кампани(й) с демографическими корректировками")

    high_ticket = audit_rules.get("high_ticket_threshold", 5000)
    aov = kpi.get("avg_order_value") or 0
    mobile_reduced = {bm.get("CampaignId") for bm in bidmodifiers
                      if bm.get("Type") == "MOBILE_ADJUSTMENT"
                      and _f((bm.get("MobileAdjustment") or {}), "BidModifier", 100) < 100}
    if aov >= high_ticket:
        missing_device = [c["Name"] for c in active_text if c["Id"] not in mobile_reduced]
        add("YD60", f"Устройства исключены осознанно (чек ≥{high_ticket}₽/B2B)", T, "High",
            "PASS" if not missing_device else "FAIL",
            "мобильный трафик прибит корректировкой" if not missing_device
            else f"смартфоны не ограничены при высоком чеке: "
                 f"{', '.join(missing_device[:5])} — budget-leak-checklist.md п.5")
    else:
        add("YD60", "Устройства исключены осознанно", T, "High", "N/A",
            "kpi.avg_order_value не задан или ниже порога — правило не применимо")

    add("YD51", "Ограничения стратегии заданы", T, "Medium", "N/A",
        "требует разбора конкретного типа стратегии — сверьте BidCeiling/"
        "WeeklySpendLimit/AverageCpa вручную по каждой кампании")

    add("YD52", "Корректировки ставок настроены (общие)", T, "Medium",
        "PASS" if bidmodifiers else "WARNING",
        f"{len(bidmodifiers)} корректировок" if bidmodifiers else "корректировок нет")

    site_cvr = kpi.get("site_cvr") or 0
    margin = kpi.get("margin") or 0
    max_bid_cfg = rules.get("max_bid") or 0
    if aov and margin and site_cvr:
        max_cpc = aov * margin * (site_cvr / 100.0)
        add("YD64", "Предельная ставка ≤ юнит-экономике", T, "High",
            "PASS" if max_bid_cfg <= max_cpc * 1.2 else "WARNING",
            f"расчётный предел {max_cpc:.0f}₽, optimization_rules.max_bid={max_bid_cfg:.0f}₽ "
            "(references/unit-economics.md)")
    else:
        add("YD64", "Предельная ставка ≤ юнит-экономике", T, "High", "N/A",
            "заполните kpi.avg_order_value, kpi.margin, kpi.site_cvr в config.json")

    add("YD48/YD53-YD55", "Расширенный гео / площадки РСЯ / релевантные фразы",
        T, "Medium", "N/A", "нужны отчёты по площадкам — sync с references/api/reports.md")

    return checks


# ---------------------------------------------------------------------------
# Scoring & report
# ---------------------------------------------------------------------------
def score(checks):
    num, den = 0.0, 0.0
    for c in checks:
        if c.status == "N/A":
            continue
        w = SEVERITY_WEIGHT.get(c.severity, 1.0) * CATEGORY_WEIGHT.get(c.category, 0.1)
        num += STATUS_SCORE.get(c.status, 0.0) * w
        den += w
    return round(num / den * 100, 1) if den else None


def grade(pts):
    if pts is None:
        return "?"
    if pts >= 90:
        return "A"
    if pts >= 75:
        return "B"
    if pts >= 60:
        return "C"
    if pts >= 40:
        return "D"
    return "F"


def print_report(checks, pts, days):
    budget_leak_ids = {"YD56", "YD57", "YD58", "YD59", "YD60", "YD61", "YD62", "YD63", "YD64"}
    leaks = [c for c in checks if c.id in budget_leak_ids]

    print("=" * 78)
    print(f"АУДИТ ЯНДЕКС ДИРЕКТ — окно {days} дней")
    print("=" * 78)

    print("\n## 7 скрытых настроек, которые сливают бюджет (см. статьи.md)\n")
    for c in sorted(leaks, key=lambda c: -SEVERITY_WEIGHT.get(c.severity, 0)):
        mark = {"PASS": "OK ", "WARNING": "!! ", "FAIL": "XX ", "N/A": "-- "}[c.status]
        print(f"  [{mark}] {c.id:<8} {c.name}")
        print(f"           {c.detail}")

    by_cat = defaultdict(list)
    for c in checks:
        by_cat[c.category].append(c)

    print("\n## Результаты по категориям\n")
    for cat, w in CATEGORY_WEIGHT.items():
        items = by_cat.get(cat, [])
        print(f"### {cat} ({int(w * 100)}%)")
        for c in items:
            mark = {"PASS": "OK ", "WARNING": "!! ", "FAIL": "XX ", "N/A": "-- "}[c.status]
            print(f"  [{mark}] {c.id:<16} {c.name}")
            print(f"           {c.detail}")
        print()

    fails = [c for c in checks if c.status == "FAIL"]
    warns = [c for c in checks if c.status == "WARNING"]
    print(f"Итог: {pts if pts is not None else 'н/д'}/100  Грейд: {grade(pts)}")
    print(f"FAIL: {len(fails)}  WARNING: {len(warns)}  "
          f"N/A: {len([c for c in checks if c.status == 'N/A'])}")

    quick = sorted(
        [c for c in fails if c.severity in ("Critical", "High")],
        key=lambda c: -SEVERITY_WEIGHT.get(c.severity, 0),
    )
    if quick:
        print("\n## Quick wins (Critical/High, чинится быстро)\n")
        for c in quick[:10]:
            print(f"  - {c.id} {c.name}: {c.detail}")


def main(argv):
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--campaign", help="только эта кампания (иначе весь аккаунт)")
    p.add_argument("--days", type=int, default=14)
    p.add_argument("--format", choices=["text", "json"], default="text")
    args = p.parse_args(argv)

    cfg = load_config()
    client = DirectClient(cfg)

    campaigns, adgroups, keywords, ads, negative_sets, bidmodifiers, cids = \
        fetch_account(client, args.campaign)
    if not campaigns:
        print("Кампаний не найдено (или нет доступа).")
        return 1

    perf = fetch_campaign_perf(client, cids, args.days)
    kw_perf = fetch_keyword_perf(client, args.campaign, args.days) if args.campaign else None

    checks = run_checks(campaigns, adgroups, keywords, ads, negative_sets,
                        bidmodifiers, perf, kw_perf, cfg)
    pts = score(checks)

    if args.format == "json":
        print(json.dumps({
            "score": pts, "grade": grade(pts),
            "checks": [c.as_dict() for c in checks],
        }, ensure_ascii=False, indent=2))
    else:
        print_report(checks, pts, args.days)
    return 0 if pts is None or pts >= 40 else 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
