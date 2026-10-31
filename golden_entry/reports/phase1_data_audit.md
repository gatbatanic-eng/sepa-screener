# Phase 1 데이터 감사 보고서

작성: 2026-10-31 · 상태: **데이터 수집 실패 — 감사 항목 전부 미수행**

## 1. 결론
이 세션에서 한국 시장 데이터를 **단 1행도 받지 못했다.** 아래 숫자는 모두 실제 실행 출력이며, 데이터가 필요한 감사 항목은 추정하지 않고 "미확보"로 둔다.

## 2. 실제 실행한 것
| 시도 | 결과 |
|---|---|
| `pykrx.get_market_ticker_list` (data.krx.co.kr) | 프록시 CONNECT 403 → 실패 |
| `pykrx.get_market_ohlcv` (fchart.stock.naver.com) | 이름 해석/CONNECT 실패 |
| `pykrx.get_index_ohlcv` (data.krx.co.kr) | 403 → 실패 |
| `fdr.StockListing('KRX-DELISTING')`, `('KRX')` | 실패 (내부 요청 실패) |
| `fdr.DataReader('005930')` (fchart.stock.naver.com) | 403 → 실패 |
| `python -m src.audit` (연말 스냅샷 2013~2026 × 2시장 = 28건, 상폐목록, 지수 2종) | 28/28 스냅샷 실패, 상폐목록·지수 모두 실패. 캐시 parquet 0개. 실패 로그 31건: `data/cache/_failures.csv` |

호스트별 접속 확인(curl): data.krx.co.kr, openapi.krx.co.kr, kind.krx.co.kr, fchart/finance/m/api.stock.naver.com, query1.finance.yahoo.com, finance.yahoo.com, stooq.com, opendart.fss.or.kr 은 모두 연결 불가. pypi.org, raw.githubusercontent.com 만 접근 가능. 따라서 **대체 소스도 없다.**

## 3. 원인 (두 가지)
1. **네트워크 정책:** 이 클라우드 환경이 위 데이터 호스트를 차단한다(프록시 로그: `gateway answered 403 to CONNECT`, 호스트 data.krx.co.kr, fchart.stock.naver.com). 환경 설정의 Network access에서 해당 도메인을 허용해야 한다.
2. **pykrx 1.2.9의 KRX 로그인 요구:** 설치된 버전은 `KRX_ID`, `KRX_PW` 환경변수가 없으면 "KRX 로그인 실패"를 출력한다. 네트워크를 열어도 data.krx.co.kr 경유 호출(종목 목록, 지수, 수급)은 KRX 계정 정보가 필요할 수 있다. 이 점은 접속이 안 되어 **실제로 확인하지 못했다.**

## 4. 3장 감사 항목별 상태
| 항목 | 상태 |
|---|---|
| 연도별 유니버스 종목 수, 이후 상폐 종목 수 | **미확보** (생존편향 점검 불가) |
| 상폐 종목 OHLCV 확보 비율 | **미확보** |
| 수정주가 검증 5건 (액면분할·무상증자) | **미수행** (사례 선정조차 하지 않음) |
| 결측일 / 거래정지 / 0거래량 일수 | **미수행** |
| 일일 ±30% 초과 이상치 | **미수행** |
| 당시 관리종목·거래정지 상태 | **미확인.** 두 소스 모두 시점별 상태를 제공하는지 확인하지 못함 |

## 5. 확보하지 못한 데이터가 결과에 미치는 영향
- 지금은 백테스트 입력이 전혀 없으므로 **Phase 2 이후는 진행할 수 없다.** 어떤 성과 수치도 만들 수 없다.
- 데이터 확보 후에도 확인해야 할 위험: 상폐 종목 OHLCV 누락 시 생존편향으로 성과 과대, 수정주가 오류 시 가짜 신호/이상치, 시점별 관리종목 상태 부재 시 유니버스 필터 한계(4장 규정대로 한계로 보고).

## 6. 만들어 둔 것 (미검증 코드)
`config.yaml`, `src/data.py`(pykrx→FDR 대체, parquet 캐시, 실패 로그), `src/audit.py`(스냅샷·상폐·지수 수집). **라이브 데이터로 한 번도 성공 실행하지 못했으므로 동작 미검증**이다. 위 실패 경로(예외 처리·로그)만 실제로 확인했다. 3장의 나머지 감사(수정주가·결측·이상치 분석)는 데이터가 있어야 작성 가능해 아직 코드화하지 않았다.

## 7. 다음 단계에 필요한 사용자 조치
1. 환경의 Network access에서 `data.krx.co.kr`, `fchart.stock.naver.com`(그리고 FDR이 쓰는 `kind.krx.co.kr`, `finance.naver.com`)을 허용.
2. pykrx용 `KRX_ID`/`KRX_PW`를 환경 변수(secrets)에 설정할지 결정 (필요 여부는 접속 후 확인).
3. 완료 후 Phase 1 재실행을 지시. 그 전까지 Phase 2로 넘어가지 않는다(규칙 6).

## 8. 미결 사항 (규칙 참고)
- 매도 거래세율: 7장 지시대로 사용자 확인 전까지 `config.yaml`에서 `null`로 둠.
