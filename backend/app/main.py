"""多源数据采集与RPA自动化平台 后端服务入口。

启动：uvicorn app.main:app --host 127.0.0.1 --port 8000
健康检查：GET /api/health
"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.routers import ROUTERS
from app.store import store

app = FastAPI(title="多源数据采集与RPA自动化平台", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

for module in ROUTERS:
    app.include_router(module.router)


def _bootstrap() -> None:
    """启动时建立示例样本的定位与缺项处置，打开页面就能看到场景。"""
    from app.services.disposition import disposition_service
    from app.services.location import location_service

    try:
        for sample_no in ("SAMP-0001", "SAMP-0002"):
            location_service.ensure_source_locations(sample_no, "正文样本")
        for upload_no in ("UPLD-0001", "UPLD-0002", "UPLD-0003"):
            try:
                location_service.ensure_source_locations(upload_no, "上传记录")
            except LookupError:
                continue
        # 首版货单先产生缺项处置，供复核员按待办领取。
        disposition_service.generate(source_no="UPLD-0001", operator="值班管理员")
    except LookupError:
        pass


_bootstrap()


@app.get("/api/health")
def health() -> dict[str, object]:
    """健康检查：确认服务已经监听、示例数据已经就绪。"""
    return {"ok": True, "app": settings.app_name, "modules": len(store.module_names())}


@app.get("/api/overview")
def overview() -> dict[str, object]:
    """运营概览：把各业务模块的待处理量汇总成看板卡片。"""
    return store.overview()
