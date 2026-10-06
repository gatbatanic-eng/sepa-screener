# NH투자증권 Open API — 공통 (Common: 인증·계좌·조회) Endpoint Index

자산군과 무관한 **플랫폼 공통 API** 입니다. 모든 자산군 조회·주문의 선행 단계이므로 아래 순서로 사용합니다.

> 정본은 [openapi.json](https://www.nhplug.com/openapi-docs/common/openapi.json) 입니다.

## 인증 (Auth)

| API Name | Method | URI | operationId |
|---|---|---|---|
| 접근 토큰 발급 | POST | `/oauth2/token` | commonAuthIssueToken |

- ⚠️ **운영(`api.nhplug.com`) 전용 — 모의투자 미제공.** 발급 토큰은 운영·모의투자 호출에 모두 사용.
- 파라미터는 **쿼리스트링**, Content-Type 은 `application/x-www-form-urlencoded`.
- 파라미터: `appkey`, `appsecretkey`, `grant_type=client_credentials`, `scope=oob`.
- 응답: `access_token`, `token_type`, `expires_in`(=86400), `scope`.
- 🔑 **토큰 재사용 필수** — access token 은 **24시간(`expires_in=86400`) 유효**합니다. 매 호출마다 재발급하지 말고 **프로세스 간 공유 캐시(파일 등)** 에 저장해 재사용하세요. 재발급은 보안 알림을 유발합니다. **재발급은 `401`(토큰 무효)일 때만** 하고, `429`(유량 초과) 재시도에는 기존 토큰을 그대로 사용하세요.

## 계좌 (Account)

| API Name | Method | URI | operationId |
|---|---|---|---|
| 계좌 목록 조회 | POST | `/n2/acctinfo` | commonAccountList |

- 헤더: `Authorization: Bearer {access_token}` **하나만 필요** (`x-client-id`·`x-client-secret` 불필요).
- ⚠️ 성공 판정: `rsp_cd` 단일 코드 비교 금지 — 응답 블록 데이터 유무 + `rsp_msg` 로 판단.
- 호출 유량: REST 초당 5회 수준 · WebSocket 동시 2채널, 채널당 30건 구독.
- 요청 바디: `{ "Input_0": {} }` (입력 없음).
- 응답: `Output_0[]` = `{ acct_no, acct_type }` 목록. `acct_no` 를 이후 잔고·주문 API 의 `act_no` 로 사용.
- 🏦 **`acct_type` = 사용 도메인 구분** — `01`: 운영(`https://api.nhplug.com:8443`) 일반 · `02`: 운영 주문대리인 · `03`: 모의투자(`https://moapi.nhplug.com:8443`) 전용. 대상 환경과 같은 구분의 계좌를 선택하세요.

## 조회 (Inquiry)

| API Name | Method | URI | operationId |
|---|---|---|---|
| 종합거래내역 | POST | `/common/inquiry/v1/totalTransaction` | commonInquiryTotalTransaction |
| 입출금내역 | POST | `/common/inquiry/v1/depositWithdrawal` | commonInquiryDepositWithdrawal |

- 자산군과 무관한 **계좌 단위 거래내역** 조회입니다(HTS 8203 · 8207).
- ⚠️ **운영(api) 전용 — 모의투자 미제공.**
- 목록이 길면 응답 헤더 `cts`·`cts_flag` 로 연속조회하세요.

## 권장 호출 순서

1. `POST /oauth2/token` → access token 발급
2. `POST /n2/acctinfo` → 계좌번호(`acct_no`) 목록 확보
3. **대상 환경에 맞는 계좌 선택** — 운영은 `acct_type=01`(일반)·`02`(주문대리인), 모의투자는 `03`
4. 선택한 계좌번호로 각 자산군의 잔고조회·주문 등 호출

## 종목마스터 파일 (Instruments)

전 종목 목록·종목명 조회 **REST API 는 없습니다.** 정적 종목정보는 마스터 파일(.mst) 28종으로 제공됩니다.

- 다운로드: `https://www.nhplug.com/instruments/<파일명>.mst` (**인증 불필요**)
- 구조체 정의: `https://www.nhplug.com/instruments/<파일명>.h` (마스터와 1:1, 예: `m_new_stock.mst` → `m_new_stock.h`, **인증 불필요**)
- 형식: **CP949 · 고정길이 · LF(0x0A) 종단 · 바이너리 모드("rb") 필수** · `파일크기 % 레코드크기 == 0` 검증
- 주요 파일: `m_new_stock.mst`(국내주식) · `m_gtsstock.mst`(해외주식) · `m_optksp.mst`(지수옵션) · `m_stkfut.mst`(주식선물) · `bond_hts.mst`(장내채권) · **해외파생** `fucode_h.mst`(CME지수선물 `FCME`) · `fucode_fhke_h.mst`(홍콩선물 `FHKE`) · `opcode_h.mst`(OPRA주식옵션 `OOPR`) · `foitem_h.mst`(상품정보)
- 상세(파싱 주의 포함)는 [overview.md](https://www.nhplug.com/openapi-docs/common/overview.md) 참조
