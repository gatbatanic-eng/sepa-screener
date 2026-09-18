"""
technical_signals/config.py — 임계값·파라미터
================================================

모든 매직넘버는 여기에 모은다(`screening.py`의 관례와 동일). 지표 자체의
표준 파라미터(MACD 12/26/9, RSI 14, 스토캐스틱 14/3/3, 볼린저 20/2σ, ADX 14)는
업계 표준값을 그대로 쓴다 — 임의로 최적화하지 않았다.
"""
from __future__ import annotations

# --- 유니버스 ---
# 코스피+코스닥 "시총 상위 N"을 원래 계획했으나 fdr.StockListing의 Marcap이
# 현재 항상 NaN이라(data.py 참고) 랭킹 없이 전체를 스크리닝한다.
MIN_TRADING_DAYS = 260          # 지표 계산에 필요한 최소 거래일 수(SMA200 등)
HISTORY_CALENDAR_DAYS = 450     # fdr 조회 시작일 = 오늘 - 이 값 (주말/휴장 고려 여유)

# --- 이동평균 골든/데드크로스 ---
MA_FAST = 50
MA_SLOW = 200
CROSS_RECENT_WINDOW = 5         # 최근 N거래일 내 교차면 "신호 발생"으로 본다

# --- MACD ---
MACD_FAST = 12
MACD_SLOW = 26
MACD_SIGNAL = 9
MACD_RECENT_WINDOW = 3

# --- RSI ---
RSI_PERIOD = 14
RSI_OVERSOLD = 30
RSI_OVERBOUGHT = 70
RSI_RECENT_WINDOW = 3           # 30선 상향 이탈(회복) 신호의 최근 판정 구간

# --- 스토캐스틱(슬로우) ---
STOCH_K_PERIOD = 14
STOCH_K_SMOOTH = 3
STOCH_D_SMOOTH = 3
STOCH_OVERSOLD = 20
STOCH_RECENT_WINDOW = 3

# --- 볼린저밴드 ---
BB_PERIOD = 20
BB_STD = 2.0
BB_BREAKOUT_VOL_RATIO = 1.5     # 상단돌파 시 요구하는 거래량/20일평균거래량 배율
BB_SQUEEZE_LOOKBACK = 120       # 밴드폭이 이 기간 중 최저면 "수축(스퀴즈)"

# --- OBV ---
OBV_SMA_PERIOD = 20             # OBV가 이 평균보다 위면 "거래량 추세 상승" 확인 신호

# --- ADX(추세 강도, 그 자체로는 매수 신호 아님 — 다른 신호의 신뢰도 보정용) ---
ADX_PERIOD = 14
ADX_TRENDING_MIN = 25.0

# --- 이격도(정보용 수치, 신호 아님) ---
DISPARITY_MA_PERIOD = 20

# --- 복합점수(0~100, 랭킹용) 가중치 ---
# 존재하는 서브지표만 가중평균 후 재정규화한다(sepa/setup.py의 setup_quality_score와 동일한 방식).
COMPOSITE_WEIGHTS = {
    "golden_cross": 0.20,
    "macd_bull": 0.20,
    "rsi_recover": 0.15,
    "stoch_bull": 0.15,
    "bb_signal": 0.15,
    "obv_rising": 0.10,
    "adx_trending": 0.05,
}
