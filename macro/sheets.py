"""
sheets.py — 구글시트 저장 (선택)

환경변수 (기존 SEPA 스크리너 screening.py 와 동일한 이름·인증 방식):
  GOOGLE_SHEET_ID              스프레드시트 ID (URL 중간의 긴 문자열)
  GOOGLE_SERVICE_ACCOUNT_JSON  서비스계정 JSON 문자열 전체 (GitHub Secret 에 그대로 붙여넣기)
둘 중 하나라도 없으면 조용히 건너뜀.

탭 구조 (기존 SEPA 탭과 분리된 별도 탭):
  macro_daily   한 줄 = 하루. 컬럼: date, regime, score, signals, summary, 그리고 지표별 value/chg_1w/pct_1y
  macro_signals 규칙 발동 이력 (date, rule_id, group, score, message)
"""
from __future__ import annotations

import os
import json
import logging

log = logging.getLogger("macro.sheets")

# 기존 screening.py 와 동일한 스코프
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]


def _client():
    """screening.py 의 upload_to_google_sheets() 와 동일한 서비스계정 인증."""
    import gspread
    from google.oauth2.service_account import Credentials

    raw = os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"]
    # Windows/PowerShell 에서 Secret 값에 UTF-8 BOM 이 섞여 들어오는 경우 방어 (screening.py 와 동일)
    info = json.loads(raw.lstrip("﻿"))
    creds = Credentials.from_service_account_info(info, scopes=SCOPES)
    return gspread.authorize(creds)


def _ensure_ws(sh, title: str, header: list[str]):
    try:
        ws = sh.worksheet(title)
    except Exception:  # noqa: BLE001
        ws = sh.add_worksheet(title=title, rows=2000, cols=max(30, len(header)))
        ws.append_row(header)
        return ws
    if not ws.row_values(1):
        ws.append_row(header)
    return ws


def save(result: dict) -> bool:
    sid = os.environ.get("GOOGLE_SHEET_ID")
    if not sid or not os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON"):
        log.info("GOOGLE_SHEET_ID / GOOGLE_SERVICE_ACCOUNT_JSON 없음 → 구글시트 저장 생략")
        return False
    try:
        gc = _client()
        sh = gc.open_by_key(sid)
        today = result["generated_at"][:10]
        inds = result["indicators"]

        # --- macro_daily ---
        header = ["date", "regime", "score", "signals", "summary"]
        row = [today, result["regime_label"], result["score"],
               " | ".join(s["id"] for s in result["signals"]), result.get("summary") or ""]
        for iid, d in inds.items():
            header += [f"{iid}", f"{iid}_1w", f"{iid}_pct"]
            row += [d["value"], d["chg_1w"] if d["chg_1w"] is not None else d["sum_5d"], d["pct_1y"]]
        ws = _ensure_ws(sh, "macro_daily", header)
        existing = ws.col_values(1)
        if today in existing:  # 같은 날 재실행 → 덮어쓰기
            ws.update([row], f"A{existing.index(today) + 1}", value_input_option="USER_ENTERED")
        else:
            ws.append_row(row, value_input_option="USER_ENTERED")

        # --- macro_signals ---
        ws2 = _ensure_ws(sh, "macro_signals", ["date", "rule_id", "group", "score", "message"])
        rows = [[today, s["id"], s["group"], s["score"], s["message"]] for s in result["signals"]]
        if rows:
            ws2.append_rows(rows, value_input_option="USER_ENTERED")
        log.info("구글시트 저장 완료 (%s): macro_daily, macro_signals", today)
        return True
    except Exception as e:  # noqa: BLE001
        log.error("구글시트 저장 실패: %s", e)
        return False
