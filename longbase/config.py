"""longbase 임계값. 전부 여기서만 정의한다."""
from dataclasses import dataclass


@dataclass(frozen=True)
class LongBaseConfig:
    # --- 롱베이스 ---
    min_base_days: int = 120          # 6개월 이상 (거래일)
    max_base_days: int = 500          # 이보다 긴 베이스는 500일로 자른다
    max_depth: float = 0.35           # 베이스 깊이(고점 대비 저점 하락) 상한
    late_contraction_max: float = 0.90  # 후반 1/3 평균 일중변동폭 / 앞 2/3 평균 (<1 이면 수축)
    # --- 돌파 ---
    vol_avg_days: int = 50
    min_vol_ratio: float = 1.5        # 돌파일 거래량 / 직전 50일 평균
    min_clv: float = 0.6              # (종가-저가)/(고가-저가)
    recent_days: int = 10             # 최근 N거래일 이내 돌파만
    max_extension: float = 0.10       # 현재 종가가 피벗 대비 +10% 초과면 과열 제외
    # --- 추세/상대강도/유동성 ---
    sma_days: int = 200
    rs_min_percentile: float = 70.0   # 유니버스 내 12개월(6개월 가중) 수익률 백분위
    min_bars: int = 260
    min_trading_value_kr: float = 1_000_000_000   # 20일 평균 거래대금 10억원
    min_trading_value_us: float = 1_000_000       # 100만 달러
    history_calendar_days: int = 1100


CONFIG = LongBaseConfig()
