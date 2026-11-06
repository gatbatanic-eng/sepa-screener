# 나스닥 폭락 이후 흐름 백테스트

- 코드: `fetch_data.py`(수집, Actions 전용), `study.py`(분석). 실행: `python -m crash_study.study IXIC` (없으면 `NDX`)
- 폭락 정의: 일간 -3%/-5% (쿨다운 20거래일), 고점 대비 -10%/-20% 첫 도달(하락 구간당 1번)
- 관찰: 1·5·10·20·60·120거래일, 무작위 날짜 5000회 부트스트랩과 비교
- `data/IXIC.csv`가 없으면 `laoer_backtest/data/NDX.csv`(1985~)로 대신 돈다. IXIC(1971~)는 `crash_data.yml` 실행 후 생긴다.
- 결과: `results/RESULTS_<지수>.md`, 이벤트 목록 `results/events_<지수>.csv`
