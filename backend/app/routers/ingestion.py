"""数据接入通道接口：维护接入通道，覆盖开通通道、暂停通道、关闭通道等动作。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.ingestion import IngestionService

router = APIRouter(prefix="/api/ingestion", tags=["数据接入通道"])

service = IngestionService()

LIST_FIELDS = ["通道编号", "通道名称", "输入格式", "解析引擎", "并发数量", "背压阈值", "缓冲队列", "通道状态"]
STATUSES = ["待开通", "开通中", "运行中", "已关闭"]


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出数据接入通道清单：返回当前过滤条件下的全量数据。"""
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "ingestion", "total": total, "items": items}


@router.get("", response_model=PageResult[dict])
def list_entries(
    request: Request,
    keyword: str | None = Query(default=None, description="按通道编号检索"),
    status: str | None = Query(default=None, description="待开通、开通中、运行中、已关闭"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按通道编号与状态过滤数据接入通道列表；没有数据时返回空页，不报错。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    extra = {k: v for k, v in request.query_params.items() if k not in ("keyword", "status", "page", "size") and v.strip()}
    items, total = service.list_entries(keyword=keyword, status=status, page=page, size=size, extra=extra)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条接入通道明细；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"接入通道 {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条接入通道，缺字段时说明原因而不是静默丢弃。"""
    entry, missing = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message="接入通道已登记", entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对单条接入通道执行开通通道、暂停通道、关闭通道；不允许的动作会被拦下并说明原因。"""
    action = str(payload.values.get("action") or "").strip()
    entry, message = service.run_action(entry_id, action)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)


@router.post("/{entry_id}/parse", response_model=ActionResult)
def parse_payload(entry_id: int, payload: EntryPayload) -> ActionResult:
    """接入解析：从正文样本或上传记录生成采集行定位，返回同一批定位记录。

    带引号的多行字段按语法切分，引号内换行不会拆出假记录；抽取失败也保留原文
    行、列，校验人员可按定位编号核对。
    """
    values = payload.values
    source_no = str(values.get("来源编号") or values.get("上传编号") or values.get("样本编号") or "").strip()
    if not source_no:
        return ActionResult(ok=False, message="请提供来源编号（上传编号或样本编号）")
    result, message = service.parse_payload(
        entry_id,
        source_no=source_no,
        source_type=str(values.get("来源类型") or "") or None,
    )
    if result is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=result)
