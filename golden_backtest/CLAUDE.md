# Golden Code 전략 백테스트 모듈 — 프로젝트 지침

이 파일은 Claude Code가 이 프로젝트에서 작업할 때 항상 지켜야 할 규칙이다.
전략의 세부 규칙은 `golden_backtest/docs/strategy_spec_v1.md`가 원본이다. 이 파일과 규격 문서가 충돌하면 규격 문서를 따르고, 충돌 사실을 사용자에게 알린다.

## 1. 목적

Golden Code 스크리너가 고른 종목에 대해 "어떤 진입 방식이 기대값이 높은가"를 검증한다.
목표는 예측 정확도가 아니라 **전략별 거래 단위 R-multiple 분포**를 정직하게 측정하는 것이다.
결과가 좋아 보이게 만드는 것은 목표가 아니다. 나쁜 결과도 그대로 보고한다.

## 2. 절대 원칙 (위반 금지)

1. **미래 정보 누수 금지.** t일 종가까지의 정보로 판단하고, 주문은 t+1에 실행한다. 롤링 지표, 52주 고점, 사상 최고가, 상대강도 계산에서 특히 주의한다.
2. **접두 불변성 테스트 필수.** 데이터를 t일에서 잘라 돌린 결과와 전체 데이터로 돌린 결과의 t일까지 신호가 같아야 한다. 모든 전략에 적용하고, 실패하면 머지하지 않는다.
   **모든 전략에 아래 두 가지를 모두 건다 (하나만으로는 부족하다).**
   - (1) 신호 열 직접 검사: `prepare()`가 만든 신호 열을 `tests/lookahead.find_prefix_violations`로 잘라 비교한다.
   - (2) 엔진 거래 검사: 데이터를 잘라 `simulate()`한 확정 거래가 전체 실행의 같은 구간과 같은지 본다.
   거래 검사만으로는 한 봉짜리 누수를 못 잡는다(누수가 영향을 주는 마지막 봉의 신호는 잘린 데이터 밖에서 체결된다). 새 전략은 두 검사와 함께, 미래를 쓰는 변형이 (1)에서 적발되는지도 확인한다.
3. **체결·비용·갭·동시발생 처리는 `engine/` 한 곳에서만.** 전략 코드는 주문 의도(EntryIntent / ExitIntent)만 반환한다. 전략 파일 안에서 체결가를 계산하지 않는다.
4. **파라미터 최적화 금지.** 모든 수치는 `config/strategies/*.yaml`의 규격 값으로 고정한다. 성과를 보고 값을 바꾸지 않는다. 민감도 테스트(±20%)는 `evaluation/sensitivity.py`에서만 한다.
5. **백테스트와 라이브 신호는 같은 전략 코드를 쓴다.** `live/`는 `strategies/`를 import해서 쓰고, 로직을 복제하지 않는다.
6. **모든 거래 기록에 전략 코드와 규격 버전을 남긴다.** 규칙이 바뀌면 버전을 올린다. 버전이 다른 결과를 섞어서 집계하지 않는다.
7. **출처 표기를 코드에도 유지한다.** 규칙마다 주석으로 `[원전]`, `[근사]`, `[임의]`, `[관례]`를 단다. 규격 문서에 없는 규칙을 추가하면 `[임의]`로 표시하고 사용자에게 알린다.

## 3. 전략 목록

| 코드 | 전략 | 진입 모델 | 상태 |
| --- | --- | --- | --- |
| A1 | 사상 최고가 돌파 + ATR 트레일링 | M1 | 손절 공식 미확정. 확정 전엔 A1-2005(10 ATR)로만 구현하고 표시 |
| A2 | Connors RSI(2) | M1 | 원전(무손절) / 안전판(3×ATR) 두 버전 |
| B1 | 터틀 System 2 (55일) | M2 | 엔진 검증용 첫 구현 대상 |
| B2 | SEPA 근사 | M1 | VCP 근사 수치는 [임의] |
| B3 | 쿨라매기 브레이크아웃 근사 | M2 | 2트랜치 부분청산 |

진입 모델:
- **M1 종가 확인형**: 종가로 신호 판정 → 다음날 시가 체결
- **M2 스탑주문형**: 전날 정한 돌파가에 매수 스탑 → 당일 고가 ≥ 돌파가면 max(시가, 돌파가) 체결

