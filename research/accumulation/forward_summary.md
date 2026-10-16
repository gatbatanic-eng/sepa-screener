# 수급 매집 후보 전향 추적

> v1 고정 2026-10-06 · v2 고정 2026-10-07 · 한국 · 참고용(매수 신호 아님) · 같은 종목은 10개 기록일 안에 다시 세지 않음 · 사례 30건 미만이면 '표본 부족'.

v1 규칙 {"days": 20, "net_pct": 0.05, "min_pos_days": 12} · v2 규칙 {"days": 20, "net_pct": 0.05, "max_day_share": 0.4, "min_pos_weeks": 3, "max_abs_ret": 0.1, "hi_low": 0.6, "hi_high": 0.9}

| 기록일 | v1 평가 종목 | FLOW | FLOW_ACC | v2 평가 종목 | V2 |
|---|---|---|---|---|---|
| 20261015 | 665 | 17 | 6 | 665 | 29 |
| 20261014 | 668 | 18 | 2 | 668 | 18 |
| 20261013 | 668 | 17 | 3 | 668 | 22 |
| 20261012 | 668 | 12 | 1 | 668 | 27 |
| 20261008 | 664 | 11 | 1 | 664 | 23 |
| 20261007 | 664 | 6 | 1 | 664 | 23 |
| 20261006 | 660 | 8 | 0 | 660 | 19 |
| 20261002 | 660 | 9 | 1 | 660 | 32 |
| 20261001 | 660 | 10 | 0 | 660 | 29 |
| 20260930 | 660 | 11 | 2 | 660 | 27 |

| 계열 | 그룹 | 보유 | 사례 | 날짜 | 평균% | 승률% | ALL 평균% | ALL 대비 %p | 95% 구간 | 판정 |
|---|---|---|---|---|---|---|---|---|---|---|
| v1 | FLOW | 5일 | 21 | 5 | -0.38 | 38.1 | -4.87 | 4.37 | None | 표본 부족 |
| v1 | FLOW | 20일 | 0 | 0 | None | None | None | None | None | 표본 부족 |
| v1 | FLOW | 40일 | 0 | 0 | None | None | None | None | None | 표본 부족 |
| v1 | FLOW_ACC | 5일 | 3 | 2 | -3.35 | 33.3 | -5.15 | 0.24 | None | 표본 부족 |
| v1 | FLOW_ACC | 20일 | 0 | 0 | None | None | None | None | None | 표본 부족 |
| v1 | FLOW_ACC | 40일 | 0 | 0 | None | None | None | None | None | 표본 부족 |
| v2 | V2 | 5일 | 50 | 6 | -2.51 | 30.0 | -4.82 | 2.91 | None |  |
| v2 | V2 | 20일 | 0 | 0 | None | None | None | None | None | 표본 부족 |
| v2 | V2 | 40일 | 0 | 0 | None | None | None | None | None | 표본 부족 |
| v2 | V2_FRG | 5일 | 3 | 2 | -4.76 | 0.0 | -0.01 | -4.99 | None | 표본 부족 |
| v2 | V2_FRG | 20일 | 0 | 0 | None | None | None | None | None | 표본 부족 |
| v2 | V2_FRG | 40일 | 0 | 0 | None | None | None | None | None | 표본 부족 |
| v2 | V2_INST | 5일 | 47 | 6 | -2.36 | 31.9 | -4.82 | 3.15 | None |  |
| v2 | V2_INST | 20일 | 0 | 0 | None | None | None | None | None | 표본 부족 |
| v2 | V2_INST | 40일 | 0 | 0 | None | None | None | None | None | 표본 부족 |
| 비교군 | C_ONLY | 5일 | 127 | 6 | -1.32 | 43.3 | -4.82 | -1.85 | None |  |
| 비교군 | C_ONLY | 20일 | 0 | 0 | None | None | None | None | None | 표본 부족 |
| 비교군 | C_ONLY | 40일 | 0 | 0 | None | None | None | None | None | 표본 부족 |
| 비교군 | D_ONLY | 5일 | 47 | 5 | -0.86 | 40.4 | -4.52 | 1.44 | None |  |
| 비교군 | D_ONLY | 20일 | 0 | 0 | None | None | None | None | None | 표본 부족 |
| 비교군 | D_ONLY | 40일 | 0 | 0 | None | None | None | None | None | 표본 부족 |