# NH투자증권 Open API — 국내주식 (Domestic Stock) Overview

NH투자증권 Open API 의 **국내주식 (Domestic Stock)** 자산군. 주문·조회·시세·실시간 제공. (기준: API명세서 260911 / 나무 환경)

---

## 환경 및 접속 정보

> ⚠️ **기본 환경은 운영(api)** 입니다. 운영은 주문이 **실제로 체결**됩니다. 테스트·검증이 필요하면 모의투자(moapi)를 사용하세요.

API 포탈: `https://www.nhplug.com` (나무)

| 용도 | REST |
|---|---|
| 🔴 운영 (기본, 실제 주문) | `https://api.nhplug.com:8443` |
| 🟢 모의투자 (교육이수·개발·검증) | `https://moapi.nhplug.com:8443` |

> **WebSocket**: 운영 `wss://api.nhplug.com:7070/websocket` · 모의투자 `wss://moapi.nhplug.com:17070/websocket` — **경로 `/websocket` 필수**.
> ⚠️ 포트는 국내/해외가 아니라 **시세/통보**로 갈립니다: **국내 시세 `7070` · 해외 시세 `7080` · 통보(체결·주문내역, 국내·해외 전부) `7070`** · 모의투자 `17070`. 통보를 `7080` 으로 보내면 `WSS10006`("허용되지 않는 tr_cd 입니다.") 가 반환됩니다.

---

## 개요

### 인증

REST 는 `Authorization: Bearer {access_token}` **하나만**(`x-client-id`·`x-client-secret` 불필요), WebSocket 은 구독 메시지 `header.token`. 토큰 발급은 `POST /oauth2/token`(**운영 전용**).

### 요청·응답

`POST` + JSON. 요청 `Input_0` / 응답 `Output_0`(+`Output_1` …, 타입은 API별 객체/배열) + `message`. 필드는 `openapi.json` 참조.

### 실시간

구독 후 서버가 **JSON** `{header,body}` 를 **비정기** push. heartbeat 불필요·암호화 없음. 채널 예시는 `openapi.json` 의 `push_example`.

---

## 기능 목록

