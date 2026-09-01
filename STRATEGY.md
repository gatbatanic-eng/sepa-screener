# 전략: Trending Value–Quality 복합 팩터 전략

## 1. 근거

| 연구 | 핵심 결과 | 이 전략에서 차용한 요소 |
|---|---|---|
| O'Shaughnessy, *What Works on Wall Street* — **Trending Value** | 가치 상위 10% 종목 중 6개월 모멘텀 상위 25종목. 1964–2009 백테스트 연 19.85~21.19% vs 시장(All Stocks) 11.22% | "저평가 종목 안에서 모멘텀으로 2차 선별" — Value + Momentum 결합 |
| Greenblatt, *The Little Book That Beats the Market* — **Magic Formula** | Earnings Yield + ROIC(자본수익률) 랭킹 결합이 장기 초과수익 | Value(수익수익률)와 Quality(자본효율)를 각각 랭크로 결합 |
| Asness, Moskowitz, Pedersen (2013) — **Value and Momentum Everywhere** | 가치와 모멘텀은 상관이 낮아(때로 음) 함께 쓰면 위험조정수익률(Sharpe)이 개별 팩터보다 개선 | 두 팩터를 독립 스코어로 두고 가중합 |
| Novy-Marx (2013), Piotroski F-score 계열 | 수익성(ROE/GPA)·재무건전성(부채)·흑자 지속이 미래 수익률과 양(+)의 관계 | Quality Score = ROE + 부채비율(역) + 흑자 여부 |

세 갈래를 종합해 **Value(40%) + Momentum(30%) + Quality(30%)** 3팩터 복합 스코어링을 사용한다.
가중치는 Trending Value가 가치를 1차 관문으로 두는 점을 반영해 Value에 최대 비중을 준 것이다.

## 2. 대상 유니버스

| 시장 | 구성 | 시가총액 필터(기본) | 데이터 소스 |
|---|---|---|---|
| 한국 (`kr`) | KOSPI + KOSDAQ 전체 상장 보통주 | 최소 **1,000억 원** | `pykrx` (무료, KRX 공개데이터) |
| 미국 (`us`) | S&P 500 구성종목 등 대형주 | 최소 **20억 달러** | `yfinance` (무료, Yahoo Finance) |

- 우선주·리츠·스팩·상장 1년 미만(200일 이동평균 산출 불가) 종목은 자동 제외.
- 모든 데이터는 무료 공개 소스만 사용한다.

## 3. 팩터 정의

유니버스 내에서 각 원지표를 **0~100 백분위 순위(percentile rank)** 로 변환한 뒤 결합한다.
결측 지표(NaN)는 순위 계산에서 그대로 NaN으로 남기고, 하위 스코어 평균 시 `skipna`로 자동 제외한다.
하위 스코어 전체가 결측이면 중립값 50을 사용한다.

### 3.1 Value Score (가중치 40%)
다음 4개 지표의 백분위 순위 평균:

| 지표 | 방향 | 비고 |
|---|---|---|
| P/E (per) | 낮을수록 좋음 → **역순위** | 0 이하(적자)는 결측 처리 |
| P/B (pbr) | 낮을수록 좋음 → **역순위** | 0 이하는 결측 처리 |
| EV/EBITDA (ev_ebitda) | 낮을수록 좋음 → **역순위** | 제공되지 않으면 결측 (KR은 대부분 결측) |
| 주주수익률 (shareholder_yield) = 배당수익률 + 자사주매입수익률 | 높을수록 좋음 → 정순위 | KR은 배당수익률만 반영될 수 있음 |

### 3.2 Momentum Score (가중치 30%)
다음 3개 지표의 백분위 순위 평균 (모두 높을수록 좋음, 정순위):

| 지표 | 정의 |
|---|---|
| ret_6m | 최근 126거래일(약 6개월) 가격수익률 |
| ret_12m | 최근 252거래일(약 12개월) 가격수익률 |
| price_to_sma200 | 현재가 / 200일 단순이동평균 − 1 |

### 3.3 Quality Score (가중치 30%)
다음 3개 지표의 백분위 순위 평균:

| 지표 | 방향 |
|---|---|
| ROE (%) | 높을수록 좋음 → 정순위 |
| 부채비율 debt_to_equity (%) | 낮을수록 좋음 → **역순위** (결측이면 제외) |
| 최근 순이익 흑자 여부 net_income_positive (0/1) | 흑자=100, 적자=0 |

