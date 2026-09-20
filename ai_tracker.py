"""AI 밸류체인 75종목을 SEPA 결과와 결합해 Google Sheets에 추적한다.

정적 산업 메타데이터는 운영 Google Sheet에서 읽고, 테스트·로컬 실행에서는
선택적으로 ``data/ai_value_chain_universe.csv``를 사용할 수 있다. 가격·추세·
진입/청산 상태는 매일 생성되는 SEPA 결과를 사용한다.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

import numpy as np
import pandas as pd


logger = logging.getLogger("sepa_screener.ai_tracker")

AI_DATA_PATH = Path(__file__).resolve().parent / "data" / "ai_value_chain_universe.csv"
OVERVIEW_SHEET = "AI_밸류체인_현황"
HISTORY_SHEET = "AI_밸류체인_이력"
KPI_SHEET = "AI_산업_KPI"

OVERVIEW_COLUMNS = [
    "ID", "기준일", "국가", "티커", "기업명", "스크리닝시장", "대분류", "세부 밸류체인",
    "AI 매출 공시", "AI Exposure", "Bottleneck", "Pricing Power", "Technology Moat",
    "CAPEX Sensitivity", "Earnings Momentum", "Supply Constraint", "Revenue Visibility",
    "Valuation Burden", "Catalyst Strength", "Cycle Stage", "Stage Tags", "Catalyst Date",
    "Catalyst", "Risk", "Valuation", "Valuation vs History", "AI Priced In",
    "상태", "제외사유", "종가", "등락률", "전체통과", "TREND OK v2", "RS 백분위", "RS Score",
    "RS 20D Change", "Setup Quality", "SETUP READY", "Entry State", "Exit State",
    "구조적 손절가", "초기 리스크", "시장 국면", "권장 진입비중", "Technical Signal",
    "체크포인트", "리서치 출처", "리서치 업데이트", "점수 근거",
]

HISTORY_COLUMNS = [
    "날짜", "국가", "티커", "기업명", "대분류", "세부 밸류체인", "상태", "종가", "등락률",
    "전체통과", "TREND OK v2", "RS 백분위", "RS Score", "RS 20D Change", "Setup Quality", "SETUP READY",
    "Entry State", "Exit State", "구조적 손절가", "초기 리스크", "시장 국면", "권장 진입비중",
    "Cycle Stage", "Stage Tags", "AI Exposure", "Bottleneck", "Pricing Power",
    "Technology Moat", "CAPEX Sensitivity", "Valuation Burden", "Catalyst Strength",
]

KPI_COLUMNS = [
    "구분", "KPI", "정의", "주기", "연결 밸류체인", "현재값", "이전값", "변화/신호",
    "기준일", "업데이트 방식", "체크포인트", "출처/링크",
]

INDUSTRY_KPIS = [
    ("Hyperscaler CAPEX", "MSFT/AMZN/GOOGL/META/ORCL 분기 CAPEX와 가이던스", "분기", "Cloud→Compute/Power", "증가율, GPU 비중, 장기자산 비중"),
    ("NVIDIA Data Center revenue", "Data Center 매출·QoQ/YoY·GM", "분기", "Compute", "출하와 가격/mix 분리"),
    ("Broadcom AI revenue", "AI semiconductor 매출·XPU/Networking mix", "분기", "ASIC/Networking", "고객 수와 ramp 일정"),
    ("HBM bit shipment", "업체별 HBM bit 성장·capacity", "분기", "Memory", "세대별 공급/수요"),
    ("HBM ASP", "HBM3E/4 계약가격·mix", "분기/반기", "Memory", "DRAM premium과 장기계약"),
    ("HBM4 yield", "양산수율·고객 인증·출하", "월/분기", "Memory/Packaging", "지연·rework 신호"),
    ("CoWoS capacity", "월간 wafer-equivalent capacity·증설", "분기", "Packaging", "실가동률과 패키지 mix"),
    ("800G/1.6T shipment", "모듈·laser·DSP 출하 및 ASP", "분기", "Optical", "1.6T ramp와 800G 가격하락"),
    ("Data center GW pipeline", "secured power, under construction, energized GW", "분기", "DC/Power", "중복계상·취소율 조정"),
    ("Transformer lead time", "대형 변압기/스위치기어 납기·백로그", "분기", "Grid", "리드타임 하락은 가격정상화 선행"),
    ("Rack power density", "평균/신규 AI rack kW", "반기", "Power/Cooling", "100kW+ 침투"),
    ("Liquid cooling penetration", "AI rack 중 direct liquid 비중", "반기", "Cooling", "CDU attach·서비스 매출"),
    ("AI inference revenue", "API·cloud inference·token volume", "분기", "Cloud/Model", "단가 하락 대비 사용량"),
    ("Enterprise AI adoption", "유료 좌석·agent deployments·NRR", "분기", "Software", "pilot→production 전환"),
    ("Agent usage", "tasks/transactions/consumption", "월/분기", "Software", "seat보다 실제 업무량"),
    ("AI software gross margin", "AI SKU GM·inference cost", "분기", "Software", "가격과 모델 비용의 spread"),
]


def _code(value, country: str | None = None) -> str:
    text = "" if value is None else str(value).strip()
    if text.endswith(".0") and text[:-2].isdigit():
        text = text[:-2]
    return text.zfill(6) if country == "KR" else text.upper()


SHEET_TO_DATA_COLUMNS = {
    "국가": "Country", "티커": "Ticker", "기업명": "Company", "스크리닝시장": "Screening_Market",
    "대분류": "Primary_ValueChain", "세부 밸류체인": "AI_Subsector",
    "AI 매출 공시": "AI_Revenue_Disclosure", "Bottleneck": "Bottleneck_Importance",
    "Pricing Power": "Pricing_Power", "Technology Moat": "Technology_Moat",
    "CAPEX Sensitivity": "CAPEX_Sensitivity", "Earnings Momentum": "Earnings_Momentum",
    "Supply Constraint": "Supply_Constraint", "Revenue Visibility": "Revenue_Visibility",
    "Valuation Burden": "Valuation_Burden", "Catalyst Strength": "Catalyst_Strength",
    "Cycle Stage": "Cycle_Stage", "Stage Tags": "Stage_Tags", "Catalyst Date": "Catalyst_Date",
    "Valuation vs History": "Valuation_vs_History", "AI Priced In": "AI_Priced_In",
    "체크포인트": "Checkpoints", "리서치 출처": "Research_Sources",
    "리서치 업데이트": "Research_Update_Date", "점수 근거": "Score_Rationale",
}


def _load_ai_universe_from_google_sheet() -> pd.DataFrame:
    """공개 저장소에 투자 유니버스를 두지 않고 운영 시트에서 읽는다."""
    import gspread
    from google.oauth2.service_account import Credentials

    creds_json = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
    sheet_id = os.environ.get("GOOGLE_SHEET_ID")
    if not creds_json or not sheet_id:
        raise RuntimeError("AI 유니버스 파일 또는 Google Sheets 인증정보가 없습니다.")
    info = json.loads(creds_json.lstrip("\ufeff"))
    credentials = Credentials.from_service_account_info(
        info, scopes=["https://www.googleapis.com/auth/spreadsheets"],
    )
    sh = gspread.authorize(credentials).open_by_key(sheet_id)
    values = sh.worksheet(OVERVIEW_SHEET).get_all_values()
    if not values:
        raise RuntimeError(f"'{OVERVIEW_SHEET}' 탭이 비어 있습니다.")
    records = _records(values)
    df = pd.DataFrame(records).rename(columns=SHEET_TO_DATA_COLUMNS)
    required = {
        "ID", "Country", "Ticker", "Company", "Screening_Market", "Primary_ValueChain",
        "AI_Subsector", "AI_Revenue_Disclosure", "AI_Exposure", "Bottleneck_Importance",
        "Pricing_Power", "Technology_Moat", "CAPEX_Sensitivity", "Earnings_Momentum",
        "Supply_Constraint", "Revenue_Visibility", "Valuation_Burden", "Catalyst_Strength",
        "Cycle_Stage", "Stage_Tags", "Catalyst_Date", "Catalyst", "Risk", "Valuation",
        "Valuation_vs_History", "AI_Priced_In", "Checkpoints", "Research_Sources",
        "Research_Update_Date", "Score_Rationale",
    }
    missing = required - set(df.columns)
    if missing:
        raise RuntimeError(f"AI 현황 탭 필수 컬럼 누락: {sorted(missing)}")
    return df[list(required)]


def load_ai_universe(path: Path | str | None = None) -> pd.DataFrame:
    source = Path(path) if path is not None else AI_DATA_PATH
    if source.exists():
        df = pd.read_csv(source, dtype={"Ticker": str, "Country": str})
    else:
        df = _load_ai_universe_from_google_sheet()
    df["Ticker"] = [_code(t, c) for t, c in zip(df["Ticker"], df["Country"])]
    numeric = [
        "ID", "AI_Exposure", "Bottleneck_Importance", "Pricing_Power", "Technology_Moat",
        "CAPEX_Sensitivity", "Earnings_Momentum", "Supply_Constraint", "Revenue_Visibility",
        "Valuation_Burden", "Catalyst_Strength",
    ]
    for col in numeric:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    if len(df) != 75 or df["Ticker"].duplicated().any():
        raise ValueError("AI 밸류체인 유니버스는 중복 없는 75종목이어야 합니다.")
    return df.sort_values("ID").reset_index(drop=True)


def extend_screening_universe(universe: pd.DataFrame, market_key: str,
                              path: Path | str | None = None) -> pd.DataFrame:
    """기존 KR/US 유니버스에 AI 75종목 중 해당 시장 종목을 빠짐없이 합친다."""
    market_key = market_key.upper()
    ai = load_ai_universe(path)
    ai = ai[ai["Country"].eq("KR") if market_key == "KR" else ~ai["Country"].eq("KR")]

    out = universe.copy()
    out["Code"] = out["Code"].map(lambda x: _code(x, "KR" if market_key == "KR" else None))
    missing = ai[~ai["Ticker"].isin(set(out["Code"]))]
    if len(missing):
        extra = pd.DataFrame({
            "Code": missing["Ticker"],
            "Name": missing["Company"],
            "Market": missing["Screening_Market"],
            "Marcap": np.nan,
        })
        out = pd.concat([out, extra], ignore_index=True)
        logger.info("AI 전용 유니버스 %d종목 추가: %s", len(extra), ", ".join(extra["Code"]))
    return out.drop_duplicates("Code", keep="first").reset_index(drop=True)


def _series(merged: pd.DataFrame, name: str, default="") -> pd.Series:
    if name in merged.columns:
        return merged[name]
    return pd.Series([default] * len(merged), index=merged.index)


def build_ai_snapshot(screen_df: pd.DataFrame, run_date: str, market_key: str,
                      path: Path | str | None = None) -> pd.DataFrame:
    """한 시장의 SEPA 결과를 AI 산업 메타데이터와 결합한다."""
    market_key = market_key.upper()
    meta = load_ai_universe(path)
    meta = meta[meta["Country"].eq("KR") if market_key == "KR" else ~meta["Country"].eq("KR")].copy()

    live = screen_df.copy()
    live["_ticker"] = live["종목코드"].map(lambda x: _code(x, "KR" if market_key == "KR" else None))
    live = live.drop_duplicates("_ticker", keep="first")
    merged = meta.merge(live, how="left", left_on="Ticker", right_on="_ticker")

    status = _series(merged, "상태")
    status = status.where(status.notna(), "스크리너 미포함")
    entry = _series(merged, "EntryState")
    legacy_entry = _series(merged, "진입판정_참고용_매수신호아님")
    technical = entry.where(entry.notna() & entry.ne(""), legacy_entry)
    technical = technical.where(technical.notna() & technical.ne(""), "관찰")

    out = pd.DataFrame({
        "ID": merged["ID"], "기준일": run_date, "국가": merged["Country"],
        "티커": merged["Ticker"], "기업명": merged["Company"],
        "스크리닝시장": merged["Screening_Market"], "대분류": merged["Primary_ValueChain"],
        "세부 밸류체인": merged["AI_Subsector"], "AI 매출 공시": merged["AI_Revenue_Disclosure"],
        "AI Exposure": merged["AI_Exposure"], "Bottleneck": merged["Bottleneck_Importance"],
        "Pricing Power": merged["Pricing_Power"], "Technology Moat": merged["Technology_Moat"],
        "CAPEX Sensitivity": merged["CAPEX_Sensitivity"], "Earnings Momentum": merged["Earnings_Momentum"],
        "Supply Constraint": merged["Supply_Constraint"], "Revenue Visibility": merged["Revenue_Visibility"],
        "Valuation Burden": merged["Valuation_Burden"], "Catalyst Strength": merged["Catalyst_Strength"],
        "Cycle Stage": merged["Cycle_Stage"], "Stage Tags": merged["Stage_Tags"],
        "Catalyst Date": merged["Catalyst_Date"], "Catalyst": merged["Catalyst"], "Risk": merged["Risk"],
        "Valuation": merged["Valuation"], "Valuation vs History": merged["Valuation_vs_History"],
        "AI Priced In": merged["AI_Priced_In"], "상태": status, "제외사유": _series(merged, "제외사유"),
        "종가": _series(merged, "종가"), "등락률": _series(merged, "등락률"),
        "전체통과": _series(merged, "전체통과(8개AND)"), "TREND OK v2": _series(merged, "TREND_OK_v2"),
        "RS 백분위": _series(merged, "RS_백분위랭킹"),
        "RS Score": _series(merged, "RS_Score"), "RS 20D Change": _series(merged, "RS_20D_Change"),
        "Setup Quality": _series(merged, "SetupQuality점수"), "SETUP READY": _series(merged, "SETUP_READY"),
        "Entry State": entry, "Exit State": _series(merged, "ExitState"),
        "구조적 손절가": _series(merged, "구조적손절가"), "초기 리스크": _series(merged, "초기리스크_pct"),
        "시장 국면": _series(merged, "시장국면_v2"), "권장 진입비중": _series(merged, "권장진입비중"),
        "Technical Signal": technical, "체크포인트": merged["Checkpoints"],
        "리서치 출처": merged["Research_Sources"], "리서치 업데이트": merged["Research_Update_Date"],
        "점수 근거": merged["Score_Rationale"],
    })
    return out[OVERVIEW_COLUMNS].sort_values("ID").reset_index(drop=True)


def _clean_cell(value):
    if value is None or value is pd.NA or (isinstance(value, float) and pd.isna(value)):
        return ""
    if isinstance(value, (np.bool_, bool)):
        return bool(value)
    if isinstance(value, (np.floating, np.integer)):
        return value.item()
    return value


def _records(values: list[list[str]]) -> list[dict]:
    if not values:
        return []
    header = values[0]
    return [dict(zip(header, row + [""] * (len(header) - len(row)))) for row in values[1:] if row]


def _worksheet(sh, title: str, rows: int, cols: int):
    import gspread
    try:
        return sh.worksheet(title), False
    except gspread.exceptions.WorksheetNotFound:
        return sh.add_worksheet(title=title, rows=str(rows), cols=str(cols)), True


def _write_table(ws, frame: pd.DataFrame, *, filter_table: bool = True) -> None:
    import gspread.utils as gutils
    values = [list(frame.columns)] + [[_clean_cell(v) for v in row] for row in frame.itertuples(index=False, name=None)]
    ws.clear()
    ws.resize(rows=max(len(values) + 10, 100), cols=max(len(frame.columns), 12))
    ws.update(values, "A1")
    ws.freeze(rows=1, cols=3)
    last_col = gutils.rowcol_to_a1(1, len(frame.columns)).rstrip("1")
    ws.format(f"A1:{last_col}1", {
        "backgroundColor": {"red": 0.90, "green": 0.90, "blue": 0.90},
        "textFormat": {"bold": True, "foregroundColor": {"red": 0, "green": 0, "blue": 0}},
        "horizontalAlignment": "CENTER",
    })
    ws.format(f"A2:{last_col}{max(2, len(values))}", {"verticalAlignment": "MIDDLE", "wrapStrategy": "WRAP"})
    if filter_table and len(values) > 1:
        try:
            ws.clear_basic_filter()
        except Exception:  # noqa: BLE001
            pass
        ws.set_basic_filter(f"A1:{last_col}{len(values)}")


def _blank_overview(path: Path | str | None = None) -> pd.DataFrame:
    parts = []
    empty = pd.DataFrame(columns=["종목코드"])
    for market in ("KR", "US"):
        part = build_ai_snapshot(empty, "", market, path)
        part["상태"] = "업데이트 대기"
        part["Technical Signal"] = "업데이트 대기"
        parts.append(part)
    return pd.concat(parts, ignore_index=True).sort_values("ID").reset_index(drop=True)


def _upsert_overview(sh, current: pd.DataFrame, path: Path | str | None = None) -> pd.DataFrame:
    ws, _ = _worksheet(sh, OVERVIEW_SHEET, 200, len(OVERVIEW_COLUMNS) + 2)
    base = {str(r["티커"]): r for r in _blank_overview(path).to_dict("records")}
    for row in _records(ws.get_all_values()):
        ticker = _code(row.get("티커"), "KR" if row.get("국가") == "KR" else None)
        if ticker in base:
            base[ticker].update({k: row.get(k, "") for k in OVERVIEW_COLUMNS})
    for row in current.to_dict("records"):
        base[str(row["티커"])].update(row)
    combined = pd.DataFrame(base.values())[OVERVIEW_COLUMNS]
    combined["ID"] = pd.to_numeric(combined["ID"], errors="coerce")
    combined = combined.sort_values("ID").reset_index(drop=True)
    _write_table(ws, combined)
    return combined


def _append_history(sh, current: pd.DataFrame, run_date: str) -> None:
    ws, _ = _worksheet(sh, HISTORY_SHEET, 2000, len(HISTORY_COLUMNS) + 2)
    old = _records(ws.get_all_values())
    tickers = set(current["티커"].astype(str))
    old = [r for r in old if not (r.get("날짜") == run_date and str(r.get("티커")) in tickers)]
    new = current.rename(columns={"기준일": "날짜"})[HISTORY_COLUMNS].to_dict("records")
    frame = pd.DataFrame(old + new, columns=HISTORY_COLUMNS)
    if len(frame):
        frame = frame.sort_values(["날짜", "티커"], ascending=[True, True]).reset_index(drop=True)
    _write_table(ws, frame)


def _truthy(value) -> bool:
    return value is True or str(value).strip().upper() == "TRUE"


def _float_values(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series.replace("", np.nan), errors="coerce").dropna()


def _upsert_kpis(sh, overview: pd.DataFrame, run_date: str) -> None:
    ws, _ = _worksheet(sh, KPI_SHEET, 100, len(KPI_COLUMNS) + 2)
    previous = {r.get("KPI"): r for r in _records(ws.get_all_values()) if r.get("KPI")}
    waiting_states = {"", "업데이트 대기", "스크리너 미포함", "다음 실행부터 추적"}
    tracked = overview[
        overview["기준일"].astype(str).ne("")
        & ~overview["상태"].astype(str).isin(waiting_states)
    ]
    rs = _float_values(tracked["RS Score"]) if len(tracked) else pd.Series(dtype=float)
    auto = [
        ("AI 유니버스", "75종목 정적 유니버스", len(overview)),
        ("당일/최근 추적 완료", "KR·US 중 최신 결과가 연결된 종목", len(tracked)),
        ("정상 판정", "상태=OK", int(tracked["상태"].eq("OK").sum())),
        ("8개 조건 전부 통과", "Minervini 8조건 AND", int(tracked["전체통과"].map(_truthy).sum())),
        ("TREND_OK v2", "v2 추세 조건 통과", int(tracked["TREND OK v2"].map(_truthy).sum())),
        ("SETUP_READY", "수축·피벗·RS를 포함한 준비 상태", int(tracked["SETUP READY"].map(_truthy).sum())),
        ("GO 상태", "GO_BREAKOUT 또는 GO_PULLBACK", int(tracked["Entry State"].astype(str).str.startswith("GO_").sum())),
        ("평균 RS Score", "유효 RS Score 평균", round(float(rs.mean()), 2) if len(rs) else ""),
    ]
    rows = []
    for name, definition, value in auto:
        old = previous.get(name, {})
        rows.append({
            "구분": "자동 스크리너", "KPI": name, "정의": definition, "주기": "일별",
            "연결 밸류체인": "75종목 전체", "현재값": value, "이전값": old.get("현재값", ""),
            "변화/신호": "", "기준일": run_date, "업데이트 방식": "자동",
            "체크포인트": "KR·US 마지막 실행일 차이를 함께 확인", "출처/링크": "SEPA 스크리너",
        })
    for name, definition, cadence, chain, checkpoint in INDUSTRY_KPIS:
        old = previous.get(name, {})
        rows.append({
            "구분": "산업 선행지표", "KPI": name, "정의": definition, "주기": cadence,
            "연결 밸류체인": chain, "현재값": old.get("현재값", ""), "이전값": old.get("이전값", ""),
            "변화/신호": old.get("변화/신호", ""), "기준일": old.get("기준일", ""),
            "업데이트 방식": "IR/산업자료 수동", "체크포인트": checkpoint,
            "출처/링크": old.get("출처/링크", ""),
        })
    _write_table(ws, pd.DataFrame(rows, columns=KPI_COLUMNS))


def update_ai_tracker_sheets(sh, screen_df: pd.DataFrame, run_date: str, market_key: str,
                             path: Path | str | None = None) -> None:
    """한 시장 실행 결과를 AI 현황·이력·KPI 3개 탭에 원자적으로 반영한다."""
    current = build_ai_snapshot(screen_df, run_date, market_key, path)
    overview = _upsert_overview(sh, current, path)
    _append_history(sh, current, run_date)
    _upsert_kpis(sh, overview, run_date)
    logger.info("AI 트래커 갱신 완료: %s %d종목 → %s / %s / %s",
                market_key, len(current), OVERVIEW_SHEET, HISTORY_SHEET, KPI_SHEET)
