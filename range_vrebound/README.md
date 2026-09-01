# RANGE-MR & V-REBOUND 스크리너

기존 `sepa-screener` 저장소의 SEPA(미너비니) 추세템플릿 스크리너와는 완전히
별도의 프로젝트다. `screening.py`/`generate_dashboard.py`나 그 GitHub
Actions 워크플로우를 참조하거나 수정하지 않는다.

설계 배경과 스펙 검토(모호점/제안)는
`C:\Users\gatba\.claude\plans\lively-herding-eclipse.md` 를 참고할 것.

## 실행

```bash
pip install -r requirements.txt
pytest -v
```

## 개발 원칙 (요약)

- 모든 전략 파라미터는 `config/strategy_config.yaml`에서만 관리한다. 코드에
  하드코딩하지 않는다.
- 백테스트는 T일 종가로 신호를 계산하고, 체결가는 T+1일 시가를 쓴다
  (look-ahead 방지).
- 펀더멘털 데이터가 없는 항목은 "UNKNOWN"으로 명시하고, 좋은 상태로 간주하지
  않는다.
- 전략 로직(`src/strategies`, `src/scoring`, `src/risk`)과 백테스트
  (`src/backtest`)는 동일한 계산 함수를 공유해야 한다 — 실시간 스크리닝과
  과거 재현이 다른 로직을 쓰지 않는다.

## 진행 상황

- **Phase 1 (완료)**: 프로젝트 구조, CONFIG 로더(`src/config.py`), 핵심
  데이터 모델(`src/models/`, `src/market/regime.py` 스키마), 데이터
  로더(`src/data/loader.py`). 테스트 24개 통과.
- **Phase 2 (완료)**: 기술지표 모듈 (`src/indicators/`) — trend(SMA, MA
  상승판정), volatility(True Range/ATR, 롤링 고저, 박스폭/포지션, 드로다운),
  momentum(Wilder RSI), volume(평균거래량/거래량배수), relative_strength
  (기간수익률/초과수익률). 경계값 테스트와 look-ahead 방지 회귀 테스트 포함,
  총 58개 통과.
- **Phase 3 (완료)**: Market Regime Engine (`src/market/regime.py`) —
  CRASH(20일 수익률/60일 드로다운 OR)·RANGE(추세부재+좁은밴드)·RECOVERY(최근
  CRASH일로부터 recovery.lookback_days 이내)·NORMAL(기본값)을 CRASH >
  RECOVERY > RANGE > NORMAL 우선순위로 인과적으로 분류. RECOVERY의 정확한
  탐지 알고리즘이 스펙에 없어 "CRASH 이벤트 직후 관찰기간" 근사로 구현했다 —
  실제 저점/회복 탐지는 Phase 5 V-REBOUND STABILIZATION이 담당한다. 부동소수점
  경계값 비교에 1e-9 허용오차 적용. 테스트 10개 추가, 총 68개 통과.
- **Phase 4 (완료)**: RANGE-MR Engine — 박스 정의/필터(`compute_box_metrics`),
  지지선 터치 탐지 및 중복 통합(`detect_support_touches`), 평균회귀 raw
  값(`compute_mean_reversion_metrics`), 트리거 5요소(`compute_trigger_flags`),
  손절/목표/RR(`compute_stop_target`, `src/risk/rr.py`) — 이상
  `src/strategies/range_mr.py`. Setup/Trigger Score와 SETUP/TRIGGER/
  BUY_CANDIDATE/INVALIDATED 분류(`src/scoring/range_score.py`). 테스트 48개
  추가, 총 106개 통과.

  **사용자 확인 대기 중인 설계 결정** (자동 모드로 권장안을 적용하고 진행함
  — 이견 있으면 언제든 조정 가능):
  - Setup Score 8개 항목의 세부 계산식 (스펙은 배점만 정의, 산식 없음) —
    기존 CONFIG 값만 재사용하는 비례 배점으로 구현
  - Trigger Score 배점표 자체가 스펙에 없어 5요소 균등 이진 배점(각 20점)
    으로 신설 (`range_mr.trigger_score_weights`)
  - Liquidity 판정에 새 임계값 `range_mr.liquidity.min_avg_trading_value_krw`
    (10억원) 신설
