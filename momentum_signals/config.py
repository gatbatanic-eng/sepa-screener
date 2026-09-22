"""momentum_signals/config.py — 임계값 설정.

사용자 스펙: "모든 임계값(배점, RSI밴드, 이격도 상한 등)은 백테스트 결과에
따라 조정 가능한 변수로 취급" — 그래서 매직넘버를 코드에 흩어놓지 않고
전부 여기 모아둔다.

**반드시 직접 수정해야 하는 값**: TOTAL_EQUITY_KRW, TOTAL_EQUITY_USD
(계좌 총자산 — 자리표시자 상태로는 리스크 사이징이 실제 계좌와 안 맞는다).
"""
from __future__ import annotations

# --- 계좌 총자산 (자리표시자 — 반드시 실제 값으로 수정) -------------------
TOTAL_EQUITY_KRW = 10_000_000.0   # 국내 계좌 총자산(원). 실제 값으로 수정하세요.
TOTAL_EQUITY_USD = 10_000.0       # 미국 계좌 총자산(달러). 실제 값으로 수정하세요.
ACCOUNT_RISK_PCT = 1.0            # 종목당 계좌 리스크 상한(총자산 대비 %)

# US 포지션 상한액(원화 기준 3단계)을 달러로 환산할 때 쓰는 환율(자리표시자).
# 스펙이 상한액을 원화로만 줬기 때문에 US 상한액은 이 환율로 역산한다.
USD_KRW_RATE = 1_350.0

# --- 유니버스 ---------------------------------------------------------
# 국내 "시총 상위 200"은 FDR StockListing의 Marcap이 상시 NaN이라(다른
# 서브시스템들에서 이미 확인된 한계, sepa/universe.py의 liquidity 모드와
# 동일한 대응) 20일 평균 거래대금 상위 200으로 대체한다.
KR_UNIVERSE_TOP_N = 200
KR_UNIVERSE_CANDIDATE_N = 260      # 유동성 계산 전 후보 pool(상위 N보다 여유있게)
US_UNIVERSE = "S&P500"

MIN_TRADING_DAYS = 260             # RS 252일 계산에 필요한 최소 데이터
HISTORY_CALENDAR_DAYS = 420
RS_TREND_LOOKBACK = (21, 63, 126, 252)
RS_TREND_WEIGHTS = (0.10, 0.40, 0.30, 0.20)   # sepa/rs.py와 동일한 IBD 스타일 가중치

# --- 유동성 필터(스펙 3번) ---------------------------------------------
MIN_AVG_TRADING_VALUE_KRW = 2_000_000_000.0   # 국내 일평균거래대금 20억원
MIN_AVG_TRADING_VALUE_USD = 10_000_000.0      # 미국 일평균거래대금 $10M
LIQUIDITY_LOOKBACK_DAYS = 20

# --- 지표 파라미터 ------------------------------------------------------
PIVOT_LOOKBACK = 20                # "20일 고점"
RSI_PERIOD = 14
ATR_PERIOD = 14
DISPARITY_MA_PERIOD = 20
VOLUME_SMA_PERIOD = 50
SWING_LOW_LOOKBACK = 20            # "최근 스윙로우" — 명시적 lookback이 스펙에 없어 20일로 정함

# --- 하드 게이트(스펙 5번, AND, 최소요건만) ------------------------------
GATE_VOLUME_RATIO_MIN = 1.2        # 당일거래량 >= 50일평균 x 1.2
GATE_MAX_INITIAL_RISK_PCT = 5.5    # (진입가-손절가)/진입가 <= 5.5%

# --- 강도 점수(스펙 6번, 100점 만점, 78점 이상만 후보) --------------------
SCORE_MIN = 78.0
SCORE_WEIGHTS = {
    "rs": 25.0,
    "volume": 15.0,
    "pivot": 10.0,
    "clv": 15.0,
    "rsi": 15.0,
    "disparity": 10.0,
    "risk_efficiency": 10.0,
}
VOLUME_SCORE_FULL_RATIO = 1.5      # 거래량 15점 만점 배율
PIVOT_SCORE_OPTIMAL_LOW = -2.0     # 20일 고점 대비 최적 구간 하한(%)
PIVOT_SCORE_OPTIMAL_HIGH = 3.0     # 20일 고점 대비 최적 구간 상한(%)
PIVOT_SCORE_DECAY_RANGE = 6.0      # 최적 구간을 벗어난 뒤 0점까지 추가로 허용하는 폭(%)
CLV_SCORE_FULL = 0.6               # CLV 15점 만점 기준
RSI_SCORE_BAND_LOW = 55.0
RSI_SCORE_BAND_HIGH = 78.0
RSI_SCORE_DECAY_RANGE = 20.0       # 밴드를 벗어난 뒤 0점까지 추가로 허용하는 RSI 폭
DISPARITY_SCORE_FULL_MAX = 22.0    # 이격도 10점 만점 상한(%)
DISPARITY_SCORE_DECAY_RANGE = 25.0 # 상한을 넘은 뒤 0점까지 추가로 허용하는 폭(%)
# 손절폭 대비 변동성 효율: 스펙에 목표가/기대수익 모델이 없어, 리스크 상한
# 대비 실제 리스크가 작을수록(=효율적으로 리스크를 쓸수록) 가점하는 근사치로
# 정의한다 — 실제 목표가 기반 손익비가 아님을 대시보드에 명시한다.
RISK_EFFICIENCY_REFERENCE_PCT = GATE_MAX_INITIAL_RISK_PCT

# --- 후보 압축(스펙 7번) -------------------------------------------------
CANDIDATE_TOP5_N = 5
FINALIST_N = 3
ENTRY_N = 2

# --- 시장 레짐(스펙 8번) -------------------------------------------------
REGIME_MA_PERIOD = 20
KR_INDEX_CODE = "KS11"
US_INDEX_CODE = "US500"

# --- 동시보유 제한(스펙 9번, 섹터 제한은 데이터 부재로 이번엔 생략) ---------
MAX_CONCURRENT_POSITIONS = 6

# --- 포지션 사이징(스펙) -------------------------------------------------
# (하한, 초과 시 다음 구간, 상한액_KRW)
SIZE_TIERS_KRW = (
    (78.0, 85.0, 3_000_000.0),
    (85.0, 92.0, 3_500_000.0),
    (92.0, 101.0, 4_000_000.0),
)

# --- 손절/포지션 관리 ----------------------------------------------------
ATR_STOP_MULT = 2.0                # 초기 손절: 종가 - ATR14*2
TRAILING_ATR_MULT = 1.5            # +2R 이후 트레일링: 종가 - ATR14*1.5
R1_MULTIPLE = 1.0
R2_MULTIPLE = 2.0
R2_PARTIAL_SELL_FRACTION = 1.0 / 3.0
TIME_STOP_TRADING_DAYS = 5         # 시장별 개별 카운트
TIME_STOP_MIN_RETURN_PCT = 3.0

# --- 기록 보관(스펙 7번) --------------------------------------------------
HISTORY_MAX_SESSIONS = 180         # 최근 180세션(날짜 수 기준)
