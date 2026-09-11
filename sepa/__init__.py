"""
sepa/ — SEPA Screener v2 코어 로직 (순수 함수 + config)
========================================================

기존 ``screening.py`` 의 8개 조건(= TREND TEMPLATE)은 그대로 유지하고, 그 위에
SETUP / ENTRY / EXIT / MARKET REGIME 단계를 얹어 "좋은 종목 + 좋은 셋업 + 실제
진입 타점 + 실패/매도 신호" 까지 판별하는 상태 머신을 제공한다.

설계 원칙
---------
- 이 패키지의 모든 함수는 **순수 함수**다. 네트워크 호출·파일 IO·전역 상태 없음.
  입력은 pandas Series/DataFrame, 출력은 값/dataclass.
- **look-ahead 금지**: "오늘" 값을 계산할 때 미래 봉을 쓰지 않는다. rolling/shift/
  ewm 은 본질적으로 인과적이며, 스윙(fractal)처럼 미래 봉 확인이 필요한 경우
  "확정된 스윙만" 반환해 마지막 ``right`` 봉은 아직 스윙이 될 수 없게 한다.
- **데이터 부족 → None**: 억지로 0/False 로 변환하지 않는다.
- 모든 임계값은 :mod:`sepa.config` 의 ``SepaConfig`` 한 곳에서만 온다. 코드에
  숫자를 하드코딩하지 않는다.

모듈
----
- :mod:`sepa.config`      모든 파라미터 (dataclass ``SepaConfig`` + ``CONFIG``)
- :mod:`sepa.states`      상태 상수 (entry_state / exit_state / regime)
- :mod:`sepa.indicators`  SMA/EMA/ATR/rolling 등 인과적 지표
- :mod:`sepa.swings`      스윙 고점·저점 및 수축(contraction) 탐지
- :mod:`sepa.rs`          RS v2 (ER21/63/126/252 → 유니버스 percentile → 가중합)
- :mod:`sepa.pivot`       base 내부 저항선(피벗) 산정 (전일까지 데이터만 사용)
- :mod:`sepa.setup`       SETUP 엔진 (변동성·거래량 수축, 셋업 품질 점수)
- :mod:`sepa.entry`       ENTRY 상태 머신 (피벗 거리 구간 + 확인 돌파 + 눌림목)
- :mod:`sepa.exit`        EXIT 엔진 (FAST_FAIL / STOP / TREND_BREAK / TIME_STOP / PROFIT_ALERT)
- :mod:`sepa.regime`      시장 국면 (GREEN/YELLOW/RED/RECOVERY) + breadth + exposure
- :mod:`sepa.universe`    유니버스 선정 (유동성 / 시총 모드) + 우선주·스팩 제외
- :mod:`sepa.pipeline`    위 조각들을 종목 1개 단위로 조립 (screening.py 가 호출)
"""

from sepa.config import CONFIG, SepaConfig, load_config

__all__ = ["CONFIG", "SepaConfig", "load_config"]
