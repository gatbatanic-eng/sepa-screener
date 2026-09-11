# `sepa/` — SEPA Screener v2

기존 `screening.py`(미너비니 추세 템플릿 8개 조건 1차 필터)를 **상태 머신**으로 확장한다.

```
UNIVERSE → TREND → SETUP → READY → ENTRY → (POSITION/관리) → EXIT
```

목적은 "통과 종목 수를 줄이는 것"이 아니라 **① 상승추세 리더 → ② 변동성·매물 수축
셋업 → ③ 피벗 대기 → ④ 거래량·종가위치 확인된 돌파만 진입 → ⑤ 과열 종목 추격
금지 → ⑥ 돌파 실패 빠른 감지 → ⑦ 강한 종목 추세 보유**를 한 화면에서 구별하는 것.

## 상태

각 종목은 **독립적인 두 상태**를 가진다.

| 필드 | 값 |
|---|---|
| `entry_state` | `TREND_FAIL` · `TREND_OK` · `SETUP` · `WATCH` · `READY` · `BREAKOUT_UNCONFIRMED` · `GO_BREAKOUT` · `GO_PULLBACK` · `LATE` · `EXTENDED` · `FAILED` |
| `exit_state` | `HOLD` · `WATCH_EXIT` · `FAST_FAIL` · `STOP` · `TREND_BREAK` · `TIME_STOP` · `PROFIT_ALERT` |

`STOP` / `TIME_STOP` 은 진입가·진입일(포지션)이 있어야 판정된다. 스크리너 단독
에서는 가격행동 기반 경고(`FAST_FAIL` / `TREND_BREAK` / `WATCH_EXIT` / `PROFIT_ALERT`)
까지만 표시하고, 포지션 인지 로직은 `sepa/exit.py::evaluate_exit(position, ...)` 에
독립 모듈로 구현해 뒀다.

## 모듈

| 파일 | 역할 |
|---|---|
| `config.py` | **모든 임계값** (dataclass `SepaConfig`, 싱글턴 `CONFIG`). `sepa/config_overrides.json` 또는 env `SEPA_CONFIG_JSON` 으로 `"그룹.필드": 값` 형태 오버라이드 (백테스트 파라미터 스윕용) |
| `states.py` | 상태 상수 + 정렬/심각도 순위 |
| `indicators.py` | SMA/EMA/ATR/rolling — 전부 인과적(causal). look-ahead 회귀 테스트로 강제 |
| `swings.py` | fractal 스윙 고점/저점, 수축(contraction) 탐지. `detect_swings()` / `detect_contractions()` 는 교체 쉽게 독립 함수 |
| `rs.py` | RS v2 (아래) |
| `pivot.py` | base 내부 저항선. **전일까지 데이터만** 사용 (당일 자기참조 방지) |
| `setup.py` | SETUP 엔진 — base_length, range_10, ATR20/ATR60 수축, 거래량 dry-up, 피벗, 수축 횟수, `setup_ready`, `setup_quality_score`(0~100, 랭킹용) |
| `entry.py` | 피벗 거리 구간 분류 + 확인된 돌파(GO_BREAKOUT) + 눌림목(GO_PULLBACK) |
| `exit.py` | FAST_FAIL / structural STOP / TREND_BREAK / TIME_STOP / PROFIT_ALERT + `Position` |
| `regime.py` | 시장 국면 GREEN/YELLOW/RED/RECOVERY + breadth50 + 권장 진입비중 |
| `universe.py` | 유동성/시총 유니버스 선정 + 우선주·스팩 제외 |
| `pipeline.py` | 위 조각을 종목 1개 단위로 조립 (`evaluate_stock_v2`) + 유니버스 RS percentile |

## TREND (조건 1~7 유지, 조건 8 = RS v2)

조건 1~7 은 기존과 동일(SMA50/150/200 정배열, 종가 위치, 52주 저가 대비 +30%↑,
52주 고가 대비 25% 이내). 조건 8 만 **RS_SCORE ≥ 80** (`config.rs.rs_min`) 으로 교체.

- `high_proximity_ratio = close / high_52w` → `SUPER_LEADER`(≥0.90) / `LEADER`(≥0.85)
  / `NORMAL`(≥0.75) / `FAIL`. 조건 7(0.75)은 TREND 통과 조건으로 유지하되, 진입
  후보 우선순위는 0.85~0.90↑ 이 높게 평가된다.
