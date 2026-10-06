# NH투자증권 Open API — 해외주식 (Global Stock) Endpoint Index

All REST URIs relative to 운영 `https://api.nhplug.com:8443` (기본) · Method 모두 `POST` · 요청 `Input_0` / 응답 `Output_0`+`message`. (기준: 260911 / 나무)

> 정본은 [openapi.json](https://www.nhplug.com/openapi-docs/gbstock/openapi.json). ⚠️ **기본 환경=운영(api)** — 주문이 실제 체결됩니다. 테스트는 모의투자(moapi) 사용.


## 주문 (Order)

| API Name | Method | URI | operationId |
|---|---|---|---|
| 해외주식 주문매수 | POST | `/gbstock/order/v1/buy` | gbstockOrderBuy |
| 해외주식 정정취소주문취소 | POST | `/gbstock/order/v1/cancel` | gbstockOrderCancel |
| 해외주식 정정취소주문정정 | POST | `/gbstock/order/v1/modify` | gbstockOrderModify |
| 해외주식 예약주문접수취소 | POST | `/gbstock/order/v1/reservedCancel` | gbstockOrderReservedCancel |
| 해외주식 예약주문접수 | POST | `/gbstock/order/v1/reservedSubmit` | gbstockOrderReservedSubmit |
| 해외주식 주문매도 | POST | `/gbstock/order/v1/sell` | gbstockOrderSell |

## 조회 (Inquiry)

| API Name | Method | URI | operationId |
|---|---|---|---|
| 해외주식 잔고 | POST | `/gbstock/inquiry/v1/balance` | gbstockInquiryBalance |
| 해외주식 매수가능금액·수량 / 매도가능수량 조회 | POST | `/gbstock/inquiry/v1/buyableAmount` | gbstockInquiryBuyableAmount |
| 해외주식 일별거래내역 | POST | `/gbstock/inquiry/v1/dailyTransaction` | gbstockInquiryDailyTransaction |
| 해외증거금 통화별조회 | POST | `/gbstock/inquiry/v1/margin` | gbstockInquiryMargin |
| 해외주식 기간손익 | POST | `/gbstock/inquiry/v1/periodPnl` | gbstockInquiryPeriodPnl |
| 해외주식 기간손익 상세 | POST | `/gbstock/inquiry/v1/periodPnlDetail` | gbstockInquiryPeriodPnlDetail |
| 해외주식 예약주문조회 | POST | `/gbstock/inquiry/v1/reservedInquiry` | gbstockInquiryReservedInquiry |
| 해외주식 주문체결내역 | POST | `/gbstock/inquiry/v1/unexecuted` | gbstockInquiryUnexecuted |

## 시세 (Market Data)

| API Name | Method | URI | operationId |
|---|---|---|---|
| 해외주식 현재가상세 | POST | `/gbstock/quote/v1/current` | gbstockQuoteCurrent |
| 해외주식 체결추이 | POST | `/gbstock/quote/v1/executionTrend` | gbstockQuoteExecutionTrend |
| 해외주식 기간별시세(개별종목) | POST | `/gbstock/quote/v1/period` | gbstockQuotePeriod |
| 해외주식 기간별시세(지수·환율) | POST | `/gbstock/quote/v1/symbolIndexFxPeriod` | gbstockQuoteSymbolIndexFxPeriod |

## 실시간 (Realtime, WebSocket)

운영 Endpoint: `wss://api.nhplug.com:7080/websocket` (모의투자: `wss://moapi.nhplug.com:17070/websocket`) — **경로 `/websocket` 필수** · ⚠️ **통보 채널은 포트 `7070`** (`7080` 사용 시 `WSS10006`) · 구독·인증·푸시 규약은 [openapi.json](https://www.nhplug.com/openapi-docs/gbstock/openapi.json) 의 `x-realtime-channels.protocol` 참조.

| 채널 | tr_cd | tr_key | 응답 필드 수 |
|---|---|---|---|
| 해외주식 실시간호가 | `RH` | `gicz15`(GIC) | 50 |
| 해외주식 지연호가(아시아) | `rh` | `gicz15`(GIC) | 50 |
| 해외주식 실시간체결가 | `RC` | `gicz15`(GIC) | 35 |
| 해외주식 지연체결가 | `rc` | `gicz15`(GIC) | 35 |
| 해외주식 실시간체결통보 | `d0` | `userid`(사용자ID) | 18 |
| 해외주식 실시간주문내역통보 | `d1` | `userid`(사용자ID) | 22 |