- **Phase 5 (완료)**: V-REBOUND Engine — 초기 필터(`compute_initial_filter`),
  저점 추적/안정화/FIRST_REBOUND_HIGH(`track_stabilization`, 재패닉 시 리셋
  포함), PANIC/REBOUND 거래량비율(`compute_volume_structure`), 브레이크아웃
  (`compute_breakout`), 손절/목표/RR(`compute_stop_target`) — 이상
  `src/strategies/v_rebound.py`. Score와 SETUP/WATCH/BUY_CANDIDATE/
  INVALIDATED 분류(`src/scoring/v_rebound_score.py`). 테스트 33개 추가,
  총 139개 통과.

  **새로 발견해 보완한 스펙 공백**: V-REBOUND는 RANGE-MR 13조 같은 전용
  STOP/TARGET 공식이 스펙에 없었다. RANGE-MR과 대칭적으로 STOP=확정저점
  -0.5*ATR, TARGET_1=FIRST_REBOUND_HIGH(근접), TARGET_2=급락 전 60일
  고점(회복 목표)으로 신설했다 — 새 CONFIG 파라미터 없이 기존
  `v_rebound.stop`(Phase 0에서 이미 마련해둔 값)만 재사용. Setup Score와
  마찬가지로 sector_excess_drawdown(10)도 quality(15)와 함께 V1에서는 항상
  제외·재환산 대상이다(섹터 데이터가 best-effort로도 없으므로).
- **Phase 6 (완료)**: Risk Engine 공통화 — RANGE-MR/V-REBOUND가 중복
  구현했던 ATR 손절 공식과 RR 일괄계산을 `src/risk/stop.py`,
  `src/risk/rr.py`(`compute_rr_series`)로 추출(리팩터링, 기존 테스트로
  회귀 검증). 두 전략을 실제 Signal로 조립하는 orchestrator
  `src/pipeline.py`(`evaluate_range_mr`, `evaluate_v_rebound`,
  `*_row_to_signal`) 신설 — 백테스트(Phase 7)와 실시간 스크리닝(Phase 9)이
  이 함수를 공유해야 스펙 33조("동일한 전략 엔진")를 지킨다. 테스트 10개
  추가, 총 149개 통과.

  **orchestrator 조립 중 발견해 수정한 버그**: 워밍업 구간(박스/저점 추적이
  아직 시작되지 않아 데이터가 부족한 초기 구간)에서 "박스 필터 미충족"이
  곧바로 INVALIDATED로 분류되던 문제를 발견했다 — 무효화는 "있던 셋업이
  깨진 것"이어야지 "애초에 데이터가 없는 것"이면 안 된다. 박스/저점 추적이
  실제로 존재할 때만 무효화를 판정하도록 게이트를 추가했다.

  **범위에서 제외한 것**: `risk/position.py`(포지션 사이징)는 만들지
  않았다 — 스펙 28조의 백테스트 지표(승률·평균수익률·프로핏팩터 등)는 전부
  %수익률 기반이라 포지션 사이징이 없어도 성립하고, 스펙 어디에도 구체적인
  사이징 규칙(예: 계좌 대비 리스크 %)이 없다. 필요해지면 그때 추가하는 게
  맞다고 판단했다.
- **Phase 7 핵심부 (완료)**: Backtest Engine — 거래 시뮬레이션
  (`src/backtest/engine.py`: `simulate_trade`, `generate_trades`)과 성과
  지표(`src/backtest/metrics.py`: `compute_metrics` — total trades, win
  rate, avg/median return, avg win/loss, profit factor, expectancy, MDD,
  Sharpe/Sortino, avg holding period). `generate_trades`는 Phase 6
  orchestrator의 BUY_CANDIDATE 신호마다 holding_periods_days(5/10/20/40)
  각각으로 별도 거래를 시뮬레이션한다. 전략별/레짐별 지표 분리는 호출자가
  trades 리스트를 필터링해서 `compute_metrics`에 넘기면 된다(그룹핑은 이
  모듈 책임이 아님). 테스트 18개 추가, 총 167개 통과.

  **구현상 정한 규칙 (스펙에 명시 없음, Phase 7 제안)**: 체결 당일은
  스탑/타깃 판정에서 제외(체결가 정의와 충돌 방지), 같은 날 스탑·타깃
  동시 히트 시 STOP 우선(보수적 가정), Sharpe/Sortino는 무위험수익률 0
  가정·거래 단위(연율화 안 함), Sortino는 표준 정의(목표수익률 0 기준
  하방편차, 전체 표본 대비).

- **Phase 7 마무리 (완료)**: Walk-forward/파라미터 민감도 (`src/backtest/
  robustness.py`, 스펙 29조) — `override_config`(중첩 CONFIG 값 하나를
  바꾼 새 설정 생성, 원본 불변), `run_parameter_sensitivity`(그리드 스윕),
  `detect_overfitting_risk`(가장 좋은 값이 나머지 대비 z-score 2 이상
  튀면 과최적화 위험으로 경고 — 임의 전략 파라미터가 아니라 표준 이상치
  판단 기준), `train_test_split_by_date`(TRAIN/OUT-OF-SAMPLE 분리). 실제
  파이프라인으로 박스기간 그리드 스윕과 train/test 분리 백테스트를 각각
  끝까지 배선한 통합 테스트 포함. 테스트 9개 추가, 총 176개 통과.

  Phase 7 전체가 이제 끝났다: 거래 시뮬레이션 → 성과 지표 → walk-forward
  민감도까지 스펙 27~29조를 모두 구현했다.