전체 목록은 [README.md](https://www.nhplug.com/openapi-docs/krstock/README.md), 필드·스키마는 [openapi.json](https://www.nhplug.com/openapi-docs/krstock/openapi.json) 정본 참조.


### 주문 (Order)

| 엔드포인트 | 설명 |
|------|------|
| `POST /krstock/order/v1/cancel` | 주식주문(정정취소) 취소 |
| `POST /krstock/order/v1/cashBuy` | 주식주문(현금) 매수 |
| `POST /krstock/order/v1/cashSell` | 주식주문(현금) 매도 |
| `POST /krstock/order/v1/creditBuy` | 주식주문(신용) 매수 |
| `POST /krstock/order/v1/creditSell` | 주식주문(신용) 매도 |
| `POST /krstock/order/v1/modify` | 주식주문(정정취소) 정정 |
| `POST /krstock/order/v1/reservedCancel` | 주식예약주문취소 |
| `POST /krstock/order/v1/reservedOrder` | 주식예약주문 |

### 조회 (Inquiry)

| 엔드포인트 | 설명 |
|------|------|
| `POST /krstock/inquiry/v1/assetStatus` | 투자계좌자산현황조회 |
| `POST /krstock/inquiry/v1/balance` | 주식잔고조회 |
| `POST /krstock/inquiry/v1/buyableQuantity` | 매수가능수량조회 |
| `POST /krstock/inquiry/v1/dailyOrderExecution` | 주식일별주문체결조회 |
| `POST /krstock/inquiry/v1/dailyPnl` | 실현손익일별합산조회 |
| `POST /krstock/inquiry/v1/integratedMargin` | 주식통합증거금 현황 |
| `POST /krstock/inquiry/v1/realizedPnl` | 주식잔고조회_실현손익 |
| `POST /krstock/inquiry/v1/reservedInquiry` | 주식예약주문조회 |
| `POST /krstock/inquiry/v1/rightsHeld` | 기간별계좌권리현황조회보유 |
| `POST /krstock/inquiry/v1/rightsScheduled` | 기간별계좌권리현황조회예정 |
| `POST /krstock/inquiry/v1/sellableQuantity` | 매도가능수량조회 |
| `POST /krstock/inquiry/v1/tradingPnl` | 종목별실현손익현황조회 |

### 시세 (Market Data)

| 엔드포인트 | 설명 |
|------|------|
| `POST /krstock/quote/v1/afterHoursCurrent` | 국내주식 시간외현재가 |
| `POST /krstock/quote/v1/afterHoursExpected` | 주식현재가 시간외시간별예상 |
| `POST /krstock/quote/v1/currentAfterHoursDaily` | 주식현재가 시간외일자별주가 |
| `POST /krstock/quote/v1/currentAfterHoursExecution` | 주식현재가 시간외시간별체결 |
| `POST /krstock/quote/v1/currentDaily` | 주식현재가 일자별 |
| `POST /krstock/quote/v1/currentExecution` | 주식현재가 당일시간대별체결 |
| `POST /krstock/quote/v1/currentInvestor` | 주식현재가 투자자 |
| `POST /krstock/quote/v1/currentPrice` | 주식현재가 시세 |
| `POST /krstock/quote/v1/etfComponents` | ETF 구성종목시세 |
| `POST /krstock/quote/v1/etfCurrent` | ETF/ETN 현재가 |
| `POST /krstock/quote/v1/period` | 국내주식기간별시세(일/주/월/년) |

### 실시간 (Realtime · WebSocket)

| 채널 | tr_cd | tr_key |
|---|---|---|
| 국내주식 실시간호가KRX | `ob` | `code`(종목코드) |
| 국내주식 실시간체결가KRX | `oc` | `code`(종목코드) |
| 국내주식 실시간예상체결KRX | `oa` | `code`(종목코드) |
| 국내주식 실시간회원사KRX | `t1` | `code`(종목코드) |
| 국내주식 실시간프로그램매매KRX | `t8` | `code`(종목코드) |
| 국내주식 시간외 실시간호가KRX | `e5` | `ecn_code`(종목코드) |
| 국내주식 시간외 실시간체결가KRX | `e2` | `ecn_code`(종목코드) |
| 국내주식 시간외 실시간예상체결KRX | `e4` | `ecn_code`(종목코드) |
| 국내주식 실시간호가통합 | `mb` | `code`(종목코드) |
| 국내주식 실시간체결가통합 | `mc` | `code`(종목코드) |
| 국내주식 실시간예상체결통합 | `ma` | `code`(종목코드) |
| 국내주식 실시간회원사통합 | `mg` | `code`(종목코드) |
| 국내주식 실시간프로그램매매통합 | `mn` | `code`(종목코드) |
| 국내주식 실시간호가NXT | `nb` | `code`(종목코드) |
| 국내주식 실시간체결가NXT | `nc` | `code`(종목코드) |
| 국내주식 실시간예상체결NXT | `na` | `code`(종목코드) |
| 국내주식 실시간회원사NXT | `ng` | `code`(종목코드) |
| 국내주식 실시간프로그램매매NXT | `nn` | `code`(종목코드) |
| 국내주식 실시간체결통보 | `d2` | `userid`(사용자ID) |
| 국내주식 실시간주문내역통보 | `d3` | `userid`(사용자ID) |
| 채권지수 실시간 체결가 | `uB` | `jisuid`(지수ID) |

---

## 시작하기

포탈에서 `appkey`·`appsecretkey` 발급 → `POST /oauth2/token` → `POST /n2/acctinfo` 로 `act_no` 확보 → 각 API 호출

---

## 종목마스터

이 자산군의 정적 종목정보(코드·종목명·업종 등)는 REST API 가 아니라 **종목마스터 파일(.mst)** 로 제공됩니다.

- 파일: `m_new_stock.mst`
- 다운로드: `https://www.nhplug.com/instruments/<파일명>.mst` (**인증 불필요**)
- 형식: **CP949 · 고정길이 · LF(0x0A) 종단 · 바이너리 모드("rb") 필수**
- 구조체 정의: `m_new_stock.h` (`https://www.nhplug.com/instruments/<파일명>.h`, 인증 불필요) · 전체 안내: [common/overview.md](https://www.nhplug.com/openapi-docs/common/overview.md)

## 비고

- **국내주식 (Domestic Stock)**, API명세서 **260911** / **나무** 기준. 공통 에러표 미제공(결과는 `message` 봉투) — 성공 판정은 `rsp_cd`·`rsp_msg` 로. 실시간 구독 한도는 **동시 2채널·채널당 30건**이며, **재연결 규약만 미확정**입니다.
