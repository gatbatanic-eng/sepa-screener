# 재무 신호 대규모 탐색 결과 (과최적화 통제)

> 실행 2026-11-03T16:52:44.167218+00:00 · 설계 2026-11-03 고정 · 생존편향 · 신호 단위 평가 · 참고용

```
재무 신호 대규모 탐색(과최적화 통제 포함). 설계는 2026-11-03에 결과를 보기 전에 고정했다. python -m leader_backtest.search_fund
search.py 와 같은 틀(훈련 2017~2020 / 검증 2021~2023 / 시험 2024~, 점수 = min(훈련 t, 검증 t), 섞은 데이터 귀무, 현실점검 p, Bonferroni)이며 진입 조건에 재무를 더한다.
재무 특징은 fund_pit.py 의 공시 시점 기준(접수일 다음 거래일부터, 처음 공시된 값, 200일 넘게 공시가 없으면 결측). 임계값을 켠 조건에서 결측은 탈락이다.
탐색 공간(무작위 10만 개): 재무 조건 1개 이상 필수 + 가격 조건(RS, 추세템플릿, 시장 필터, 거래량, 돌파, 52주 고점 근접, ATR) + 청산 20종(search.py 와 동일).
재무: 매출증가율 {0,10,20,30,50}%+, 영업이익증가율 {0,20,50}%+(흑자전환 포함), 영업이익률 {0,5,10,15}%+, 영업이익률 개선 {0,2}%p+, 매출 증가율 가속, 흑자전환, 순이익 흑자, 부채비율 {100,200,300}% 이하, 공시 후 경과 {20,60,120}일 이내.
한계: 현재 상장 종목(생존편향), 일부 종목 계정 결측, 시험 구간은 이전에 본 적 있음, 신호 단위 평가(포트폴리오 제약 미반영).
```

## KR — 조합 100,000개(유효 49,093), 행 1,359,968, 거래일 2405

- 귀무 복제 40회 최고 점수: [2.3, 2.24, 2.28, 2.16, 2.26, 2.02, 2.28, 2.18, 2.09, 2.1, 2.49, 2.72, 2.71, 1.74, 2.28, 2.97, 2.26, 2.07, 2.29, 1.98, 2.16, 2.37, 2.01, 2.01, 2.39, 2.53, 2.21, 2.57, 1.87, 2.41, 2.36, 1.99, 2.03, 2.3, 2.55, 2.16, 2.63, 2.25, 2.29, 2.0] / 귀무 점수 분위수: {'0.5': -0.77, '0.9': 0.36, '0.99': 1.1, '0.999': 1.58}
- 실제 점수 분위수: {'0.5': 0.21, '0.9': 1.25, '0.99': 1.99, '0.999': 2.49}
- 현실점검 p(귀무 최고 점수가 실제 최고 이상인 비율): 0.024390243902439025
- 유효 조합 전체의 구간별 평균 초과수익 %p(신호 단위): {'train': 3.17, 'val': 1.377, 'test': 4.593}
- 상위 20개 중 시험 구간 초과수익이 양수인 비율: 1.0

