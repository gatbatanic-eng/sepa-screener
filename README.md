# Trending Value–Quality 복합 팩터 스크리너 (`screener/`)

가치(Value 40%) + 모멘텀(Momentum 30%) + 퀄리티(Quality 30%) 3팩터를 유니버스 내
백분위 순위로 결합해 **BUY / WATCH / SELL / NEUTRAL** 신호를 내는 스크리너.
전략 근거·팩터 정의·신호 임계값은 [STRATEGY.md](STRATEGY.md) 참고.

> 이 저장소에는 별도의 스크리너가 더 있습니다: SEPA 스크리너([screening.py](screening.py)),
> RANGE-MR & V-REBOUND([range_vrebound/](range_vrebound/)). 이 문서는 `screener/` 패키지 전용입니다.

## 구성

| 파일 | 역할 |
|---|---|
| `screener/schema.py` | 표준 스키마 정의 (데이터 소스 ↔ 팩터 로직 계약) |
| `screener/factors.py` | 시장 무관 순수 로직 — 팩터 점수 계산 |
| `screener/signals.py` | BUY/WATCH/SELL/NEUTRAL 판정 + 트레일링 스탑 |
| `screener/data_kr.py` | 한국(KOSPI+KOSDAQ) 유니버스 → 표준 스키마 |
| `screener/data_us.py` | 미국(S&P 500) 유니버스 → 표준 스키마 |
| `screener/main.py` | CLI: 수집 → 스코어 → 판정 → CSV + 콘솔 요약 |
| `screener/report.py` | 랭킹 리포트 (Markdown) 생성 |
| `screener/index_builder.py` | 추적 지수(스크리너 인덱스) 순방향 빌더 |
| `screener/generate_dashboard.py` | 정적 HTML 대시보드 생성 (`docs/screener/`) |
| `tests/test_factors_synthetic.py` | 합성 데이터로 factors/signals 검증 (네트워크 불필요) |
| `tests/test_index_synthetic.py` | mock 데이터로 index_builder 검증 (네트워크 불필요) |

## 설치

```bash
pip install -r requirements.txt
```

주요 의존성: `finance-datareader`, `pandas`, `numpy`, `requests`, `yfinance`.

## 실행

### 1) 스크리닝

```bash
# 한국+미국 전체
python -m screener.main --market all

# 빠른 점검 (소규모)
python -m screener.main --market kr --kr-max-tickers 20

# 보유종목 트레일링 스탑까지 판정
python -m screener.main --market all --holdings my_holdings.csv --output output/screening_result.csv
```

`my_holdings.csv` 형식:

```csv
symbol,buy_price,peak_price
005930,72000,81000
AAPL,180.5,210.0
```

주요 옵션: `--kr-max-tickers`(기본 300, 0=무제한), `--us-max-tickers`(기본 120),
`--kr-min-marcap`(원, 기본 1,000억), `--us-min-marcap`(달러, 기본 20억), `--top`(콘솔 상위 N).

결과 CSV(`output/screening_result.csv`)에는 종목별 3팩터 세부 점수, 종합 점수,
신호, 판정 사유가 들어간다.

### 2) 랭킹 리포트

```bash
python -m screener.report --top 20
```

`output/report_YYYYMMDD.md` 를 생성하고 콘솔에 신호별 요약을 출력한다.
시장(한국/미국)별 섹션 → 신호(BUY/WATCH/SELL)별 상위 종목 표.

### 3) 추적 지수 (스크리너 인덱스)

```bash
python -m screener.index_builder --market all --top-n 20 --rebalance monthly
```

BUY 신호 상위 N종목으로 **동일가중** 바스켓을 구성하고, 실행할 때마다
지수값을 시계열로 누적 기록한다. 나스닥지수처럼 시간에 따른 성과 추적용.

