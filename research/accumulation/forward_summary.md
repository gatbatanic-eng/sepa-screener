# 수급 매집 후보 전향 추적

> v1 고정 2026-10-06 · v2 고정 2026-10-07 · 한국 · 참고용(매수 신호 아님) · 같은 종목은 10개 기록일 안에 다시 세지 않음 · 사례 30건 미만이면 '표본 부족'.

v1 규칙 {"days": 20, "net_pct": 0.05, "min_pos_days": 12} · v2 규칙 {"days": 20, "net_pct": 0.05, "max_day_share": 0.4, "min_pos_weeks": 3, "max_abs_ret": 0.1, "hi_low": 0.6, "hi_high": 0.9}

| 기록일 | v1 평가 종목 | FLOW | FLOW_ACC | v2 평가 종목 | V2 |
|---|---|---|---|---|---|
| 20261016 | 665 | 20 | 11 | 665 | 33 |
| 20261015 | 665 | 17 | 6 | 665 | 29 |
| 20261014 | 668 | 18 | 2 | 668 | 18 |
| 20261013 | 668 | 17 | 3 | 668 | 22 |
| 20261012 | 668 | 12 | 1 | 668 | 27 |
| 20261008 | 664 | 11 | 1 | 664 | 23 |
| 20261007 | 664 | 6 | 1 | 664 | 23 |
| 20261006 | 660 | 8 | 0 | 660 | 19 |
| 20261002 | 660 | 9 | 1 | 660 | 32 |
| 20261001 | 660 | 10 | 0 | 660 | 29 |

| 계열 | 그룹 | 보유 | 사례 | 날짜 | 평균% | 승률% | ALL 평균% | ALL 대비 %p | 95% 구간 | 판정 |
|---|---|---|---|---|---|---|---|---|---|---|
| v1 | FLOW | 5일 | 24 | 6 | -0.61 | 37.5 | -4.44 | 3.66 | None | 표본 부족 |
| v1 | FLOW | 20일 | 0 | 0 | None | None | None | None | None | 표본 부족 |
| v1 | FLOW | 40일 | 0 | 0 | None | None | None | None | None | 표본 부족 |
| v1 | FLOW_ACC | 5일 | 4 | 3 | -5.91 | 25.0 | -4.14 | -3.66 | None | 표본 부족 |
| v1 | FLOW_ACC | 20일 | 0 | 0 | None | None | None | None | None | 표본 부족 |
| v1 | FLOW_ACC | 40일 | 0 | 0 | None | None | None | None | None | 표본 부족 |
| v2 | V2 | 5일 | 50 | 7 | -2.5 | 30.0 | -4.47 | 2.11 | None |  |
| v2 | V2 | 20일 | 0 | 0 | None | None | None | None | None | 표본 부족 |
| v2 | V2 | 40일 | 0 | 0 | None | None | None | None | None | 표본 부족 |
| v2 | V2_FRG | 5일 | 5 | 3 | -2.37 | 20.0 | -0.7 | -2.23 | None | 표본 부족 |
| v2 | V2_FRG | 20일 | 0 | 0 | None | None | None | None | None | 표본 부족 |
| v2 | V2_FRG | 40일 | 0 | 0 | None | None | None | None | None | 표본 부족 |
| v2 | V2_INST | 5일 | 46 | 7 | -2.38 | 32.6 | -4.47 | 1.65 | None |  |
| v2 | V2_INST | 20일 | 0 | 0 | None | None | None | None | None | 표본 부족 |
| v2 | V2_INST | 40일 | 0 | 0 | None | None | None | None | None | 표본 부족 |
| 비교군 | C_ONLY | 5일 | 129 | 7 | -1.47 | 42.6 | -4.47 | -1.97 | None |  |
| 비교군 | C_ONLY | 20일 | 0 | 0 | None | None | None | None | None | 표본 부족 |
| 비교군 | C_ONLY | 40일 | 0 | 0 | None | None | None | None | None | 표본 부족 |
| 비교군 | D_ONLY | 5일 | 48 | 6 | -0.8 | 39.6 | -4.15 | 2.14 | None |  |
| 비교군 | D_ONLY | 20일 | 0 | 0 | None | None | None | None | None | 표본 부족 |
| 비교군 | D_ONLY | 40일 | 0 | 0 | None | None | None | None | None | 표본 부족 |