- 8개 전부 충족 → `trend_ok = True`. 하나라도 `None` 이면 `trend_ok = None`(억지 False 아님).
- **레거시** `전체통과(8개AND)` (조건8 = 3·6·12개월 달력일 초과수익 백분위 ≥70) 컬럼은
  비교용으로 그대로 남는다.

## RS v2

1. 각 종목: 거래일 기준 초과수익 4개
   `ER_k = stock_return_k − benchmark_return_k`, k ∈ {21, 63, 126, 252}
   (benchmark 는 종목 거래일 인덱스에 ffill 정렬. KR: 소속시장 KOSPI/KOSDAQ 지수, US: S&P500)
2. 각 `ER_k` 를 **유니버스 내 percentile rank(0~100)** 로 변환 (데이터 정상 종목만 모수)
3. `RS_SCORE = 0.10·p21 + 0.40·p63 + 0.30·p126 + 0.20·p252` (0~100)
4. `rs_change_20d = RS_SCORE(오늘) − RS_SCORE(20거래일 전)`
   — 20거래일 전 시점으로 **유니버스를 다시 랭킹**해서 계산 (미래 데이터 미사용)
5. `RS_LINE = stock_close / benchmark_close`, 최근 126거래일 신고가 여부(`rs_line_new_high`)

기본 임계값: `RS_SCORE ≥ 80` TREND 통과, `≥ 90` 강한 리더.

> ⚠️ **논점**: base 구간에서는 중기(63일) 초과수익이 눌리므로, 타이트한 base 를
> 그리는 리더도 RS_SCORE 가 80 아래로 떨어질 수 있다. "base 중엔 리더 자격 보류"
> 라는 관점에서는 타당하지만, TREND_OK 수가 consolidation 국면에 줄어든다.
> `config.rs.rs_min` 으로 조정 가능. 실데이터로 분포를 본 뒤 판단 권장.

## SETUP (deterministic heuristic — "완전한 Minervini VCP 재현" 아님)

| 지표 | 계산 | 기본 조건 |
|---|---|---|
| `base_length` | 수축 시작 스윙 이후 거래일 수 (없으면 피벗 아래 머문 연속 봉) | ≥ 20 |
| `atr_contraction_ratio` | ATR20 / ATR60 | ≤ 0.75 |
| `volume_dryup_ratio` | 평균거래량10 / 평균거래량50 | ≤ 0.70 |
| `contraction_count` | 스윙 고점→저점 pullback 폭이 순차 감소한 다리 수 (예 18%→11%→6%) | ≥ 2 |
| `range_10_pct` | (10일 고가/10일 저가 − 1)·100 | ≤ 10% (**기본 quality factor**, `config.setup.range10_hard_filter=True` 시 hard) |

`setup_ready = trend_ok AND base_length≥20 AND atr_contraction≤0.75 AND vol_dryup≤0.70 AND contraction_count≥2`.
`setup_quality_score`(0~100) = RS / RS가속 / RS라인신고가 / 52주고점근접 / ATR수축 /
dry-up / 수축횟수 / range tightness / 피벗근접 의 가중 평균 — **랭킹용이며 GO
hard rule 을 대체하지 않는다**.

## Pivot

`config.pivot.min_bars_ago`(기본 1) 만큼 전까지의 데이터로만 계산. 우선순위:
base 구간(기본 60거래일) 내 **확정 스윙 고점**(base 최고가의 97%↑) → 없으면 전일까지 rolling 최고가.
`pivot_distance_pct = (close / pivot_price − 1)·100`.

## ENTRY

| `pivot_distance_pct` | 구간 |
|---|---|
| < −5 | `SETUP` |
| −5 ~ −2 | `WATCH` |
| −2 ~ 0 | `READY` |
| 0 ~ +3 | 돌파구간 → 확인 시 `GO_BREAKOUT`, 아니면 `BREAKOUT_UNCONFIRMED` |
| +3 ~ +5 | `LATE` |
| > +5 | `EXTENDED` (추격 금지) |

**Confirmed Breakout** = `0 ≤ pivot_distance ≤ 3` AND `today_volume / AvgVol50 ≥ 1.40`
AND `CLV = (close−low)/(high−low) ≥ 0.70` (high==low 는 0.5 처리).

**GO_PULLBACK** = 최근 10거래일 내 확인된 돌파 존재 AND 현재가가 피벗/EMA10/EMA20
중 하나에 1.5% 이내 AND 조정 거래량 축소(최근 3일 평균 ≤ 0.80·AvgVol20) AND
종가>전일·종가>시가 AND CLV≥0.60 AND (RS_SCORE≥80 또는 rs_change_20d ≥ −15).

