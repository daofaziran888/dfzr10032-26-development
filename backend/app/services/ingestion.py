"""数据接入通道业务规则：状态流转、字段校验与筛选口径都收在这里。"""
from __future__ import annotations

from typing import Any

from app.store import store

MODULE = "ingestion"
REQUIRED_FIELDS = ["通道编号", "通道名称", "输入格式"]
STATUS_ORDER = ["待开通", "开通中", "运行中", "已关闭"]
ACTION_RULES = {"开通通道": "开通中", "暂停通道": "运行中", "关闭通道": "已关闭"}
NEGATIVE_ACTIONS = []


class IngestionService:
    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
        extra: dict[str, str] | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("通道编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        if extra:
            for field, val in extra.items():
                rows = [row for row in rows if val in str(row.get(field, ""))]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        return store.find(MODULE, entry_id)

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in REQUIRED_FIELDS})
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        rows.append(entry)
        return entry, []

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"接入通道 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于数据接入通道可执行范围"
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"
        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        return entry, f"接入通道已{action}"

    # ---------- 接入解析：复用采集行定位 ----------

    def parse_payload(
        self,
        entry_id: int,
        *,
        source_no: str,
        source_type: str | None = None,
    ) -> tuple[dict[str, Any] | None, str]:
        """接入接口解析正文样本或上传记录，返回采集行定位记录。

        带引号的多行字段按语法切分：引号内的换行属于字段值，不会被误切成下一条
        记录。解析异常不拦截接入，记录照常落成定位，供校验人员核对原文位置。
        """
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"接入通道 {entry_id} 不存在或已归档"

        from app.services.location import location_service

        try:
            kind, source_row, locations = location_service.ensure_source_locations(source_no, source_type)
        except LookupError as error:
            return None, str(error)

        entry["最近解析来源"] = source_row.get("上传编号") if kind == "上传记录" else source_row.get("样本编号")
        entry["最近解析定位"] = [item.get("定位编号") for item in locations]
        entry["解析异常"] = list(source_row.get("解析异常") or [])
        abnormal = bool(source_row.get("解析异常"))
        entry["abnormal"] = abnormal
        message = f"已接入 {len(locations)} 条记录，复用采集行定位 {len(locations)} 条"
        if abnormal:
            message += "；存在解析异常，已保留原文位置供核对"
        return {
            "通道": entry,
            "来源类型": kind,
            "来源编号": source_row.get("上传编号") if kind == "上传记录" else source_row.get("样本编号"),
            "物理行数": source_row.get("物理行数"),
            "解析状态": source_row.get("解析状态"),
            "解析异常": list(source_row.get("解析异常") or []),
            "items": locations,
        }, message
