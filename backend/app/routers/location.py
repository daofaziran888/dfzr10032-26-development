"""采集行定位与缺项处置接口：接入解析、采集校验、复核回放共用一套定位记录。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

from app.schemas import ActionResult, EntryPayload, PageResult, RecheckPayload
from app.services.disposition import OPEN_STATUSES, disposition_service
from app.services.location import STATUS_ERROR, location_service
from app.store import store

router = APIRouter(prefix="/api/location", tags=["采集行定位"])

DISPOSITION_STATUSES = ["待领取", "核查中", "已关闭", "已被替代"]


@router.get("/sources")
def list_sources() -> dict[str, Any]:
    """正文样本与上传记录清单，用来选择要解析定位的来源。"""
    samples = store.rows("sample")
    uploads = store.rows("upload")
    return {
        "samples": samples,
        "uploads": uploads,
        "total": len(samples) + len(uploads),
    }


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出采集行定位数据集：每条记录都带原文物理行坐标。"""
    items, total = location_service.list_entries(page=1, size=10000)
    return {"module": "row_location", "total": total, "items": items}


@router.get("/parse")
def parse_source(
    source_no: str = Query(description="上传编号 UPLD-xxxx 或样本编号 SAMP-xxxx"),
    source_type: str | None = Query(default=None, description="正文样本、上传记录；不传则自动识别"),
) -> dict[str, Any]:
    """解析正文样本或上传记录，返回带原始坐标的定位记录。

    带引号的多行字段按语法切分：引号内的换行算字段值，不当作下一条记录；
    抽取失败时记录仍保留原文行、列与异常说明，校验人员可以按原文核对。
    """
    try:
        kind, source_row, locations = location_service.ensure_source_locations(source_no, source_type)
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return {
        "来源类型": kind,
        "来源编号": source_row.get("上传编号") if kind == "上传记录" else source_row.get("样本编号"),
        "版本": source_row.get("版本") or "",
        "物理行数": source_row.get("物理行数"),
        "记录数": len(locations),
        "解析状态": source_row.get("解析状态"),
        "解析异常": source_row.get("解析异常", []),
        "items": locations,
    }


@router.get("/dispositions", response_model=PageResult[dict])
def list_dispositions(
    request: Request,
    keyword: str | None = Query(default=None, description="按运单号、处置编号或当前定位编号检索"),
    status: str | None = Query(default=None, description="待领取、核查中、已关闭、已被替代"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """缺项处置明细；解析结果与缺项校验都通过定位编号引用同一条记录。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    extra = {
        k: v for k, v in request.query_params.items()
        if k not in ("keyword", "status", "page", "size") and v.strip()
    }
    items, total = disposition_service.list_entries(
        keyword=keyword, status=status, page=page, size=size, extra=extra
    )
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/dispositions/todos")
def list_todos(assignee: str | None = Query(default=None)) -> dict[str, Any]:
    """复核人员的待办：未关闭且未领取给他人的缺项处置。"""
    items = disposition_service.todos(assignee)
    return {"total": len(items), "items": items}


@router.get("/dispositions/statuses")
def disposition_statuses() -> dict[str, Any]:
    """缺项处置可选状态，供前端筛选。"""
    return {"statuses": DISPOSITION_STATUSES, "open": list(OPEN_STATUSES), "error": STATUS_ERROR}


@router.get("/dispositions/{entry_id}", response_model=dict)
def get_disposition(entry_id: int) -> dict:
    """单条缺项处置；``历史位置`` 与 ``复核批次`` 支撑历史回放。"""
    entry = disposition_service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"缺项处置 {entry_id} 不存在或已归档")
    return entry


@router.get("/dispositions/{entry_id}/locate")
def locate_disposition(entry_id: int, sample_no: str | None = Query(default=None)) -> dict[str, Any]:
    """按原处置号回到当时的物理行；给出样本编号时再按新样本定位当前位置。"""
    entry = disposition_service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"缺项处置 {entry_id} 不存在或已归档")
    old = disposition_service.locate_by_disposition(str(entry.get("处置编号")))
    current = None
    if sample_no:
        current = disposition_service.locate_by_sample(sample_no, str(entry.get("运单号")), str(entry.get("字段名")))
    return {"old": old, "current": current}


@router.post("/dispositions/generate")
def generate_dispositions(payload: EntryPayload) -> ActionResult:
    """从采集行定位数据集生成缺项处置明细。"""
    values = payload.values
    source_no = str(values.get("来源编号") or values.get("上传编号") or "").strip() or None
    try:
        result = disposition_service.generate(
            upload_no=None if source_no else str(values.get("上传编号") or "") or None,
            source_no=source_no,
            source_type=str(values.get("来源类型") or "") or None,
            fields=values.get("字段名") if isinstance(values.get("字段名"), list) else None,
        )
    except LookupError as error:
        return ActionResult(ok=False, message=str(error))
    message = f"已生成缺项处置：新建 {len(result['新建'])} 条，更新 {len(result['更新'])} 条"
    return ActionResult(ok=True, message=message, entry=result)


@router.post("/dispositions/{entry_id}/claim")
def claim_disposition(entry_id: int, payload: EntryPayload) -> ActionResult:
    """复核员按待办领取缺项处置。"""
    entry = disposition_service.get_entry(entry_id)
    if entry is None:
        return ActionResult(ok=False, message=f"缺项处置 {entry_id} 不存在或已归档")
    assignee = str(payload.values.get("复核人") or "值班管理员").strip()
    updated, message = disposition_service.claim(str(entry.get("处置编号")), assignee)
    if updated is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=updated)


@router.post("/recheck")
def recheck(payload: RecheckPayload) -> ActionResult:
    """再次校验：对照最新样本位置，说明手上核查能否覆盖、还应领取哪些待办。"""
    try:
        result = disposition_service.recheck(
            disposition_ref=payload.disposition_ref,
            waybill=payload.waybill,
            upload_no=payload.upload_no,
            assignee=payload.assignee,
            auto_claim=payload.auto_claim,
        )
    except LookupError as error:
        return ActionResult(ok=False, message=str(error))
    covered = [item["字段名"] for item in result["coverage"] if item["结论"] == "已覆盖"]
    message = (
        f"再次校验完成：{len(covered)}/{len(result['缺项字段'])} 项可覆盖，"
        f"待领取 {len(result['should_claim'])} 项"
    )
    return ActionResult(ok=True, message=message, entry=result)



@router.get("/replay")
def replay_mark(
    source_no: str = Query(description="当时的样本或上传编号"),
    field_name: str = Query(description="字段名"),
    record_no: int = Query(default=1, description="记录序号"),
    waybill: str | None = Query(default=None),
) -> dict[str, Any]:
    """沿旧样本标记回放：只按当时的编号与记录序号，回到当时的字段与物理行。"""
    result = disposition_service.replay_mark({
        "来源编号": source_no,
        "字段名": field_name,
        "记录序号": record_no,
        "运单号": waybill,
    })
    if result is None:
        raise HTTPException(status_code=404, detail=f"样本 {source_no} 中没有第 {record_no} 条记录")
    return result
