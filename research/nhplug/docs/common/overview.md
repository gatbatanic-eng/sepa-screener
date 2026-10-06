# NH투자증권 Open API — 공통 (Common: 인증·계좌·조회) Overview

## 개요

자산군에 속하지 않는 **플랫폼 공통 API** 2종입니다.

1. **접근 토큰 발급** (`POST /oauth2/token`) — 앱키/앱시크릿으로 access token 을 발급받습니다. ⚠️ **운영(`api.nhplug.com`) 전용 — 모의투자 미제공.** 발급받은 토큰은 운영·모의투자 호출에 모두 사용할 수 있습니다.
2. **계좌 목록 조회** (`POST /n2/acctinfo`) — 자격증명에 연결된 보유 계좌번호 목록을 조회합니다.

> 기본 환경은 **운영(Live · `api.nhplug.com:8443`)** 이며 주문이 실제로 체결됩니다. 테스트·검증은 **모의투자(`moapi.nhplug.com:8443`)** 를 사용하세요.

자산군 API 는 계좌번호(`act_no`)를 요구하므로, 두 API 가 **모든 호출의 선행 단계**입니다.

## 인증 방식

- **토큰 발급**: `/oauth2/token` — 쿼리 파라미터 `appkey`·`appsecretkey`·`grant_type=client_credentials`·`scope=oob`, Content-Type `application/x-www-form-urlencoded`.
- **이후 모든 REST 호출**: 헤더에 `Authorization: Bearer {access_token}` **하나만** 실으면 됩니다. (`x-client-id`·`x-client-secret` 는 불필요)

> 🔑 **토큰은 24시간(`expires_in=86400`) 유효.** 매 호출 재발급 금지 — **재발급은 보안 알림을 유발**합니다. **파일 등 공유 캐시**에 만료시각과 저장해 재사용하고, **재발급은 `401` 일 때만**(`429` 재시도엔 기존 토큰).


## 봉투 규약

- `/n2/acctinfo` 는 다른 REST API 와 동일하게 요청 `Input_0` / 응답 `Output_0`(+`Output_1` …) 봉투를 사용합니다.
- 응답 공통 봉투: `rsp_cd`(응답코드) · `rsp_msg`(응답메시지) · `cust_no`(고객번호).
- ⚠️ **성공 판정**: HTTP 200 이어도 업무 오류일 수 있습니다. **`rsp_cd` 를 단일 코드와 비교하지 말고**, ① 기대한 응답 블록에 값이 있는지 ② `rsp_msg` 가 완료를 뜻하는지로 판단하고 `rsp_cd` 는 로그·문의용으로 보관하세요.
- **호출 유량**: REST 초당 5회 수준. 초과 시 오류 응답 — 재시도 대신 **간격을 늘리세요**(토큰 재발급 금지). WebSocket 은 **동시 2개 채널·채널당 30건 구독**까지.
- `/oauth2/token` 은 예외적으로 봉투를 쓰지 않고 `access_token` 을 직접 반환합니다.
- **연속조회(`cts`)**: `cts`·`cts_flag` 는 **응답 헤더**로 내려옵니다(본문 아님). `cts_flag` `Y`=다음 페이지 있음 / `N`=종료. 다음 페이지 요청 시 직전 응답의 `cts`·`cts_flag` 를 요청 헤더에 그대로 전달합니다. 일부 API 는 헤더 없이 응답코드 `00165`·`00218` 로만 알립니다. `cts` 가 직전과 동일하면 중단(전체 문자열 비교).

## 전형적 흐름

```
1) POST /oauth2/token           → access_token 획득
2) POST /n2/acctinfo            → Output_0[].acct_no 목록 획득
3) POST /krstock/inquiry/v1/balance (act_no = 위 acct_no)  → 잔고
   POST /krstock/order/v1/cashBuy   (act_no = 위 acct_no)  → 매수 주문
```

## 종목마스터 파일 (Instruments)

> ⚠️ **전 종목 목록·종목명·업종을 조회하는 REST API 는 없습니다.** 종목 정적정보는 아래 마스터 파일을 사용하세요.