| 순위 | 점수(min t) | Bonferroni p(유효 N) | 훈련 t/초과% | 검증 t/초과% | 시험 t/초과%/신호 | 조건 |
|---|---|---|---|---|---|---|
| 1 | 3.19 | 0.025 | 3.19/2.51 | 3.45/2.84 | 1.09/0.33/8578 | regime=150, vol=1.5, highMin=-0.2, atrMin=0.02, exit=H60_S8, opMargin=0, dMargin=0, netPos=1, de=200 |
| 2 | 3.06 | 0.025 | 3.06/3.25 | 3.32/3.65 | 0.47/1.57/764 | trend=1, regime=200, vol=1.5, breakout=1, highMin=-0.2, exit=H40_S8, revYoY=0, opMargin=5, dMargin=0, de=200, recent=120 |
| 3 | 3.06 | 0.025 | 3.06/3.72 | 3.13/3.67 | 0.24/1.84/817 | rs=80, trend=1, regime=150, vol=1.5, highMin=-0.1, atrMin=0.02, exit=H20_S6, revYoY=30, opYoY=20, dMargin=0, netPos=1, de=200, recent=120 |
| 4 | 2.92 | 0.05 | 2.92/5.54 | 3.43/5.42 | 1.43/4.85/3099 | trend=1, regime=150, vol=1.5, highMin=-0.2, atrMin=0.02, exit=H60_S12, revYoY=0, opYoY=20, opMargin=0, dMargin=0, accel=1, netPos=1, recent=120 |
| 5 | 2.87 | 0.05 | 2.87/2.85 | 3.10/3.84 | 0.91/4.41/904 | rs=50, trend=1, regime=150, vol=1.5, highMin=-0.1, atrMin=0.02, exit=H20, revYoY=30, opYoY=20, opMargin=10, dMargin=0, netPos=1, de=200, recent=120 |
| 6 | 2.85 | 0.05 | 3.82/3.56 | 2.85/5.47 | 2.91/6.99/20147 | regime=150, highMin=-0.3, atrMin=0.03, exit=H60, revYoY=20, opMargin=5, dMargin=0, netPos=1, de=300 |
| 7 | 2.82 | 0.05 | 2.92/4.50 | 2.82/4.50 | 2.48/4.84/15139 | regime=150, highMin=-0.2, atrMin=0.03, exit=H40, revYoY=20, accel=1, netPos=1, recent=120 |
| 8 | 2.81 | 0.05 | 3.67/5.37 | 2.81/3.42 | 2.02/4.97/6832 | regime=150, vol=1.0, highMin=-0.1, exit=SMA50X, revYoY=0, opYoY=50, dMargin=0, recent=120 |
| 9 | 2.76 | 0.05 | 3.15/5.52 | 2.76/7.19 | 0.28/4.14/999 | trend=1, regime=150, vol=1.5, highMin=-0.1, exit=H40, revYoY=20, opYoY=20, opMargin=0, dMargin=0, accel=1, de=100, recent=120 |
| 10 | 2.75 | 0.05 | 3.10/2.50 | 2.75/2.15 | -0.04/0.87/1494 | rs=50, regime=150, vol=1.5, highMin=-0.1, atrMin=0.02, exit=H20_S6, revYoY=20, opYoY=0, opMargin=0, dMargin=0, netPos=1, de=100, recent=120 |
| 11 | 2.71 | 0.1 | 2.90/2.23 | 2.71/2.00 | 0.83/0.72/871 | trend=1, regime=200, vol=1.0, breakout=1, highMin=-0.3, atrMin=0.03, exit=H10_S12, revYoY=0, opYoY=0, opMargin=0, dMargin=0, netPos=1, recent=60 |
| 12 | 2.71 | 0.1 | 2.71/5.13 | 2.82/5.79 | 1.25/4.71/3453 | rs=50, trend=1, regime=150, vol=1.5, atrMin=0.02, exit=H60, opYoY=0, dMargin=0, accel=1, netPos=1, recent=120 |
| 13 | 2.71 | 0.1 | 2.71/2.45 | 2.95/2.36 | 0.14/1.15/783 | rs=70, trend=1, regime=150, breakout=1, highMin=-0.3, exit=H10_S8, revYoY=10, opYoY=0, opMargin=5, accel=1, recent=120 |
| 14 | 2.7 | 0.1 | 2.85/2.12 | 2.70/2.40 | 1.32/1.15/12036 | rs=70, trend=1, regime=200, highMin=-0.3, atrMin=0.03, exit=H20_S6, revYoY=10, opMargin=0, accel=1, netPos=1, de=300, recent=120 |
| 15 | 2.69 | 0.1 | 2.69/3.78 | 2.85/5.39 | 0.94/2.58/995 | trend=1, regime=200, vol=1.5, breakout=1, highMin=-0.2, atrMin=0.02, exit=H60_S12, revYoY=0, opYoY=0, opMargin=0, dMargin=0, netPos=1 |
| 16 | 2.66 | 0.1 | 2.70/4.19 | 2.66/4.70 | 1.57/6.22/4282 | rs=80, trend=1, regime=150, vol=1.0, highMin=-0.2, atrMin=0.03, exit=H60_S12, revYoY=0, accel=1, netPos=1, recent=120 |
| 17 | 2.65 | 0.1 | 2.81/6.90 | 2.65/5.36 | 2.28/5.85/7025 | trend=1, regime=150, vol=1.0, highMin=-0.2, atrMin=0.03, exit=H60, opYoY=0, dMargin=0, accel=1, de=200 |
| 18 | 2.63 | 0.1 | 2.63/3.46 | 2.85/4.23 | 1.46/2.00/4664 | rs=50, regime=200, vol=1.5, atrMin=0.03, exit=H60_S8, revYoY=0, opYoY=0, opMargin=0, dMargin=0, accel=1, netPos=1, recent=120 |
| 19 | 2.63 | 0.125 | 2.63/6.80 | 2.85/5.67 | 2.11/8.94/2850 | rs=70, trend=1, regime=150, vol=1.0, highMin=-0.2, atrMin=0.03, exit=H60, revYoY=20, opYoY=0, opMargin=0, dMargin=0, accel=1, netPos=1, de=200 |
| 20 | 2.63 | 0.15 | 2.71/2.52 | 2.63/2.95 | 2.29/5.38/16815 | regime=150, highMin=-0.3, atrMin=0.03, exit=H60_S8, revYoY=10, opYoY=20, opMargin=5, dMargin=2, accel=1, de=300, recent=120 |
