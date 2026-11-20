"""缺项处置业务规则：从采集行定位数据集生成处置明细，并处理复核领取与再次校验。

每条处置只引用 ``定位编号``，不复制原文；重新打开历史处置时，按当时的样本
标记（样本编号 + 上传编号 + 记录序号 + 字段名）就能回放到当时的字段与物理行。
复核人员按待办领取处置后，新样本再校验时不再重复生成同字段处置，而是把新位置
挂到已有处置的复核批次上，并告诉复核员手上的核查能否覆盖新位置、还应领取哪些
待办。
"""
from __future__ import annotations

from typing import Any

from app.services.location import (
    REQUIRED_VALUE_FIELDS,
    location_service,
)
from app.store import store

MODULE = "disposition"
SOURCE_CHECK = "采集结果校验"
STATUS_OPEN = "待领取"
STATUS_CLAIMED = "核查中"
STATUS_CLOSED = "已关闭"
STATUS_SUPERSEDED = "已被替代"
OPEN_STATUSES = (STATUS_OPEN, STATUS_CLAIMED)


def _next_id(rows: list[dict[str, Any]]) -> int:
    return max((int(row.get("id", 0)) for row in rows), default=0) + 1


def _next_no(rows: list[dict[str, Any]], prefix: str = "DISP") -> str:
    numbers: list[int] = []
    for row in rows:
        value = str(row.get("处置编号") or "")
        if value.startswith(prefix + "-"):
            tail = value.rsplit("-", 1)[-1]
            if tail.isdigit():
                numbers.append(int(tail))
    return f"{prefix}-{max(numbers, default=0) + 1:04d}"


def _source_mark(location: dict[str, Any], field_name: str) -> dict[str, Any]:
    """历史回放用的样本标记：只写编号与版本，不复制原文。"""
    return {
        "样本编号": location.get("样本编号") or "",
        "上传编号": location.get("上传编号") or "",
        "来源类型": location.get("来源类型") or "",
        "来源编号": location.get("来源编号") or "",
        "记录序号": location.get("记录序号"),
        "字段名": field_name,
    }


def _position_ref(location: dict[str, Any], field_name: str) -> dict[str, Any]:
    position = location_service.position_of_field(location, field_name)
    return {
        "定位编号": position["定位编号"],
        "上传编号": position["上传编号"],
        "样本编号": position["样本编号"],
        "字段名": field_name,
        "物理行": position["物理行"],
        "起始行": position["起始行"],
        "结束行": position["结束行"],
        "记录起始行": position["记录起始行"],
        "记录结束行": position["记录结束行"],
        "值": position["字段定位"].get("值"),
        "解析状态": position["解析状态"],
        "异常说明": position["异常说明"],
    }


def _snapshot_position(snapshot: dict[str, Any] | None, field_name: str) -> dict[str, Any] | None:
    if not snapshot:
        return None
    return {
        "定位编号": snapshot.get("定位编号"),
        "上传编号": snapshot.get("上传编号"),
        "样本编号": snapshot.get("样本编号"),
        "字段名": field_name,
        "物理行": snapshot.get("物理行"),
        "起始行": snapshot.get("起始行"),
        "结束行": snapshot.get("结束行"),
        "值": snapshot.get("值"),
    }


def _same_ticket(row: dict[str, Any], waybill: str) -> bool:
    return bool(waybill) and str(row.get("运单号")) == str(waybill)


def _row_for_field(waybill: str, field_name: str) -> dict[str, Any] | None:
    """同一票货同一字段的处置：优先未关闭的，其次最新的一条。"""
    rows = [
        row for row in store.rows(MODULE)
        if _same_ticket(row, waybill) and row.get("字段名") == field_name
    ]
    if not rows:
        return None
    open_rows = [row for row in rows if row.get("status") in OPEN_STATUSES]
    pool = open_rows or rows
    return max(pool, key=lambda row: int(row.get("id", 0)))


def _latest_batch(disposition: dict[str, Any]) -> dict[str, Any]:
    return disposition["复核批次"][-1]