- 상태: `output/index_state.json` (기준값·리밸런싱일·구성종목·entry_price)
- 히스토리: `output/index_history.csv` (`date, index_value, num_constituents, is_rebalance_day`)
- 첫 실행 시 기준값 **1000.0** 으로 개시.
- 리밸런싱: 기본 월 1회(월이 바뀐 뒤 첫 실행). `--rebalance weekly` 가능.
- 리밸런싱 시점에 BUY 신호가 0개면 → 기존 바스켓을 그대로 유지(경고만 로그).
- BUY 가 N개보다 적으면 있는 만큼만 사용 (WATCH 로 채우지 않음).

> ⚠️ **주의:** 이 지수는 **실시간 순방향 추적**이며 과거 백테스트가 아니다.
> 지수값은 **거래비용·세금·슬리피지가 반영되지 않은 이론치**다.
> 매 실행 시점의 종가를 쓰므로 실행 빈도·시각에 따라 경로가 달라질 수 있다.

### 4) 웹 대시보드

```bash
python -m screener.generate_dashboard
```

`docs/screener/index.html` (자기완결형 정적 페이지)을 생성한다. GitHub Pages 가 `docs/` 를
서빙하므로 **https://gatbatanic-eng.github.io/sepa-screener/screener/** 로 접근한다.

- 스크리너 인덱스 시계열 차트(리밸런싱일 표시), 신호별 요약 카드, 실행일별 신호 수 추이
- 한국/미국 탭, 신호 필터, 정렬 가능한 종목 표(3팩터 세부 점수 + 판정 사유)
- 상단에 SEPA·RANGE-MR/V-REBOUND 대시보드로 가는 네비게이션

`docs/screener/data/signal_history.json` 에 실행일별 신호 수가 순방향 누적된다.

### 일반적인 사용 흐름

```bash
python -m screener.main --market all           # 1. 스크리닝 → screening_result.csv
python -m screener.report                       # 2. 리포트
python -m screener.index_builder --top-n 20      # 3. 지수 갱신 (screening_result.csv 사용)
python -m screener.generate_dashboard           # 4. 대시보드 갱신
```

### 자동화 (GitHub Actions)

`.github/workflows/screener_daily.yml` 이 평일 2회(한국장·미국장 마감 후) 위 4단계를 실행하고
`output/index_state.json`, `output/index_history.csv`, `docs/screener/` 를 저장소에 커밋한다.
`workflow_dispatch` 로 수동 실행 및 종목 수 조절도 가능하다.

## 테스트

```bash
python tests/test_factors_synthetic.py
python tests/test_index_synthetic.py
# 또는
pytest tests/
```

두 테스트 모두 네트워크가 필요 없다.

## 알려진 한계

- **실시간 접속 필요.** 한국은 FinanceDataReader + 네이버 금융, 미국은 yfinance 에
  매 실행 접속한다. 네트워크가 막힌 환경에서는 합성 데이터 테스트만 가능하다.
- **당초 명세는 한국 데이터에 pykrx 를 지정**했으나, KRX 데이터포털이 비로그인
  JSON 요청을 차단(HTTP 400 "LOGOUT")하도록 바뀌어 pykrx 1.2.7 이 동작하지 않는다.
  동일하게 무료·공개인 FinanceDataReader(유니버스·가격) + 네이버 금융 모바일
  API(재무지표)로 대체했다. 표준 스키마 출력은 명세 그대로다.
- **재무 데이터 시점 혼재.** PER/PBR 등은 소스가 주는 최신 값(분기/연간),
  ROE·부채비율·순이익은 최근 연간 실적 기준. 가격·모멘텀은 당일. 정밀
  백테스트용이 아니라 현재 시점 스크리닝용.
- **EV/EBITDA** 는 한국 종목에서 신뢰 가능한 무료 소스가 없어 항상 NaN (팩터
  계산에서 자동 제외).
- **yfinance `.info`** 는 종목별 개별 호출이라 느리고 간헐적으로 429 된다.
  `--us-max-tickers` 로 종목 수를 제한한다.
