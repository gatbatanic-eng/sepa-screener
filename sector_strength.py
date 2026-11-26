"""섹터별 상대강도(RS) 집계.

SEPA 스크리너가 이미 종목마다 계산한 RS 점수(rsScore, 0~99 백분위)와 20일 변화(rsChange20d)를
섹터별로 묶어 docs/data/sector_strength.json 하나로 낸다. 가격을 새로 받지 않는다.

섹터 출처
- 한국: NH 시세의 KRX 업종(research/nhplug/kr_extra.json·kr_sector_extra.json, "코스피 전기·전자" → "전기·전자").
  NH에 없는 종목은 KRX 상장사 정보(FinanceDataReader 'KRX-DESC')의 표준산업분류(KSIC) 이름을 키워드로 KRX 업종에 맞춘다.
- 미국: S&P500 구성종목 목록(FinanceDataReader 'S&P500')의 GICS 섹터 11개.
목록 조회가 실패해도 지난번에 받은 docs/data/sector_map_{kr,us}.json 캐시로 이어간다.

섹터 지표(섹터 안 종목 기준)
- rs: RS 점수 중앙값 — 순위 기준
- rsDelta: rs − 20거래일 전 RS 중앙값(종목별 rsScore − rsChange20d) — 섹터 RS가 오르는 중인지
- strongPct: RS 80 이상 비율, trendPct: 추세템플릿 8/8 통과 비율
종목이 MIN_MEMBERS 미만인 섹터는 순위에서 빼고 '표본 적음'으로 따로 보인다.
매 실행마다 그날 섹터 RS를 history에 쌓는다(같은 기준일은 덮어씀, 최근 HISTORY_DAYS일).

    python sector_strength.py              # 섹터 목록을 새로 받아 시도(실패하면 캐시)
    python sector_strength.py --no-fetch   # 캐시만 사용(오프라인 확인용)
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import statistics
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "docs" / "data"
OUT = DATA / "sector_strength.json"
NH_FILES = [ROOT / "research" / "nhplug" / "kr_extra.json", ROOT / "research" / "nhplug" / "kr_sector_extra.json"]

MIN_MEMBERS = 3
STRONG_RS = 80
LEADERS = 5
HISTORY_DAYS = 120
MAP_MAX_AGE_DAYS = 7
UNCLASSIFIED = "미분류"

log = logging.getLogger("sector_strength")

# screening.py의 US_TICKER_OVERRIDES와 같은 보정(S&P500 목록은 점을 빼고, 시세·SEPA 결과는 하이픈 표기)
US_TICKER_OVERRIDES = {"BRKB": "BRK-B", "BFB": "BF-B"}

GICS_KO = {
    "Information Technology": "정보기술", "Health Care": "헬스케어", "Financials": "금융",
    "Consumer Discretionary": "경기소비재", "Communication Services": "커뮤니케이션", "Industrials": "산업재",
    "Consumer Staples": "필수소비재", "Energy": "에너지", "Utilities": "유틸리티", "Real Estate": "부동산",
    "Materials": "소재",
}

# KSIC 산업 이름 → KRX 업종. 위에서부터 처음 맞는 규칙을 쓴다(구체적인 것을 먼저).
KSIC_RULES = [
    (("반도체", "전자부품", "컴퓨터 및 주변", "통신 및 방송 장비", "영상 및 음향", "전기장비", "전동기", "전지", "절연선", "조명"), "전기·전자"),
    (("의약품", "의료용 물질", "생물학적"), "제약"),
    (("의료용 기기", "측정", "광학", "정밀기기"), "의료·정밀기기"),
    (("자동차", "선박", "항공기", "운송장비", "철도"), "운송장비·부품"),
    (("기계", "장비 제조"), "기계·장비"),
    (("1차 철강", "1차 비철", "금속"), "금속"),
    (("화학", "플라스틱", "고무", "화장품", "비료", "석유 정제"), "화학"),
    (("비금속",  "시멘트", "유리"), "비금속"),
    (("식료품", "음료", "담배"), "음식료·담배"),
    (("섬유", "의복", "가죽", "신발"), "섬유·의류"),
    (("종이", "목재", "펄프"), "종이·목재"),
    (("소프트웨어", "컴퓨터 프로그래밍", "정보서비스", "자료처리", "포털"), "IT 서비스"),
    (("통신업", "전기 통신"), "통신"),
    (("영화", "방송", "오디오", "창작", "예술", "스포츠", "유원지", "게임"), "오락·문화"),
    (("출판",), "출판·매체복제"),
    (("보험",), "보험"),
    (("증권", "투자"), "증권"),
    (("은행", "금융", "신탁", "여신"), "금융"),
    (("건설", "건물", "토목", "부동산 개발"), "건설"),
    (("부동산",), "부동산"),
    (("전기업", "가스", "증기", "발전"), "전기·가스"),
    (("운송", "창고", "항공 운송", "해상", "육상", "물류"), "운송·창고"),
    (("도매", "소매", "상품 중개"), "유통"),
]


def _read(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _write(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def krx_sector_name(raw) -> str | None:
    """NH 업종명 "코스피 전기·전자"/"코스닥 전기·전자" → "전기·전자". 시장 이름만 있거나 비면 None."""
    s = str(raw or "").strip()
    for p in ("코스피 ", "코스닥 "):
        if s.startswith(p):
            s = s[len(p):].strip()
    return None if s in ("", "코스피", "코스닥", "제조") else s


def ksic_to_krx(industry) -> str | None:
    s = str(industry or "")
    if not s or s == "nan":
        return None
    for keys, sector in KSIC_RULES:
        if any(k in s for k in keys):
            return sector
    return None


def _fresh(cache: dict) -> bool:
    try:
        age = dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(cache["updatedAt"])
    except (KeyError, TypeError, ValueError):
        return False
    return age.days < MAP_MAX_AGE_DAYS


def _listing(market: str):
    import FinanceDataReader as fdr  # 네트워크가 필요한 경로에서만 불러온다

    return fdr.StockListing(market)


def kr_sector_map(codes: set[str], fetch: bool, cache_path: Path = DATA / "sector_map_kr.json") -> dict[str, dict]:
    out: dict[str, dict] = {}
    for f in NH_FILES:
        for code, d in ((_read(f, {}) or {}).get("stocks") or {}).items():
            name = krx_sector_name((d or {}).get("sector"))
            if name:
                out[str(code).zfill(6)] = {"sector": name, "source": "krx"}
    cache = _read(cache_path, {}) or {}
    industry = cache.get("industry") or {}
    # 목록은 MAP_MAX_AGE_DAYS마다 한 번만 다시 받는다(업종은 거의 안 바뀌고, 매번 받으면 캐시 파일이 매일 커밋된다).
    if fetch and not _fresh(cache):
        try:
            listing = _listing("KRX-DESC")
            industry = {str(c).zfill(6): str(i) for c, i in zip(listing["Code"], listing["Industry"]) if str(i) not in ("", "nan", "None")}
            _write(cache_path, {"updatedAt": dt.datetime.now(dt.timezone.utc).isoformat(), "source": "FinanceDataReader KRX-DESC", "industry": industry})
        except Exception as exc:  # noqa: BLE001 — 목록 장애여도 캐시로 이어간다
            log.warning("KRX 상장사 업종 조회 실패 — 캐시 사용: %s", exc)
    for code in codes:
        if code not in out:
            name = ksic_to_krx(industry.get(code))
            if name:
                out[code] = {"sector": name, "source": "ksic"}
    return out


def us_sector_map(codes: set[str], fetch: bool, cache_path: Path = DATA / "sector_map_us.json") -> dict[str, dict]:
    cache = _read(cache_path, {}) or {}
    sectors = cache.get("sector") or {}
    if fetch and not _fresh(cache):
        try:
            listing = _listing("S&P500")
            got = {US_TICKER_OVERRIDES.get(str(s), str(s).replace(".", "-")): str(g) for s, g in zip(listing["Symbol"], listing["Sector"]) if str(g) not in ("", "nan")}
            if got:
                sectors = got
                _write(cache_path, {"updatedAt": dt.datetime.now(dt.timezone.utc).isoformat(), "source": "FinanceDataReader S&P500 (GICS)", "sector": sectors})
        except Exception as exc:  # noqa: BLE001
            log.warning("S&P500 섹터 조회 실패 — 캐시 사용: %s", exc)
    return {c: {"sector": GICS_KO.get(sectors[c], sectors[c]), "source": "gics"} for c in codes if c in sectors}


def _num(v):
    return v if isinstance(v, (int, float)) and v == v else None  # NaN 제외


def _pct(part: int, whole: int) -> float | None:
    return round(100 * part / whole, 1) if whole else None


def aggregate(rows: list[dict], sector_of: dict[str, dict]) -> dict:
    """종목 행 → 섹터별 지표. rsScore가 없는 종목(확인불가 등)은 뺀다."""
    groups: dict[str, list[dict]] = {}
    unclassified = 0
    for r in rows:
        if _num(r.get("rsScore")) is None:
            continue
        sec = (sector_of.get(str(r.get("code"))) or {}).get("sector")
        if not sec:
            unclassified += 1
            sec = UNCLASSIFIED
        groups.setdefault(sec, []).append(r)

    sectors = []
    for name, members in groups.items():
        rs = [r["rsScore"] for r in members]
        past = [r["rsScore"] - r["rsChange20d"] for r in members if _num(r.get("rsChange20d")) is not None]
        med = statistics.median(rs)
        leaders = sorted(members, key=lambda r: -r["rsScore"])[:LEADERS]
        sectors.append({
            "sector": name,
            "n": len(members),
            "rs": round(med, 1),
            "rsDelta": round(med - statistics.median(past), 1) if past else None,
            "strongPct": _pct(sum(1 for x in rs if x >= STRONG_RS), len(rs)),
            "trendPct": _pct(sum(1 for r in members if r.get("passAll")), len(members)),
            "small": len(members) < MIN_MEMBERS or name == UNCLASSIFIED,
            "leaders": [{"code": r.get("code"), "name": r.get("name"), "rs": round(r["rsScore"], 1),
                         "rsChange20d": _num(r.get("rsChange20d")), "passAll": bool(r.get("passAll")),
                         "changePct": _num(r.get("changePct"))} for r in leaders],
        })
    ranked = sorted((s for s in sectors if not s["small"]), key=lambda s: (-s["rs"], s["sector"]))
    for i, s in enumerate(ranked, 1):
        s["rank"] = i
    rest = sorted((s for s in sectors if s["small"]), key=lambda s: (s["sector"] == UNCLASSIFIED, -s["rs"]))
    counted = sum(s["n"] for s in sectors)
    return {"sectors": ranked + rest, "stocks": counted, "unclassified": unclassified,
            "coveragePct": _pct(counted - unclassified, counted)}


def update_history(history: list[dict], date: str, sectors: list[dict]) -> list[dict]:
    """같은 기준일 항목은 바꾸고, 날짜순으로 최근 HISTORY_DAYS개만 남긴다."""
    snap = {"date": date, "rs": {s["sector"]: s["rs"] for s in sectors if not s["small"]}}
    kept = [h for h in history if h.get("date") != date]
    return sorted(kept + [snap], key=lambda h: h["date"])[-HISTORY_DAYS:]


def attach_rank_change(sectors: list[dict], history: list[dict], date: str) -> None:
    """직전 기준일 대비 순위 변화(+면 올라감). 직전 기록이 없으면 None."""
    prev = [h for h in history if h["date"] < date]
    if not prev:
        return
    order = sorted(prev[-1]["rs"].items(), key=lambda kv: (-kv[1], kv[0]))
    prev_rank = {name: i for i, (name, _) in enumerate(order, 1)}
    for s in sectors:
        if "rank" in s and s["sector"] in prev_rank:
            s["rankChange"] = prev_rank[s["sector"]] - s["rank"]


def build_market(market: str, fetch: bool, previous: dict) -> dict | None:
    rows = _read(DATA / f"latest_{market}.json", None)
    if not rows:
        log.warning("%s: latest_%s.json 없음 — 건너뜀", market, market)
        return None
    hist = _read(DATA / f"history_{market}.json", []) or []
    date = hist[-1]["date"] if hist and hist[-1].get("date") else dt.date.today().isoformat()
    codes = {str(r.get("code")) for r in rows if r.get("code")}
    sector_of = kr_sector_map(codes, fetch) if market == "kr" else us_sector_map(codes, fetch)
    result = aggregate(rows, sector_of)
    history = update_history((previous.get(market) or {}).get("history") or [], date, result["sectors"])
    attach_rank_change(result["sectors"], history, date)
    sources = sorted({v["source"] for v in sector_of.values()})
    return {"date": date, "source": {"kr": "KRX 업종(NH 시세) · 부족분은 KSIC 산업분류로 보완",
                                     "us": "S&P500 GICS 섹터"}[market], "sectorSources": sources,
            **result, "history": history}


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--no-fetch", action="store_true", help="섹터 목록을 새로 받지 않고 캐시만 사용")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    previous = (_read(OUT, {}) or {}).get("markets") or {}
    markets = {}
    for m in ("kr", "us"):
        built = build_market(m, not args.no_fetch, previous)
        markets[m] = built if built else previous.get(m)
    markets = {k: v for k, v in markets.items() if v}
    if not markets:
        raise SystemExit("섹터 강도: 읽을 SEPA 결과가 없음")
    _write(OUT, {"schemaVersion": 1, "generatedAt": dt.datetime.now(dt.timezone.utc).isoformat(),
                 "rules": {"minMembers": MIN_MEMBERS, "strongRs": STRONG_RS, "deltaDays": 20}, "markets": markets})
    for m, d in markets.items():
        top = ", ".join(f"{s['sector']}({s['rs']})" for s in d["sectors"][:3])
        log.info("%s %s: 섹터 %d개, 분류율 %s%% — 상위 %s", m, d["date"], len(d["sectors"]), d.get("coveragePct"), top)


if __name__ == "__main__":
    main()
