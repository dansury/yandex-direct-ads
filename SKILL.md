---
name: yandex-direct
description: |
  Audit, run, and optimize Yandex.Direct (Яндекс Директ) ad campaigns end-to-end
  via Direct API v5 and Metrica. Audits an account against 65 checks (including
  the 7 hidden settings that burn budget by default: click-only strategy, wide
  autotargeting, unfiltered demographics, RSY mixed into Search, unrestricted
  devices, thin negative-keyword lists, and optimizing toward "visit" instead of
  a real goal). Also creates campaigns, ad groups, keywords and ads; generates ad
  copy (titles/texts/sitelinks); tracks leads; and optimizes bids/budgets toward
  a target CPA/ROI. Use when the user asks to audit, set up, manage, scale, or
  optimize Yandex.Direct advertising, find wasted ad spend / бюджет сливается,
  generate ad creatives/texts, lower CPA, or report on campaign performance.
  Triggers: "Яндекс Директ", "Yandex Direct", "директ", "РСЯ", "YAN", "директ
  аудит", "директ сливает бюджет", "директ кампании", "директ статистика",
  "директ оптимизация", "Direct API", "keyword bids".
---

# Yandex.Direct — audit-first campaign management

Manage a user's Yandex.Direct account: audit it against the mistakes that
actually burn budget, build campaigns, write the copy, track leads through
Metrica, and optimize spend toward a target cost-per-lead (CPA) and positive
ROI. Direct API v5 only, Python **stdlib** (no `pip install`).

This skill consolidates five previously separate community Yandex.Direct
skills into one, and its audit/optimization logic is configured against a set
of practitioner articles about where Yandex.Direct budgets actually leak —
distilled into `references/budget-leak-checklist.md` and
`references/unit-economics.md` as automated checks.

## First: mandatory onboarding
Before any API action, the agent must collect and confirm two access blocks:
1. **Yandex.Direct access**:
   - OAuth token for Direct/Metrica
   - sandbox vs production mode
   - Metrica counter id + primary goal id
2. **Creative production access** for photos/banners (only needed for
   creative generation workflows, not for audit/reporting):
   - provider type: `api` or `mcp`
   - provider/service name
   - auth token / API key / connector access details
   - base URL or MCP tool name if non-default
   - expected asset types: lifestyle photo, product visual, banner, RSYA image set

If any of these are missing for the workflow being requested, stop and ask the
user for them first. Do not invent credentials, do not silently skip the
creative provider, and do not start campaign creation before access is
clarified. An audit only needs block 1.

Opening intake should ask:
- "Пришлите OAuth-токен Яндекс.Директа/Метрики, режим sandbox/prod, counter_id и goal_id."
- (only for creative work) "Пришлите токен/ключ и API или MCP-сервис для генерации рекламных фото/баннеров."

## Then: check setup
Before any Direct API action, ensure `scripts/config.json` exists and the token works:
```bash
cd scripts && python direct_api.py ping
```
- If config is missing or token is invalid → walk the user through
  `references/setup-guide.md` (OAuth token, Metrica counter+goal, fill config).
- `sandbox: true` (default) is safe — no real money. Only switch to `false`
  after confirming with the user.

**Money is real in production.** Confirm with the user before: switching
`sandbox` to `false`, raising budgets/bids in production, or applying bulk
changes (`optimize.py --apply`) on a live account.

## Scripts (run from `scripts/`, Python stdlib only)
| Script | Purpose |
|--------|---------|
| `direct_api.py` | Core client. `ping`, raw `call`. Batch-error parsing + cost guard |
| `audit.py` | **Account audit**: 65 checks (YD01-YD65), weighted score 0-100, grade A-F. Leads with the 7 budget-leak checks from `references/budget-leak-checklist.md` |
| `build.py` | **Orchestrator**: whole campaign from one blueprint, idempotent, dry-run |
| `state.py` | Local id-map (no duplicate creates) + audit log (`changelog.jsonl`) |
| `campaigns.py` | create / list / suspend / resume / set-budget / strategy |
| `adgroups.py` | groups, keywords, minus-words |
| `ads.py` | validate + create text ads (char limits) + callouts |
| `extensions.py` | callouts (AdExtensions) |
| `keywords.py` | bids, **auction** bids, mine wasteful + winning queries |
| `research.py` | expand seeds → phrases, `hasSearchVolume` demand filter |
| `adjustments.py` | bid modifiers: mobile, demographics, dayparting |
| `negatives.py` | shared negative-keyword sets across campaigns |
| `dictionaries.py` | reference data: regions, currencies, constants |
| `image_ads.py` | РСЯ image ads: upload image, create image ad |
| `generate_creatives.py` | generate image/banner assets via external API or MCP manifest |
| `creative_provider.py` | provider bridge for creative generation (`api` / `mcp`) |
| `creative_to_image_manifest.py` | convert generated/uploaded creatives into bulk image-ad manifest |
| `reports.py` | performance stats (campaigns/adgroups/keywords/ads) |
| `metrica.py` | leads/conversions by goal |
| `crm_upload.py` | **offline conversions** (CRM sales → real ROI signal) |
| `optimize.py` | rules engine: bid/budget/pause/minus (dry-run default) |
| `abtest.py` | A/B creatives, z-test winner, pause losers |
| `anomalies.py` | spend-spike / conversion-drop / CPA-blowout alerts |
| `autopilot.py` | weekly loop: mine → optimize → anomalies → report |
| `rollback.py` | undo session creates (suspend) via audit log |

