# `longbase/` — 롱베이스 돌파 스크리너

6개월(120거래일) 이상 박스권을 거래량 동반으로 돌파한 한국(KOSPI+KOSDAQ)·미국(S&P500) 종목 탐색.

```bash
pip install finance-datareader pandas numpy
python -m longbase.run --market ALL          # 전체
python -m longbase.run --market KR --limit 50
python tests/test_longbase.py                # 합성 데이터 검증 (네트워크 불필요)
```

## 기준 (`config.py`)
- **베이스 길이** L = 돌파일 종가 이상의 고가가 마지막으로 나온 뒤 경과 거래일 (≥120, 최대 500)
- **깊이** ≤ 35%, **후반 1/3 일중변동폭 / 앞 2/3 ≤ 0.9** (수축)
- **돌파**: 종가가 베이스 최고 고가(pivot) 초과, 거래량 ≥ 직전 50일 평균 ×1.5, CLV ≥ 0.6, 최근 10거래일 이내
- **필터**: 200일선 위, 현재 종가 ≥ pivot 이고 pivot +10% 이내(과열 제외), RS 백분위 ≥ 70
  (6개월 0.6 + 12개월 0.4 수익률, 조회된 유니버스 내), 20일 평균 거래대금 KR 10억원 / US 100만달러
- 정렬: 베이스 길이 → 거래량배수 내림차순

산출: `output/longbase/longbase_{kr,us}.csv`, `output/longbase/REPORT.md`, `docs/data/longbase_{kr,us}.json`

한계: 조회 구간이 약 3년이라 그 이상 긴 베이스는 500일(`ath_in_data=True`)로 잘린다. 한국 우선주는 제외.
수정주가 기준은 FinanceDataReader 반환값에 따른다.
