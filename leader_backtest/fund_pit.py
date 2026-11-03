"""공시 시점 기준(point-in-time) 분기 재무 수집·특징 계산. 설계는 2026-11-03에 결과를 보기 전에 고정했다.
python -m leader_backtest.fund_pit  (환경변수 DART_API_KEY, SEC_USER_AGENT 필요; 결과 research/leader_backtest/[LB_OUT/]fund_pit/{kr,us}.csv.gz)
원칙: (1) 값은 '처음 공시된' 버전만 쓴다(정정·이후 보고서의 비교 수치로 덮어쓰지 않는다). (2) 접수일(filed) 다음 거래일부터 사용한다(같은 날 장중 공시 가능성 배제).
(3) 한국은 DART 다중회사 주요계정(fnlttMultiAcnt)으로 연결(CFS) 우선·없으면 별도(OFS), 누적 금액의 차로 분기 값을 만든다(Q4 = 사업보고서 - 3분기 누적).
(4) 미국은 SEC companyfacts, 기존 publish_us_fundamentals 의 분기 재구성 로직을 가장 이른 공시 우선으로 바꿔 사용한다.
한계: 현재 상장 종목 유니버스(상장폐지 제외 -> 생존편향), 업종별 계정명 차이로 일부 종목 결측, 연결/별도 전환 시 불연속."""
from __future__ import annotations

import datetime as dt
import gzip
import json
import logging
import math
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np
import pandas as pd

from .main import OUT_DIR, ROOT

log = logging.getLogger("leader_backtest.fund_pit")
FIRST_YEAR = 2015
REPORTS = {1: "11013", 2: "11012", 3: "11014", 4: "11011"}
KR_NAMES = {"revenue": ("매출액", "수익(매출액)", "영업수익", "매출"), "op": ("영업이익", "영업이익(손실)", "영업손익"),
            "net": ("당기순이익", "당기순이익(손실)", "분기순이익", "반기순이익", "연결당기순이익", "당기순손익"),
            "liabilities": ("부채총계",), "equity": ("자본총계",)}
FLOW = ("revenue", "op", "net")


def _num(v):
    s = str(v if v is not None else "").replace(",", "").strip()
    if re.fullmatch(r"\(\d+(?:\.\d+)?\)", s):
        s = "-" + s[1:-1]
    return float(s) if re.fullmatch(r"-?\d+(?:\.\d+)?", s) else None


def _norm(s):
    return re.sub(r"\s+", "", s or "")


def kr_rows_to_cum(rows: list[dict]) -> dict:
    """DART 다중회사 주요계정 응답 한 건(회사×보고서) -> {item: (누적 금액, 시점금액)} 와 접수번호/연결구분. CFS 우선."""
    by_div = {}
    for r in rows:
        by_div.setdefault(r.get("fs_div"), []).append(r)
    div = "CFS" if by_div.get("CFS") else ("OFS" if by_div.get("OFS") else None)
    if div is None:
        return {}
    out = {"basis": div, "rcept": rows[0].get("rcept_no", "")}
    for item, names in KR_NAMES.items():
        hit = [r for r in by_div[div] if _norm(r.get("account_nm")) in names and r.get("sj_div") in (("IS", "CIS") if item in FLOW else ("BS",))]
        if not hit:
            continue
        r = hit[0]
        out[item] = {"amount": _num(r.get("thstrm_amount")), "add": _num(r.get("thstrm_add_amount"))}
    return out


def _cum(rep: dict | None, item: str, q: int):
    """보고서 한 건에서 해당 분기까지의 누적 값. Q1=분기 금액, Q2/Q3=누적(add), Q4=사업보고서 연간 금액."""
    if not rep or not rep.get(item):
        return None
    return rep[item]["amount"] if q in (1, 4) else rep[item]["add"]


def kr_quarters(reports: dict[tuple[int, int], dict]) -> list[dict]:
    """reports[(year, q)] = kr_rows_to_cum 결과. 흐름 항목은 누적 차이로 분기 값, 재무상태 항목은 기말 잔액. 연결/별도가 바뀐 해는 차이를 만들지 않고 결측."""
    out = []
    for (year, q), rep in sorted(reports.items()):
        if not rep:
            continue
        m = re.fullmatch(r"(\d{4})(\d{2})(\d{2})\d{6}", rep.get("rcept") or "")
        rec = {"year": year, "q": q, "basis": rep["basis"], "filed": f"{m[1]}-{m[2]}-{m[3]}" if m else None,
               "qend": (pd.Timestamp(year, q * 3, 1) + pd.offsets.MonthEnd(0)).date()}
        for item in FLOW:
            cur = _cum(rep, item, q)
            if q == 1:
                rec[item] = cur
                continue
            prev_rep = reports.get((year, q - 1))
            prev = _cum(prev_rep, item, q - 1) if prev_rep and prev_rep.get("basis") == rep["basis"] else None
            rec[item] = cur - prev if cur is not None and prev is not None else None
        for item in ("liabilities", "equity"):
            rec[item] = rep[item]["amount"] if rep.get(item) else None
        out.append(rec)
    return out