## EXIT

| 유형 | 조건(기본) |
|---|---|
| `FAST_FAIL` | 돌파 후 ≤5거래일에 (`Close < Pivot` AND `Volume > AvgVol50`) 또는 2거래일 연속 `Close < Pivot`. → `entry_state = FAILED` |
| structural `STOP` | 마지막 의미있는 스윙 저점 − max(1%·저점, 0.5·ATR20). `risk_pct = (기준가−손절가)/기준가·100 > 7` 이면 `ENTRY_RISK_TOO_HIGH` 경고(손절가를 억지로 끌어올리지 않음) |
| `TREND_BREAK` | `Close < SMA50` (`confirm_days`=1 확인) — 대량거래(≥1.5·AvgVol50) 동반 여부를 사유에 표기 |
| `WATCH_EXIT` | `Close < EMA20`(또는 EMA10) 확인, 아직 SMA50 위 |
| `TIME_STOP` | 진입 후 ≥10거래일 AND MFE < +3% AND rs_change_20d ≤ 0 (**포지션 필요**) |
| `PROFIT_ALERT` | 급등(15일 +25%↑) / EMA10 대비 +20%↑ 이격 / 당일 거래량 2×↑ / 긴 윗꼬리 / 갭상승 후 종가약세 중 **2개 이상** — 경고만, 강제매도 아님 |

## MARKET REGIME

- `GREEN` 지수>SMA50>SMA200 / `YELLOW` 지수>SMA200 이나 SMA50 약화 / `RED` 지수<SMA200
  / `RECOVERY` 오늘 RED 아님 + 최근 10거래일 내 RED (우선순위 RED>RECOVERY>YELLOW>GREEN)
- `breadth_50` = 유니버스 중 `Close > SMA50` 비율 → STRONG(≥60%)/NORMAL(≥45%)/WEAK(≥30%)/RISK_OFF
- `entry_size_factor` = 국면별 기본(GREEN 1.0 / YELLOW 0.6 / RED 0.2 / RECOVERY 0.5)에
  breadth 로 감쇠 (WEAK ×0.8, RISK_OFF ×0.5). **권장치일 뿐 주문 기능 없음.**

## 유니버스

| 모드 | KR 선정 | 비고 |
|---|---|---|
| `liquidity` (기본) | 단일일 거래대금 상위 ~700 후보 조회 → 20거래일 평균 거래대금(=Σ close·volume) 상위 **600** 확정 | floor `5억원` 미만 제외 |
| `legacy_market_cap` | 시가총액 상위 200 | 기존 방식 |

우선주·스팩은 종목명 기반 제외. **거래정지·관리·위험종목은 FDR 리스팅에서
신뢰성 있게 판별할 수 없어 제외하지 않는다**(로그에 한계 명시). 미국은 S&P500
구성종목 유지.

## 알려진 한계 / 편향

- **비대칭 유니버스**: 미국은 시총 하한 없이 S&P500 전체, 한국만 유동성 상위 600.
- **RS v2 percentile 모수**가 시장 전체가 아니라 오늘 스캔한 유니버스(600/503).
  IBD RS(시장 전체 대비)와 절대 수준이 다르다.
- **base 구간 RS 하락**(위 RS v2 논점 참조).
- **VCP heuristic** 은 스윙 기반 근사. 실제 다중 파동 탐지·핸들 인식은 하지 않는다.
- **거래정지/관리종목 미제외**.
- **survivorship bias**: 미국 S&P500 을 과거 백테스트에 쓰면 *현재* 구성종목만
  과거에 적용돼 결과가 낙관적으로 편향된다. 백테스트 시 반드시 감안할 것.
- 스테이지(와인스타인 4단계)·베이스 카운트·펀더멘털·촉매는 여전히 자동 판정하지 않는다.

## 테스트

`python tests/test_sepa_v2.py` (또는 pytest). 각 신호(GO_BREAKOUT / BREAKOUT_UNCONFIRMED /
LATE / EXTENDED / GO_PULLBACK / FAST_FAIL / TIME_STOP / structural stop / regime /
setup_ready)의 발생과 **look-ahead 회귀**(절단 시계열 == 전체 시계열, 미래 봉을
붙여도 과거 피벗 불변)를 검증한다.
