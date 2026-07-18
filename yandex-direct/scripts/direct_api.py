#!/usr/bin/env python3
"""Yandex.Direct API v5 client — stdlib only (zero pip deps).

Single shared transport for every other script in this skill.

Usage as module:
    from direct_api import DirectClient, load_config
    cfg = load_config()
    client = DirectClient(cfg)
    result = client.call("campaigns", "get", {
        "SelectionCriteria": {},
        "FieldNames": ["Id", "Name"],
    })

Usage as CLI:
    python direct_api.py ping              # verify token, show units left
    python direct_api.py call campaigns get '{"SelectionCriteria":{},"FieldNames":["Id","Name"]}'
"""

import json
import os
import sys
import time
import urllib.error
import urllib.request

# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
PROD_BASE = "https://api.direct.yandex.com/json/v5/"
SANDBOX_BASE = "https://api-sandbox.direct.yandex.com/json/v5/"
PROD_REPORTS = "https://api.direct.yandex.com/json/v5/reports"
SANDBOX_REPORTS = "https://api-sandbox.direct.yandex.com/json/v5/reports"

# Human-readable hints for the error codes people actually hit.
ERROR_HINTS = {
    53: "No access rights to the object. Check Client-Login / agency rights.",
    58: "No access to the API. Apply for production API access in the Direct UI.",
    152: "Out of API units (daily points). Wait for reset or request a larger quota.",
    8000: "Invalid OAuth token. Re-issue the token (see references/setup-guide.md).",
    8800: "Token issued for a different application. Re-issue with the correct app.",
    1000: "Validation error. Check field limits in references/api-v5-reference.md.",
}

CONFIG_NAMES = ("config.json", "config.local.json")


def _find_config():
    """Look for config.json next to scripts/ or in CWD."""
    here = os.path.dirname(os.path.abspath(__file__))
    candidates = []
    for name in CONFIG_NAMES:
        candidates.append(os.path.join(here, name))
        candidates.append(os.path.join(os.getcwd(), name))
    for path in candidates:
        if os.path.isfile(path):
            return path
    return None


def load_config(path=None):
    """Load config.json. Token may also come from env YANDEX_DIRECT_TOKEN."""
    path = path or _find_config()
    cfg = {}
    if path:
        with open(path, "r", encoding="utf-8") as fh:
            cfg = json.load(fh)
    env_token = os.environ.get("YANDEX_DIRECT_TOKEN")
    if env_token:
        cfg["token"] = env_token
    if not cfg.get("token"):
        raise SystemExit(
            "No token. Copy scripts/config.example.json -> config.json and set "
            "\"token\", or export YANDEX_DIRECT_TOKEN. See references/setup-guide.md."
        )
    cfg.setdefault("sandbox", True)
    cfg.setdefault("lang", "ru")
    return cfg


def collect_add_results(result, action="add"):
    """Parse a *.add/update response into (ids, errors, warnings).

    Direct returns per-item results in AddResults/UpdateResults: each item has
    either an Id or an Errors[] / Warnings[] list. Callers must inspect this —
    a 200 OK can still mean half your objects were rejected.

    Returns:
        ids:      list of successfully created/updated ids (None for failed rows)
        errors:   list of {index, code, message, details}
        warnings: list of {index, code, message}
    """
    key = "AddResults" if action == "add" else "UpdateResults"
    rows = result.get(key, result.get("AddResults", []))
    ids, errors, warnings = [], [], []
    for i, row in enumerate(rows):
        if row.get("Id") is not None:
            ids.append(row["Id"])
        else:
            ids.append(None)
        for e in row.get("Errors", []) or []:
            errors.append({"index": i, "code": e.get("Code"),
                           "message": e.get("Message"), "details": e.get("Details", "")})
        for w in row.get("Warnings", []) or []:
            warnings.append({"index": i, "code": w.get("Code"), "message": w.get("Message")})
    return ids, errors, warnings


class CostGuardError(Exception):
    """Raised when a money-spending action would exceed a configured hard cap."""


def cost_guard(config, projected_daily_total):
    """Block runaway spend. Checks projected total daily budget vs kpi.max_daily_total.

    Only enforced in production (sandbox spends nothing). Returns silently if OK,
    raises CostGuardError otherwise. projected_daily_total is in currency units.
    """
    if config.get("sandbox", True):
        return
    cap = config.get("kpi", {}).get("max_daily_total")
    if cap and projected_daily_total > cap:
        raise CostGuardError(
            f"BLOCKED: projected daily budget {projected_daily_total:.0f} exceeds "
            f"kpi.max_daily_total={cap} (production). Raise the cap in config.json "
            f"if this is intentional."
        )


class DirectError(Exception):
    def __init__(self, code, message, detail=""):
        self.code = code
        self.message = message
        self.detail = detail
        hint = ERROR_HINTS.get(code, "")
        text = f"Direct API error {code}: {message}"
        if detail:
            text += f" | {detail}"
        if hint:
            text += f"\n  hint: {hint}"
        super().__init__(text)


