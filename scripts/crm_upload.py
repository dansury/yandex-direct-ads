#!/usr/bin/env python3
"""Offline conversions upload — turn CRM sales into real ROI signal.

This is the upgrade from "we got a lead" to "the lead became money". You feed
Metrica which clicks (by Yandex click id = yclid) actually became sales and for
how much. Direct's auto-strategies and our optimizer then chase revenue, not raw
form-fills — killing cheap-but-junk leads.

Input CSV (UTF-8), header required. Two modes:
  by yclid (recommended for Direct):  Yclid,Target,DateTime,Price,Currency
  by ClientId (Metrica client id):    ClientId,Target,DateTime,Price,Currency
  - Target   = goal name/identifier in Metrica (or omit Price for a plain goal)
  - DateTime = unix seconds of the conversion
  - Price    = order value; Currency = RUB/USD/...

Examples:
    python crm_upload.py --file sales.csv --key yclid
    python crm_upload.py --file sales.csv --key clientid --comment "CRM 2026-06"
"""

import argparse
import sys
import urllib.parse
import urllib.request

from direct_api import load_config

METRICA_BASE = "https://api-metrika.yandex.net"
KEY_TO_TYPE = {"yclid": "YCLID", "clientid": "CLIENT_ID", "userid": "USER_ID"}


def upload(cfg, csv_bytes, key="yclid", comment=""):
    token = cfg["token"]
    counter = cfg["metrica"]["counter_id"]
    client_id_type = KEY_TO_TYPE[key]
    url = (f"{METRICA_BASE}/management/v1/counter/{counter}"
           f"/offline_conversions/upload?client_id_type={client_id_type}")
    if comment:
        url += "&comment=" + urllib.parse.quote(comment)

    boundary = "----ydirectskill0boundary"
    body = bytearray()
    body += f"--{boundary}\r\n".encode()
    body += b'Content-Disposition: form-data; name="file"; filename="conversions.csv"\r\n'
    body += b"Content-Type: text/csv\r\n\r\n"
    body += csv_bytes
    body += f"\r\n--{boundary}--\r\n".encode()

    req = urllib.request.Request(url, data=bytes(body), method="POST", headers={
        "Authorization": f"OAuth {token}",
        "Content-Type": f"multipart/form-data; boundary={boundary}",
    })
    with urllib.request.urlopen(req, timeout=120) as resp:
        return resp.read().decode("utf-8")


def main(argv):
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--file", required=True, help="CSV with header")
    p.add_argument("--key", choices=list(KEY_TO_TYPE.keys()), default="yclid")
    p.add_argument("--comment", default="")
    args = p.parse_args(argv)

    cfg = load_config()
    if not cfg.get("metrica", {}).get("counter_id"):
        print("Set metrica.counter_id in config.json", file=sys.stderr)
        return 1
    with open(args.file, "rb") as fh:
        csv_bytes = fh.read()
    if b"," not in csv_bytes.splitlines()[0]:
        print("CSV header looks wrong (need comma-separated header)", file=sys.stderr)
        return 1
    print(upload(cfg, csv_bytes, key=args.key, comment=args.comment))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