def collect_kr(codes: list[str], key: str, last_year: int | None = None) -> pd.DataFrame:
    from publish_fundamentals import Dart
    api = Dart(key)
    corps = api.corporations()
    corp_of = {c.zfill(6): corps[c.zfill(6)] for c in codes if c.zfill(6) in corps}
    inv = {v: k for k, v in corp_of.items()}
    today = dt.date.today()
    last_year = last_year or today.year
    jobs = []
    ids = list(inv)
    for year in range(FIRST_YEAR, last_year + 1):
        for q, rc in REPORTS.items():
            end = dt.date(year, q * 3, 28)
            if end > today - dt.timedelta(days=30):
                continue
            for i in range(0, len(ids), 100):
                jobs.append((year, q, rc, ids[i:i + 100]))
    log.info("DART 호출 %d건 (종목 %d)", len(jobs), len(ids))
    reports: dict[str, dict] = {}

    def run(job):
        year, q, rc, batch = job
        for attempt in range(4):
            try:
                rows = api.request("fnlttMultiAcnt.json", corp_code=",".join(batch), bsns_year=year, reprt_code=rc)
                return job, rows
            except Exception as exc:                      # noqa: BLE001
                time.sleep(3 * (attempt + 1))
        return job, None
    failed = 0
    with ThreadPoolExecutor(4) as pool:
        for fut in as_completed([pool.submit(run, j) for j in jobs]):
            (year, q, rc, batch), rows = fut.result()
            if rows is None:
                failed += 1
                continue
            grouped: dict[str, list] = {}
            for r in rows:
                grouped.setdefault(r.get("corp_code"), []).append(r)
            for corp, rs in grouped.items():
                if corp in inv:
                    reports.setdefault(inv[corp], {})[(year, q)] = kr_rows_to_cum(rs)
    log.info("DART 실패 호출 %d", failed)
    recs = []
    for code, rep in reports.items():
        for r in kr_quarters(rep):
            recs.append({"market": "kr", "code": code, **{k: r[k] for k in ("qend", "filed", "revenue", "op", "net", "liabilities", "equity", "basis")}})
    df = pd.DataFrame(recs)
    df.attrs["failedCalls"] = failed
    return df


