# 재무 신호 대규모 탐색 결과 (과최적화 통제)

> 실행 2026-11-03T14:45:23.867313+00:00 · 설계 2026-11-03 고정 · 생존편향 · 신호 단위 평가 · 참고용

```
재무 신호 대규모 탐색(과최적화 통제 포함). 설계는 2026-11-03에 결과를 보기 전에 고정했다. python -m leader_backtest.search_fund
search.py 와 같은 틀(훈련 2017~2020 / 검증 2021~2023 / 시험 2024~, 점수 = min(훈련 t, 검증 t), 섞은 데이터 귀무, 현실점검 p, Bonferroni)이며 진입 조건에 재무를 더한다.
재무 특징은 fund_pit.py 의 공시 시점 기준(접수일 다음 거래일부터, 처음 공시된 값, 200일 넘게 공시가 없으면 결측). 임계값을 켠 조건에서 결측은 탈락이다.
탐색 공간(무작위 10만 개): 재무 조건 1개 이상 필수 + 가격 조건(RS, 추세템플릿, 시장 필터, 거래량, 돌파, 52주 고점 근접, ATR) + 청산 20종(search.py 와 동일).
재무: 매출증가율 {0,10,20,30,50}%+, 영업이익증가율 {0,20,50}%+(흑자전환 포함), 영업이익률 {0,5,10,15}%+, 영업이익률 개선 {0,2}%p+, 매출 증가율 가속, 흑자전환, 순이익 흑자, 부채비율 {100,200,300}% 이하, 공시 후 경과 {20,60,120}일 이내.
한계: 현재 상장 종목(생존편향), 일부 종목 계정 결측, 시험 구간은 이전에 본 적 있음, 신호 단위 평가(포트폴리오 제약 미반영).
```

## KR — 조합 100,000개(유효 0), 행 1,360,623, 거래일 2405

- 귀무 복제 40회 최고 점수: [-99.0, -99.0, -99.0, -99.0, -99.0, -99.0, -99.0, -99.0, -99.0, -99.0, -99.0, -99.0, -99.0, -99.0, -99.0, -99.0, -99.0, -99.0, -99.0, -99.0, -99.0, -99.0, -99.0, -99.0, -99.0, -99.0, -99.0, -99.0, -99.0, -99.0, -99.0, -99.0, -99.0, -99.0, -99.0, -99.0, -99.0, -99.0, -99.0, -99.0] / 귀무 점수 분위수: None
- 실제 점수 분위수: None
- 현실점검 p(귀무 최고 점수가 실제 최고 이상인 비율): 1.0
- 유효 조합 전체의 구간별 평균 초과수익 %p(신호 단위): {'train': None, 'val': None, 'test': None}
- 상위 20개 중 시험 구간 초과수익이 양수인 비율: 0.0