엔진 공통 규칙:
- 손절·청산 의도에는 진입 모델과 무관하게 `kind = intraday | close`를 둔다 (v1.3)
  - intraday: 시가 ≤ 손절가면 시가, 아니면 저가 ≤ 손절가일 때 손절가 체결. 진입 당일에도 적용 (M1은 시가 진입 후, M2는 돌파가 진입 후)
  - close: 종가 < 손절가면 다음날 시가 체결. 진입 당일 장중 저가로는 손절하지 않고 진입 당일 종가부터 판정
- M2 진입 당일 intraday 손절은 손절가 체결로 고정. M2는 장중 진입이라 진입 전 시가는 이후 손절 체결가와 무관하다
- 데이터 끝 미청산은 통계에서 빼지 않는다. 확정 거래 통계와 마지막 종가 평가(`exit_reason = open_mtm`) 통계를 둘 다 낸다
- 비용: 편도 수수료 0.05% + 슬리피지 0.05% (config에서 종목군별 조정 가능)
- 종목당 1포지션, 피라미딩 없음. 부분청산은 B3만
- 워밍업 게이트 [임의, 공통]: 종목별 데이터 시작 후 252봉 안의 신호는 쓰지 않는다. **엔진(`engine/simulator.py`)에서 한 번만** 적용하고 전략 파일에 따로 넣지 않는다 (값: `config/engine.yaml`). A1은 논문에 없는 [임의] 이탈로 규격에 명시한다
- 가격은 수정가(총수익) 기준이다. 실전에서 비수정 가격 스탑 주문은 대형 특별배당 배당락일에 체결될 수 있다는 차이를 보고서에 명시하고, 보유 기간에 배당락 수익률 10% 이상 배당이 낀 거래는 플래그 열로 표시한다

## 4. 폴더 구조

기존 스크리너 저장소에 붙일 때는 아래 구조를 기본으로 하되, 기존 코드 구조와 맞지 않으면 통합 계획을 먼저 제안하고 사용자 확인을 받는다.

```
golden_backtest/
├── config/            # strategies/*.yaml, costs.yaml, universe.yaml
├── data/              # providers/(기존 스크리너 데이터 어댑터), store.py, calendar.py
├── indicators/        # 순수 함수: sma, ema, atr(wilder), rsi, donchian, adr, rs_pct
├── regime/            # 지수 10개월선, VIX 백분위, 이벤트 플래그
├── strategies/        # base.py + a1_ath.py, a2_rsi2.py, b1_turtle.py, b2_sepa.py, b3_qulla.py
├── engine/            # intents.py(의도 타입·StopSpec), simulator.py, fills.py, costs.py, position.py
├── records/           # trade.py (거래 기록 스키마)
├── evaluation/        # metrics, baseline(B&H, 랜덤), walkforward, sensitivity, overlap
├── live/              # signals.py, outcomes.py (R7/R30/R60/R90)
├── reports/
└── tests/             # test_lookahead.py, test_fills.py, 전략별 테스트
```

## 5. Strategy 인터페이스

```python
class Strategy:
    code: str          # "B3"
    version: str       # "1.0"
    entry_model: str   # "M1" | "M2"

    def prepare(self, df) -> pd.DataFrame:
        """벡터화로 지표·셋업 조건 계산. t일 종가까지만 사용."""

    def entry_intent(self, row) -> EntryIntent | None:
        """t일 기준으로 t+1에 쓸 주문. M1: next_open / M2: buy_stop(가격)."""

    def initial_stop(self, row, fill_price) -> StopSpec: ...

    def manage(self, position, row) -> list[ExitIntent]:
        """손절선 갱신, 트랜치 청산, 시간 청산. 상태 기반."""
```

의도 타입(`EntryIntent`, `ExitIntent`, `StopSpec`)은 `engine/intents.py`에 둔다. **strategies가 engine을 import하는 방향만 허용**하고 engine은 strategies를 import하지 않는다.
`initial_stop`은 손절 종류를 함께 알려야 하므로 float 대신 `StopSpec(price, kind, ...)`을 반환한다 (3단계에서 확인 요청한 변경).

인터페이스(Strategy 메서드 시그니처·반환 타입, 의도 타입)를 바꿔야 하면 **구현 전에** 이유를 설명하고 확인을 받는다. 구현 후 보고는 확인으로 치지 않는다.

## 6. 거래 기록 스키마

