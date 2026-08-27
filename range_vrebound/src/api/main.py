from fastapi import FastAPI

from src.api.routes import router

app = FastAPI(
    title="RANGE-MR & V-REBOUND Screener API",
    description="저장된 스크리닝 신호/거래 결과를 조회하는 API. 계산 자체는 하지 않는다.",
)
app.include_router(router)