전 종목의 코드·종목명·업종·지수편입 여부 등 **정적 종목정보**는 **종목마스터 파일(.mst)** 로 제공합니다. 총 **28종**(국내주식·해외주식·국내선물옵션·해외파생·장내채권).

- **다운로드**: `https://www.nhplug.com/instruments/<파일명>.mst` — **인증 불필요**(토큰·헤더 없이 공개 다운로드)
- **구조체 정의**(오프셋·길이·코드값·레코드크기): `https://www.nhplug.com/instruments/<파일명>.h` — 마스터 파일과 **1:1 대응**(예: `m_new_stock.mst` → `m_new_stock.h`). **인증 불필요**

### 파일 공통 형식

- 인코딩 **CP949** (UTF-8 아님)
- **고정 길이** 레코드. 파일 헤더 없음(0번 오프셋부터 첫 레코드)
- 좌측정렬 + 공백(`0x20`) 우측 패딩 → 길이 기반 슬라이싱 후 우측 공백 제거
- 레코드 끝 1바이트 **LF(`0x0A`)**. CRLF 아님
- 반드시 **바이너리 모드(`"rb"`)로 열 것**
- **`파일크기 % 레코드크기 == 0` 을 먼저 검증**할 것

### 주요 마스터

| 구분 | 마스터 파일 | 구조체 정의 |
|---|---|---|
| 국내주식 | `m_new_stock.mst` | `m_new_stock.h` |
| 해외주식 | `m_gtsstock.mst` | `m_gtsstock.h` |
| 지수옵션 | `m_optksp.mst` | `m_optksp.h` |
| 주식선물 | `m_stkfut.mst` | `m_stkfut.h` |
| 장내채권 | `bond_hts.mst` | `bond_hts.h` |
| 해외파생 — CME지수선물 (`exnm=FCME`) | `fucode_h.mst` | `fucode_h.h` |
| 해외파생 — 홍콩선물 (`exnm=FHKE`) | `fucode_fhke_h.mst` | `fucode_fhke_h.h` |
| 해외파생 — OPRA주식옵션 (`exnm=OOPR`) | `opcode_h.mst` | `opcode_h.h` |
| 해외파생 — 상품정보(품목 마스터) | `foitem_h.mst` | `foitem_h.h` |

- 해외파생 `fucode_h` 의 **`Leadmonth` 필드(`1`=선도월물)** 로 최근월물을 판별할 수 있습니다.

### 파싱 주의

- 지수옵션 `sPrice` 는 **실제 행사가 × 100** → `/100` (주식옵션 `m_optstp` 의 `sValue` 는 스케일 없음)
- 위클리옵션 `sMonth` 는 **YYMMWW(주차)** — 날짜로 파싱 금지
- 콜/풋 구분은 **CP949 한글 2바이트**(`"콜"`/`"풋"`)
- 지수 편입 플래그는 **`== "Y"` 로만** 판정
- 국내주식 한글종목명 선두 1바이트는 지수 마커(`*` KOSPI200 / `#` 코스닥150) — 정렬·검색 시 제거

> 금현물은 마스터 파일이 없고 전문(`IVOGLDREQ01`)으로 조회합니다.

## 참고

- `acct_no`(계좌목록 응답) 와 `act_no`(잔고·주문 입력) 는 필드명이 다르지만 **값은 동일**합니다.
- **계좌구분코드 `acct_type` — 사용 도메인이 결정됩니다.**

| `acct_type` | 용도 | 사용 도메인 |
|---|---|---|
| `01` | 🔴 운영 (Live) — 일반 | `https://api.nhplug.com:8443` |
| `02` | 🔴 운영 (Live) — 주문대리인 | `https://api.nhplug.com:8443` |
| `03` | 🟢 모의투자 (Mock) | `https://moapi.nhplug.com:8443` |

  계좌 목록에는 여러 구분의 계좌가 함께 내려옵니다. 호출하려는 환경과 **같은 구분의 계좌**를 사용하세요.