- **Phase 8 (완료)**: 단위/통합 테스트 전체 점검 (스펙 30조 체크리스트
  대조 + 커버리지 분석, `pytest --cov`). 네트워크 호출 함수(`fetch_*`,
  의도적으로 미검증)를 빼면 **코드 커버리지 99%**. 테스트 16개 추가, 총
  192개 통과.

  **이 점검 과정에서 발견해 고친 실제 버그 2건**:
  1. **V-REBOUND 무효화 로직 오류**: "초기 필터(60일 -25% 드로다운 등)를
     더 이상 통과하지 못함"을 INVALIDATED로 처리하고 있었는데, 이는 주가가
     충분히 회복했다는 뜻 — V-REBOUND가 노리는 성공 시나리오이지 실패가
     아니다. 실제로 급락→저점안정→반등하는 합성 시나리오를 돌려보니 반등이
     진행될수록 WATCH였던 신호가 INVALIDATED로 뒤집히는 것을 발견했다.
     "이미 안정화로 확정됐던 저점이 재패닉으로 다시 깨질 때"만 무효화하도록
     `track_stabilization`에 `broke_confirmed_low` 플래그를 추가해 고쳤다
     ([src/strategies/v_rebound.py](src/strategies/v_rebound.py)).
  2. **`detect_overfitting_risk`의 부동소수점 오차**: "나머지 값이 완전히
     동일하면 표준편차=0"이라고 가정했는데, 실제로는 `0.05, 0.05, 0.05`의
     `np.std`가 `8.5e-18` 같은 미세한 오차로 계산돼 0과 정확히 같지 않아
     엉뚱하게 극단적인 z-score(과최적화 오탐)가 나왔다. 정확한 `== 0`
     비교 대신 `< 1e-9` 허용오차로 수정했다.

  두 버그 모두 "실제로 신호가 발생하는 시나리오를 끝까지 돌려보는" 통합
  테스트를 작성하는 과정에서 드러났다 — 단위 테스트만으로는 잡히지 않는
  종류였다.
- **Phase 9 (완료)**: SQLite 저장소(`src/storage.py`) — Signal/Trade
  pydantic 모델과 1:1 대응하는 SQLAlchemy 테이블, 저장/조회 함수. FastAPI
  (`src/api/main.py`, `src/api/routes.py`, `src/api/deps.py`) —
  `/health`, `/config`, `/signals`(전략·신호상태·종목 필터), `/signals/
  {symbol}`, `/trades`, `/backtest/metrics`. API는 계산을 직접 하지 않고
  저장된 결과만 조회한다 — 실제 계산은 여전히 `src/pipeline.py`/
  `src/backtest/`가 담당한다. DB 엔진은 첫 요청 시점에 지연 생성되어
  모듈을 import만 해도 `data/screener.db`가 생기지 않는다. 테스트 17개
  추가(저장소 6개, API 11개), 총 209개 통과.

  **아직 없는 것**: 실제 매일 데이터를 받아와 파이프라인을 돌리고
  결과를 저장소에 적재하는 배치 스크립트(및 그 GitHub Actions 워크플로우)
  는 아직 안 만들었다 — API/저장소는 준비됐지만 아직 데이터가 채워지지
  않는다.
- **일일 배치 스크립트 (완료)**: `run_daily_screen.py`(루트) +
  `src/screening.py` — 유니버스 조회 → 코스피/코스닥 지수·레짐 계산 →
  종목별 OHLCV 조회 → RANGE-MR/V-REBOUND 평가 → 저장소 적재까지 전체
  배선. 종목 단위 로직(`process_symbol`)은 네트워크 없이 합성 데이터로
  테스트했다(6개 추가). 한 종목에서 오류가 나도 로그만 남기고 나머지는
  계속 처리한다.

  **이번에 같이 고친 것**: Phase 0에서 정의해두고 실제로는 연결이 안 되어
  있던 **레짐 게이팅**(RANGE-MR은 RANGE/NORMAL에서만, V-REBOUND는 CRASH/
  RECOVERY에서만 평가)을 `src/pipeline.py`에 실제로 연결했다
  (`_apply_regime_gate`, 테스트 3개 추가). 또한 `fetch_kr_universe`가
  종목별 시장 구분(KOSPI/KOSDAQ)을 보존하도록 고쳤다 — 배치 스크립트가
  종목마다 어느 지수(KS11/KQ11)를 벤치마크로 쓸지 정해야 하는데 기존
  구현은 이 정보를 시총 정렬 과정에서 버리고 있었다. 테스트 총 218개 통과.

  **DB 영속성 (자동 모드로 권장안 적용, 이견 있으면 조정 가능)**: GitHub
  Actions는 실행이 끝나면 파일시스템이 사라지므로, 실행마다
  `data/screener.db`를 저장소에 커밋·푸시하도록
  [.github/workflows/range_vrebound_daily.yml](../.github/workflows/range_vrebound_daily.yml)
  을 만들었다(SEPA의 `daily_screen.yml`과 완전히 분리, 스케줄도 20분 뒤인
  평일 20:30 KST). `.gitignore`에서 `range_vrebound/data/*.db` 제외
  규칙도 걷어냈다 — 이제 의도적으로 커밋 대상이다. **아직 저장소에
  커밋/푸시하지 않았다** — 워크플로우 파일과 코드 전부 로컬에만 있다.