All scripts have `--help` and print JSON/TSV/text you can read back.
Write actions are **dry-run by default**; `--apply` enacts. State + audit make
re-runs safe (no duplicates) and reversible (`rollback.py`).

## Workflows

### A. Audit an account (do this first, always)
Whether the user asks to "set up" or "fix" or "audit" an account, run the
audit before touching settings — it tells you what's actually broken instead
of guessing.
1. `audit.py --campaign <id> --days 30` (omit `--campaign` for the whole
   account). Read the **budget-leak block first** (YD56-YD64) — it is
   ordered by severity and maps directly to
   `references/budget-leak-checklist.md`.
2. Cross-reference FAIL/WARNING items with `references/yandex-audit.md`
   (what the check means) and `references/benchmarks.md` (is this number
   actually bad for this niche).
3. Present a prioritized plan (Critical → High → Medium → Low), and only
   apply fixes the user confirms — this workflow reports, it does not write.
4. For a narrative report instead of raw check output, delegate to
   `agents/audit-yandex.md`.

Common quick fixes surfaced here (see `references/budget-leak-checklist.md`
for the full "why"):
- Strategy is "Maximum clicks" instead of a conversion goal (YD56).
- Autotargeting on Search is uncontrolled (YD57).
- RSY (YAN) is silently mixed into a "Search" campaign via a unified
  strategy that never turned `Network.BiddingStrategyType` to `SERVING_OFF`
  (YD59).
- The optimization goal is the default "Visit", not a real action (YD62).
- Weekly budget can't fund the ~10 conversions Direct needs to learn a goal
  (YD63) — see `references/unit-economics.md` for the math.

### B. Launch a new campaign (fast path: blueprint)
1. Fill the brief (`assets/creative_brief_template.md`): product, USP, geo,
   landing, budget, target CPA, intents, plus creative-provider access.
2. Research keywords: `research.py expand --seed ... --mod ...` →
   `research.py demand --region <id> --only-live` to drop dead phrases.
3. Write a blueprint JSON (see `assets/campaign_blueprint.example.json`):
   campaign + ad groups + keywords + minus-words + ads. Pick a conversion
   strategy (`auto_cpa`/`auto_roi`), not `manual`/max-clicks, unless the
   account has too little data to learn from yet (see Workflow A, YD56/YD63).
4. `build.py --blueprint campaign.json` (dry-run) → review → `--apply`.
   build.py is idempotent (state.json) and validates ad copy before upload.
5. `ads.py moderate --group <id>` to submit. Add bid adjustments
   (`adjustments.py`) and a shared negatives set (`negatives.py`) as needed.
6. Always put UTM tags on every `href` so Metrica attributes leads.
7. Run `audit.py --campaign <id>` once the campaign is live to catch
   misconfiguration before spend accumulates.

(Manual path still works: `campaigns.py` → `adgroups.py` → `ads.py` step by step.)

### C. Generate ad copy (creatives/texts)
The model writes the copy; scripts only validate + upload.
1. Read `references/ad-copy-rules.md` (limits + the selling formula).
2. Produce 2–3 variants per ad group as the `ads.json` array.
3. Always `ads.py validate --file ads.json` before uploading — it's the hard gate.

### C2. Generate images, banners, and RSYA creatives
This skill must support paired work with a creative generator so text + visuals
are produced together.
1. Confirm which creative backend is available:
   - direct API integration to an external image/banner generation service, or
   - any MCP connector/tool the current agent can call for content production.
2. Ask the user for the backend token/key and endpoint/tool name if they were
   not already provided during onboarding.
3. Generate visuals that match the brief:
   - square and landscape banners for RSYA,
   - product/lifestyle images,
   - image variants for A/B tests,
   - headline/copy overlays only if the provider supports them cleanly.
4. Keep text/copy generation and visual generation synchronized:
   same offer, same audience, same CTA, same geo and landing URL.
5. If the current runtime has an MCP creative tool available, prefer using it.
   If not, collect the external API details and prepare assets for upload via
   `image_ads.py` or other upload flow.
6. If no creative provider is connected yet, do not pretend image generation is
   available. Ask for the provider credentials or MCP connection explicitly.
7. Concrete runtime:
   - `generate_creatives.py --brief brief.json` for generation,
   - `image_ads.py upload-dir --dir <folder>` for image upload,
   - `creative_to_image_manifest.py` + `image_ads.py create-bulk` for batch ad creation.

