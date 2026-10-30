"""골든코드 시장 전체 순위 수집기(조회 전용). python -m goldencode.rankings --output DIR [--watch]
네이버 모바일 증권 순위(코스피·코스닥)를 모아 후보 발견용 파일로 저장한다. 매매 신호가 아니다.
각 행의 체결 시각(localTradedAt)과 수집 시각의 차이를 초 단위로 기록한다."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import subprocess
import time
from pathlib import Path

import requests

KST = dt.timezone(dt.timedelta(hours=9))
ROOT = Path(__file__).resolve().parent.parent
DATA_BRANCH = "goldencode-live-data"
HEADERS = {"User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15 Mobile/15E148 Safari/604.1",
           "Referer": "https://m.stock.naver.com/"}
URL = "https://m.stock.naver.com/api/stocks/{kind}/{market}"
MARKETS = ("KOSPI", "KOSDAQ")
# 목록 이름 -> (API 경로, 목표 종목 수, 의미). 정렬 기준은 2026-10-30 프로브로 확인했다.
LISTS = {
    "value": ("priceTop", 50, "거래대금 상위(누적)"),
    "rise": ("up", 30, "상승률 상위"),
    "fall": ("down", 30, "하락률 상위(시장 진단용, 강세 후보 아님)"),
    "popular": ("searchTop", 20, "인기 검색 상위(순간/누적 기준 미확인)"),
    "high52": ("high52week", 30, "52주 신고가 종목"),
}
NOT_AVAILABLE = ["거래량·거래대금 급증 순위(네이버 모바일 API에 해당 경로 없음)"]
SESSION_END = dt.time(15, 35)
MAX_ROW_AGE = 300
PAGE_SIZE = 30


def won(text):
    """'4조 22억원', '3.7억원' -> 원. 못 읽으면 None."""
    s = str(text or "").replace(",", "").replace(" ", "")
    jo, eok = re.search(r"([0-9.]+)조", s), re.search(r"([0-9.]+)억", s)
    if not (jo or eok):
        return None
    return (float(jo.group(1)) * 1e12 if jo else 0.0) + (float(eok.group(1)) * 1e8 if eok else 0.0)


def num(v):
    try:
        return float(str(v).replace(",", ""))
    except (ValueError, TypeError):
        return None


def kind_of(row):
    name = str(row.get("stockName") or "")
    end = str(row.get("stockEndType") or "stock").lower()
    if end in ("etf", "etn"):
        return end
    if "스팩" in name or re.search(r"제\d+호$", name):
        return "spac"
    if re.search(r"[0-9]?우(B|C)?$", name):
        return "preferred"
    return "common" if end == "stock" else end


def normalize(row, market, fetched):
    code = str(row.get("itemCode") or "").zfill(6)
    traded = None
    try:
        traded = dt.datetime.fromisoformat(row["localTradedAt"])
    except (KeyError, ValueError, TypeError):
        pass
    warnings = []
    value = None
    shown, hangeul = num(row.get("accumulatedTradingValue")), won(row.get("accumulatedTradingValueKrwHangeul"))
    if shown is not None:  # 표시값 단위는 백만원. 한글 표기와 맞는지 교차 확인한다.
        value = shown * 1e6
        if hangeul and abs(value - hangeul) / hangeul > .05:
            warnings.append("turnover_unit_mismatch")
            value = hangeul
    elif hangeul:
        value = hangeul
    age = (fetched - traded).total_seconds() if traded else None
    if age is None:
        warnings.append("no_trade_time")
    elif age > MAX_ROW_AGE:
        warnings.append("row_older_than_300s")
    return {"code": code, "name": row.get("stockName"), "market": market, "kind": kind_of(row),
            "price": num(row.get("closePrice")), "changePct": num(row.get("fluctuationsRatio")),
            "volume": num(row.get("accumulatedTradingVolume")), "turnoverKRW": value,
            "marcapHangeul": row.get("marketValueHangeul"),
            "tradedAt": traded.isoformat() if traded else None,
            "ageSeconds": round(age, 1) if age is not None else None,
            "marketStatus": row.get("marketStatus"), "warnings": warnings}


def find_rows(obj):
    """응답 구조가 바뀌어도 itemCode가 든 가장 큰 배열을 찾는다."""
    best = []
    stack = [obj]
    while stack:
        o = stack.pop()
        if isinstance(o, list):
            rows = [x for x in o if isinstance(x, dict) and "itemCode" in x]
            if len(rows) > len(best):
                best = rows
            stack.extend(o)
        elif isinstance(o, dict):
            stack.extend(o.values())
    return best


def fetch_list(get, kind, market, want, fetched):
    """페이지 크기 30은 프로브로 확인한 값이다. 크기를 바꾸면 페이지 경계가 어긋나 행이 빠질 수 있다."""
    rows, page = [], 1
    while len(rows) < want and page <= 3:
        r = get(URL.format(kind=kind, market=market), params={"page": page, "pageSize": PAGE_SIZE}, headers=HEADERS, timeout=20)
        r.raise_for_status()
        got = find_rows(r.json())
        if not got:
            break
        rows += got
        page += 1
    return [normalize(x, market, fetched) for x in rows[:want]]


def collect(get=requests.get, now=None):
    started = now or dt.datetime.now(KST)
    lists, errors, merged = {}, [], {}
    for name, (kind, want, meaning) in LISTS.items():
        for market in MARKETS:
            key = f"{name}_{market}"
            try:
                fetched = dt.datetime.now(KST)
                rows = fetch_list(get, kind, market, want, fetched)
            except (requests.RequestException, ValueError) as e:
                errors.append({"list": key, "error": type(e).__name__})
                rows = []
            lists[key] = {"meaning": meaning, "target": want, "received": len(rows), "rows": rows}
            for i, row in enumerate(rows, 1):
                c = merged.setdefault(row["code"], {**row, "sources": {}})
                c["sources"][key] = i
            time.sleep(0.3)
    finished = dt.datetime.now(KST)
    cands = list(merged.values())
    for c in cands:
        c["sourceCount"] = len(c["sources"])
        c["warnings"] = sorted(set(c["warnings"]))
    ages = [c["ageSeconds"] for c in cands if c["ageSeconds"] is not None]
    got_all = all(v["received"] >= min(v["target"], 20) for k, v in lists.items() if not k.startswith("high52"))
    stale = sum(1 for c in cands if "row_older_than_300s" in c["warnings"])
    return {"schemaVersion": 1, "source": "naver-mobile", "startedAt": started.isoformat(), "generatedAt": finished.isoformat(),
            "exchangeBasis": "네이버 표시 기준(KRX/NXT 통합 여부 미확인), 응답 지연시간 필드 delayTime=0",
            "status": "failed" if not cands else "ok" if got_all and not errors and not stale else "degraded",
            "notAvailable": NOT_AVAILABLE, "errors": errors,
            "quality": {"candidateCount": len(cands), "maxAgeSeconds": max(ages) if ages else None,
                        "medianAgeSeconds": sorted(ages)[len(ages) // 2] if ages else None, "staleRows": stale},
            "lists": lists, "candidates": sorted(cands, key=lambda c: (-c["sourceCount"], -(c["turnoverKRW"] or 0)))}


def git(*a, cwd):
    return subprocess.run(["git", *a], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def publish(snap, output, keep_history):
    rel = Path("research/goldencode/rankings")
    folder = output / rel
    folder.mkdir(parents=True, exist_ok=True)
    text = json.dumps(snap, ensure_ascii=False, indent=2)
    (folder / "latest.json").write_text(text, encoding="utf-8")
    if keep_history:
        stamp = dt.datetime.fromisoformat(snap["generatedAt"]).strftime("%Y-%m-%d/%H%M%S")
        p = folder / (stamp + ".json")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    git("add", "-f", "--", str(rel), cwd=output)
    if not git("status", "--porcelain", cwd=output):
        return
    git("-c", "user.name=github-actions[bot]", "-c", "user.email=github-actions[bot]@users.noreply.github.com",
        "commit", "-m", "GoldenCode rankings " + snap["generatedAt"], cwd=output)
    for wait in (0, 2, 4, 8):
        time.sleep(wait)
        try:
            if wait:
                git("pull", "--rebase", "origin", DATA_BRANCH, cwd=output)
            git("push", "origin", "HEAD:refs/heads/" + DATA_BRANCH, cwd=output)
            return
        except subprocess.CalledProcessError:
            continue
    raise RuntimeError("publish failed")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--output", type=Path, required=True)
    ap.add_argument("--watch", action="store_true", help="마감(15:35)까지 반복")
    ap.add_argument("--interval", type=int, default=120)
    ap.add_argument("--max-minutes", type=int, default=330)
    a = ap.parse_args()
    deadline = time.monotonic() + a.max_minutes * 60
    n = 0
    while True:
        snap = collect()
        n += 1
        publish(snap, a.output, keep_history=(n % 5 == 1))
        q = snap["quality"]
        print(snap["generatedAt"], snap["status"], "candidates", q["candidateCount"], "maxAge", q["maxAgeSeconds"], "errors", len(snap["errors"]), flush=True)
        now = dt.datetime.now(KST)
        if not a.watch or now.time() >= SESSION_END or now.weekday() >= 5 or time.monotonic() + a.interval > deadline:
            return
        time.sleep(a.interval)


if __name__ == "__main__":
    main()
