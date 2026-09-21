"""
technical_signals/config.py — 임계값·파라미터
================================================

모든 매직넘버는 여기에 모은다(`screening.py`의 관례와 동일). 지표 자체의
표준 파라미터(MACD 12/26/9, RSI 14, 스토캐스틱 14/3/3, 볼린저 20/2σ, ADX 14)는
업계 표준값을 그대로 쓴다 — 임의로 최적화하지 않았다.

2026-09-18 리뷰 반영: 지표 중복(모멘텀 지표 3개를 flat 가중합하면 사실상
같은 상승을 여러 번 세는 문제), 진입 위치 게이트 부재, 손절/리스크 부재,
시장 국면 무시, 추세추종·박스권반등 혼재 문제를 지적받고 구조를 다시 짰다.
백테스트(룩어헤드 방지 포함)는 이번 범위에 포함하지 않는다 — technical_signals
에는 아직 SEPA의 research_tracker.py 같은 백테스트 인프라 자체가 없어서
지표 하나 고치는 수준이 아니라 별도 서브시스템이 필요하기 때문이다.
"""
from __future__ import annotations

# --- 유니버스 ---
# 코스피+코스닥 "시총 상위 N"을 원래 계획했으나 fdr.StockListing의 Marcap이
# 현재 항상 NaN이라(data.py 참고) 랭킹 없이 전체를 스크리닝한다.
MIN_TRADING_DAYS = 260          # 지표 계산에 필요한 최소 거래일 수(SMA200 등)
HISTORY_CALENDAR_DAYS = 450     # fdr 조회 시작일 = 오늘 - 이 값 (주말/휴장 고려 여유)

# --- 이동평균 골든/데드크로스 + 정배열 상태 ---
MA_FAST = 50
MA_SLOW = 200
CROSS_RECENT_WINDOW = 5         # 최근 N거래일 내 교차면 "신호 발생"(이벤트)으로 본다
# trend_aligned(상태, 이벤트 아님) = close>SMA200 AND SMA50>SMA200 — 매수검토 필수조건

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
RSI_CHASE_WARNING = 75.0        # 이 이상이면 "추격 경고"(진입보류 사유)
RSI_HEALTHY_TREND_LO = 50.0     # 추세추종 트랙의 "건강한 상승" 구간(과매도 회복이 아니라 유지 여부)
RSI_HEALTHY_TREND_HI = 70.0

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

# --- 이격도(정보용 수치, 박스권반등 트랙의 "위치" 판정에도 쓰인다) ---
DISPARITY_MA_PERIOD = 20
REBOUND_DISPARITY_MAX = -5.0    # 이격도가 이보다 낮으면(많이 눌렸으면) 박스권반등 "위치" 조건 충족

# --- 피벗(최근 N거래일 고가) 돌파 = 추세추종 트랙의 "진입 트리거" ---
PIVOT_LOOKBACK = 60             # 최근 약 3개월 고가를 피벗으로 삼는다(당일 제외, 전일까지)
BREAKOUT_RECENT_WINDOW = 5      # 돌파 이후 최근 N거래일까지는 "막 돌파했다"로 본다
BREAKOUT_CLV_MIN = 0.60         # 돌파 당일 종가 위치(고가-저가 구간 내)가 이 이상이어야 "확인된 돌파"
# 거래량뿐 아니라 거래대금 최소치도 요구한다(소형주 거래량 왜곡 방지 — 7번 리뷰 반영).
# 완전한 ADV 정규화는 하지 않고, 절대 거래대금 하한선만 둔다.
MIN_TRADING_VALUE_KRW = 300_000_000       # 3억원
MIN_TRADING_VALUE_USD = 300_000            # 30만 달러

# --- 손절/리스크 (SEPA sepa/exit.py의 구조적 STOP과 같은 철학, 독립 재구현) ---
SWING_LOW_LOOKBACK = 40         # 최근 지지선(스윙저점) 탐색 구간
ATR_STOP_MULT = 1.75            # ATR 기준 손절 = close - 1.75*ATR (1.5~2.0 권장 범위의 중간값)
STOP_ATR_BUFFER_MULT = 0.5      # 구조적 손절 = 스윙저점 - 0.5*ATR (노이즈 여유)
MAX_RISK_PCT = 7.0              # 이 초과면 진입금지/리스크플래그

# --- 시장 국면 게이트 (개별 종목 신호와 별개, 지수·breadth 기반) ---
KR_INDEX_CODES = {"KOSPI": "KS11", "KOSDAQ": "KQ11"}
US_INDEX_CODE = "US500"
BREADTH_MA_PERIOD = 50          # breadth = 이 이평선 위에 있는 종목 비율
BREADTH_GREEN_MIN = 0.50
BREADTH_RED_MAX = 0.30

# --- 그룹별 점수(0~100, 랭킹용) — 그룹 내부는 "경쟁"(존재하는 멤버의 True 비율만큼만),
# 그룹 간에는 합산. 같은 그룹 지표가 전부 좋아도 그 그룹 배점(최댓값)을 못 넘는다.
TREND_GROUP_WEIGHTS = {"trend": 30.0, "trigger": 25.0, "momentum": 20.0, "volatility": 15.0, "volume": 10.0}
REBOUND_GROUP_WEIGHTS = {"position": 30.0, "trigger": 25.0, "momentum": 20.0, "volatility": 15.0, "volume": 10.0}

# --- 매수검토/진입준비/진입보류 판정 기준값 ---
SCORE_REVIEW_MIN = 70.0
SCORE_READY_MIN = 80.0
PIVOT_DISTANCE_MAX_REVIEW = 3.0   # 매수검토: 피벗대비 이격 +3% 이내
PIVOT_DISTANCE_HOLD = 5.0         # 진입보류: 피벗대비 +5% 이상(추격)
WATCH_PIVOT_DISTANCE_MIN = -3.0    # 관찰: 피벗 아래 -3% ~ 위 +3% 안(매수검토 상한과 동일)
READY_VOLUME_RATIO_MIN = 1.3      # 진입준비: 거래량/50일평균 최소 배율
