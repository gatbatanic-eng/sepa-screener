# 나스닥 폭락 이후 흐름 백테스트

- 코드: `fetch_data.py`(수집, Actions 전용), `study.py`(분석). 실행: `python -m crash_study.study IXIC` (없으면 `NDX`)
- 폭락 정의: 일간 -3%/-5% (쿨다운 20거래일), 고점 대비 -10%/-20% 첫 도달(하락 구간당 1번)
- 관찰: 1·5·10·20·60·120거래일, 무작위 날짜 5000회 부트스트랩과 비교
- `data/IXIC.csv`가 없으면 `laoer_backtest/data/NDX.csv`(1985~)로 대신 돈다. IXIC(1971~)는 `crash_data.yml` 실행 후 생긴다.
- 결과: `results/RESULTS_<지수>.md`(폭락 후 흐름), `results/TQQQ_<지수>.md`(합성 TQQQ 매수 전략, `tqqq_study.py`), 이벤트 목록 `results/events_<지수>.csv`
- 폭락 신호 정의 2가지: 기본(사상 최고가 기준, 신고가 전까지 구간당 1번)과 `1y`(최근 252거래일 고점 기준, -5% 안쪽으로 회복하면 재신호). 2000년 고점이 2015년에야 회복돼 기본 정의에서는 2008년이 별도 이벤트로 안 잡힌다. `python -m crash_study.study IXIC 1y` 처럼 인자로 고르고, 결과 파일은 `_1y` 접미사가 붙는다.
- `combo_study.py`: 손절선, 현재 변동성조절 규칙에 얹기, QQQ(1x) 비교 → `results/COMBO_<지수>[_1y].md`
