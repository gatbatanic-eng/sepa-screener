"""NHPLUG 전종목 순위 대체 가능성 점검. (python -m nhplug.probe_rank [sample|full|top|antc])
NHPLUG에는 순위 조회가 없어(공개 명세 확인) 전종목 현재가(/krstock/quote/v1/currentPrice)를 직접 받아 순위를 만들 수 있는지 본다.
조회 전용: 시세 경로(probe.ALLOWED)만 부르고 주문·계좌 경로는 코드에 없다. 토큰·키는 결과에 남기지 않는다.
확인 대상: ① 거래비율(vol_rate)의 뜻 ② 예상체결가(antc_cnpr) 제공 여부 ③ 전종목 1회 소요 시간·유량 초과 ④ 종목 목록 출처
⑤ KRX/NXT/UNT 응답 차이 ⑥ 네이버 거래대금 순위와의 일치도. 결과: research/nhplug/probe/rank_probe.json (연구용, 매수 신호 아님)."""
from __future__ import annotations

import datetime as dt
import json
import statistics
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import probe

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "research" / "nhplug" / "probe" / "rank_probe.json"
OUT_ANTC = ROOT / "research" / "nhplug" / "probe" / "rank_probe_antc.json"
OUT_TOP = ROOT / "research" / "nhplug" / "probe" / "rank_probe_top.json"
KST = dt.timezone(dt.timedelta(hours=9))
PATH = "/krstock/quote/v1/currentPrice"
GAP = 0.22          # 명세 권고 0.2초 이상, 초당 5회 수준
MAX_STREAK = 30     # 연속 실패가 이만큼이면 중단(유량·장애)
SAMPLES = {"005930": "삼성전자(코스피)", "247540": "에코프로비엠(코스닥)", "069500": "KODEX 200(ETF)",
           "005935": "삼성전자우(우선주)", "000660": "SK하이닉스"}


def num(v):
    try:
        return float(str(v).replace(",", ""))
    except (TypeError, ValueError):
        return None


def fetch(tok, code, mkt="KRX"):
    """한 종목 현재가. (행, 오류) 중 하나만 채워진다."""
    try:
        res = probe.call(tok, PATH, {"market_cd": mkt, "iem_cd": code})
    except Exception as e:  # noqa: BLE001 - 점검용: 사유만 남긴다
        return None, {"kind": type(e).__name__, "http": None}
    data = res.get("data") or {}
    o0 = data.get("Output_0")
    if res.get("http") != 200 or not isinstance(o0, dict) or num(o0.get("stck_prpr")) is None:
        return None, {"kind": "no_output", "http": res.get("http"), "rsp_cd": res.get("rsp_cd"), "rsp_msg": str(res.get("rsp_msg"))[:80]}
    o2 = data.get("Output_2") if isinstance(data.get("Output_2"), dict) else {}
    return {"code": code, "name": o0.get("iem_nm"), "mkt": o0.get("mrkt_div_isnm"), "grp": o0.get("scrt_grp_isnm"),
            "price": num(o0.get("stck_prpr")), "pct": num(o0.get("prdy_ctrt")), "vol": num(o0.get("acml_vol")),
            "tr": num(o0.get("acml_tr_pbmn")), "pvol": num(o0.get("prdy_vol")), "ptr": num(o0.get("prdy_tr_pbmn")),
            "volRate": num(o0.get("vol_rate")), "alarm": o0.get("mrkt_alrm_code"), "overheat": o0.get("short_over_code"),
            "w52h": num(o0.get("w52_hgpr")), "d20h": num(o0.get("d20_hgpr")), "cap": num(o0.get("hts_avls")),
            "status": o0.get("market_status"), "hogaTime": o0.get("hoga_bsop_hour"),
            "ask1": num(o0.get("askp1")), "bid1": num(o0.get("bidp1")), "seln1": o0.get("seln_mbcr_name1"),
            "antcPrice": num(o2.get("antc_cnpr")), "antcVol": num(o2.get("antc_vol")),
            "ovtmPrice": num(o2.get("ovtm_untp_prpr"))}, None


