# 수급 매집 후보 전향 추적

> v1 고정 2026-10-06 · v2 고정 2026-10-07 · 한국 · 참고용(매수 신호 아님) · 같은 종목은 10개 기록일 안에 다시 세지 않음 · 사례 30건 미만이면 '표본 부족'.

v1 규칙 {"days": 20, "net_pct": 0.05, "min_pos_days": 12} · v2 규칙 {"days": 20, "net_pct": 0.05, "max_day_share": 0.4, "min_pos_weeks": 3, "max_abs_ret": 0.1, "hi_low": 0.6, "hi_high": 0.9}

| 기록일 | v1 평가 종목 | FLOW | FLOW_ACC | v2 평가 종목 | V2 |
|---|---|---|---|---|---|
| 20261008 | 664 | 11 | 1 | 664 | 23 |
| 20261007 | 664 | 6 | 1 | 664 | 23 |
| 20261006 | 660 | 8 | 0 | 660 | 19 |
| 20261002 | 660 | 9 | 1 | 660 | 32 |
| 20261001 | 660 | 10 | 0 | 660 | 29 |
| 20260930 | 660 | 11 | 2 | 660 | 27 |
| 20260929 | 660 | 12 | 0 | 660 | 25 |

| 계열 | 그룹 | 보유 | 사례 | 날짜 | 평균% | 승률% | ALL 평균% | ALL 대비 %p | 95% 구간 | 판정 |
|---|---|---|---|---|---|---|---|---|---|---|
| v1 | FLOW | 5일 | 13 | 2 | -1.03 | 46.2 | -0.25 | -2.04 | None | 표본 부족 |
| v1 | FLOW | 20일 | 0 | 0 | None | None | None | None | None | 표본 부족 |
| v1 | FLOW | 40일 | 0 | 0 | None | None | None | None | None | 표본 부족 |
| v1 | FLOW_ACC | 5일 | 1 | 1 | 3.27 | 100.0 | -3.85 | 7.12 | None | 표본 부족 |
| v1 | FLOW_ACC | 20일 | 0 | 0 | None | None | None | None | None | 표본 부족 |
| v1 | FLOW_ACC | 40일 | 0 | 0 | None | None | None | None | None | 표본 부족 |
| v2 | V2 | 5일 | 28 | 2 | -1.12 | 35.7 | -0.25 | -1.46 | None | 표본 부족 |
| v2 | V2 | 20일 | 0 | 0 | None | None | None | None | None | 표본 부족 |
| v2 | V2 | 40일 | 0 | 0 | None | None | None | None | None | 표본 부족 |
| v2 | V2_FRG | 5일 | 4 | 2 | -4.9 | 0.0 | -0.25 | -5.59 | None | 표본 부족 |
| v2 | V2_FRG | 20일 | 0 | 0 | None | None | None | None | None | 표본 부족 |
| v2 | V2_FRG | 40일 | 0 | 0 | None | None | None | None | None | 표본 부족 |
| v2 | V2_INST | 5일 | 24 | 2 | -0.49 | 41.7 | -0.25 | -0.59 | None | 표본 부족 |
| v2 | V2_INST | 20일 | 0 | 0 | None | None | None | None | None | 표본 부족 |
| v2 | V2_INST | 40일 | 0 | 0 | None | None | None | None | None | 표본 부족 |
| 비교군 | C_ONLY | 5일 | 76 | 2 | 3.38 | 57.9 | -0.25 | -2.28 | None |  |
| 비교군 | C_ONLY | 20일 | 0 | 0 | None | None | None | None | None | 표본 부족 |
| 비교군 | C_ONLY | 40일 | 0 | 0 | None | None | None | None | None | 표본 부족 |
| 비교군 | D_ONLY | 5일 | 33 | 2 | 0.41 | 45.5 | -0.25 | 0.1 | None |  |
| 비교군 | D_ONLY | 20일 | 0 | 0 | None | None | None | None | None | 표본 부족 |
| 비교군 | D_ONLY | 40일 | 0 | 0 | None | None | None | None | None | 표본 부족 |