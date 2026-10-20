"""Read-only NHPLUG intraday collector. Never calls account/order endpoints.

Snapshots contain only public market data. Credentials stay in process memory.
The one-day watcher publishes to a separate data branch, not the screener files.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os
from pathlib import Path
import re
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request

KST = dt.timezone(dt.timedelta(hours=9))
HOST = "https://api.nhplug.com:8443"
ALLOWED = {"/krstock/quote/v1/currentPrice", "/krstock/quote/v1/currentExecution",
           "/krstock/quote/v1/currentInvestor", "/krstock/quote/v1/period"}
ROOT = Path(__file__).resolve().parent.parent
DATA_BRANCH = "nhplug-live-data"
PRIORITY = ["007810", "353200", "222800", "35320K", "042510", "053800", "263860",
            "090460", "009150", "007660", "005930", "000660", "373220", "006400",
            "012450", "064350", "010120", "267260", "207940", "068270", "010950",
            "034020", "277810", "005380", "035420", "069500", "229200"]


class DataError(RuntimeError):
    pass


def number(v):
    try:
        n = float(str(v).replace(",", ""))
        return n if math.isfinite(n) else None
    except (ValueError, TypeError):
        return None


def message(data):
    m = data.get("message")
    m = m if isinstance(m, dict) else {}
    return {"code": str(data.get("rsp_cd") or m.get("msg_code") or ""),
            "text": str(data.get("rsp_msg") or m.get("usr_msg") or "")[:160]}


def block(data, key):
    v = data.get(key)
    return v if isinstance(v, list) else ([v] if isinstance(v, dict) else [])


class Client:
    def __init__(self):
        self.access_token = None
        self.expires_at = 0

    def token(self):
        if self.access_token and time.time() < self.expires_at:
            return self.access_token
        params = urllib.parse.urlencode({"appkey": os.environ["NHPLUG_APPKEY"],
            "appsecretkey": os.environ["NHPLUG_APPSECRET"],
            "grant_type": "client_credentials", "scope": "oob"})
        req = urllib.request.Request(HOST + "/oauth2/token?" + params, data=b"",
            headers={"Content-Type": "application/x-www-form-urlencoded"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=30) as response:
                data = json.load(response)
        except Exception:
            # Do not include request URLs: token endpoint query contains credentials.
            raise DataError("Token request failed; inspect credentials/permissions/network") from None
        if not isinstance(data, dict) or not data.get("access_token"):
            raise DataError("Token response missing access_token")
        self.access_token = data["access_token"]
        self.expires_at = time.time() + max(60, int(data.get("expires_in", 86400)) - 120)
        print("::add-mask::" + self.access_token, flush=True)
        return self.access_token

    def call(self, path, body):
        if path not in ALLOWED:
            raise ValueError("Read-only endpoint not allowed")
        for attempt in range(2):
            time.sleep(0.35)
            req = urllib.request.Request(HOST + path,
                data=json.dumps({"Input_0": body}).encode(), method="POST",
                headers={"Authorization": "Bearer " + self.token(), "Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=12) as response:
                    data = json.load(response)
                if not isinstance(data, dict):
                    raise DataError("API response is not an object")
                return data
            except urllib.error.HTTPError as exc:
                if exc.code == 401 and attempt == 0:
                    self.access_token = None
                    self.expires_at = 0
                    continue
                if exc.code == 429 and attempt < 1:
                    time.sleep(2 ** (attempt + 1))
                    continue
                raise DataError("API HTTP " + str(exc.code)) from None
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
                if attempt == 1:
                    raise DataError("API network/JSON failure") from None
        raise DataError("API retries exhausted")


def normalize(code, data, now=None):
    now = now or dt.datetime.now(KST)
    out = block(data, "Output_0")
    if not out or not isinstance(out[0], dict):
        raise DataError("Missing Output_0: " + json.dumps(message(data), ensure_ascii=False))
    x = out[0]
    price = number(x.get("stck_prpr"))
    if price is None or price <= 0:
        raise DataError("Missing/invalid current price: " + json.dumps(message(data), ensure_ascii=False))
    prev, ret = number(x.get("stck_prdy_clpr")), number(x.get("prdy_ctrt"))
    flags = []
    if prev and ret is not None and abs((price / prev - 1) * 100 - ret) > 0.25:
        flags.append("return_base_mismatch")
    qt = str(x.get("hoga_bsop_hour") or "").replace(":", "")
    if len(qt) == 8:
        qt = qt[:6]
    qts, age = None, None
    if re.fullmatch(r"\d{6}", qt):
        try:
            qts = now.replace(hour=int(qt[:2]), minute=int(qt[2:4]), second=int(qt[4:6]), microsecond=0)
            age = (now - qts).total_seconds()
            if age < -60 or age > 180:
                flags.append("quote_time_stale_or_future")
        except ValueError:
            qts = None
    if qts is None:
        flags.append("quote_time_unverified")
    hi, lo = number(x.get("stck_hgpr")), number(x.get("stck_lwpr"))
    if hi and lo and not lo <= price <= hi:
        flags.append("ohlc_mismatch")
    volume, amount = number(x.get("acml_vol")), number(x.get("acml_tr_pbmn"))
    return {"code": code, "name": str(x.get("iem_nm") or code).strip(), "market": "KRX",
        "observedAt": now.isoformat(), "quoteTime": qts.isoformat() if qts else None,
        "quoteDateVerified": False, "quoteAgeSeconds": age,
        "price": price, "previousClose": prev, "changePct": ret,
        "open": number(x.get("stck_oprc")), "high": hi, "low": lo,
        "volume": volume, "turnoverKRW": amount,
        "vwap": amount / volume if amount and volume else None,
        "distanceFromHighPct": (price / hi - 1) * 100 if hi else None,
        "rangePosition": (price - lo) / (hi - lo) if hi and lo and hi > lo else None,
        "sector": x.get("bstp_kor_isnm"), "sectorCode": x.get("bstp_cls_code"),
        "warnings": flags}


def period_body(code, market="1", minutes=True):
    return {"market_cd": "KRX", "iem_cd": code, "mrkt_div_cls_code": market,
        "edate": dt.datetime.now(KST).strftime("%Y%m%d"), "array_cnt": "0100",
        "maxavg": "000", "gubun": "5" if minutes else "1", "xtick": "005" if minutes else "000",
        "today_cls_code": "1" if minutes else "0", "fake_tick": "1", "sur_flag": "0",
        "sur_gb_day_cnt": "00", "sur_bf_end_time": "", "out1_scale_change": "0",
        "out2_scale_change": "0", "view_main_yn": "Y"}


def load_universe():
    p = ROOT / "docs/data/latest_kr.json"
    items = json.loads(p.read_text(encoding="utf-8")) if p.exists() else []
    return {x["code"]: x for x in items if re.fullmatch(r"\d{6}", str(x.get("code", "")))}


def summarize(quotes):
    usable = [x for x in quotes if not x["warnings"] and x["changePct"] is not None]
    groups = {}
    for q in usable:
        if q.get("sector"):
            groups.setdefault(q["sector"], []).append(q)
    sectors = [{"sector": k, "count": len(v), "advancing": sum(x["changePct"] > 0 for x in v),
                "meanChangePct": sum(x["changePct"] for x in v) / len(v),
                "turnoverKRW": sum(x["turnoverKRW"] or 0 for x in v)} for k, v in groups.items() if len(v) >= 3]
    candidates = [q for q in usable if q["changePct"] > 0 and (q["turnoverKRW"] or 0) >= 5e9
                  and (q["rangePosition"] or 0) >= 0.65 and (q["distanceFromHighPct"] or 0) >= -3]
    candidates.sort(key=lambda q: (q["changePct"], q["turnoverKRW"] or 0), reverse=True)
    return {"eligibleQuotes": len(usable), "advancing": sum(q["changePct"] > 0 for q in usable),
        "declining": sum(q["changePct"] < 0 for q in usable),
        "sectors": sorted(sectors, key=lambda x: x["meanChangePct"], reverse=True),
        "observationCandidates": [q["code"] for q in candidates[:15]],
        "note": "���� ���Ϲ��� ǥ��. �ĺ��� ���� �� �������̸� �ż� ��ȣ�� �ƴ�. ���������� ���� ���� Ȯ��."}


def collect(client, codes, universe, with_details=True):
    quotes, errors = [], []
    started = dt.datetime.now(KST)
    for code in codes:
        try:
            q = normalize(code, client.call("/krstock/quote/v1/currentPrice", {"market_cd": "KRX", "iem_cd": code}))
            quotes.append(q)
        except (DataError, KeyError) as exc:
            errors.append({"code": code, "error": str(exc)[:240]})
        if len(errors) >= 5 and not quotes:
            break  # Do not hammer an unavailable service for all 700 stocks.
    details = {}
    focus = list(dict.fromkeys(["007810", "353200", "042510"] + summarize(quotes)["observationCandidates"][:5]))
    if with_details and quotes:
        for code in focus:
            if code not in {q["code"] for q in quotes}:
                continue
            try:
                market = "4" if universe.get(code, {}).get("market") == "KOSDAQ" else "1"
                p = client.call("/krstock/quote/v1/period", period_body(code, market))
                meta = block(p, "Output_0")
                meta = meta[0] if meta else {}
                bars = block(p, "Output_1")
                keys = ("bsop_date", "bsop_time", "stck_oprc", "stck_hgpr", "stck_lwpr", "stck_prpr", "vol", "tr_pbmn")
                details[code] = {"queryDate": meta.get("qry_date"), "queryTime": meta.get("qry_time"),
                    "bars5m": [{k: b.get(k) for k in keys} for b in bars if isinstance(b, dict)]}
                for q in quotes:
                    if q["code"] == code:
                        q["quoteDateVerified"] = bool(bars and str(bars[0].get("bsop_date")) == started.strftime("%Y%m%d"))
            except DataError as exc:
                details[code] = {"error": str(exc)[:240]}
    finished = dt.datetime.now(KST)
    return {"schemaVersion": 1, "source": "NHPLUG", "exchange": "KRX", "mode": "REST polling",
        "startedAt": started.isoformat(), "generatedAt": finished.isoformat(),
        "requestedCount": len(codes), "receivedCount": len(quotes), "universeCount": len(universe),
        "status": "ok" if len(quotes) >= len(codes) * .8 else "degraded" if quotes else "failed",
        "quotes": quotes, "errors": errors, "details": details, "summary": summarize(quotes)}


def git(*args, cwd=None):
    return subprocess.run(["git", *args], cwd=cwd or ROOT, check=True, capture_output=True, text=True).stdout.strip()


def publish(snapshot, output):
    output.mkdir(parents=True, exist_ok=True)
    rel = Path("research/nhplug/intraday")
    folder = output / rel
    folder.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(snapshot, ensure_ascii=False, indent=2)
    (folder / "latest.json").write_text(encoded, encoding="utf-8")
    stamp = dt.datetime.fromisoformat(snapshot["generatedAt"]).strftime("%Y-%m-%d/%H%M%S")
    p = folder / (stamp + ".json")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(encoded, encoding="utf-8")
    git("add", "--", str(rel), cwd=output)
    if not git("status", "--porcelain", cwd=output):
        return
    git("-c", "user.name=github-actions[bot]", "-c", "user.email=github-actions[bot]@users.noreply.github.com",
        "commit", "-m", "NHPLUG intraday " + snapshot["generatedAt"], cwd=output)
    git("push", "origin", "HEAD:refs/heads/" + DATA_BRANCH, cwd=output)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--watch", action="store_true")
    ap.add_argument("--date", default="2026-10-20")
    ap.add_argument("--output", type=Path)
    args = ap.parse_args()
    now = dt.datetime.now(KST)
    if now.date().isoformat() != args.date or now.hour >= 16:
        print("One-day collection window closed")
        return
    client, universe = Client(), load_universe()
    codes = list(dict.fromkeys(PRIORITY))
    # Publish a small authenticated health check before the broader sweep.
    snap = collect(client, ["007810", "353200"], universe, with_details=False)
    if args.output:
        publish(snap, args.output)
    else:
        print(json.dumps(snap, ensure_ascii=False, indent=2))
    print("Initial snapshot:", snap["status"], snap["receivedCount"], "/", snap["requestedCount"], flush=True)
    if not snap["quotes"]:
        raise DataError("No valid quotes; see sanitized snapshot errors")
    if not args.watch:
        return
    # Continuous one-day worker avoids GitHub's delayed cron for today's session.
    # Every sixth sweep covers the whole screener universe; other sweeps retain
    # priority stocks plus liquid leaders discovered in the last broad sweep.
    cycle, active = 0, codes
    while dt.datetime.now(KST).date().isoformat() == args.date and dt.datetime.now(KST).time() < dt.time(15, 26):
        begin = time.monotonic()
        sweep = list(dict.fromkeys(codes + list(universe))) if cycle % 6 == 0 else active
        snap = collect(client, sweep, universe)
        if args.output:
            publish(snap, args.output)
        if snap["status"] == "failed":
            raise DataError("API unavailable; failed snapshot published, old data not reused")
        liquid = sorted(snap["quotes"], key=lambda q: q["turnoverKRW"] or 0, reverse=True)[:70]
        active = list(dict.fromkeys(codes + [q["code"] for q in liquid] + snap["summary"]["observationCandidates"]))
        print("Snapshot:", snap["generatedAt"], snap["status"], snap["receivedCount"], flush=True)
        cycle += 1
        # Never run past today's market window. No cache or credentials published.
        wait = max(0, 300 - (time.monotonic() - begin))
        remaining = (dt.datetime.combine(dt.datetime.now(KST).date(), dt.time(15, 26), KST) - dt.datetime.now(KST)).total_seconds()
        time.sleep(max(0, min(wait, remaining)))


if __name__ == "__main__":
    main()