| 순위 | 점수(min t) | Bonferroni p(유효 N) | 훈련 t/초과% | 검증 t/초과% | 시험 t/초과%/신호 | 조건 |
|---|---|---|---|---|---|---|
| 1 | -99.0 | None | n/a | n/a | n/a/0 | rs=80, trend=1, regime=200, highMin=-0.2, exit=H40_S8, revYoY=20, opYoY=20, opMargin=15, dMargin=0, turn=1, netPos=1, de=100, recent=20 |
| 2 | -99.0 | None | n/a | n/a | n/a/0 | rs=80, highMin=-0.1, exit=H10_S8, revYoY=0, opYoY=50, opMargin=0, accel=1, de=300, recent=120 |
| 3 | -99.0 | None | n/a | n/a | n/a/0 | vol=1.5, highMin=-0.1, atrMin=0.02, exit=H10_S8, revYoY=50, opYoY=0, opMargin=5, turn=1, netPos=1, de=300, recent=20 |
| 4 | -99.0 | None | n/a | n/a | n/a/0 | rs=70, trend=1, regime=200, vol=1.5, highMin=-0.2, exit=H40_S6, revYoY=50, opYoY=20, dMargin=2, accel=1, turn=1, de=300 |
| 5 | -99.0 | None | n/a | n/a | n/a/0 | rs=70, trend=1, regime=200, vol=1.0, breakout=1, highMin=-0.2, exit=H20, revYoY=20, opYoY=20, opMargin=0, accel=1, recent=120 |
| 6 | -99.0 | None | n/a | n/a | n/a/0 | trend=1, highMin=-0.1, exit=H40, revYoY=30, opYoY=20, opMargin=10, de=200, recent=120 |
| 7 | -99.0 | None | n/a | n/a | n/a/0 | rs=70, vol=1.5, breakout=1, highMin=-0.1, atrMin=0.03, exit=H10_S8, dMargin=2, accel=1, turn=1, de=200 |
| 8 | -99.0 | None | n/a | n/a | n/a/0 | rs=70, trend=1, regime=150, breakout=1, highMin=-0.1, atrMin=0.03, exit=H10_S12, revYoY=50, opYoY=0, opMargin=0, dMargin=0, de=100, recent=20 |
| 9 | -99.0 | None | n/a | n/a | n/a/0 | trend=1, highMin=-0.3, exit=H40_S12, revYoY=50, opYoY=0, opMargin=0, turn=1 |
| 10 | -99.0 | None | n/a | n/a | n/a/0 | rs=70, trend=1, highMin=-0.2, atrMin=0.03, exit=H60_S6, revYoY=20, opYoY=0, opMargin=15, dMargin=0, turn=1, netPos=1, de=300 |
| 11 | -99.0 | None | n/a | n/a | n/a/0 | rs=80, trend=1, regime=200, breakout=1, highMin=-0.2, atrMin=0.03, exit=H20, dMargin=2, de=300, recent=60 |
| 12 | -99.0 | None | n/a | n/a | n/a/0 | rs=90, trend=1, vol=1.0, breakout=1, highMin=-0.3, exit=H60_S6, revYoY=20, opYoY=50, opMargin=5, netPos=1, de=100, recent=60 |
| 13 | -99.0 | None | n/a | n/a | n/a/0 | rs=80, trend=1, vol=1.0, breakout=1, atrMin=0.02, exit=H40_S12, revYoY=0, dMargin=2 |
| 14 | -99.0 | None | n/a | n/a | n/a/0 | rs=90, regime=200, breakout=1, highMin=-0.2, atrMin=0.03, exit=TR20, revYoY=10, opYoY=20, opMargin=0, dMargin=0, netPos=1, de=200, recent=20 |
| 15 | -99.0 | None | n/a | n/a | n/a/0 | rs=70, trend=1, vol=1.5, breakout=1, highMin=-0.2, atrMin=0.02, exit=TR15, opMargin=10, accel=1, de=100, recent=20 |
| 16 | -99.0 | None | n/a | n/a | n/a/0 | rs=80, trend=1, regime=150, vol=1.0, highMin=-0.3, atrMin=0.03, exit=H20, revYoY=20, opYoY=0, opMargin=15, dMargin=0, turn=1, de=300 |
| 17 | -99.0 | None | n/a | n/a | n/a/0 | rs=50, regime=150, vol=1.0, highMin=-0.2, exit=H10_S8, opYoY=20, opMargin=10, dMargin=0, accel=1, de=300, recent=20 |
| 18 | -99.0 | None | n/a | n/a | n/a/0 | trend=1, regime=150, vol=1.5, highMin=-0.2, exit=H60_S12, revYoY=0, opYoY=20, opMargin=15, dMargin=0, accel=1, de=200, recent=120 |
| 19 | -99.0 | None | n/a | n/a | n/a/0 | rs=50, trend=1, regime=150, breakout=1, atrMin=0.02, exit=H20_S12, revYoY=20, opYoY=50, dMargin=0, accel=1, de=200, recent=20 |
| 20 | -99.0 | None | n/a | n/a | n/a/0 | rs=80, vol=1.5, highMin=-0.2, atrMin=0.03, exit=H40_S8, revYoY=20, opYoY=20, opMargin=0, dMargin=0, turn=1, de=200 |