def universe():
    """(코드, 이름, 시장, 네이버 거래대금) 목록과 출처. 네이버 실패 시 감시 목록 700종목으로 대체."""
    try:
        import naver_listing
        df = naver_listing.naver_kr_listing()
        rows = [(r.Code, r.Name, r.Market, None if r.Amount != r.Amount else float(r.Amount)) for r in df.itertuples()]
        return rows, "naver-listing"
    except Exception as e:  # noqa: BLE001
        rows_ = json.loads((ROOT / "docs" / "data" / "latest_kr.json").read_text(encoding="utf-8"))
        return [(r["code"], r.get("name"), r.get("market"), None) for r in rows_ if r.get("code")], f"latest_kr.json (네이버 실패: {type(e).__name__})"


def pick(rows, scan):
    if scan == "full":
        return [r[0] for r in rows]
    ranked = sorted((r for r in rows if r[3]), key=lambda r: -r[3])
    head = [r[0] for r in ranked[:60]]
    rest = [r[0] for r in rows if r[0] not in set(head)]
    step = max(1, len(rest) // 40)
    return head + rest[::step][:40]


def ratio_stats(rows):
    """거래비율이 무엇인지 추정하기 위해 후보 정의와의 비율을 본다. 일치하는 정의가 없으면 미확인으로 둔다."""
    cands = {"vol/pvol*100": lambda r: r["vol"] / r["pvol"] * 100 if r["pvol"] else None,
             "tr/ptr*100": lambda r: r["tr"] / r["ptr"] * 100 if r["ptr"] else None,
             "vol/pvol": lambda r: r["vol"] / r["pvol"] if r["pvol"] else None}
    out = {}
    for name, f in cands.items():
        rs = []
        for r in rows:
            c = f(r) if r["vol"] is not None else None
            if c and r["volRate"]:
                rs.append(r["volRate"] / c)
        out[name] = ({"n": len(rs), "medianRatio": round(statistics.median(rs), 4),
                      "p10": round(sorted(rs)[len(rs) // 10], 4), "p90": round(sorted(rs)[len(rs) * 9 // 10], 4)} if len(rs) >= 5 else {"n": len(rs)})
    ut = [r["tr"] / (r["price"] * r["vol"]) for r in rows if r["tr"] and r["price"] and r["vol"]]
    out["trUnit(tr/(price*vol))"] = {"n": len(ut), "median": round(statistics.median(ut), 4)} if ut else {"n": 0}
    return out


def tops(rows, key, n, reverse=True):
    rs = [r for r in rows if r.get(key) is not None]
    rs.sort(key=lambda r: r[key], reverse=reverse)
    return [{k: r[k] for k in ("code", "name", "mkt", "grp", "price", "pct", "tr", "volRate", "alarm")} for r in rs[:n]]


def main(scan="sample"):
    started = time.time()
    tok = probe.token()
    probe.GAP = GAP
    res = {"ranAt": dt.datetime.now(KST).isoformat(), "scan": scan, "gapSeconds": GAP}
    # ⑤ 같은 종목의 KRX/NXT/UNT 응답 비교 + ② 예상체결 필드 + 호가·거래원
    res["marketCompare"] = {}
    for code, label in SAMPLES.items():
        res["marketCompare"][code] = {"label": label}
        for mkt in ("KRX", "NXT", "UNT"):
            row, err = fetch(tok, code, mkt)
            res["marketCompare"][code][mkt] = row or {"error": err}
    # ④ 종목 목록
    uni, source = universe()
    res["universe"] = {"source": source, "count": len(uni), "kospi": sum(1 for r in uni if str(r[2]).upper() == "KOSPI"),
                       "kosdaq": sum(1 for r in uni if str(r[2]).upper() == "KOSDAQ")}
    codes = pick(uni, scan)
    # ③ 전종목(또는 표본) 현재가 수집
    got, errs, streak, t0 = [], [], 0, time.time()
    for i, code in enumerate(codes):
        row, err = fetch(tok, code, "KRX")
        if row:
            got.append(row)
            streak = 0
        else:
            errs.append({"code": code, **err})
            streak += 1
            if streak >= MAX_STREAK:
                res["aborted"] = f"연속 {MAX_STREAK}건 실패로 {i + 1}번째에서 중단"
                break
        if (i + 1) % 200 == 0:
            print(f"{i + 1}/{len(codes)} 완료, 성공 {len(got)}, 실패 {len(errs)}, {time.time() - t0:.0f}초", flush=True)
    el = time.time() - t0
    kinds = {}
    for e in errs:
        k = f"{e.get('kind')}/{e.get('http')}/{e.get('rsp_cd')}"
        kinds[k] = kinds.get(k, 0) + 1
    res["throughput"] = {"requested": len(codes), "ok": len(got), "failed": len(errs), "elapsedSeconds": round(el, 1),
                         "secondsPerCall": round(el / max(1, len(got) + len(errs)), 3), "errorKinds": kinds, "errorSamples": errs[:5],
                         "extrapolatedFullUniverseMinutes": round(el / max(1, len(got) + len(errs)) * len(uni) / 60, 1)}
    res["dataStatus"] = {"marketStatus": sorted({str(r["status"]) for r in got}), "latestHogaTime": max((str(r["hogaTime"]) for r in got if r["hogaTime"]), default=None),
                         "antcFilled": sum(1 for r in got if r["antcPrice"]), "ovtmFilled": sum(1 for r in got if r["ovtmPrice"]),
                         "volRateFilled": sum(1 for r in got if r["volRate"] is not None), "pvolFilled": sum(1 for r in got if r["pvol"]),
                         "groups": {g: sum(1 for r in got if r["grp"] == g) for g in sorted({str(r["grp"]) for r in got})}}
    # ① 거래비율 정의 추정
    res["volRateDefinition"] = ratio_stats(got)
    # ⑥ 순위: 직접 계산한 상위와 네이버 거래대금 상위 비교
    nv = {r[0]: r[3] for r in uni if r[3]}
    mine = {r["code"] for r in sorted((r for r in got if r["tr"]), key=lambda r: -r["tr"])[:50]}
    naver50 = {c for c, _ in sorted(nv.items(), key=lambda kv: -kv[1])[:50]}
    res["rankCheck"] = {"top50Overlap": len(mine & naver50) if scan == "full" else None,
                        "note": "full 모드에서만 의미 있음. 거래대금 단위·시각이 달라 완전 일치는 기대하지 않음" if scan != "full" else "상위 50 교집합 개수"}
    res["tops"] = {"turnover": tops(got, "tr", 50), "pctUp": tops([r for r in got if (r["tr"] or 0) >= 5e9], "pct", 30),
                   "volRate": tops([r for r in got if (r["tr"] or 0) >= 5e9], "volRate", 30)} if scan == "full" else {}
    res["totalSeconds"] = round(time.time() - started, 1)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    print("저장:", OUT)
    print(json.dumps({k: res[k] for k in ("universe", "throughput", "dataStatus", "volRateDefinition", "rankCheck")}, ensure_ascii=False, indent=1))


class Limiter:
    """호출 시작 간격을 전체 스레드에 걸쳐 일정 이상으로 유지한다(명세: REST 초당 5회 수준)."""

    def __init__(self, per_sec):
        self.gap, self.lock, self.next = 1.0 / per_sec, threading.Lock(), 0.0

    def wait(self):
        with self.lock:
            now = time.monotonic()
            at = max(now, self.next)
            self.next = at + self.gap
        if at > now:
            time.sleep(at - now)


def run_stage(tok, codes, workers, lim):
    def one(code):
        lim.wait()
        t = time.monotonic()
        row, err = fetch(tok, code, "KRX")
        return code, row, err, time.monotonic() - t

    t0, start = time.monotonic(), dt.datetime.now(KST).isoformat()
    with ThreadPoolExecutor(max_workers=workers) as ex:
        out = list(ex.map(one, codes))
    el = time.monotonic() - t0
    lat = sorted(o[3] for o in out)
    kinds = {}
    for _, row, err, _ in out:
        if not row:
            k = f"{err.get('kind')}/{err.get('http')}/{err.get('rsp_cd')}/{err.get('rsp_msg')}"
            kinds[k] = kinds.get(k, 0) + 1
    ok = [o[1] for o in out if o[1]]
    return ok, {"workers": workers, "requested": len(codes), "ok": len(ok), "failed": len(codes) - len(ok), "startedAt": start,
                "elapsedSeconds": round(el, 1), "callsPerSecond": round(len(codes) / el, 2) if el else None,
                "medianLatency": round(statistics.median(lat), 2) if lat else None, "p90Latency": round(lat[len(lat) * 9 // 10], 2) if lat else None,
                "errorKinds": kinds}


def spearman(a, b):
    def ranks(v):
        order = sorted(range(len(v)), key=lambda i: v[i])
        r = [0.0] * len(v)
        for pos, i in enumerate(order):
            r[i] = pos
        return r
    ra, rb = ranks(a), ranks(b)
    n = len(a)
    if n < 5:
        return None
    ma, mb = sum(ra) / n, sum(rb) / n
    cov = sum((x - ma) * (y - mb) for x, y in zip(ra, rb))
    va, vb = sum((x - ma) ** 2 for x in ra), sum((x - mb) ** 2 for x in rb)
    return round(cov / (va * vb) ** 0.5, 3) if va and vb else None


def main_top(pool_n=150):
    """네이버 거래대금 상위로 후보를 잡고(발견), NHPLUG로 확인한다. 병렬 호출 한도·거래대금 일치도·상위 밖 누락도 본다."""
    tok = probe.token()
    probe.GAP = 0.0
    lim = Limiter(5.0)
    res = {"ranAt": dt.datetime.now(KST).isoformat(), "scan": "top", "poolSize": pool_n, "limiterPerSecond": 5.0}
    uni, source = universe()
    nv = {r[0]: (r[1], r[3]) for r in uni if r[3]}
    ranked = [c for c, _ in sorted(nv.items(), key=lambda kv: -kv[1][1])]
    res["universe"] = {"source": source, "count": len(uni), "withAmount": len(nv)}
    pool, leak = ranked[:pool_n], ranked[pool_n:pool_n + 250:5]
    rows, stages = [], []
    third = pool_n // 3
    plan = [(3, pool[:third]), (5, pool[third:2 * third])]
    for w, codes in plan:
        ok, st = run_stage(tok, codes, w, lim)
        rows += ok
        stages.append(st)
    clean = all(s["failed"] == 0 for s in stages)
    w3 = 6 if clean else 3     # 앞 단계가 깨끗하면 6까지만 올린다(5/초 한도를 약간 넘는 수준에서 멈춤)
    ok, st = run_stage(tok, pool[2 * third:], w3, lim)
    rows += ok
    stages.append(st)
    ok, st = run_stage(tok, leak, 5, lim)
    leak_rows = ok
    st["purpose"] = "상위 밖 누락 점검(네이버 순위 151~400위 5개마다)"
    stages.append(st)
    res["stages"] = stages
    # 거래대금 일치도(네이버 Amount ↔ NHPLUG acml_tr_pbmn 백만원)
    pair = [(nv[r["code"]][1], r["tr"] * 1e6, r) for r in rows if r["tr"]]
    ratios = sorted(b / a for a, b, _ in pair if a)
    q = lambda f: round(ratios[int(len(ratios) * f)], 3) if ratios else None  # noqa: E731
    res["turnoverAgreement"] = {"n": len(ratios), "nhOverNaverMedian": round(statistics.median(ratios), 3) if ratios else None, "p10": q(0.1), "p90": q(0.9)}
    byn = sorted(pair, key=lambda t: -t[0])
    byh = sorted(pair, key=lambda t: -t[1])
    n50 = min(50, len(pair))
    res["rankAgreementWithinPool"] = {"top50Overlap": len({t[2]["code"] for t in byn[:n50]} & {t[2]["code"] for t in byh[:n50]}),
                                      "spearman": spearman([t[0] for t in pair], [t[1] for t in pair])}
    # 상위 밖 누락: 풀의 50번째 거래대금보다 큰 종목이 풀 밖 표본에 있는가
    thr = byh[n50 - 1][1] if n50 else None
    above = [{"code": r["code"], "name": r["name"], "naverRank": ranked.index(r["code"]) + 1, "nhTurnoverWon": r["tr"] * 1e6, "pct": r["pct"]}
             for r in leak_rows if thr and r["tr"] and r["tr"] * 1e6 >= thr]
    res["leakCheck"] = {"sampled": len(leak_rows), "thresholdWonPoolTop50": thr, "sampledAboveThreshold": len(above), "items": above[:10]}
    res["dataStatus"] = {"antcNonZero": sum(1 for r in rows if r["antcPrice"]), "antcSample": [{k: r[k] for k in ("code", "name", "price", "antcPrice", "antcVol")} for r in rows if r["antcPrice"]][:3],
                         "alarm": sum(1 for r in rows if r["alarm"] not in (None, "", "0")), "overheat": sum(1 for r in rows if r["overheat"] not in (None, "", "0")),
                         "groups": {g: sum(1 for r in rows if r["grp"] == g) for g in sorted({str(r["grp"]) for r in rows})},
                         "latestHogaTime": max((str(r["hogaTime"]) for r in rows if r["hogaTime"]), default=None)}
    res["tops"] = {"turnover": tops(rows, "tr", 30), "pctUp": tops([r for r in rows if (r["tr"] or 0) >= 5000], "pct", 15),
                   "volRate": tops([r for r in rows if (r["tr"] or 0) >= 5000], "volRate", 15)}
    OUT_TOP.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    print("저장:", OUT_TOP)
    print(json.dumps({k: res[k] for k in ("stages", "turnoverAgreement", "rankAgreementWithinPool", "leakCheck", "dataStatus")}, ensure_ascii=False, indent=1))


def main_antc(n=10, rounds=4, wait=180):
    """종가 단일가 구간(15:20~15:30)의 예상체결가 필드 점검. 네이버 거래대금 상위 n종목만 순차(호출 간 1초) 조회한다.
    정기 수집과 같은 앱 키를 쓰므로 호출량을 최소로 한다. rounds회를 wait초 간격으로 반복해 장중→단일가→마감 후 변화를 본다."""
    tok = probe.token()
    probe.GAP = 1.0
    uni, source = universe()
    codes = [c for c, _ in sorted(((r[0], r[3]) for r in uni if r[3]), key=lambda t: -t[1])[:n]]
    res = {"ranAt": dt.datetime.now(KST).isoformat(), "scan": "antc", "universe": source, "codes": codes, "rounds": []}
    for i in range(rounds):
        if i:
            time.sleep(wait)
        rd = {"startedAt": dt.datetime.now(KST).isoformat(), "items": []}
        for code in codes:
            try:
                r = probe.call(tok, PATH, {"market_cd": "KRX", "iem_cd": code})
                d = r.get("data") or {}
                o0, o2 = d.get("Output_0") or {}, d.get("Output_2") or {}
                rd["items"].append({"code": code, "name": o0.get("iem_nm"), "http": r.get("http"), "rsp_cd": r.get("rsp_cd"), "price": o0.get("stck_prpr"),
                                    "marketStatus": o0.get("market_status"), "hogaTime": o0.get("hoga_bsop_hour"), "ask1": o0.get("askp1"), "bid1": o0.get("bidp1"),
                                    "output2": o2})
            except Exception as e:  # noqa: BLE001
                rd["items"].append({"code": code, "error": type(e).__name__})
        rd["antcNonZero"] = sum(1 for x in rd["items"] if num((x.get("output2") or {}).get("antc_cnpr")))
        rd["endedAt"] = dt.datetime.now(KST).isoformat()
        res["rounds"].append(rd)
        OUT_ANTC.parent.mkdir(parents=True, exist_ok=True)
        OUT_ANTC.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
        print(rd["startedAt"], "antc 채워진 종목", rd["antcNonZero"], "/", len(codes), flush=True)


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "sample"
    main_antc() if mode == "antc" else main_top() if mode == "top" else main(mode)