class DirectClient:
    def __init__(self, config):
        self.token = config["token"]
        self.sandbox = bool(config.get("sandbox", True))
        self.lang = config.get("lang", "ru")
        self.client_login = config.get("client_login")  # agency only
        self.base = SANDBOX_BASE if self.sandbox else PROD_BASE
        self.reports_url = SANDBOX_REPORTS if self.sandbox else PROD_REPORTS
        self.last_units = None
        self.max_retries = int(config.get("max_retries", 3))

    # -- headers ----------------------------------------------------------
    def _headers(self, extra=None):
        h = {
            "Authorization": f"Bearer {self.token}",
            "Accept-Language": self.lang,
            "Content-Type": "application/json; charset=utf-8",
        }
        if self.client_login:
            h["Client-Login"] = self.client_login
        if extra:
            h.update(extra)
        return h

    # -- core JSON call ---------------------------------------------------
    def call(self, service, method, params):
        """Call <base>/<service> with {method, params}. Returns the 'result' dict."""
        url = self.base + service
        body = json.dumps({"method": method, "params": params}, ensure_ascii=False)
        data = body.encode("utf-8")

        for attempt in range(self.max_retries + 1):
            req = urllib.request.Request(url, data=data, headers=self._headers(), method="POST")
            try:
                with urllib.request.urlopen(req, timeout=60) as resp:
                    units = resp.headers.get("Units")
                    if units:
                        self.last_units = units
                    payload = json.loads(resp.read().decode("utf-8"))
                break
            except urllib.error.HTTPError as e:
                if e.code in (429, 500, 502, 503, 504) and attempt < self.max_retries:
                    time.sleep(2 ** attempt)
                    continue
                detail = e.read().decode("utf-8", "replace")
                raise DirectError(e.code, f"HTTP {e.code}", detail)
            except urllib.error.URLError as e:
                if attempt < self.max_retries:
                    time.sleep(2 ** attempt)
                    continue
                raise DirectError(0, f"Network error: {e.reason}")

        if "error" in payload:
            err = payload["error"]
            raise DirectError(
                err.get("error_code", -1),
                err.get("error_string", "unknown"),
                err.get("error_detail", ""),
            )
        return payload.get("result", {})

    # -- reports (TSV) ----------------------------------------------------
    def report(self, report_definition, skip_report_header=True, skip_column_header=False,
               skip_report_summary=True):
        """Run a Reports request. Returns raw TSV text. Handles 201/202 polling."""
        body = json.dumps({"params": report_definition}, ensure_ascii=False).encode("utf-8")
        extra = {
            "processingMode": "auto",
            "returnMoneyInMicros": "false",
            "skipReportHeader": "true" if skip_report_header else "false",
            "skipColumnHeader": "true" if skip_column_header else "false",
            "skipReportSummary": "true" if skip_report_summary else "false",
        }
        for attempt in range(20):
            req = urllib.request.Request(
                self.reports_url, data=body, headers=self._headers(extra), method="POST"
            )
            try:
                with urllib.request.urlopen(req, timeout=120) as resp:
                    code = resp.getcode()
                    text = resp.read().decode("utf-8")
                    units = resp.headers.get("Units")
                    if units:
                        self.last_units = units
                    if code == 200:
                        return text
            except urllib.error.HTTPError as e:
                code = e.code
                if code in (201, 202):
                    retry_in = int(e.headers.get("retryIn", 5))
                    time.sleep(min(retry_in, 10))
                    continue
                detail = e.read().decode("utf-8", "replace")
                raise DirectError(code, f"Report HTTP {code}", detail)
        raise DirectError(0, "Report not ready after polling")

    def ping(self):
        """Cheap sanity call: list up to 1 campaign id."""
        res = self.call("campaigns", "get", {
            "SelectionCriteria": {},
            "FieldNames": ["Id", "Name"],
            "Page": {"Limit": 1},
        })
        return res


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _main(argv):
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    cfg = load_config()
    client = DirectClient(cfg)
    cmd = argv[0]

    if cmd == "ping":
        res = client.ping()
        env = "SANDBOX" if client.sandbox else "PRODUCTION"
        n = len(res.get("Campaigns", []))
        print(f"OK [{env}] token valid. Campaigns visible: {n}. Units: {client.last_units}")
        return 0

    if cmd == "call":
        if len(argv) < 4:
            print("usage: direct_api.py call <service> <method> '<json params>'")
            return 1
        service, method, params = argv[1], argv[2], json.loads(argv[3])
        res = client.call(service, method, params)
        print(json.dumps(res, ensure_ascii=False, indent=2))
        print(f"# units: {client.last_units}", file=sys.stderr)
        return 0

    print(f"unknown command: {cmd}")
    return 1


if __name__ == "__main__":
    try:
        sys.exit(_main(sys.argv[1:]))
    except DirectError as e:
        print(str(e), file=sys.stderr)
        sys.exit(2)
