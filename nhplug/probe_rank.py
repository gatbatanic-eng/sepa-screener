"""NHPLUG 전종목 순위 대체 가능성 점검. (python -m nhplug.probe_rank [sample|full])
NHPLUG에는 순위 조회가 없어(공개 명세 확인) 전종목 현재가(/krstock/quote/v1/currentPrice)를 직접 받아 순위를 만들 수 있는지 본다.
조회 전용: 시세 경로(probe.ALLOWED)만 부르고 주문·계좌 경로는 코드에 없다. 토큰·키는 결과에 남기지 않는다.
확인 대상: ① 거래비율(vol_rate)의 뜻 ② 예상체결가(antc_cnpr) 제공 여부 ③ 전종목 1회 소요 시간·유량 초과 ④ 종목 목록 출처
⑤ KRX/NXT/UNT 응답 차이 ⑥ 네이버 거래대금 순위와의 일치도. 결과: research/nhplug/probe/rank_probe.json (연구용, 매수 신호 아님)."""
from __future__ import annotations

import datetime as dt
import json
import statistics
import sys
import time
from pathlib import Path

from . import probe

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "research" / "nhplug" / "probe" / "rank_probe.json"
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


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "sample")