### 3.4 Composite Score
```
Composite = 0.40 × Value + 0.30 × Momentum + 0.30 × Quality      (0~100)
```
구성 스코어가 NaN이면 해당 항목만 50으로 대체해 Composite는 항상 산출된다.

## 4. 신호 판정 기준 (임계값 고정)

평가 순서: **① 트레일링 스탑(보유종목) → ② BUY → ③ SELL → ④ WATCH → ⑤ NEUTRAL**
(BUY와 SELL 조건은 상호배타적이므로 순서로 인한 왜곡은 없다. 트레일링 스탑만 스코어와 무관하게 우선한다.)

### BUY — 아래 **모두** 충족
- Composite Score ≥ **80**
- 현재가 > 200일 이동평균선
- 6개월 모멘텀(ret_6m) ≥ **+3%** — 단순 양수가 아니라 의미 있는 상승폭
- ROE ≥ **8%** 그리고 최근 순이익 흑자
- 부채비율 ≤ **200%** (데이터가 없으면(NaN) 이 조건은 통과로 간주하고 사유에 명시)

### WATCH — BUY 미충족 상태에서 아래 중 **하나라도** 해당
- Composite Score **60 이상 80 미만**
- Composite Score ≥ 80 이지만 현재가가 200일선 근처(|현재가/200MA − 1| ≤ 5%)라 추세 확인 대기
- Composite Score ≥ 80 이지만 6개월 모멘텀이 +3% 미만
- Value Score ≥ 80 이지만 Momentum Score < 40 (저평가된 소외주)

### SELL — 아래 중 **하나라도** 해당
- Composite Score < **40**
- 현재가 < 200일 이동평균선 **그리고** 6개월 모멘텀 < 0%
- (보유종목 한정) 매수 후 고점(peak_price) 대비 **−15%** 이상 하락 — 트레일링 스탑, 스코어와 무관하게 최우선

### NEUTRAL
위 어디에도 해당하지 않는 경우.

## 5. 표준 스키마 (데이터 소스 → 팩터 로직 계약)

`data_kr.py` / `data_us.py`는 아래 컬럼을 가진 DataFrame을 반환해야 한다.
`factors.py`는 이 스키마만 알고 시장에는 무관하다.

| 컬럼 | 타입 | 의미 |
|---|---|---|
| symbol | str | 종목코드 (KR: 6자리, US: 티커) |
| name | str | 종목명 |
| market | str | `KOSPI` / `KOSDAQ` / `US` |
| price | float | 현재가(최근 종가) |
| sma200 | float | 200일 단순이동평균 |
| ret_6m | float | 6개월 가격수익률 (0.05 = +5%) |
| ret_12m | float | 12개월 가격수익률 |
| per | float | P/E (없으면 NaN) |
| pbr | float | P/B (없으면 NaN) |
| ev_ebitda | float | EV/EBITDA (없으면 NaN) |
| shareholder_yield | float | 배당수익률 + 자사주매입수익률 (0.03 = 3%) |
| roe | float | ROE, **퍼센트 단위** (12.5 = 12.5%) |
| debt_to_equity | float | 부채비율, **퍼센트 단위** (150 = 150%). 없으면 NaN |
| net_income_positive | bool | 최근 회계연도 순이익 흑자 여부 |

## 6. 알려진 한계

- **실시간 접속 필요.** pykrx는 KRX, yfinance는 Yahoo Finance에 매 실행 접속한다. 네트워크가
  제한된 환경에서는 합성 데이터 테스트(`tests/`)만 가능하다.
- **pykrx 한계.** 부채비율·EV/EBITDA를 직접 제공하지 않아 KR 종목은 해당 필드가 NaN이다.
  ROE는 `EPS/BPS`로, 흑자 여부는 `EPS>0`으로 근사한다. 주주수익률은 배당수익률만 반영된다.
- **yfinance `.info` 속도.** 종목별 개별 API 호출이라 느리고 간헐적으로 rate-limit(429)된다.
  기본 종목 수를 제한(`--us-max-tickers`, 기본 120)하고 재시도한다.
- **재무 데이터 시점 불일치.** 팩터의 재무지표는 소스가 제공하는 최신 값(분기/연간 혼재)이며
  가격·모멘텀은 당일 값이다. 정밀 백테스트용이 아니라 현재 시점 스크리닝용이다.
- **트레일링 스탑**은 `--holdings` CSV에 `peak_price`가 주어질 때만 판정한다. 고점 추적은
  사용자가 관리하거나 `index_builder`가 별도로 갱신한다.