class DispositionService:
    # ---------- 生成 ----------

    def generate(
        self,
        *,
        upload_no: str | None = None,
        source_no: str | None = None,
        source_type: str | None = None,
        fields: list[str] | None = None,
        operator: str = "值班管理员",
    ) -> dict[str, Any]:
        """从定位数据集生成缺项处置明细。

        默认针对样本的最新上传版本。``fields`` 可以只校验指定字段，默认校验
        ``REQUIRED_VALUE_FIELDS``。
        """
        if source_no:
            kind, source_row, _ = location_service.ensure_source_locations(source_no, source_type)
        else:
            upload = location_service.latest_upload(upload_no)
            if upload is None:
                raise LookupError("没有可校验的上传记录")
            kind, source_row, _ = location_service.ensure_source_locations(str(upload.get("上传编号")), "上传记录")

        target_fields = list(fields or REQUIRED_VALUE_FIELDS)
        source_no_value = str(
            source_row.get("上传编号") if kind == "上传记录" else source_row.get("样本编号")
        )
        locations = location_service.locations_of_source(source_no_value, kind)
        rows = store.rows(MODULE)
        created: list[dict[str, Any]] = []
        refreshed: list[dict[str, Any]] = []
        unchanged: list[dict[str, Any]] = []

        for location in locations:
            for field_name in target_fields:
                snapshot = location_service.field_snapshot(location, field_name)
                if snapshot is not None and str(snapshot.get("值") or "").strip():
                    continue
                reason = "字段不存在" if snapshot is None else "字段值为空"

                waybill = str(location.get("运单号") or "")
                existing = _row_for_field(waybill, field_name)
                if existing is not None and existing.get("status") in OPEN_STATUSES:
                    if self._supersede(existing, location, field_name, source_row, operator):
                        refreshed.append(existing)
                    else:
                        unchanged.append(existing)
                    continue
                if existing is not None and existing.get("status") == STATUS_CLOSED:
                    existing["status"] = STATUS_SUPERSEDED
                    existing["pending"] = False

                position = _position_ref(location, field_name)
                disposition = {
                    "id": _next_id(rows + created),
                    "处置编号": _next_no(rows + created),
                    "status": STATUS_OPEN,
                    "pending": True,
                    "abnormal": False,
                    "运单号": waybill,
                    "字段名": field_name,
                    "缺项原因": reason,
                    "生成来源": SOURCE_CHECK,
                    "样本标记": _source_mark(location, field_name),
                    "当前定位编号": location.get("定位编号"),
                    "当前位置": position,
                    "当前值": position.get("值") or "",
                    "解析状态": location.get("解析状态"),
                    "异常说明": location.get("异常说明") or "",
                    "产生批次": str(source_row.get("版本") or kind),
                    "复核批次": [{
                        "批次号": 1,
                        "来源编号": location.get("来源编号"),
                        "来源版本": source_row.get("版本") or "",
                        "定位编号": location.get("定位编号"),
                        "样本标记": _source_mark(location, field_name),
                        "位置": position,
                        "处置号": None,
                        "created_by": operator,
                        "created_at": str(source_row.get("上传时间") or ""),
                        "active": True,
                    }],
                    "历史位置": [],
                    "领取人": "",
                    "领取时间": "",
                }
                disposition["复核批次"][0]["处置号"] = disposition["处置编号"]
                created.append(disposition)
                rows.append(disposition)

        return {
            "来源类型": kind,
            "来源编号": source_no_value,
            "来源版本": source_row.get("版本") or "",
            "校验字段": target_fields,
            "定位条数": len(locations),
            "新建": created,
            "更新": refreshed,
            "未变化": unchanged,
        }

    def _supersede(
        self,
        disposition: dict[str, Any],
        location: dict[str, Any],
        field_name: str,
        source_row: dict[str, Any],
        operator: str,
    ) -> bool:
        """新版本重现同一缺项时，把新位置挂到已有处置，旧位置进历史。"""
        location_no = str(location.get("定位编号") or "")
        if disposition.get("当前定位编号") == location_no:
            return False

        position = _position_ref(location, field_name)
        batch = _latest_batch(disposition)
        disposition.setdefault("历史位置", []).append({
            "定位编号": batch.get("定位编号"),
            "上传编号": (batch.get("位置") or {}).get("上传编号"),
            "样本编号": (batch.get("位置") or {}).get("样本编号"),
            "字段名": field_name,
            "物理行": (batch.get("位置") or {}).get("物理行"),
            "值": (batch.get("位置") or {}).get("值"),
            "样本标记": batch.get("样本标记"),
            "来源版本": batch.get("来源版本"),
            "处置号": disposition.get("处置编号"),
        })
        batch["active"] = False
        disposition["复核批次"].append({
            "批次号": int(batch.get("批次号", 0)) + 1,
            "来源编号": location.get("来源编号"),
            "来源版本": source_row.get("版本") or "",
            "定位编号": location_no,
            "样本标记": _source_mark(location, field_name),
            "位置": position,
            "处置号": disposition.get("处置编号"),
            "created_by": operator,
            "created_at": str(source_row.get("上传时间") or ""),
            "active": True,
        })
        disposition["当前定位编号"] = location_no
        disposition["当前位置"] = position
        disposition["当前值"] = position.get("值") or ""
        disposition["样本标记"] = _source_mark(location, field_name)
        disposition["解析状态"] = location.get("解析状态")
        disposition["异常说明"] = location.get("异常说明") or ""
        # 值已补齐就关闭；仍缺则保持原状态，不打断复核员手上的核查。
        if str(position.get("值") or "").strip():
            disposition["status"] = STATUS_CLOSED
            disposition["pending"] = False
            disposition["abnormal"] = True
        return True

    # ---------- 查询 ----------

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
            rows = [
                row for row in rows
                if keyword in str(row.get("运单号", ""))
                or keyword in str(row.get("处置编号", ""))
                or keyword in str(row.get("当前定位编号", ""))
            ]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        if extra:
            for field, value in extra.items():
                rows = [row for row in rows if value in str(row.get(field, ""))]
        rows = sorted(rows, key=lambda row: int(row.get("id", 0)))
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        return store.find(MODULE, entry_id)

    def todos(self, assignee: str | None = None) -> list[dict[str, Any]]:
        rows = [row for row in store.rows(MODULE) if row.get("status") in OPEN_STATUSES]
        if assignee:
            rows = [row for row in rows if not row.get("领取人") or row.get("领取人") == assignee]
        return sorted(rows, key=lambda row: int(row.get("id", 0)))

    def resolve(self, ref: str) -> dict[str, Any] | None:
        """按处置编号（DISP-0001）或数字 id 找处置。"""
        text = str(ref or "").strip()
        if not text:
            return None
        for row in store.rows(MODULE):
            if str(row.get("处置编号")) == text or str(row.get("id")) == text:
                return row
        return None

    # ---------- 领取 / 再次校验 ----------

    def claim(self, disposition_ref: str, assignee: str) -> tuple[dict[str, Any] | None, str]:
        disposition = self.resolve(disposition_ref)
        if disposition is None:
            return None, f"处置 {disposition_ref} 不存在或已归档"
        if disposition.get("status") == STATUS_CLOSED:
            return None, f"处置 {disposition.get('处置编号')} 已关闭，无需领取"
        if disposition.get("status") == STATUS_SUPERSEDED:
            return None, f"处置 {disposition.get('处置编号')} 已被替代，请领取新处置"
        if disposition.get("领取人") and disposition.get("领取人") != assignee:
            return None, f"处置已由 {disposition.get('领取人')} 领取"
        if not disposition.get("领取时间"):
            disposition["领取时间"] = "2026-09-02 11:00"
        disposition["领取人"] = assignee
        disposition["status"] = STATUS_CLAIMED
        disposition["pending"] = True
        return disposition, f"处置 {disposition.get('处置编号')} 已领取"

    def recheck(
        self,
        *,
        disposition_ref: str | None = None,
        waybill: str | None = None,
        upload_no: str | None = None,
        assignee: str = "值班管理员",
        auto_claim: bool = False,
    ) -> dict[str, Any]:
        """再次校验：对照最新样本位置，说明手上核查能否覆盖、还应领取哪些待办。

        默认只诊断不落库；``auto_claim`` 为真时把可领取的处置领到复核人名下，
        并把已领取的处置位置同步到最新一批。无论处置当前归到哪一批，原处置号
        始终能回放到产生时的物理行。
        """
        if disposition_ref:
            seed = self.resolve(disposition_ref)
            if seed is None:
                raise LookupError(f"处置 {disposition_ref} 不存在或已归档")
            waybill = str(seed.get("运单号") or "")
        if not waybill:
            raise LookupError("请提供处置编号或运单号")

        latest_location = self._latest_location_for_waybill(waybill, upload_no)
        if latest_location is None:
            raise LookupError(f"运单号 {waybill} 在最新样本里没有可用定位")
        latest_no = str(latest_location.get("定位编号") or "")

        coverage: list[dict[str, Any]] = []
        should_claim: list[dict[str, Any]] = []

        for field_name in REQUIRED_VALUE_FIELDS:
            snapshot = location_service.field_snapshot(latest_location, field_name)
            filled = bool(snapshot and str(snapshot.get("值") or "").strip())
            row = _row_for_field(waybill, field_name)
            new_position = _position_ref(latest_location, field_name)
            old_position = _snapshot_position((row or {}).get("当前位置"), field_name)

            if filled:
                # 业务值已更正：未关闭的处置可以收口，不占待办。
                result = ""
                if row is not None and row.get("status") in OPEN_STATUSES:
                    if auto_claim:
                        self._touch_batch(row, latest_location, field_name, assignee)
                        result = "已按新值关闭"
                    else:
                        result = "待关闭"
                coverage.append({
                    "字段名": field_name,
                    "处置编号": (row or {}).get("处置编号"),
                    "处置状态": (row or {}).get("status"),
                    "结论": "已更正" if result != "待关闭" else "可关闭",
                    "说明": "新样本该项已有值，缺项不再成立",
                    "旧位置": old_position,
                    "新位置": new_position,
                    "处理结果": result,
                })
                continue

            if row is None:
                coverage.append({
                    "字段名": field_name,
                    "处置编号": None,
                    "处置状态": None,
                    "结论": "无处置",
                    "说明": "新样本仍缺该字段，之前也没有对应处置，需要先生成再领取",
                    "旧位置": None,
                    "新位置": new_position,
                    "处理结果": "",
                })
                should_claim.append({
                    "字段名": field_name,
                    "处置编号": None,
                    "建议动作": "生成处置并领取",
                    "说明": "该字段没有历史处置，需按最新定位生成",
                })
                continue

            state = row.get("status")
            holder = str(row.get("领取人") or "")
            same_holder = bool(holder) and holder == assignee
            already_latest = str(row.get("当前定位编号") or "") == latest_no
            action = None
            result = ""

            # 位置变化时先比对字段值：旧位置有值、新位置为空，是新出现的缺项，
            # 不属于原核查能覆盖的范围，需要另起处置。
            if not already_latest and not filled:
                old_value = str((row.get("当前位置") or {}).get("值") or "")
                if old_value.strip():
                    result = "原处置已关闭" if auto_claim else "待关闭原处置"
                    if auto_claim:
                        self._touch_batch(row, latest_location, field_name, assignee)
                        row["status"] = STATUS_CLOSED
                        row["pending"] = False
                    coverage.append({
                        "字段名": field_name,
                        "处置编号": row.get("处置编号"),
                        "处置状态": row.get("status"),
                        "结论": "新缺项",
                        "说明": "旧位置该项有值、新样本变空；原核查覆盖不到，需要按新缺项重新生成处置",
                        "旧位置": old_position,
                        "新位置": new_position,
                        "处理结果": result,
                    })
                    should_claim.append({
                        "字段名": field_name,
                        "处置编号": None,
                        "建议动作": "生成处置并领取",
                        "说明": "新样本新出现的缺项，需按最新定位生成处置",
                    })
                    continue

            if state == STATUS_CLOSED:
                conclusion = "已关闭"
                note = "处置已关闭，但新样本仍缺该项，需要重新生成后再领取"
                action = "重新生成并领取"
            elif state == STATUS_SUPERSEDED:
                conclusion = "已失效"
                note = "原处置已被替代，需要按新处置领取"
                action = "重新生成并领取"
            elif state == STATUS_CLAIMED and not same_holder:
                conclusion = "他人核查中"
                note = f"处置由 {holder} 领取，不能重复领取，需要交接"
                action = "交接"
            elif state == STATUS_OPEN:
                conclusion = "待领取"
                note = "缺项仍未更正，处置尚未领取"
                action = "领取"
            elif same_holder and already_latest:
                conclusion = "已覆盖"
                note = "手上核查已指向最新定位，可继续按新物理行核对"
                if auto_claim:
                    self._touch_batch(row, latest_location, field_name, assignee)
            elif same_holder:
                conclusion = "需更新位置"
                note = "核查已领取，但位置仍指向旧样本，需要同步到最新位置"
                action = "更新位置"
            else:
                conclusion = "待领取"
                note = "处置尚未领取"
                action = "领取"

            if action in ("领取", "更新位置") and auto_claim:
                if action == "领取":
                    self.claim(str(row.get("处置编号")), assignee)
                self._touch_batch(row, latest_location, field_name, assignee)
                if str((row.get("当前位置") or {}).get("值") or "").strip():
                    row["status"] = STATUS_CLOSED
                    row["pending"] = False
                result = "已领取并同步位置" if action == "领取" else "已同步到最新位置"
                if row.get("当前定位编号") == latest_no:
                    conclusion = "已覆盖"
            elif action and action not in ("交接", "重新生成并领取"):
                should_claim.append({
                    "字段名": field_name,
                    "处置编号": row.get("处置编号"),
                    "建议动作": action,
                    "说明": note,
                })
            elif action == "交接":
                should_claim.append({
                    "字段名": field_name,
                    "处置编号": row.get("处置编号"),
                    "建议动作": "交接",
                    "说明": note,
                })

            coverage.append({
                "字段名": field_name,
                "处置编号": row.get("处置编号"),
                "处置状态": row.get("status"),
                "结论": conclusion,
                "说明": note,
                "旧位置": old_position,
                "新位置": new_position,
                "处理结果": result,
            })

        missing_fields = [item["字段名"] for item in coverage if item["结论"] != "已更正"]
        missing_fields = [
            field_name for field_name in missing_fields
            if not (
                location_service.field_snapshot(latest_location, field_name)
                and str(location_service.field_snapshot(latest_location, field_name).get("值") or "").strip()
            )
        ]
        coverable = {
            item["字段名"] for item in coverage
            if item["结论"] == "已覆盖" or item["结论"] == "已更正"
        }
        all_held = [row for row in store.rows(MODULE) if _same_ticket(row, waybill)]

        return {
            "运单号": waybill,
            "复核人": assignee,
            "最新定位编号": latest_no,
            "最新样本标记": self._source_version_mark(latest_location),
            "缺项字段": missing_fields,
            "coverage": coverage,
            "can_cover": bool(missing_fields) and all(field in coverable for field in missing_fields),
            "should_claim": should_claim,
            "locate_by_old_disposition": {
                str(row.get("处置编号")): self.locate_by_disposition(str(row.get("处置编号")))
                for row in all_held
            },
            "locate_by_new_sample": {
                "定位编号": latest_no,
                "运单号": waybill,
                "位置": _position_ref(latest_location, "整条记录"),
                "字段位置": {
                    field_name: _position_ref(latest_location, field_name)
                    for field_name in dict.fromkeys([*REQUIRED_VALUE_FIELDS, "整条记录"])
                },
            },
        }

    def _latest_location_for_waybill(self, waybill: str, upload_no: str | None) -> dict[str, Any] | None:
        if upload_no:
            kind, source_row, _ = location_service.ensure_source_locations(upload_no, "上传记录")
            candidates = location_service.locations_of_waybill(
                waybill, str(source_row.get("上传编号")), kind
            )
            return candidates[0] if candidates else None

        sample_nos = {
            str(row.get("样本编号"))
            for row in location_service.locations_of_waybill(waybill)
            if row.get("样本编号")
        }
        latest: dict[str, Any] | None = None
        for sample_no in sorted(sample_nos):
            upload = location_service.latest_upload(sample_no)
            if upload is None:
                continue
            kind, source_row, _ = location_service.ensure_source_locations(
                str(upload.get("上传编号")), "上传记录"
            )
            candidates = location_service.locations_of_waybill(
                waybill, str(source_row.get("上传编号")), kind
            )
            if candidates:
                latest = candidates[0]
        return latest

    def _touch_batch(
        self,
        disposition: dict[str, Any],
        location: dict[str, Any],
        field_name: str,
        assignee: str,
    ) -> None:
        batches = disposition.setdefault("复核批次", [])
        batch = batches[-1] if batches else None
        if batch and batch.get("定位编号") == location.get("定位编号"):
            batch["checked_by"] = assignee
            batch["active"] = True
        else:
            position = _position_ref(location, field_name)
            if batch:
                batch["active"] = False
                disposition.setdefault("历史位置", []).append({
                    "定位编号": batch.get("定位编号"),
                    "上传编号": (batch.get("位置") or {}).get("上传编号"),
                    "样本编号": (batch.get("位置") or {}).get("样本编号"),
                    "字段名": field_name,
                    "物理行": (batch.get("位置") or {}).get("物理行"),
                    "值": (batch.get("位置") or {}).get("值"),
                    "样本标记": batch.get("样本标记"),
                    "来源版本": batch.get("来源版本"),
                    "处置号": disposition.get("处置编号"),
                })
            batches.append({
                "批次号": int(batch.get("批次号", 0)) + 1 if batch else 1,
                "来源编号": location.get("来源编号"),
                "来源版本": self._source_version_mark(location).get("来源版本", ""),
                "定位编号": location.get("定位编号"),
                "样本标记": _source_mark(location, field_name),
                "位置": position,
                "处置号": disposition.get("处置编号"),
                "created_by": assignee,
                "created_at": "2026-09-02 11:05",
                "checked_by": assignee,
                "active": True,
            })
        disposition["当前定位编号"] = location.get("定位编号")
        disposition["当前位置"] = _position_ref(location, field_name)
        disposition["当前值"] = disposition["当前位置"].get("值") or ""
        disposition["样本标记"] = _source_mark(location, field_name)
        if str(disposition["当前值"]).strip():
            disposition["status"] = STATUS_CLOSED
            disposition["pending"] = False

    @staticmethod
    def _source_version_mark(location: dict[str, Any]) -> dict[str, Any]:
        version = ""
        upload_no = str(location.get("上传编号") or "")
        if upload_no:
            for row in store.rows("upload"):
                if str(row.get("上传编号")) == upload_no:
                    version = str(row.get("版本") or "")
                    break
        return {
            "样本编号": location.get("样本编号"),
            "上传编号": location.get("上传编号"),
            "来源类型": location.get("来源类型"),
            "来源编号": location.get("来源编号"),
            "来源版本": version,
            "记录序号": location.get("记录序号"),
        }

    # ---------- 历史回放 ----------

    def locate_by_disposition(self, disposition_ref: str) -> dict[str, Any] | None:
        """按原处置号定位到当时的物理行（处置产生时的那一批）。"""
        disposition = self.resolve(disposition_ref)
        if disposition is None:
            return None
        batches = disposition.get("复核批次") or []
        original = batches[0] if batches else None
        return {
            "处置编号": disposition.get("处置编号"),
            "运单号": disposition.get("运单号"),
            "字段名": disposition.get("字段名"),
            "处置状态": disposition.get("status"),
            "原批次号": (original or {}).get("批次号"),
            "当时位置": (original or {}).get("位置"),
            "当时样本标记": (original or {}).get("样本标记"),
            "当前位置": disposition.get("当前位置"),
            "复核批次": batches,
            "历史位置": disposition.get("历史位置") or [],
        }

    def locate_by_sample(self, sample_ref: str, waybill: str, field_name: str | None = None) -> dict[str, Any] | None:
        """按样本/上传版本 + 运单号定位当前位置。"""
        kind, source_row, _ = location_service.ensure_source_locations(sample_ref)
        source_no = str(source_row.get("上传编号") if kind == "上传记录" else source_row.get("样本编号"))
        rows = location_service.locations_of_waybill(waybill, source_no, kind)
        if not rows:
            return None
        location = rows[0]
        field = field_name or "整条记录"
        return {
            "来源类型": kind,
            "来源编号": source_no,
            "来源版本": source_row.get("版本") or "",
            "运单号": waybill,
            "定位编号": location.get("定位编号"),
            "位置": _position_ref(location, field),
        }

    def replay_mark(self, mark: dict[str, Any]) -> dict[str, Any] | None:
        """沿旧样本标记回放到当时的字段与物理行。

        ``mark`` 至少包含 ``来源编号``（上传或样本编号）、``记录序号``、
        ``字段名``；只靠编号就能重建，不依赖处置当前指向哪一批。
        """
        field_name = str(mark.get("字段名") or "")
        source_no = str(mark.get("上传编号") or mark.get("来源编号") or mark.get("样本编号") or "")
        if not source_no:
            return None
        try:
            kind, source_row, locations = location_service.ensure_source_locations(
                source_no, str(mark.get("来源类型") or "") or None
            )
        except LookupError:
            return None
        record_no = mark.get("记录序号")
        location = next(
            (item for item in locations if item.get("记录序号") == record_no),
            None,
        )
        if location is None:
            location = next(
                (item for item in locations if str(item.get("运单号")) == str(mark.get("运单号"))),
                None,
            )
        if location is None:
            return None
        position = _position_ref(location, field_name or "整条记录")
        return {
            "样本标记": mark,
            "来源类型": kind,
            "来源编号": source_no,
            "来源版本": source_row.get("版本") or "",
            "定位编号": location.get("定位编号"),
            "位置": position,
        }


disposition_service = DispositionService()
