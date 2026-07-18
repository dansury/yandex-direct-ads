#!/usr/bin/env python3
"""Local state + audit log — make the skill idempotent and accountable.

Two files live next to scripts/:
  - state.json     : maps stable local keys -> Direct object ids, so re-running a
                     build does NOT create duplicates.
  - changelog.jsonl: append-only audit trail of every write action (for rollback).

A "local key" is a human-stable identifier you choose, e.g.
  campaign:"Search RU — Диваны"  -> CampaignId
  group:<campaign_key>/"Горячий спрос" -> AdGroupId

Usage:
    from state import State
    st = State()
    cid = st.get("campaign", "Search RU")           # -> id or None
    st.put("campaign", "Search RU", 12345)
    st.log("create_campaign", {"name": "Search RU"}, {"id": 12345})
"""

import datetime
import json
import os
import threading

_HERE = os.path.dirname(os.path.abspath(__file__))
STATE_PATH = os.path.join(_HERE, "state.json")
CHANGELOG_PATH = os.path.join(_HERE, "changelog.jsonl")
_LOCK = threading.Lock()


class State:
    def __init__(self, path=STATE_PATH, changelog=CHANGELOG_PATH):
        self.path = path
        self.changelog = changelog
        self.data = {}
        if os.path.isfile(path):
            with open(path, "r", encoding="utf-8") as fh:
                self.data = json.load(fh)

    # -- id mapping -------------------------------------------------------
    @staticmethod
    def _k(kind, key):
        return f"{kind}:{key}"

    def get(self, kind, key):
        return self.data.get(self._k(kind, key))

    def put(self, kind, key, obj_id):
        self.data[self._k(kind, key)] = obj_id
        self._save()

    def forget(self, kind, key):
        self.data.pop(self._k(kind, key), None)
        self._save()

    def _save(self):
        with _LOCK:
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(self.data, fh, ensure_ascii=False, indent=2)
            os.replace(tmp, self.path)

    # -- audit log --------------------------------------------------------
    def log(self, action, request, response, sandbox=None):
        """Append one immutable record. Used for review and rollback."""
        rec = {
            "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "action": action,
            "sandbox": sandbox,
            "request": request,
            "response": response,
        }
        with _LOCK, open(self.changelog, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return rec

    def history(self, limit=50):
        if not os.path.isfile(self.changelog):
            return []
        with open(self.changelog, "r", encoding="utf-8") as fh:
            lines = fh.readlines()
        return [json.loads(x) for x in lines[-limit:]]


def _main(argv):
    if argv and argv[0] in ("-h", "--help"):
        print("usage: state.py [show|log [N]]\n\n"
              "  show       print the local key->id state map\n"
              "  log [N]    print last N audit-log records")
        return 0
    st = State()
    if not argv or argv[0] == "show":
        print(json.dumps(st.data, ensure_ascii=False, indent=2))
        return 0
    if argv[0] == "log":
        for rec in st.history(int(argv[1]) if len(argv) > 1 else 50):
            ids = rec["response"].get("ids") if isinstance(rec["response"], dict) else None
            print(f"{rec['ts']}  {rec['action']:<22} {ids if ids else ''}")
        return 0
    print("usage: state.py [show|log [N]]")
    return 1


if __name__ == "__main__":
    import sys
    sys.exit(_main(sys.argv[1:]))