def collect_us(tickers: list[str]) -> pd.DataFrame:
    import publish_us_fundamentals as us
    ua = os.environ.get("SEC_USER_AGENT", "").strip()
    if not ua:
        raise RuntimeError("SEC_USER_AGENT is required")
    tk = us.fetch("https://www.sec.gov/files/company_tickers.json")
    mapping = {v["ticker"].replace(".", "-").upper(): v["cik_str"] for v in tk.values()}
    today = dt.date.today().isoformat()
    orig = us.choose
    us.choose = lambda rows: (sorted(rows, key=lambda r: (r["rank"], us.day(r["filed"]).toordinal(), r.get("accn", "")))[0] if rows else None)   # 가장 이른 공시 우선
    recs, miss = [], 0
    try:
        for t in tickers:
            cik = mapping.get(t.replace(".", "-").upper())
            if cik is None:
                miss += 1
                continue
            try:
                data = us.fetch(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{int(cik):010d}.json")
                for r in us.normalize(data, today, last=None):
                    recs.append({"market": "us", "code": t, "qend": r["periodEnd"], "filed": r["filedAt"], "revenue": r.get("revenue"), "op": r.get("operatingProfit"),
                                 "net": r.get("netIncome"), "liabilities": r.get("liabilities"), "equity": r.get("equity"), "basis": "US-GAAP"})
            except Exception as exc:                      # noqa: BLE001
                miss += 1
                log.warning("%s 실패: %s", t, exc)
    finally:
        us.choose = orig
    df = pd.DataFrame(recs)
    df.attrs["missing"] = miss
    return df


def quarter_features(q: pd.DataFrame) -> pd.DataFrame:
    """분기 레코드(code, qend, filed, revenue, op, net, liabilities, equity) -> 공시 시점의 특징. 비교 기준(전년 동기)은 그 시점에 이미 공시된 값이다."""
    q = q.copy()
    q["qend"], q["filed"] = pd.to_datetime(q["qend"]), pd.to_datetime(q["filed"])
    q = q.dropna(subset=["qend", "filed"]).sort_values(["code", "qend"]).reset_index(drop=True)
    prev = q[["code", "qend", "revenue", "op", "net"]].copy()
    prev["qend_next"] = prev["qend"] + pd.DateOffset(years=1)
    prev = prev.rename(columns={"revenue": "p_rev", "op": "p_op", "net": "p_net"}).sort_values("qend_next")
    m = pd.merge_asof(q.sort_values("qend"), prev.drop(columns="qend"), left_on="qend", right_on="qend_next", by="code", direction="nearest", tolerance=pd.Timedelta(days=20))
    m = m.sort_values(["code", "qend"]).reset_index(drop=True)
    pct = lambda cur, base: np.where((base > 0) & cur.notna(), (cur / base - 1) * 100, np.nan)
    f = pd.DataFrame({"code": m["code"], "filed": m["filed"], "qend": m["qend"]})
    f["revYoY"] = pct(m["revenue"], m["p_rev"])
    f["opYoY"] = pct(m["op"], m["p_op"])
    f["turn"] = ((m["p_op"] <= 0) & (m["op"] > 0)).astype(float)
    f.loc[m["p_op"].isna() | m["op"].isna(), "turn"] = np.nan
    f["opMargin"] = np.where(m["revenue"] > 0, m["op"] / m["revenue"] * 100, np.nan)
    p_margin = np.where(m["p_rev"] > 0, m["p_op"] / m["p_rev"] * 100, np.nan)
    f["dMargin"] = f["opMargin"] - p_margin
    f["accel"] = f.groupby("code")["revYoY"].diff()
    f["netPos"] = np.where(m["net"].notna(), (m["net"] > 0).astype(float), np.nan)
    f["de"] = np.where(m["equity"] > 0, m["liabilities"] / m["equity"] * 100, np.nan)
    return f


FUND_COLS = ("revYoY", "opYoY", "turn", "opMargin", "dMargin", "accel", "netPos", "de")


def attach(panel: pd.DataFrame, feats: pd.DataFrame) -> pd.DataFrame:
    """panel(date, code, ...)에 그 날짜 '이전에' 공시된 가장 최근 분기의 특징을 붙인다(접수일 당일은 제외). daysSince = 접수 후 경과 일수."""
    f = feats.dropna(subset=["filed"]).sort_values("filed").rename(columns={"filed": "filedAt"})
    f = f.drop_duplicates(["code", "filedAt"], keep="last")
    p = panel.sort_values("date")
    m = pd.merge_asof(p, f[["code", "filedAt", *FUND_COLS]], left_on="date", right_on="filedAt", by="code", direction="backward", allow_exact_matches=False)
    m["daysSince"] = (m["date"] - m["filedAt"]).dt.days.astype(float)
    m.loc[m["daysSince"] > 200, list(FUND_COLS) + ["daysSince"]] = np.nan          # 200일 넘게 새 공시가 없으면 낡은 값으로 보고 결측 처리
    return m.drop(columns="filedAt")


def load_quarters(market: str) -> pd.DataFrame:
    path = ROOT / "research" / "leader_backtest" / "fund_pit" / f"{market}.csv.gz"
    return pd.read_csv(path)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    kr = [r for r in json.loads((ROOT / "docs" / "data" / "latest_kr.json").read_text(encoding="utf-8")) if r.get("status") == "OK" and r.get("code")]
    us = [r for r in json.loads((ROOT / "docs" / "data" / "latest_us.json").read_text(encoding="utf-8")) if r.get("status") == "OK" and r.get("code")]
    out = OUT_DIR / "fund_pit"
    out.mkdir(parents=True, exist_ok=True)
    report = {"ranAt": dt.datetime.now(dt.timezone.utc).isoformat()}
    only = os.environ.get("FP_ONLY", "")
    if only != "us":
        key = os.environ.get("DART_API_KEY", "").strip()
        if not key:
            raise RuntimeError("DART_API_KEY is not configured")
        dk = collect_kr([str(r["code"]).zfill(6) for r in kr], key)
        dk.to_csv(out / "kr.csv.gz", index=False)
        report["kr"] = {"rows": int(len(dk)), "codes": int(dk["code"].nunique()) if len(dk) else 0, "failedCalls": dk.attrs.get("failedCalls"),
                        "nonNull": {c: int(dk[c].notna().sum()) for c in ("revenue", "op", "net", "liabilities", "equity")} if len(dk) else {}, "sample": dk.head(3).to_dict("records") if len(dk) else []}
    if only != "kr":
        du = collect_us([str(r["code"]) for r in us])
        du.to_csv(out / "us.csv.gz", index=False)
        report["us"] = {"rows": int(len(du)), "codes": int(du["code"].nunique()) if len(du) else 0, "missing": du.attrs.get("missing"),
                        "nonNull": {c: int(du[c].notna().sum()) for c in ("revenue", "op", "net", "liabilities", "equity")} if len(du) else {}, "sample": du.head(3).to_dict("records") if len(du) else []}
    (out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=1, default=str))


if __name__ == "__main__":
    main()
