"""personas/logic.py — 페르소나별 판단 논리구조 (규칙 기반 증거 패킷).

목적
----
추세 통과 종목 한 개에 대해 7개 페르소나가 각자의 렌즈로 **근거(support) · 우려(concern) ·
체크포인트(check)** 를 뽑는다. 매수/매도 결론은 내지 않는다(형식 자체가 없다).

이 모듈은 LLM을 부르지 않는다. 여기서 만든 증거 패킷의 수치·문장만이 이후 LLM 단계의
유일한 근거가 되어야 한다(환각 방지). API 키가 없을 때의 폴백도 이 패킷 그대로다.

구조
----
- 입력: 스크리너 결과 row(dict, docs/data/latest_*.json 한 줄) + 재무 JSON(선택)
- 각 페르소나 = 함수 하나. 필요한 값이 없으면 규칙을 건너뛰고 ``dataGaps`` 에 기록한다.
- 모든 임계값은 아래 ``THRESHOLDS`` 한 곳에 있다(초기값, 실제 분포를 보고 조정 예정).
- ``severity``: 1 참고 / 2 주의 / 3 경고. 우려 정렬과 이후 강조에 쓰인다.

7개 페르소나
------------
trend       추세추종     이평 정배열·RS·고점 근접·시장 국면·청산 경고
technical   기술적       피벗 구간·수축(VCP 근사)·거래량 dry-up·돌파 확인 품질
quant       퀀트         RS 순위/가속·유동성·변동성·이익 품질(재무) — 통계적 렌즈
value       가치투자     PER/PBR·이익 안정성·부채·현금흐름 (재무 필요)
growth      성장주       매출/이익 YoY·가속·연속 성장·주가-실적 괴리 (재무 필요)
risk        리스크관리   구조적 손절폭·시장 권장비중·변동성·유동성·참고 비중 계산
contrarian  반론가       위 논리가 틀릴 수 있는 지점 + 반증 조건(무엇이 확인되면 반론이 약해지나)

한계
----
- 재무 파일이 없는 종목은 value/growth 를 "데이터 없음"으로 처리한다(현재 추세통과 종목은 한/미 모두 재무 파일 있음).
- 한국 코드는 숫자로 저장돼 앞자리 0이 빠진 경우가 있어 ``normalize_code`` 로 6자리 정규화한다.
- 업종 평균, 실적 발표일, 뉴스/이벤트, 컨센서스는 데이터에 없다 → 체크포인트로만 남긴다.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from personas.fund_metrics import CONSEC_GROWTH_PCT, fund_metrics

# ---------------------------------------------------------------------------
# 임계값 (초기값 — 분포를 본 뒤 조정)
# ---------------------------------------------------------------------------
THRESHOLDS: dict[str, Any] = {
    # 추세
    "ext_caution": 0.15, "ext_warn": 0.25,          # 종가/SMA50 - 1
    "near_high": 0.85,                               # 52주 고점 근접(리더 등급)
    "rs_leader": 90.0, "rs_pass": 80.0,
    "rs_chg_up": 10.0, "rs_chg_dn": -10.0, "rs_chg_dn_hard": -20.0,
    "breadth_weak": 0.30, "breadth_strong": 0.60,
    # 셋업(기술적)
    "atrc_good": 0.75, "atrc_expand": 1.25,      # 추세통과 60종목 분포: p50 1.11 / p75 1.23
    "dryup_good": 0.70, "dryup_none": 1.20,      # p50 0.98 / p75 1.19
    "base_min": 20.0,
    "range10_tight": 8.0, "range10_loose": 25.0,    # p25 8.9 / p75 27.5
    "bo_vol": 1.4, "bo_clv": 0.70,
    "setupq_good": 55.0,                              # p50 49 / p90 56 (합성 점수라 70은 사실상 도달 불가)
    # 퀀트/리스크
    "atr_pct_high": 0.06, "atr_pct_low": 0.02,       # ATR20/종가 (p50 4.3% / p75 6.8%)
    "liq_min": {"kr": 5e9, "us": 5e7},               # 20일 평균 거래대금(원/달러)
    "liq_good": {"kr": 2e10, "us": 2e8},
    "risk_ok": 7.0, "risk_high": 15.0,               # 구조적 손절폭 % (p50 12 / 7%는 sepa 하드룰)
    "size_ok": 0.8, "size_low": 0.6, "size_very_low": 0.3,
    "risk_per_trade_pct": 1.0,                       # 참고 계산용 가정(계좌 대비 1건당 리스크 %)
    # 재무(value/growth)
    "per_ok": {"us": 25.0, "kr": 12.0},
    "per_high": {"us": 40.0, "kr": 25.0},
    "per_extreme": {"us": 70.0, "kr": 40.0},
    "pbr_high": {"kr": 3.0},
    "de_ok": {"us": 150.0, "kr": 100.0},
    "de_high": {"us": 300.0, "kr": 200.0},
    "opm_good": 15.0, "opm_low": 5.0, "margin_chg_pp": 2.0,
    "ocf_ni_good": 0.9, "ocf_ni_bad": 0.5,
    "g_strong": 25.0, "g_weak": 10.0,
    "stale_days": 150,
}

PERSONAS: dict[str, dict[str, str]] = {
    "trend": {"name": "추세추종 투자자", "lens": "가격이 상승 추세에 있고 시장이 받쳐주는 한 추세를 따른다. 추세가 꺾이는 신호를 가장 중시한다."},
    "technical": {"name": "기술적 분석가", "lens": "피벗 구간, 변동성·거래량 수축, 돌파 확인의 품질로 진입 구조를 본다."},
    "quant": {"name": "퀀트 투자자", "lens": "순위·수치·통계로 본다. 서사보다 팩터 강도, 유동성, 변동성 대비 위험을 본다."},
    "value": {"name": "가치 투자자", "lens": "이익의 지속성·재무 건전성·현금흐름과 지불하는 가격(PER/PBR)을 본다."},
    "growth": {"name": "성장주 투자자", "lens": "매출·이익의 성장률과 가속, 주가가 실적을 따라오는지를 본다(CAN SLIM 관점)."},
    "risk": {"name": "리스크 관리자", "lens": "얼마를 잃을 수 있고 얼마나 담아도 되는지를 본다. 손절 기준과 시장 환경이 우선이다."},
    "contrarian": {"name": "반론가", "lens": "낙관 논리가 틀릴 수 있는 지점을 찾고, 무엇이 확인되면 반론이 약해지는지 명시한다."},
}

DISCLAIMER = "규칙 기반 참고 정보이며 매수·매도 권유가 아닙니다. 최종 판단과 책임은 본인에게 있습니다."


# ---------------------------------------------------------------------------
# 데이터 구조
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Finding:
    id: str
    kind: str            # "support" | "concern" | "check"
    text: str
    severity: int = 1    # 1 참고 / 2 주의 / 3 경고
    metrics: dict = field(default_factory=dict)


def _S(id_: str, text: str, sev: int = 1, **m: Any) -> Finding:
    return Finding(id_, "support", text, sev, m)


def _C(id_: str, text: str, sev: int = 2, **m: Any) -> Finding:
    return Finding(id_, "concern", text, sev, m)


def _K(id_: str, text: str, **m: Any) -> Finding:
    return Finding(id_, "check", text, 1, m)


def normalize_code(market: str, code: Any) -> str:
    """한국 종목코드를 6자리 문자열로 맞춘다.

    latest_kr.json 의 code 가 숫자로 저장되어 앞자리 0이 사라진 경우(000500 → 500)가 있어,
    재무 파일명(000500.json)과 대조할 때 반드시 이 함수를 거쳐야 한다.
    """
    text = str(code).strip()
    if text.endswith(".0"):
        text = text[:-2]
    return text.zfill(6) if market.lower() == "kr" and text.isdigit() else text


def _f(row: dict, key: str) -> Optional[float]:
    v = row.get(key)
    if isinstance(v, bool) or v is None or v == "":
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def _b(row: dict, key: str) -> Optional[bool]:
    v = row.get(key)
    if isinstance(v, bool):
        return v
    if isinstance(v, str) and v.strip():
        return v.strip().upper() == "TRUE"
    return None


def _s(row: dict, key: str) -> Optional[str]:
    v = row.get(key)
    return str(v) if v not in (None, "") else None


class Ctx:
    """한 종목 평가에 필요한 값과 헬퍼. 규칙은 이 객체만 본다."""

    def __init__(self, row: dict, fm: Optional[dict], T: dict, context: dict, yv: Optional[dict] = None):
        self.row, self.fm, self.T, self.context, self.yv = row, fm, T, context, yv
        mk = str(row.get("market") or "").upper()
        self.market = "us" if mk == "US" else "kr"
        self.gaps: list[str] = []

    def f(self, k: str) -> Optional[float]:
        return _f(self.row, k)

    def b(self, k: str) -> Optional[bool]:
        return _b(self.row, k)

    def s(self, k: str) -> Optional[str]:
        return _s(self.row, k)

    def px(self, x: Optional[float]) -> str:
        if x is None:
            return "-"
        return f"{x:,.0f}" if self.market == "kr" else f"{x:,.2f}"

    def gap(self, what: str) -> None:
        if what not in self.gaps:
            self.gaps.append(what)

    def thr(self, key: str) -> float:
        v = self.T[key]
        return v[self.market] if isinstance(v, dict) else v

    def per(self) -> dict:
        """PER 과 출처. 공시 기반이 있으면 우선, 없으면 Yahoo(외부). 어느 쪽도 없으면 value=None."""
        filing = self.fm["valuation"].get("per") if self.fm else None
        yv = self.yv
        yper = yv.get("trailingPE") if yv else None
        if filing is not None:
            label = "최근 4분기 EPS(공시)" if self.market == "us" else "시가총액/최근 4분기 순이익(공시)"
            if yper is not None and abs(yper / filing - 1) > 0.25:
                self.gap(f"PER 출처 간 불일치: 공시 기반 {filing:.1f} vs Yahoo {yper:.1f} — 공시 기반 값을 사용")
            return {"value": filing, "source": "filing", "label": label, "note": None}
        if yper is not None:
            if yv.get("stale"):
                self.gap(f"Yahoo 밸류에이션 갱신 실패로 이전 값 사용({(yv.get('fetchedAt') or '')[:10]} 기준)")
            if yv.get("peInconsistent"):
                self.gap("Yahoo trailingPE 와 현재가/EPS 가 크게 어긋남 — PER 신뢰도 낮음")
            yc, close = yv.get("currentPrice"), self.f("close")
            if yc and close and abs(yc / close - 1) > 0.10:
                self.gap(f"Yahoo 현재가({yc:,.2f})와 스크리너 종가({close:,.2f})가 10% 넘게 다름 — 기준 시점 차이 가능")
            return {"value": yper, "source": "yahoo", "label": "Yahoo trailingPE(외부 데이터, 공시 아님)", "note": None}
        note = self.fm["valuation"].get("perNote") if self.fm else None
        if yv and yv.get("lossMaking"):
            note = "Yahoo 기준 최근 EPS 적자 — PER 없음(정상)"
        return {"value": None, "source": None, "label": None, "note": note}

    def ext50(self) -> Optional[float]:
        close, s50 = self.f("close"), self.f("sma50")
        return close / s50 - 1 if close and s50 else None

    def atr_pct(self) -> Optional[float]:
        atr, close = self.f("atr20"), self.f("close")
        return atr / close if atr and close else None

    def need_fund(self, persona_label: str) -> bool:
        """재무가 없으면 gap 기록 후 False."""
        if self.fm is None:
            self.gap(f"재무제표 없음 → {persona_label} 재무 판단 불가")
            return False
        if self.fm["ageDays"] > self.T["stale_days"]:
            self.gap(f"재무 최신성 낮음(최근 분기 {self.fm['latestPeriod']}, {self.fm['ageDays']}일 경과)")
        return True


# ---------------------------------------------------------------------------
# 1) 추세추종
# ---------------------------------------------------------------------------
def _trend(c: Ctx) -> list[Finding]:
    out: list[Finding] = []
    T = c.T
    close, s50, s150, s200 = (c.f(k) for k in ("close", "sma50", "sma150", "sma200"))
    if None not in (close, s50, s150, s200):
        if close > s50 > s150 > s200:
            out.append(_S("ma_stack", f"종가 {c.px(close)} > SMA50 {c.px(s50)} > SMA150 {c.px(s150)} > SMA200 {c.px(s200)} 정배열",
                          close=close, sma50=s50, sma150=s150, sma200=s200))
        else:
            out.append(_C("ma_stack_broken", "이동평균 정배열(종가>SMA50>150>200)이 아님", 3))
        ext = close / s50 - 1
        if ext >= T["ext_warn"]:
            out.append(_C("ext_sma50", f"종가가 SMA50 대비 {ext:+.1%} 이격 — 과열 이격 구간(≥{T['ext_warn']:.0%})", 3, ext=ext))
        elif ext >= T["ext_caution"]:
            out.append(_C("ext_sma50", f"종가가 SMA50 대비 {ext:+.1%} 이격 — 이격 확대(≥{T['ext_caution']:.0%})", 2, ext=ext))
        out.append(_K("sma50_guard", f"종가가 SMA50({c.px(s50)}) 아래로 내려오면 추세 훼손 신호 — 현재 SMA50 대비 {ext:+.1%}", ext=ext))
    else:
        c.gap("이동평균(SMA50/150/200)")

    if c.b("c3") is True:
        out.append(_S("sma200_rising", "SMA200이 20거래일 전보다 상승 중"))
    elif c.b("c3") is False:
        out.append(_C("sma200_flat", "SMA200이 상승 중이 아님(장기 추세 확인 실패)", 3))

    hp, tier = c.f("highProximity"), c.s("highTier")
    if hp is not None:
        txt = f"52주 고점의 {hp:.1%} 수준({tier})"
        if hp >= T["near_high"]:
            out.append(_S("near_high", txt, hp=hp))
        else:
            out.append(_C("far_from_high", txt + f" — 리더 근접 기준({T['near_high']:.0%}) 미만", 2 if hp < 0.80 else 1, hp=hp))
    else:
        c.gap("52주 고점 근접도")

    rs = c.f("rsScore")
    if rs is not None:
        if rs >= T["rs_leader"]:
            out.append(_S("rs_leader", f"RS Score {rs:.0f} — 강한 리더 구간(≥{T['rs_leader']:.0f})", rs=rs))
        elif rs >= T["rs_pass"]:
            out.append(_S("rs_pass", f"RS Score {rs:.0f} — 통과선({T['rs_pass']:.0f}) 이상", rs=rs))
        else:
            out.append(_C("rs_low", f"RS Score {rs:.0f} — 통과선({T['rs_pass']:.0f}) 미달", 3, rs=rs))
    else:
        c.gap("RS Score")
    chg = c.f("rsChange20d")
    if chg is not None:
        if chg >= T["rs_chg_up"]:
            out.append(_S("rs_improving", f"RS Score 20거래일 변화 {chg:+.1f} — 상대강도 개선", chg=chg))
        elif chg <= T["rs_chg_dn_hard"]:
            out.append(_C("rs_fading", f"RS Score 20거래일 변화 {chg:+.1f} — 상대강도 급락", 3, chg=chg))
        elif chg <= T["rs_chg_dn"]:
            out.append(_C("rs_fading", f"RS Score 20거래일 변화 {chg:+.1f} — 상대강도 약화", 2, chg=chg))
    if c.b("rsLineHigh") is True:
        out.append(_S("rs_line_high", "RS 라인(종목/지수)이 최근 126거래일 신고가 — 지수 대비 강세 지속"))

    out.extend(_regime_findings(c, persona="trend"))
    out.extend(_exit_findings(c))
    out.append(_K("regime_watch", "시장 국면이 GREEN에서 벗어나는지(지수의 SMA50/SMA200 위치) 계속 확인"))
    return out


def _regime_findings(c: Ctx, persona: str) -> list[Finding]:
    out: list[Finding] = []
    T = c.T
    reg, sf, bd = c.s("regime"), c.f("sizeFactor"), c.f("breadth")
    if reg:
        if reg == "GREEN":
            sfx = f", 권장 진입비중 계수 {sf:.2f}" if sf is not None else ""
            out.append(_S("regime_green", f"시장 국면 GREEN(지수>SMA50>SMA200){sfx}", regime=reg, size=sf))
        else:
            sev = 3 if reg == "RED" else 2
            sfx = f" — 권장 진입비중 계수 {sf:.2f}" if sf is not None else ""
            out.append(_C("regime_" + reg.lower(), f"시장 국면 {reg}{sfx}", sev, regime=reg, size=sf))
    else:
        c.gap("시장 국면")
    if bd is not None:
        if bd < T["breadth_weak"]:
            out.append(_C("breadth_weak", f"breadth50 {bd:.0%} — 유니버스 중 SMA50 위 종목이 {T['breadth_weak']:.0%} 미만(시장 참여 저조)", 2, breadth=bd))
        elif bd >= T["breadth_strong"] and persona == "trend":
            out.append(_S("breadth_strong", f"breadth50 {bd:.0%} — 시장 전반이 함께 오르는 환경", breadth=bd))
    return out


def _exit_findings(c: Ctx) -> list[Finding]:
    ex, why = c.s("exitState"), c.s("exitReason")
    if ex in ("TREND_BREAK", "FAST_FAIL", "STOP"):
        return [_C("exit_" + ex.lower(), f"청산 경고 {ex}" + (f" — {why}" if why else ""), 3, exitState=ex)]
    if ex == "WATCH_EXIT":
        return [_C("exit_watch", "청산 관찰 신호(WATCH_EXIT): 단기 이평(EMA10/20) 이탈, SMA50은 아직 위" + (f" — {why}" if why else ""), 2, exitState=ex)]
    if ex == "PROFIT_ALERT":
        return [_C("exit_profit_alert", "과열 경고(PROFIT_ALERT): 급등·이격·거래량 폭증 등 2개 이상" + (f" — {why}" if why else ""), 2, exitState=ex)]
    return []


# ---------------------------------------------------------------------------
# 2) 기술적 분석가
# ---------------------------------------------------------------------------
def _technical(c: Ctx) -> list[Finding]:
    out: list[Finding] = []
    T = c.T
    st, zone = c.s("entryState"), c.s("zone")
    pdist, piv = c.f("pivotDist"), c.f("pivotV2")
    bov, clv = c.f("boVolRatio"), c.f("boClv")

    if st in ("GO_BREAKOUT", "GO_PULLBACK"):
        bits = []
        if bov is not None:
            bits.append(f"거래량 {bov:.2f}배")
        if clv is not None:
            bits.append(f"종가위치(CLV) {clv:.2f}")
        if pdist is not None:
            bits.append(f"피벗 대비 {pdist:+.1f}%")
        label = "확인된 돌파" if st == "GO_BREAKOUT" else "돌파 후 눌림목 반등"
        out.append(_S(st.lower(), f"{label}: " + ", ".join(bits), state=st))
    elif st == "BREAKOUT_UNCONFIRMED":
        why = []
        if bov is not None and bov < T["bo_vol"]:
            why.append(f"거래량 {bov:.2f}배(<{T['bo_vol']})")
        if clv is not None and clv < T["bo_clv"]:
            why.append(f"종가위치 {clv:.2f}(<{T['bo_clv']})")
        out.append(_C("bo_unconfirmed", "돌파 구간이나 확인 미달" + (": " + ", ".join(why) if why else ""), 2, state=st))
    elif st == "FAILED":
        out.append(_C("bo_failed", "돌파 실패 상태(FAILED) — 돌파 직후 피벗 아래로 되돌림", 3, state=st))

    if st not in ("GO_BREAKOUT", "GO_PULLBACK", "FAILED"):
        if zone == "READY":
            out.append(_S("zone_ready", f"피벗 아래 2% 이내 대기 구간(READY)" + (f", 피벗 대비 {pdist:+.1f}%" if pdist is not None else ""), pdist=pdist))
        elif zone in ("WATCH", "SETUP") and pdist is not None:
            out.append(_K("zone_far", f"피벗까지 {abs(pdist):.1f}% 남음({zone}) — 아직 돌파 구간이 아님", pdist=pdist))
        elif zone == "BREAKOUT_ZONE":
            out.append(_K("zone_bo", "피벗 상향 0~3% 구간 — 거래량·종가위치 확인 결과에 따라 GO/미확인이 갈림"))
        elif zone == "LATE":
            out.append(_C("zone_late", f"피벗 대비 {pdist:+.1f}% — 늦은 진입 구간(LATE, +3~5%)" if pdist is not None else "늦은 진입 구간(LATE)", 2, pdist=pdist))
        elif zone == "EXTENDED":
            out.append(_C("zone_extended", f"피벗 대비 {pdist:+.1f}% — 과확장(EXTENDED, +5% 초과)" if pdist is not None else "과확장(EXTENDED)", 3, pdist=pdist))

    n, widths = c.f("contractionCount"), c.s("contractionWidths")
    if n is not None:
        if n >= 2:
            out.append(_S("contractions", f"스윙 기반 수축 {n:.0f}회" + (f" (수축폭 목록 {widths})" if widths else ""), n=n))
        else:
            out.append(_C("contractions_few", f"스윙 기반 수축 {n:.0f}회 — 2회 미만(수축 패턴 미형성)", 1, n=n))
    else:
        c.gap("수축 횟수")

    atrc = c.f("atrContraction")
    if atrc is not None:
        if atrc <= T["atrc_good"]:
            out.append(_S("atr_contract", f"ATR20/ATR60 {atrc:.2f} — 변동성 수축(≤{T['atrc_good']})", atrc=atrc))
        elif atrc > T["atrc_expand"]:
            out.append(_C("atr_expand", f"ATR20/ATR60 {atrc:.2f} — 변동성이 뚜렷이 확대(>{T['atrc_expand']})", 2, atrc=atrc))
    dry = c.f("volDryup")
    if dry is not None:
        if dry <= T["dryup_good"]:
            out.append(_S("dryup", f"거래량 dry-up {dry:.2f} (10일/50일 평균, ≤{T['dryup_good']}) — 매물 소화 진행", dry=dry))
        elif dry >= T["dryup_none"]:
            out.append(_C("no_dryup", f"거래량 dry-up 없음 {dry:.2f}(≥{T['dryup_none']}) — 조정 구간에서 거래가 마르지 않음", 1, dry=dry))
    base = c.f("baseLength")
    if base is not None:
        if base >= T["base_min"]:
            out.append(_S("base_len", f"베이스 길이 {base:.0f}거래일(≥{T['base_min']:.0f})", base=base))
        else:
            out.append(_C("base_short", f"베이스 길이 {base:.0f}거래일 — {T['base_min']:.0f}일 미만(기간 부족)", 1, base=base))
    r10 = c.f("range10")
    if r10 is not None:
        if r10 <= T["range10_tight"]:
            out.append(_S("range10_tight", f"최근 10일 고저폭 {r10:.1f}% — 타이트", r10=r10))
        elif r10 >= T["range10_loose"]:
            out.append(_C("range10_loose", f"최근 10일 고저폭 {r10:.1f}% — 변동 큼(≥{T['range10_loose']:.0f}%)", 1, r10=r10))
    if c.s("pivotSrc") == "rolling":
        out.append(_C("pivot_rolling", "피벗이 확정 스윙 고점이 아니라 단순 최근 최고가 기반 — 저항선 신뢰도 낮음", 1))
    if c.b("setupReady") is True:
        out.append(_S("setup_ready", "SETUP READY: 추세·베이스 길이·수축·dry-up 조건 동시 충족"))
    sq = c.f("setupQuality")
    if sq is not None and sq >= T["setupq_good"]:
        out.append(_S("setup_quality", f"Setup Quality {sq:.0f}/100 (랭킹용 합성 점수)", sq=sq))

    if st not in ("GO_BREAKOUT", "GO_PULLBACK", "FAILED"):
        pv = f"피벗({c.px(piv)})" if piv else "피벗"
        out.append(_K("bo_confirm", f"{pv} 종가 상향 돌파 + 거래량 ≥ 50일 평균 {T['bo_vol']}배 + 종가위치(CLV) ≥ {T['bo_clv']}가 함께 확인되는지", pivot=piv))
    return out


# ---------------------------------------------------------------------------
# 3) 퀀트
# ---------------------------------------------------------------------------
def _pick_rate(c: Ctx) -> Optional[dict]:
    ctx = c.context or {}
    rates = ctx.get("baseRates") or {}
    grp = "GO" if str(c.s("entryState") or "").startswith("GO_") else "TREND"
    br = rates.get(grp)
    if br and br.get("status") in ("ok", "clustered", "thin") and br.get("primary"):
        return br
    return rates.get("TREND") or ctx.get("baseRate")


def _track_findings(c: Ctx, contrarian: bool) -> list[Finding]:
    """성과 트래커 기저율. 표본이 얕으면 성과 수치를 근거/반론으로 쓰지 않고 한계와 함께 확인 항목으로만 낸다."""
    out: list[Finding] = []
    br = _pick_rate(c)
    if not br:
        if contrarian:
            c.gap("성과 트래커 기록 없음 → 기저율 반론 검증 불가")
            out.append(_K("x_base_rate", "이 스크리너 신호의 사후 성과(5·20·60거래일) 기록을 확인 — 통과했다는 사실만으로 이후 상승을 보장하지 않음"))
        return out
    st = br.get("status")
    if st == "none" or not br.get("primary"):
        c.gap(f"성과 트래커 사후 성과 표본 없음({br.get('reason') or '미완료'})")
        if contrarian:
            out.append(_K("x_base_rate", "이 스크리너 신호의 사후 성과 기록이 아직 쌓이지 않음 — 통과 사실만으로 이후 상승을 보장하지 않음"))
        return out
    h = br["horizons"][br["primary"]]
    mean, exc, win = h.get("meanReturnPct"), h.get("meanExcessPct"), h.get("winRate")
    mean_txt = "-" if mean is None else f"{mean:+.2f}%"
    body = (f"{br['group']} 신호(종목당 최초 1회) 사후 {br['primary']}거래일: 표본 {h['n']}개·신호일 {h['distinctSignalDates']}일, "
            f"평균 {mean_txt}" + (f", 지수 대비 {exc:+.2f}%p" if exc is not None else "")
            + (f", 승률 {win:.0%}" if win is not None else "") + f" (기준일 {br.get('asOf')})")
    meta = dict(group=br["group"], horizon=br["primary"], n=h["n"], dates=h["distinctSignalDates"],
                mean=mean, excess=exc, winRate=win, status=st)
    fid = "x_base_rate" if contrarian else "q_base_rate"
    if st != "ok":
        c.gap(f"성과 트래커 표본 한계: {br.get('reason')}")
        out.append(_K(fid, body + f" — 참고용: {br.get('reason')}", **meta))
    else:
        bad = (exc is not None and exc < 0) or (exc is None and mean is not None and mean < 0)
        if bad:
            out.append(_C(fid, body + " — 지수보다 못했음", 2, **meta))
        elif contrarian:
            out.append(_K(fid, body + " — 과거 성과이며 미래를 보장하지 않음", **meta))
        else:
            out.append(_S(fid, body, **meta))
    if br.get("seededCohort") and br["seededCohort"] >= 0.5 * max(br.get("unique") or 1, 1):
        c.gap(f"성과 표본의 {br['seededCohort']}/{br['unique']}가 추적 시작 시점에 이미 통과 중이던 종목(시드) — 신규 진입 성과와 다를 수 있음")

    state = (c.context or {}).get("trackerState")
    if state and contrarian:
        from personas import track_record
        mine = track_record.stock_signals(state, c.market, c.row.get("code"))
        if mine:
            parts = []
            for s_ in mine:
                done = [f"+{k}일 {o['returnPct']:+.1f}%" for k, o in s_["outcomes"].items()
                        if o["status"] == "complete" and o["returnPct"] is not None]
                parts.append(f"{s_['group']} {s_['date']}: " + (", ".join(done) if done else "사후 성과 대기 중"))
            out.append(_K("x_own_signals", "이 종목의 신호 이력 — " + " / ".join(parts)))
    return out


def _quant(c: Ctx) -> list[Finding]:
    out: list[Finding] = []
    T = c.T
    rs, chg = c.f("rsScore"), c.f("rsChange20d")
    if rs is not None:
        if rs >= T["rs_leader"]:
            out.append(_S("q_rs_top", f"RS Score {rs:.0f}: 오늘 스캔 유니버스 내 순위 상위 구간(≥{T['rs_leader']:.0f})", rs=rs))
        if rs >= 95:
            out.append(_K("q_rs_extreme", f"RS Score {rs:.0f}는 유니버스 내 극단 상위 — 유사한 극단 구간이 이후 어떻게 움직였는지 분포 확인 필요"))
    if chg is not None:
        if chg > 0:
            out.append(_S("q_rs_accel", f"RS Score 20거래일 변화 {chg:+.1f} — 모멘텀 가속", chg=chg))
        else:
            out.append(_C("q_rs_decel", f"RS Score 20거래일 변화 {chg:+.1f} — 모멘텀 둔화", 2 if chg <= T["rs_chg_dn"] else 1, chg=chg))

    liq = c.f("avgTradingValue20")
    if liq is not None:
        unit = "원" if c.market == "kr" else "달러"
        if liq < c.thr("liq_min"):
            out.append(_C("liq_low", f"20일 평균 거래대금 {liq:,.0f}{unit} — 최소 기준({c.thr('liq_min'):,.0f}) 미만, 체결·슬리피지 부담", 2, liq=liq))
        elif liq >= c.thr("liq_good"):
            out.append(_S("liq_good", f"20일 평균 거래대금 {liq:,.0f}{unit} — 유동성 충분", liq=liq))
    else:
        c.gap("20일 평균 거래대금")

    ap = c.atr_pct()
    if ap is not None:
        if ap >= T["atr_pct_high"]:
            out.append(_C("atr_high", f"ATR20/종가 {ap:.1%} — 일간 변동성 큼(≥{T['atr_pct_high']:.0%})", 2, atr_pct=ap))
        elif ap <= T["atr_pct_low"]:
            out.append(_S("atr_low", f"ATR20/종가 {ap:.1%} — 일간 변동성 낮음", atr_pct=ap))
        out.append(_K("vol_sizing", f"ATR20/종가 {ap:.1%}: 변동성 기준 동일 위험으로 담으려면 ATR%가 낮은 종목보다 비중이 작아짐", atr_pct=ap))

    fm = c.fm
    if fm is not None:
        opm, oc = fm.get("opMargin"), fm.get("ocfToNi")
        if opm is not None and opm >= T["opm_good"]:
            out.append(_S("q_margin", f"영업이익률 {opm:.1f}% (최근 분기)", opm=opm))
        if oc is not None:
            if oc >= T["ocf_ni_good"]:
                out.append(_S("q_cash_quality", f"영업현금흐름/순이익 {oc:.2f} (TTM) — 이익의 현금 뒷받침", oc=oc))
            elif oc <= T["ocf_ni_bad"]:
                out.append(_C("q_cash_weak", f"영업현금흐름/순이익 {oc:.2f} (TTM) — 이익 대비 현금 유입이 약함", 2, oc=oc))
    else:
        c.gap("재무제표 없음 → 퀀트 품질 팩터(마진·현금흐름) 판단 불가")
    sec = (c.yv or {}).get("sector")
    if sec:
        ind = (c.yv or {}).get("industry")
        out.append(_K("q_dup", f"섹터 {sec}" + (f" / {ind}" if ind else "") + "(Yahoo) — 같은 섹터 종목을 함께 담으면 팩터 노출이 중복됨", sector=sec))
    else:
        out.append(_K("q_dup", "동일 섹터·테마 종목을 함께 담으면 팩터 노출이 중복됨(업종 데이터 없음 — 직접 확인)"))
    out.extend(_track_findings(c, contrarian=False))
    return out


# ---------------------------------------------------------------------------
# 4) 가치투자
# ---------------------------------------------------------------------------
def _per_findings(c: Ctx, out: list) -> None:
    p = c.per()
    per = p["value"]
    if per is not None:
        txt = f"PER {per:.1f} ({p['label']} 기준)"
        meta = dict(per=per, source=p["source"])
        if per >= c.thr("per_extreme"):
            out.append(_C("per_extreme", txt + f" — 매우 높음(≥{c.thr('per_extreme'):.0f})", 3, **meta))
        elif per >= c.thr("per_high"):
            out.append(_C("per_high", txt + f" — 높은 편(≥{c.thr('per_high'):.0f})", 2, **meta))
        elif per <= c.thr("per_ok"):
            out.append(_S("per_ok", txt + f" — 절대 기준 무난(≤{c.thr('per_ok'):.0f})", **meta))
        else:
            out.append(_K("per_mid", txt + " — 업종 평균 없이는 판단 유보", **meta))
        hp = c.f("highProximity")
        if per >= c.thr("per_high") and hp is not None and hp >= c.T["near_high"]:
            out.append(_C("per_and_highs", f"PER {per:.1f}이면서 주가가 52주 고점의 {hp:.0%} — 강한 추세가 이미 높은 기대를 가격에 반영했을 수 있음", 2))
    elif p["note"]:
        c.gap("PER 산출 불가: " + p["note"])
    else:
        c.gap("PER 산출 불가: 공시·Yahoo 모두 값 없음")
    fpe = c.yv.get("forwardPE") if c.yv else None
    if fpe is not None:
        out.append(_K("v_fwd_pe", f"선행 PER {fpe:.1f} — 애널리스트 추정 기반(Yahoo, 공시 실적 아님)", forward_pe=fpe))


def _value(c: Ctx) -> list[Finding]:
    out: list[Finding] = []
    T = c.T
    if not c.need_fund("가치"):
        _per_findings(c, out)
        out.append(_K("v_no_data", "재무 데이터 확보 후 PER·부채·현금흐름을 재평가해야 함"))
        return out
    fm = c.fm
    _per_findings(c, out)
    pbr = fm["valuation"].get("pbr")
    ypbr = c.yv.get("priceToBook") if c.yv else None
    if pbr is not None and c.market == "kr" and pbr >= T["pbr_high"]["kr"]:
        out.append(_C("pbr_high", f"PBR {pbr:.2f} — 자본 대비 높은 가격(≥{T['pbr_high']['kr']})", 1, pbr=pbr))
    elif pbr is not None:
        out.append(_K("pbr_info", f"PBR {pbr:.2f} (자본총계에 비지배지분 포함 가능)", pbr=pbr))
    elif ypbr is not None:
        out.append(_K("pbr_info", f"PBR {ypbr:.2f} (Yahoo, 외부 데이터) — 업종별 기준이 달라 절대 판단 유보", pbr=ypbr))

    ni = fm["ttm"].get("netIncome")
    if ni is not None:
        if ni > 0:
            out.append(_S("v_profit", "최근 4분기 순이익 합계 흑자", ni=ni))
        else:
            out.append(_C("v_loss", "최근 4분기 순이익 합계 적자 — 이익 기반 가치 판단이 성립하지 않음", 3, ni=ni))
    opm, chg = fm.get("opMargin"), fm.get("opMarginChangePP")
    if opm is not None:
        if opm >= T["opm_good"]:
            out.append(_S("v_margin", f"영업이익률 {opm:.1f}%", opm=opm))
        elif opm <= T["opm_low"]:
            out.append(_C("v_margin_low", f"영업이익률 {opm:.1f}% — 낮은 수익성(업종 특성 확인 필요)", 1, opm=opm))
    if chg is not None:
        if chg >= T["margin_chg_pp"]:
            out.append(_S("v_margin_up", f"영업이익률 전년 동기 대비 {chg:+.1f}%p 개선", chg=chg))
        elif chg <= -T["margin_chg_pp"]:
            out.append(_C("v_margin_down", f"영업이익률 전년 동기 대비 {chg:+.1f}%p 악화", 2, chg=chg))
    de = fm.get("debtToEquity")
    if de is not None:
        if de <= c.thr("de_ok"):
            out.append(_S("v_de_ok", f"부채비율(총부채/자본) {de:.0f}%", de=de))
        elif de >= c.thr("de_high"):
            out.append(_C("v_de_high", f"부채비율(총부채/자본) {de:.0f}% — 높음(≥{c.thr('de_high'):.0f}%, 순차입 기준 아님)", 2, de=de))
    oc = fm.get("ocfToNi")
    if oc is not None:
        if oc >= T["ocf_ni_good"]:
            out.append(_S("v_ocf", f"영업현금흐름/순이익 {oc:.2f} (TTM) — 이익의 질 양호", oc=oc))
        elif oc <= T["ocf_ni_bad"]:
            out.append(_C("v_ocf_weak", f"영업현금흐름/순이익 {oc:.2f} (TTM) — 이익의 질 의문", 2, oc=oc))
    ocf_ttm = fm["ttm"].get("operatingCashFlow")
    if ocf_ttm is not None and ocf_ttm < 0:
        out.append(_C("v_ocf_neg", "최근 4분기 영업현금흐름 합계 음수", 3))
    fcf = fm.get("fcfTtm")
    if fcf is not None:
        if fcf > 0:
            out.append(_S("v_fcf", "최근 4분기 잉여현금흐름(영업CF−CAPEX) 양수", fcf=fcf))
        else:
            out.append(_C("v_fcf_neg", "최근 4분기 잉여현금흐름 음수", 2, fcf=fcf))
    elif c.market == "kr":
        c.gap("한국 재무는 CAPEX가 없어 잉여현금흐름 산출 불가")
    out.append(_K("v_peer", "업종 평균 PER/PBR과 비교(데이터 없음)하고, 일회성 손익·회계 기준(연결/별도) 여부를 공시에서 확인"))
    return out


# ---------------------------------------------------------------------------
# 5) 성장주
# ---------------------------------------------------------------------------
def _pct(x: Optional[float]) -> str:
    return "n/a" if x is None else f"{x:+.1f}%"


def _growth(c: Ctx) -> list[Finding]:
    out: list[Finding] = []
    T = c.T
    if not c.need_fund("성장주"):
        out.append(_K("g_no_data", "재무 데이터 확보 후 매출·이익 YoY와 가속 여부를 재평가해야 함"))
        return out
    fm = c.fm
    yoy = fm["yoy"]
    for key, label, sid in (("revenue", "매출", "rev"), ("operatingProfit", "영업이익", "op"),
                            ("netIncome", "순이익", "ni"), ("eps", "EPS", "eps")):
        pct, lab = yoy[key]["pct"], yoy[key]["label"]
        if pct is not None:
            if pct >= T["g_strong"]:
                out.append(_S(f"g_{sid}_strong", f"{label} YoY {pct:+.1f}% (최근 분기) — 강한 성장(≥{T['g_strong']:.0f}%)", pct=pct))
            elif key == "revenue" and pct < T["g_weak"]:
                out.append(_C("g_rev_weak", f"매출 YoY {pct:+.1f}% — 성장률 낮음(<{T['g_weak']:.0f}%)", 3 if pct < 0 else 2, pct=pct))
            elif pct < 0:
                out.append(_C(f"g_{sid}_neg", f"{label} YoY {pct:+.1f}% — 역성장", 3, pct=pct))
        elif lab:
            if "흑자전환" in lab:
                out.append(_S(f"g_{sid}_turn", f"{label} {lab}"))
            elif "적자" in lab:
                out.append(_C(f"g_{sid}_loss", f"{label} {lab}", 3))

    rs = fm["revYoySeries"]
    known = [v for v in rs if v is not None]
    if len(known) >= 3:
        a, b, cc = known[-3:]
        if cc > b > a:
            out.append(_S("g_accel", f"매출 YoY 가속: {_pct(a)} → {_pct(b)} → {_pct(cc)}", series=known[-3:]))
        elif cc < b < a:
            out.append(_C("g_decel", f"매출 YoY 둔화: {_pct(a)} → {_pct(b)} → {_pct(cc)}", 2, series=known[-3:]))
    if fm["consecRevGrowth"] >= 3:
        out.append(_S("g_consec", f"매출 YoY {CONSEC_GROWTH_PCT:.0f}% 이상이 {fm['consecRevGrowth']}분기 연속"))

    hp, rsc = c.f("highProximity"), c.f("rsScore")
    ni_pct = yoy["netIncome"]["pct"]
    if hp is not None and hp >= T["near_high"] and ni_pct is not None and ni_pct < 0:
        out.append(_C("g_price_ahead", f"주가는 52주 고점의 {hp:.0%}인데 순이익 YoY {ni_pct:+.1f}% — 주가가 실적을 앞서가는 괴리", 2))
    if rsc is not None and rsc >= T["rs_pass"] and hp is not None and hp >= T["near_high"]:
        out.append(_S("g_price_confirms", f"주가 신호가 성장주 조건과 부합: RS {rsc:.0f}, 52주 고점 근접 {hp:.0%}"))

    if fm["ageDays"] <= T["stale_days"]:
        out.append(_K("g_base", "다음 분기 YoY는 전년 동기(비교 기저)가 높아질 수 있음 — 직전 분기 성장률 대비 유지 여부 확인"))
    out.append(_K("g_next_report", "다음 실적 발표일과 시장 기대치 확인(발표일·컨센서스 데이터 없음)"))
    return out


# ---------------------------------------------------------------------------
# 6) 리스크 관리자
# ---------------------------------------------------------------------------
def _risk(c: Ctx) -> list[Finding]:
    out: list[Finding] = []
    T = c.T
    ir, sl, sw, flag = c.f("initRisk"), c.f("structStop"), c.f("swingLow"), c.s("riskFlag")
    sf = c.f("sizeFactor")
    if ir is not None:
        base = f"구조적 손절가 {c.px(sl)}" + (f"(스윙 저점 {c.px(sw)} 기준)" if sw else "") + f", 현재가 대비 손절폭 {ir:.1f}%"
        if ir <= T["risk_ok"]:
            out.append(_S("risk_ok", base + f" — {T['risk_ok']:.0f}% 이내", ir=ir, stop=sl))
        elif ir <= T["risk_high"]:
            out.append(_C("risk_wide", base + f" — {T['risk_ok']:.0f}% 초과", 2, ir=ir, stop=sl, flag=flag))
        else:
            out.append(_C("risk_too_wide", base + f" — {T['risk_high']:.0f}% 초과(진입 리스크 과대)", 3, ir=ir, stop=sl, flag=flag))
        rp = T["risk_per_trade_pct"]
        w = min(rp / ir * 100.0, 100.0) if ir > 0 else None
        if w is not None:
            txt = f"[참고 계산] 계좌 리스크 {rp:.1f}%/건 가정 시 손절폭 {ir:.1f}% → 최대 비중 약 {w:.1f}%"
            if sf is not None:
                txt += f", 시장 국면 계수 {sf:.2f} 적용 시 약 {w * sf:.1f}%"
            out.append(_K("risk_sizing", txt + " (가정값이며 개인 위험 허용도에 맞게 조정)", weight=w, size=sf))
    else:
        c.gap("구조적 손절폭(initRisk)")

    reg = c.s("regime")
    if sf is not None:
        if sf >= T["size_ok"]:
            out.append(_S("size_ok", f"시장 국면 {reg or '-'} — 권장 진입비중 계수 {sf:.2f}", size=sf))
        elif sf <= T["size_very_low"]:
            out.append(_C("size_very_low", f"시장 국면 {reg or '-'} — 권장 진입비중 계수 {sf:.2f}로 매우 낮음", 3, size=sf))
        elif sf <= T["size_low"]:
            out.append(_C("size_low", f"시장 국면 {reg or '-'} — 권장 진입비중 계수 {sf:.2f}로 낮음", 2, size=sf))
    else:
        c.gap("권장 진입비중 계수")
    bd = c.f("breadth")
    if bd is not None and bd < T["breadth_weak"]:
        out.append(_C("r_breadth", f"breadth50 {bd:.0%} — 시장 참여 저조, 추세 훼손 시 동반 약세 위험", 2, breadth=bd))

    ap = c.atr_pct()
    if ap is not None and ap >= T["atr_pct_high"]:
        out.append(_C("r_atr", f"ATR20/종가 {ap:.1%} — 하루 변동 폭이 커 손절 이탈 가능성이 큼", 2, atr_pct=ap))
    liq = c.f("avgTradingValue20")
    if liq is not None and liq < c.thr("liq_min"):
        out.append(_C("r_liq", f"20일 평균 거래대금 {liq:,.0f} — 유동성 부족, 원하는 가격에 청산이 어려울 수 있음", 2, liq=liq))

    out.extend(_exit_findings(c))
    if c.s("zone") == "EXTENDED":
        out.append(_C("r_extended", "피벗 대비 +5% 초과(EXTENDED) — 지금 진입하면 손절폭이 커지는 위치", 3))
    out.append(_K("r_stop_rule", "진입 전에 손절 규칙(가격·비중)을 먼저 정해둘 것 — 포지션 기준 STOP/TIME_STOP은 보유 정보가 있어야 판정됨"))
    out.append(_K("r_earnings", "실적 발표 예정일 확인 필요(데이터 없음) — 발표 전후 갭 위험"))
    c.gap("실적 발표 예정일")
    return out


# ---------------------------------------------------------------------------
# 7) 반론가
# ---------------------------------------------------------------------------
def _contrarian(c: Ctx) -> list[Finding]:
    out: list[Finding] = []
    T = c.T
    rs, hp, tier = c.f("rsScore"), c.f("highProximity"), c.s("highTier")
    if tier == "SUPER_LEADER" and rs is not None and rs >= T["rs_leader"]:
        out.append(_C("x_crowded", f"이미 모두가 보는 리더 — RS {rs:.0f}, 52주 고점의 {hp:.0%}. 추가 상승 여력보다 되돌림 폭이 문제가 될 수 있음" if hp is not None else f"이미 모두가 보는 리더 — RS {rs:.0f}", 1))
    ext = c.ext50()
    if ext is not None and ext >= T["ext_caution"]:
        out.append(_C("x_extension", f"SMA50 대비 {ext:+.1%} 이격 — 이평으로 되돌림이 나오면 손실이 커지는 위치", 2 if ext < T["ext_warn"] else 3))
    bd = c.f("breadth")
    if bd is not None and bd < T["breadth_weak"]:
        out.append(_C("x_narrow", f"breadth50 {bd:.0%}: 강세가 소수 종목에 집중된 시장 — 주도주 약세 전환 시 지수·개별 종목 모두 취약", 2))
    reg = c.s("regime")
    if reg and reg != "GREEN":
        out.append(_C("x_regime", f"시장 국면이 {reg} — 개별 종목 추세와 시장 환경이 엇갈림", 3 if reg == "RED" else 2))
    zone, st = c.s("zone"), c.s("entryState")
    if zone in ("SETUP", "WATCH", "READY") and st not in ("GO_BREAKOUT", "GO_PULLBACK"):
        out.append(_C("x_no_breakout", "아직 돌파 전 — 돌파가 실패하거나 지연되면 베이스가 길어지거나 무너질 수 있음", 1))
    chg = c.f("rsChange20d")
    if chg is not None and chg < 0:
        out.append(_C("x_rs_fade", f"RS Score 20거래일 변화 {chg:+.1f} — 상대강도 정점이 지났을 가능성", 2 if chg <= T["rs_chg_dn"] else 1))
    out.extend(_exit_findings(c))

    fm = c.fm
    if fm is not None:
        rs_series = [v for v in fm["revYoySeries"] if v is not None]
        if len(rs_series) >= 3 and rs_series[-1] < rs_series[-2] < rs_series[-3]:
            out.append(_C("x_growth_decel", f"매출 YoY가 {len(rs_series[-3:])}분기 연속 둔화({' → '.join(f'{v:+.1f}%' for v in rs_series[-3:])})", 2))
        ni_pct = fm["yoy"]["netIncome"]["pct"]
        if ni_pct is not None and ni_pct < 0 and hp is not None and hp >= T["near_high"]:
            out.append(_C("x_price_ahead", f"주가는 고점 근처인데 순이익 YoY {ni_pct:+.1f}%", 2))
    else:
        c.gap("재무제표 없음 → 성장 둔화 반론은 검증 불가")
    per = c.per()["value"]
    if per is not None and per >= c.thr("per_high"):
        out.append(_C("x_valuation", f"PER {per:.1f} — 기대가 이미 가격에 반영되어 실적이 조금만 미달해도 민감할 수 있음", 2))
    elif per is None and fm is None:
        c.gap("PER 없음 → 밸류에이션 반론은 검증 불가")

    out.extend(_track_findings(c, contrarian=True))

    # 반증 조건: 무엇이 확인되면 위 반론이 약해지나
    s50 = c.f("sma50")
    if s50:
        out.append(_K("x_falsify_trend", f"종가가 SMA50({c.px(s50)}) 위에서 유지되는 동안 '추세 훼손' 반론은 약함; 이탈하면 강해짐"))
    piv = c.f("pivotV2")
    if piv and st not in ("GO_BREAKOUT", "GO_PULLBACK"):
        out.append(_K("x_falsify_breakout", f"피벗({c.px(piv)}) 위에서 거래량 ≥1.4배·종가위치 ≥0.70으로 마감하면 '돌파 실패' 반론은 약해짐"))
    if fm is not None:
        out.append(_K("x_falsify_growth", "다음 분기 실적에서 매출·이익 YoY가 유지/가속되면 '성장 둔화·밸류에이션' 반론은 약해짐"))
    return out


_PERSONA_FUNCS: dict[str, Callable[[Ctx], list[Finding]]] = {
    "trend": _trend, "technical": _technical, "quant": _quant, "value": _value,
    "growth": _growth, "risk": _risk, "contrarian": _contrarian,
}


# ---------------------------------------------------------------------------
# 공개 API
# ---------------------------------------------------------------------------
def build_evidence(row: dict, fund: Optional[dict] = None, *, today: Optional[dt.date] = None,
                   thresholds: Optional[dict] = None, context: Optional[dict] = None,
                   valuation: Optional[dict] = None) -> dict:
    """종목 한 개의 페르소나별 증거 패킷.

    row      docs/data/latest_{kr,us}.json 한 줄
    fund     docs/data/fundamentals/{kr,us}/{code}.json (없으면 None)
    context  선택. ``personas.track_record.context_for(market)`` 결과
             (``baseRates``: 그룹별 기저율, ``trackerState``: 종목별 신호 이력용 원본)
    valuation 선택. ``personas.valuation.get(cache, code)`` — Yahoo 밸류에이션(외부 데이터, 공시 아님)
    """
    T = {**THRESHOLDS, **(thresholds or {})}
    today = today or dt.datetime.now(dt.timezone(dt.timedelta(hours=9))).date()  # KST(UTC+9), tzdata 불필요
    market = "us" if str(row.get("market") or "").upper() == "US" else "kr"
    fm = fund_metrics(fund, market, close=_f(row, "close"), marcap=row.get("marcap"), today=today)

    personas = []
    global_gaps: list[str] = []
    for pid, meta in PERSONAS.items():
        c = Ctx(row, fm, T, context or {}, valuation)
        findings = _PERSONA_FUNCS[pid](c)
        sup = [asdict(f) for f in findings if f.kind == "support"]
        con = sorted((asdict(f) for f in findings if f.kind == "concern"), key=lambda d: -d["severity"])
        chk = [asdict(f) for f in findings if f.kind == "check"]
        personas.append({"id": pid, "name": meta["name"], "lens": meta["lens"],
                         "supports": sup, "concerns": con, "checks": chk, "dataGaps": c.gaps})
        for g in c.gaps:
            if g not in global_gaps:
                global_gaps.append(g)

    keys = ("close", "changePct", "entryState", "exitState", "zone", "pivotDist", "regime", "marketGate",
            "rsScore", "rsChange20d", "highProximity", "initRisk", "structStop")
    snap = {k: row.get(k) for k in keys}
    if valuation:
        snap["yahoo"] = {k: valuation.get(k) for k in ("trailingPE", "forwardPE", "priceToBook", "sector", "industry",
                                                         "fetchedAt", "stale")}
    if fm is not None:
        snap["fundamentals"] = {"latestPeriod": fm["latestPeriod"], "ageDays": fm["ageDays"],
                                "per": fm["valuation"].get("per"), "pbr": fm["valuation"].get("pbr"),
                                "opMargin": fm.get("opMargin")}
    return {"code": normalize_code(market, row.get("code")), "name": row.get("name"), "market": row.get("market"),
            "snapshot": snap, "personas": personas, "dataGaps": global_gaps,
            "hasFundamentals": fm is not None, "disclaimer": DISCLAIMER}


def format_text(ev: dict) -> str:
    """사람이 검토하기 위한 텍스트 출력."""
    lines = [f"{ev['name']} ({ev['code']}, {ev['market']})  — {ev['disclaimer']}", ""]
    for p in ev["personas"]:
        lines.append(f"■ {p['name']}  [{p['lens']}]")
        for tag, key in (("근거", "supports"), ("우려", "concerns"), ("체크", "checks")):
            for f in p[key]:
                sev = f" ({'!' * f['severity']})" if key == "concerns" else ""
                lines.append(f"   {tag}{sev}  {f['text']}")
        for g in p["dataGaps"]:
            lines.append(f"   [데이터 없음] {g}")
        lines.append("")
    return "\n".join(lines)


def _load(market: str, code: str) -> tuple[dict, Optional[dict]]:
    root = Path(__file__).resolve().parents[1]
    code = normalize_code(market, code)
    rows = json.loads((root / "docs" / "data" / f"latest_{market}.json").read_text(encoding="utf-8"))
    row = next((r for r in rows if normalize_code(market, r.get("code")) == code), None)
    if row is None:
        raise SystemExit(f"{market}/{code} 종목을 latest_{market}.json에서 찾지 못했습니다")
    fp = root / "docs" / "data" / "fundamentals" / market / f"{code}.json"
    fund = json.loads(fp.read_text(encoding="utf-8")) if fp.exists() else None
    return row, fund


def main() -> None:
    ap = argparse.ArgumentParser(description="페르소나 증거 패킷 미리보기 (LLM 호출 없음)")
    ap.add_argument("--market", choices=["kr", "us"], required=True)
    ap.add_argument("--code", required=True)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    row, fund = _load(a.market, a.code)
    from personas import track_record, valuation
    root = Path(__file__).resolve().parents[1]
    yv = valuation.get(valuation.load_cache(root / "docs" / "data" / "valuation_us.json"), a.code) if a.market == "us" else None
    ev = build_evidence(row, fund, context=track_record.context_for(a.market, root), valuation=yv)
    print(json.dumps(ev, ensure_ascii=False, indent=1) if a.json else format_text(ev))

