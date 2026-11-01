# Golden Entry offline audit v0.2

가격·유니버스·체결 전제의 데이터 품질만 감사한다. 전략 신호·성과는 계산하지 않는다.
원본을 수정·정리·삭제하지 않으며 운영 스크리너나 Actions와 연결하지 않는다.

## 재현

Python 3.12 환경에서 이 디렉터리를 기준으로 실행한다.

```bash
python -m pip install -r requirements.txt
python fetch_inputs.py --source-tree source_tree.json --output inputs
python -m unittest discover -s . -p 'test_*.py' -v
python audit_data.py --data-dir inputs --source-tree source_tree.json --events inputs/phase1_dart_events.csv --output outputs/run_001
```

입력은 359ce852283c91c8fd59a7cd4fdc3e485a092cea에 고정된다.
다운로드와 감사 모두 Git blob SHA-1·크기를 확인하고, 감사 결과에는 SHA256도 기록한다.
기존 출력 폴더를 덮어쓰지 않는다. API 키나 계좌 연결은 필요 없다.

## 출력과 해석

- summary.json: 감사 집계·런타임·한계·백테스트 보류 상태.
- source_manifest.csv: 원본 Parquet 11개와 DART 사건 CSV의 파일 크기·해시.
- status_reconciliation.csv: 상태표와 실제 가격의 행 수·시작/끝 일자 대조.
- security_classification.csv: 현재 목록의 분류 재확인, 잠정 주권, 범위 검토, 수익증권 제외 구분.
- invalid_bar_details.csv: 거래정지 표준 패턴 외의 비정상 봉 상세.
- corporate_action_exceptions.csv: ±30% 초과 봉과 150일 선행 공시 후보. 전부 미해결 상태.
- delisting_tails.csv: 상폐 전 관측 종료와 마지막 양의 거래량 날짜를 분리.
- zero_volume_runs.csv: 종목별 연속 0거래량 구간.
- calendar_issues.csv, duplicate_keys.csv: 날짜 집합 검사·고유키 검사.
- row_flags.parquet: 모든 원본 행의 검사 플래그. 다운로드 원본과 이 대형 파생 파일은 재현 가능하므로 중복 커밋하지 않는다.

mechanically_valid_bar는 양수 가격·정합 OHLC·양수 거래량 등 기계적 검사만 통과했다는 뜻이다.
기업행사, 공식 거래 가능성, 시점별 자산 분류의 검증 통과가 아니다.
EX_POST_DATA_AUDIT_ONLY 플래그 및 현재/상폐 원장의 정보는 과거 신호 필터에 직접 사용할 수 없다.

가격 수정 계수·유니버스·보유일·최종 상폐 청산의 사양은 아직 동결하지 않았다.
이상치의 사후 삭제나 자동 수익률 보정도 하지 않는다.

## 다음 작업

1. 공급원 원본과 독립 가격/기업행사 증빙으로 비정상 OHLC와 급변 구간을 분류한다.
2. 당시 시장 구분·상장일·거래정지/관리종목·자산 유형 원장을 보완한다.
3. 원본 달력과 가격을 보존하는 지표/거래가능성 마스크 및 보유 중 예외 처리 사양을 고정한다.
4. 데이터 게이트를 통과한 뒤 전략 규칙과 개발/검증/미사용 구간을 동결한다.