### D. Track leads
- `metrica.py goals` — list goals. `metrica.py conversions --days 14` — leads by
  Direct campaign. `metrica.py summary` — visits/leads/CR.
- Conversions also come through Direct reports when `Goals` are passed
  (`reports.py ... --goals <id>`).
- If the goal is still "Visit" (default), that's YD62 in the audit — fix it
  before trusting any conversion numbers downstream.

### E. Optimize (lower CPA, cut waste, scale winners)
1. `reports.py campaigns --days 7` and `keywords --campaign <id>` for the picture.
2. `keywords.py mine-queries --campaign <id>` → pick junk → `adgroups.py minus`.
3. `optimize.py --campaign <id>` (dry-run) → review the plan with the user →
   `optimize.py --campaign <id> --apply`.
4. Rewrite weak ads (low CTR), scale profitable groups (more budget/bid).
Full logic + thresholds: `references/optimization-playbook.md` and
`references/unit-economics.md` (how `target_cpa`/`max_bid` should be derived
from margin and site conversion rate, not guessed).

### F. Report
Pull `reports.py` + `metrica.py`, compute CTR/CR/CPA/ROI, compare to target_cpa
from config and to `references/benchmarks.md` for the niche, and summarize:
what spent, what converted, what changed, next steps.

### G. Real ROI from CRM (not just leads)
Optimizing on form-fills chases cheap junk. Feed back actual sales:
1. Export CRM sales as CSV: `Yclid,Target,DateTime,Price,Currency`.
2. `crm_upload.py --file sales.csv --key yclid` → Metrica gets real revenue.
3. Switch campaigns to `auto_roi`/`auto_cpa` strategy on the goal, or let the
   optimizer weigh revenue. This is what turns "leads" into "money in plus".

### H. A/B creatives
`abtest.py eval --group <id> --days 14` → z-test winner once volume is enough →
`--apply` pauses losers. Refresh copy of losers using `keywords.py mine-winners`.
When image/banner generation is available, refresh both copy and visuals as one
creative pack instead of changing text alone.

### I. Weekly autopilot (hands-off)
`autopilot.py --campaign <id>` runs the whole loop (mine waste/winners →
optimize → anomalies → report). Dry-run by default; `--apply` enacts. Pair with
`/schedule` for a recurring weekly cloud run. `anomalies.py` can run daily as a
cheap guard, `audit.py` weekly or monthly to catch settings drift (YD65:
"campaign was set up once and abandoned").

### Safety / recovery
- `state.py show` — what this skill created. `state.py log` — audit trail.
- `rollback.py undo --last 1 --apply` — suspend objects from the last build if
  something went wrong (suspend, not delete — reversible).
- Cost guard: in production, budget changes above `kpi.max_daily_total` are blocked.

## Reference files (read when relevant)
- `references/setup-guide.md` — token, Metrica, config (onboarding).
- `references/budget-leak-checklist.md` — the 7 hidden settings that burn
  budget by default, and how to fix each one.
- `references/unit-economics.md` — `target_cpa`/`max_cpc` formulas, the
  "≥10 conversions to learn" rule, the 3x kill rule.
- `references/yandex-audit.md` — all 65 audit checks (YD01-YD65) with severity.
- `references/scoring-system.md` — how `audit.py` weighs checks into a 0-100 grade.
- `references/benchmarks.md` — Russian-market CTR/CPC/CVR/CPA benchmarks by niche.
- `references/bidding-strategies.md` — bidding strategy guide.
- `references/compliance.md` — Yandex moderation rules & ad policies.
- `references/image-specs.md` — image sizes/specs for YAN (RSY) ads.
- `references/api-v5-reference.md` — services, fields, limits, error codes, money units.
- `references/api/` — deep per-service API reference: `campaigns.md`,
  `targeting.md`, `extensions.md`, `reports.md`, `other-services.md`,
  `use-cases.md` (bash call examples for less common operations).
- `references/ad-copy-rules.md` — char limits, moderation, copy formula.
- `references/optimization-playbook.md` — CPA/ROI math, rules, auto-strategies, weekly loop.

## Subagents
- `agents/audit-yandex.md` — turns `audit.py` output into a prioritized,
  narrative Russian-language report. Use for "audit my account" style asks.

## Conventions
- Money to the API is in micros (×1 000 000); scripts handle it. Reports are in plain units.
- Each API call burns units (quota); batch and prefer reports over polling.
- Default to dry-run; never apply bulk changes to a production account without confirmation.
- This skill is agent-agnostic: any agent may use it, but it must preserve the same
  onboarding order and must request creative-provider access before promising
  banner/photo generation.
- Audit first, act second: don't hand-tune bids/strategy before `audit.py` has
  ruled out the 7 hidden-setting leaks — they usually dwarf whatever the
  rules engine would optimize afterward.