`ticker, strategy, version, signal_date, entry_date, entry_price, initial_stop, exit_date, exit_price, exit_reason(stop/trailing/time/rule/partial/open_mtm), tranche, hold_days, costs, r_multiple, mfe_r, mae_r, regime_tag, event_flag`
추가 필드(v1.3): `strategy_version, entry_model, risk_basis, stop_kind`, 트랜치 행을 묶기 위한 `trade_id, weight`

- `version`은 규격 버전, `strategy_version`은 전략 구현 버전이다
- 트랜치마다 한 행이다. `costs`와 `r_multiple`은 `weight`를 곱한 값이라 같은 `trade_id`의 행을 합하면 거래 전체 값이다

- 1R = 진입가 − 초기 손절가
- r_multiple은 비용 반영 후 값
- A2-원전처럼 손절이 없는 전략은 명목 리스크(3×ATR14)로 R을 계산하고, 그 사실을 기록에 표시한다

## 7. 데이터 원칙

- 기존 스크리너 데이터를 우선 활용한다. 새 데이터 소스를 추가하기 전에 사용자에게 묻는다.
- 수정주가(분할·배당 보정)와 비수정 가격을 둘 다 보관한다. 비수정 가격은 10달러 필터에만 쓴다.
- 사상 최고가 계산은 상장일부터의 전체 이력이 있어야 한다. 이력이 부족하면 그 종목의 A1 신호는 "판정 불가"로 두고, 임의로 짧은 기간 고점으로 대체하지 않는다.
- 지표 워밍업 기간(최소 1년)을 백테스트 시작일 이전에 확보한다.
- 데이터 결측·이상치는 조용히 채우지 말고 로그로 남기고 보고한다.

## 8. 작업 방식

- **단계별로 진행하고, 각 단계 끝에 멈춰서 사용자 확인을 받는다.** 한 번에 여러 단계를 구현하지 않는다.
- 새 단계를 시작할 때는 먼저 계획(바꿀 파일, 접근 방식, 확인이 필요한 점)을 보여준다.
- 규격이 모호하거나 일봉으로 구현할 수 없는 규칙을 만나면 임의로 정하지 말고 선택지를 제시하고 묻는다.
- 테스트를 먼저 또는 함께 작성한다. 엔진의 엣지 케이스(갭, 동시발생, 트랜치)는 손으로 계산한 기대값으로 검증한다.

구현 순서:
1. 기존 스크리너 구조 파악 + 데이터 요구사항 점검 + 통합 계획 (코드 작성 없음)
2. data 어댑터 + indicators + 누수 테스트 틀
3. engine (fills, costs, position) + 엣지 케이스 테스트
4. B1 터틀로 엔진 검증
5. A2 → B2 → B3 추가 (A1은 손절 공식 확정 후)
6. evaluation, reports
7. live (P4 단계)

## 9. 결과 보고 방식

- 지표는 PF 단독으로 보고하지 않는다. 거래 수, 승률, 평균 R, 비용 후 PF, MDD, 최대 연속 손실을 함께 보고한다. 기대값은 평균 R과 같은 값이라 표에서 빼고 평균 이익 R / 평균 손실 R / 중앙값 R을 싣는다.
- 최대 연속 손실은 거래를 **청산일 순**으로 정렬해 계산한다. 여러 종목 합산도 종목별로 이어 붙이지 않고 전체를 청산일 순으로 섞는다.
- 항상 Buy & Hold와 랜덤 진입 기준선을 같이 보여준다.
- 거래 수 30회 미만인 집계 셀은 "판정 불가"로 표시한다.
- P1(A안 고정 유니버스) 결과는 생존편향으로 낙관적일 수 있다는 점을 보고서에 명시한다.
- 엣지 케이스 결과 표에는 기대값과 함께 입력값(진입가, 손절가, 청산가, 비용률)을 적는다. 기대값만으로는 검산할 수 없다.

## 10. 하지 말 것

- 성과를 개선하려고 규격 파라미터를 바꾸기
- 전략 파일 안에서 체결·비용 처리
- 미래 데이터를 쓰는 pandas 연산 (`shift(-n)`, 중앙 정렬 rolling, 전체 기간 정규화 등)을 신호 계산에 사용
- 누수 테스트를 건너뛰거나 실패한 테스트를 비활성화
- 사용자 확인 없이 새 외부 데이터 소스나 유료 API 추가
