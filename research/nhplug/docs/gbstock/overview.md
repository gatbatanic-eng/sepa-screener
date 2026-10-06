# NH투자증권 Open API — 해외주식 (Global Stock) Overview

NH투자증권 Open API 의 **해외주식 (Global Stock)** 자산군. 주문·조회·시세·실시간 제공. (기준: API명세서 260911 / 나무 환경)

---

## 환경 및 접속 정보

> ⚠️ **기본 환경은 운영(api)** 입니다. 운영은 주문이 **실제로 체결**됩니다. 테스트·검증이 필요하면 모의투자(moapi)를 사용하세요.

API 포탈: `https://www.nhplug.com` (나무)

| 용도 | REST |
|---|---|
| 🔴 운영 (기본, 실제 주문) | `https://api.nhplug.com:8443` |
| 🟢 모의투자 (교육이수·개발·검증) | `https://moapi.nhplug.com:8443` |

> **WebSocket**: 운영 `wss://api.nhplug.com:7080/websocket` · 모의투자 `wss://moapi.nhplug.com:17070/websocket` — **경로 `/websocket` 필수**.
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

전체 목록은 [README.md](https://www.nhplug.com/openapi-docs/gbstock/README.md), 필드·스키마는 [openapi.json](https://www.nhplug.com/openapi-docs/gbstock/openapi.json) 정본 참조.


### 주문 (Order)

| 엔드포인트 | 설명 |
|------|------|
| `POST /gbstock/order/v1/buy` | 해외주식 주문매수 |
| `POST /gbstock/order/v1/cancel` | 해외주식 정정취소주문취소 |
| `POST /gbstock/order/v1/modify` | 해외주식 정정취소주문정정 |
| `POST /gbstock/order/v1/reservedCancel` | 해외주식 예약주문접수취소 |
| `POST /gbstock/order/v1/reservedSubmit` | 해외주식 예약주문접수 |
| `POST /gbstock/order/v1/sell` | 해외주식 주문매도 |

### 조회 (Inquiry)

| 엔드포인트 | 설명 |
|------|------|
| `POST /gbstock/inquiry/v1/balance` | 해외주식 잔고 |
| `POST /gbstock/inquiry/v1/buyableAmount` | 해외주식 매수가능금액·수량 / 매도가능수량 조회 |
| `POST /gbstock/inquiry/v1/dailyTransaction` | 해외주식 일별거래내역 |
| `POST /gbstock/inquiry/v1/margin` | 해외증거금 통화별조회 |
| `POST /gbstock/inquiry/v1/periodPnl` | 해외주식 기간손익 |
| `POST /gbstock/inquiry/v1/periodPnlDetail` | 해외주식 기간손익 상세 |
| `POST /gbstock/inquiry/v1/reservedInquiry` | 해외주식 예약주문조회 |
| `POST /gbstock/inquiry/v1/unexecuted` | 해외주식 주문체결내역 |

### 시세 (Market Data)

| 엔드포인트 | 설명 |
|------|------|
| `POST /gbstock/quote/v1/current` | 해외주식 현재가상세 |
| `POST /gbstock/quote/v1/executionTrend` | 해외주식 체결추이 |
| `POST /gbstock/quote/v1/period` | 해외주식 기간별시세(개별종목) |
| `POST /gbstock/quote/v1/symbolIndexFxPeriod` | 해외주식 기간별시세(지수·환율) |

### 실시간 (Realtime · WebSocket)

| 채널 | tr_cd | tr_key |
|---|---|---|
| 해외주식 실시간호가 | `RH` | `gicz15`(GIC) |
| 해외주식 지연호가(아시아) | `rh` | `gicz15`(GIC) |
| 해외주식 실시간체결가 | `RC` | `gicz15`(GIC) |
| 해외주식 지연체결가 | `rc` | `gicz15`(GIC) |
| 해외주식 실시간체결통보 | `d0` | `userid`(사용자ID) |
| 해외주식 실시간주문내역통보 | `d1` | `userid`(사용자ID) |

---

## 시작하기

포탈에서 `appkey`·`appsecretkey` 발급 → `POST /oauth2/token` → `POST /n2/acctinfo` 로 `act_no` 확보 → 각 API 호출

---

## 종목마스터

이 자산군의 정적 종목정보(코드·종목명·업종 등)는 REST API 가 아니라 **종목마스터 파일(.mst)** 로 제공됩니다.

- 파일: `m_gtsstock.mst`
- 다운로드: `https://www.nhplug.com/instruments/<파일명>.mst` (**인증 불필요**)
- 형식: **CP949 · 고정길이 · LF(0x0A) 종단 · 바이너리 모드("rb") 필수**
- 구조체 정의: `m_gtsstock.h` (`https://www.nhplug.com/instruments/<파일명>.h`, 인증 불필요) · 전체 안내: [common/overview.md](https://www.nhplug.com/openapi-docs/common/overview.md)

## 비고

- **해외주식 (Global Stock)**, API명세서 **260911** / **나무** 기준. 공통 에러표 미제공(결과는 `message` 봉투) — 성공 판정은 `rsp_cd`·`rsp_msg` 로. 실시간 구독 한도는 **동시 2채널·채널당 30건**이며, **재연결 규약만 미확정**입니다.
