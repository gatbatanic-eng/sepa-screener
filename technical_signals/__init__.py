"""
technical_signals — 순수 기술적 지표 기반 매수 신호 스크리너
==============================================================

SEPA(`sepa/`)의 추세 템플릿, 멀티팩터(`screener/`)의 팩터 랭킹, RANGE-MR·
V-REBOUND(`range_vrebound/`)의 되돌림 전략과는 독립된 4번째 서브시스템이다.
펀더멘털·추세템플릿 조건과 무관하게, 순수 가격·거래량 기술적 지표(이동평균
교차, MACD, RSI, 스토캐스틱, 볼린저밴드, OBV, ADX, 이격도)만으로 계산한다.

다른 서브시스템의 코드를 import하거나 수정하지 않는다(`range_vrebound/`와
동일한 원칙) — 코스피/코스닥·S&P500 유니버스를 고르는 방식(fdr.StockListing
기반 시가총액 상위 N / S&P500 전체)은 관례상 동일하게 따르되, 이 폴더 안에서
독립적으로 재구현한다.

모듈 구성
---------
- `indicators.py` : 인과적(causal) 지표 계산 순수 함수(SMA/EMA/MACD/RSI/
  스토캐스틱/볼린저/OBV/ADX/이격도). 미래 데이터를 보지 않는다.
- `signals.py`     : 지표 시계열로부터 "최근 N거래일 내 신호 발생" 불리언과
  0~100 랭킹용 복합점수를 만든다. 하드 매수 게이트가 아니다.
- `data.py`        : 네트워크 호출(유니버스·OHLCV 조회)만 모아둔다.
- `pipeline.py`    : 유니버스 조회 → OHLCV 조회 → 지표/신호 계산 → 레코드 생성.
- `run_daily_screen.py` : CLI 진입점.
- `generate_dashboard.py` : `docs/technical/index.html` 생성.

실행 방법 (technical_signals/ 안에서)
------------------------------------
    python run_daily_screen.py --market KR   # 또는 US, ALL
    python generate_dashboard.py
"""
