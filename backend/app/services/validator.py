"""采集结果校验业务规则：状态流转、字段校验与筛选口径都收在这里。"""
from __future__ import annotations

from typing import Any

from app.store import store

MODULE = "validator"
REQUIRED_FIELDS = ["规则编号", "校验字段", "校验类型"]
STATUS_ORDER = ["草稿", "待测试", "已启用", "已停用"]
ACTION_RULES = {"提交测试": "待测试", "启用规则": "已启用", "停用规则": "已停用"}
NEGATIVE_ACTIONS = ["停用规则"]


class ValidatorService:
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
            rows = [row for row in rows if keyword in str(row.get("规则编号", ""))]
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
            return None, f"校验规则 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于采集结果校验可执行范围"
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"
        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        return entry, f"校验规则已{action}"

    # ---------- 缺项校验：复用采集行定位 ----------

    def check_results(
        self,
        entry_id: int,
        *,
        source_no: str | None = None,
        source_type: str | None = None,
        fields: list[str] | None = None,
        upload_no: str | None = None,
    ) -> tuple[dict[str, Any] | None, str]:
        """读取采集行定位数据集，生成缺项处置明细。

        处置只引用 ``定位编号``，与接入解析共用同一条定位记录；同一票货重投
        时不会重复建处置，而是把新位置挂到已有处置的复核批次上。
        """
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"校验规则 {entry_id} 不存在或已归档"

        from app.services.disposition import disposition_service

        try:
            result = disposition_service.generate(
                upload_no=upload_no if not source_no else None,
                source_no=source_no,
                source_type=source_type,
                fields=fields,
                operator="采集结果校验",
            )
        except LookupError as error:
            return None, str(error)

        entry["最近校验来源"] = result["来源编号"]
        entry["最近处置"] = [item.get("处置编号") for item in result["新建"] + result["更新"]]
        entry["abnormal"] = bool(result["新建"])
        entry["pending"] = bool(result["新建"] or result["更新"])
        message = (
            f"已按 {result['定位条数']} 条定位生成缺项处置："
            f"新建 {len(result['新建'])} 条，更新 {len(result['更新'])} 条"
        )
        return result, message