- **Phase 10 (완료)**: 정적 대시보드. SEPA의 `generate_dashboard.py`
  (자기완결적 단일 HTML, 외부 CDN 없음) 패턴을 그대로 따랐다. 다만 SEPA는
  CSV 스냅샷을 JSON으로 별도 누적하지만, 여기서는 SQLite DB 자체가 매
  실행마다 저장소에 커밋되어(Phase 9) 전체 이력을 이미 갖고 있으므로
  스냅샷 파일 없이 DB를 직접 읽어 "최신일 현황"과 "일별 BUY_CANDIDATE
  추이"를 만든다.
  - `src/dashboard.py`: `build_payload`(Signal 리스트 → 전략별 최신일
    rows + 일별 추이 history, 순수 함수라 DB 없이 테스트), `render_html`,
    `build`.
  - `generate_dashboard.py`(루트): `data/screener.db`를 읽어
    `../docs/range_vrebound/index.html`을 만든다 — SEPA 대시보드
    (`../docs/index.html`)와 경로가 겹치지 않는 별도 하위 폴더.
  - 워크플로우([.github/workflows/range_vrebound_daily.yml](../.github/workflows/range_vrebound_daily.yml))에
    스크리닝 뒤 대시보드 생성 + 커밋 단계를 추가했다(SEPA와 동일한 순서).
  - 실제 브라우저로 라이트/다크 모드, 탭 전환, 필터, 가로 스크롤까지
    렌더링을 확인했다(임시 시드 데이터로 테스트 후 삭제 — 저장소에는
    가짜 데이터를 남기지 않았다). 테스트 7개 추가, 총 225개 통과.

- **Phase 10 추가 개선 (완료, 사용자 요청)**:
  - `Signal`에 `name`(종목명) 필드 추가 (스펙 25조 스키마에는 없는 표시용
    추가 필드) — 유니버스 조회 시 이미 갖고 있던 종목명(`fdr.StockListing`의
    Name 컬럼)을 `process_symbol` → `Signal` → 저장소 → 대시보드까지
    끝까지 흘려보낸다. 대시보드 표/검색에 종목명이 종목코드와 함께
    표시된다.
  - 신호 근거(`reasons`)를 전부 한글로 번역했다(`src/pipeline.py`의
    `range_mr_row_to_signal`/`v_rebound_row_to_signal`) — 계산 로직은
    그대로고 표시 문구만 바뀐 것이라 전략 동작에는 영향이 없다.
  - 표에 뜨는(필터를 통과한) 종목마다 "차트보기" 버튼을 추가했다. 클릭하면
    모달로 종가+MA20/60/120/200 오버레이, 거래량 막대, RSI(14) — 세
    패널을 순수 인라인 SVG로 그린다(외부 차트 라이브러리 없이 SEPA
    대시보드와 동일하게 자기완결적으로 유지). `src/dashboard.py`의
    `build_chart_data`(지표 계산, 순수 함수·테스트 가능)와
    `generate_dashboard.py`의 `fetch_charts`(표시되는 종목만 골라 네트워크
    조회 — 전체 유니버스가 아님)로 나눴다. 이동평균은 화면 표시 구간보다
    훨씬 긴 전체 이력으로 계산한 뒤에 최근 구간만 잘라서 보여준다 — 잘라낸
    뒤 계산하면 MA200처럼 긴 이동평균이 부정확해지는 문제를 피하기 위해서다.
    브라우저에서 실제로 모달 열기/차트 3패널 렌더링/탭 전환 시 차트 갱신/
    닫기(버튼·오버레이 클릭·ESC)까지 구조적으로 검증했다. 테스트 9개 추가,
    총 234개 통과.
