"""采集行定位业务规则：把正文样本、上传记录解析成带原文坐标的定位数据集。

定位记录是接入解析与采集结果校验共用的唯一事实来源：接入接口解析出记录后，
返回的就是这里的 ``定位编号``；采集结果校验生成缺项处置时，也只引用同一个
``定位编号``。解析异常不会丢记录，``字段定位`` 仍保留原始行、列，方便校验人员
按原文核对。
"""
from __future__ import annotations

from typing import Any

from app.services.dsv import DelimitedParse, parse_delimited
from app.store import store

MODULE = "row_location"
SAMPLE_MODULE = "sample"
UPLOAD_MODULE = "upload"
SOURCE_SAMPLE = "正文样本"
SOURCE_UPLOAD = "上传记录"
# 业务上必须有值的字段；缺了就要生成缺项处置。
REQUIRED_VALUE_FIELDS = ["邮编", "电话"]

STATUS_OK = "已解析"
STATUS_ERROR = "解析异常"


def _next_id(rows: list[dict[str, Any]]) -> int:
    return max((int(row.get("id", 0)) for row in rows), default=0) + 1


def _next_no(rows: list[dict[str, Any]], field: str, prefix: str) -> str:
    """按已有编号生成下一个流水号，例如 LOC-0004。"""
    numbers: list[int] = []
    for row in rows:
        value = str(row.get(field) or "")
        if value.startswith(prefix + "-"):
            tail = value.rsplit("-", 1)[-1]
            if tail.isdigit():
                numbers.append(int(tail))
    return f"{prefix}-{max(numbers, default=0) + 1:04d}"


def _source_config(source_row: dict[str, Any], sample_row: dict[str, Any] | None) -> dict[str, Any]:
    def pick(key: str, default: Any) -> Any:
        value = source_row.get(key)
        if value in (None, ""):
            value = (sample_row or {}).get(key)
        return default if value in (None, "") else value

    field_names = pick("字段名列表", [])
    if not field_names:
        raw = pick("字段名", "")
        field_names = [item.strip() for item in str(raw).replace("，", ",").split(",") if item.strip()]
    return {
        "delimiter": str(pick("分隔符", ",")),
        "quote": str(pick("引号字符", '"')),
        "field_names": list(field_names) if isinstance(field_names, (list, tuple)) else [str(field_names)],
        "has_header": source_row.get("含标题行"),
    }


def _field_rows(parsed: DelimitedParse) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for record in parsed.records:
        fields: list[dict[str, Any]] = []
        for position, span in enumerate(record.fields):
            name = parsed.field_names[position] if position < len(parsed.field_names) else f"列{position + 1}"
            fields.append({
                "字段名": name,
                "值": span.value,
                "起始行": span.start_line,
                "起始列": span.start_col,
                "结束行": span.end_line,
                "结束列": span.end_col,
                "字符起": span.char_start,
                "字符止": span.char_end,
                "引号包裹": span.quoted,
                "异常": span.error,
            })
        rows.append({
            "记录序号": record.index,
            "起始行": record.start_line,
            "结束行": record.end_line,
            "字符起": record.start_char,
            "字符止": record.end_char,
            "字段定位": fields,
            "异常说明": record.error,
        })
    return rows


def _waybill(fields: list[dict[str, Any]]) -> str:
    for item in fields:
        if item["字段名"] == "运单号":
            return str(item["值"]).strip()
    return str(fields[0]["值"]).strip() if fields else ""


class LocationService:
    """从来源解析定位记录，并提供按来源、按运单号、按字段的回查。"""

    # ---------- 来源解析 ----------

    def find_source(self, source_ref: str, source_type: str | None = None) -> tuple[str, dict[str, Any], dict[str, Any] | None]:
        """按来源编号（UPLD-/SAMP-）或数字 id 找上传记录或正文样本。"""
        ref = str(source_ref or "").strip()
        if not ref:
            raise LookupError("请提供来源编号（上传编号或样本编号）")

        candidates: list[tuple[str, dict[str, Any], dict[str, Any] | None]] = []
        if source_type in (None, SOURCE_UPLOAD):
            for row in store.rows(UPLOAD_MODULE):
                if str(row.get("上传编号")) == ref or str(row.get("id")) == ref:
                    sample = self._sample(str(row.get("样本编号") or ""))
                    candidates.append((SOURCE_UPLOAD, row, sample))
        if source_type in (None, SOURCE_SAMPLE):
            for row in store.rows(SAMPLE_MODULE):
                if str(row.get("样本编号")) == ref or str(row.get("id")) == ref:
                    candidates.append((SOURCE_SAMPLE, row, row))
        if not candidates:
            raise LookupError(f"来源 {ref} 不存在或已归档")
        # 优先按业务编号精确命中上传记录，其次样本。
        for item in candidates:
            if item[1].get("上传编号") == ref:
                return item
        return candidates[0]

    def _sample(self, sample_no: str) -> dict[str, Any] | None:
        for row in store.rows(SAMPLE_MODULE):
            if str(row.get("样本编号")) == sample_no:
                return row
        return None

    def _source_no(self, source_type: str, source_row: dict[str, Any]) -> str:
        return str(source_row.get("上传编号") if source_type == SOURCE_UPLOAD else source_row.get("样本编号") or "")

    def latest_upload(self, sample_no: str | None = None) -> dict[str, Any] | None:
        rows = [row for row in store.rows(UPLOAD_MODULE) if row.get("是否最新")]
        if sample_no:
            rows = [row for row in rows if str(row.get("样本编号")) == sample_no]
        if rows:
            return max(rows, key=lambda row: int(row.get("版本号", 0) or 0))
        uploads = store.rows(UPLOAD_MODULE)
        return max(uploads, key=lambda row: int(row.get("版本号", 0) or 0)) if uploads else None

    # ---------- 生成定位记录 ----------

    def ensure_source_locations(self, source_ref: str, source_type: str | None = None) -> tuple[str, dict[str, Any], list[dict[str, Any]]]:
        """解析来源并落成定位记录；同一来源重复调用返回同一批记录。"""
        kind, source_row, sample_row = self.find_source(source_ref, source_type)
        source_no = self._source_no(kind, source_row)
        existing = self.locations_of_source(source_no, kind)
        if existing:
            return kind, source_row, existing
        body = str(source_row.get("正文内容") or "")
        if not body.strip():
            raise LookupError(f"来源 {source_no} 没有正文内容，无法解析定位")

        config = _source_config(source_row, sample_row)
        parsed = parse_delimited(
            str(source_row.get("正文内容") or ""),
            delimiter=config["delimiter"],
            quote=config["quote"],
            has_header=config["has_header"],
            field_names=config["field_names"] or None,
        )
        rows = store.rows(MODULE)
        created: list[dict[str, Any]] = []
        for parsed_row in _field_rows(parsed):
            fields = parsed_row["字段定位"]
            abnormal = bool(parsed_row["异常说明"])
            waybill = _waybill(fields)
            entry = {
                "id": _next_id(rows + created),
                "定位编号": _next_no(rows + created, "定位编号", "LOC"),
                "来源类型": kind,
                "来源编号": source_no,
                "样本编号": source_row.get("样本编号"),
                "上传编号": source_no if kind == SOURCE_UPLOAD else "",
                "记录序号": parsed_row["记录序号"],
                "运单号": waybill,
                "起始行": parsed_row["起始行"],
                "结束行": parsed_row["结束行"],
                "物理行": self._line_label(parsed_row["起始行"], parsed_row["结束行"]),
                "字段数": len(fields),
                "字段定位": fields,
                "原文片段": str(source_row.get("正文内容") or "")[parsed_row["字符起"]:parsed_row["字符止"]],
                "异常说明": parsed_row["异常说明"] or "",
                "解析状态": STATUS_ERROR if abnormal else STATUS_OK,
                "status": STATUS_ERROR if abnormal else STATUS_OK,
                "pending": False,
                "abnormal": abnormal,
            }
            created.append(entry)
        rows.extend(created)

        source_row["解析状态"] = STATUS_ERROR if parsed.has_error else STATUS_OK
        source_row["物理行数"] = parsed.physical_lines
        source_row["记录数"] = len(parsed.records)
        source_row["定位编号集"] = [item["定位编号"] for item in created]
        source_row["解析异常"] = list(parsed.errors)
        source_row["status"] = STATUS_ERROR if parsed.has_error else STATUS_OK
        source_row["abnormal"] = parsed.has_error
        return kind, source_row, created

    @staticmethod
    def _line_label(start: int, end: int) -> str:
        return str(start) if start == end else f"{start}-{end}"

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
            rows = [row for row in rows if keyword in str(row.get("运单号", "")) or keyword in str(row.get("定位编号", ""))]
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

    def locations_of_source(self, source_no: str, source_type: str | None = None) -> list[dict[str, Any]]:
        rows = [row for row in store.rows(MODULE) if str(row.get("来源编号")) == str(source_no)]
        if source_type:
            rows = [row for row in rows if row.get("来源类型") == source_type]
        return sorted(rows, key=lambda row: int(row.get("记录序号", 0)))

    def locations_of_waybill(self, waybill: str, source_no: str | None = None, source_type: str | None = None) -> list[dict[str, Any]]:
        if source_no:
            rows = self.locations_of_source(source_no, source_type)
        else:
            rows = store.rows(MODULE)
        return sorted(
            [row for row in rows if str(row.get("运单号")) == str(waybill)],
            key=lambda row: (str(row.get("来源编号")), int(row.get("记录序号", 0))),
        )

    @staticmethod
    def field_snapshot(location: dict[str, Any], field_name: str) -> dict[str, Any] | None:
        for item in location.get("字段定位", []):
            if item.get("字段名") == field_name:
                return {
                    "字段名": item["字段名"],
                    "值": item["值"],
                    "起始行": item["起始行"],
                    "起始列": item["起始列"],
                    "结束行": item["结束行"],
                    "结束列": item["结束列"],
                    "字符起": item["字符起"],
                    "字符止": item["字符止"],
                    "引号包裹": item["引号包裹"],
                    "异常": item["异常"],
                }
        return None

    @staticmethod
    def record_snapshot(location: dict[str, Any]) -> dict[str, Any]:
        return {
            "字段名": "整条记录",
            "值": str(location.get("原文片段") or ""),
            "起始行": location.get("起始行"),
            "起始列": 1,
            "结束行": location.get("结束行"),
            "结束列": 0,
            "字符起": None,
            "字符止": None,
            "引号包裹": False,
            "异常": location.get("异常说明") or None,
        }

    def snapshot_of(self, location: dict[str, Any], field_name: str) -> dict[str, Any]:
        if field_name == "整条记录":
            return self.record_snapshot(location)
        return self.field_snapshot(location, field_name) or self.record_snapshot(location)

    def position_of_field(self, location: dict[str, Any], field_name: str) -> dict[str, Any]:
        snapshot = self.snapshot_of(location, field_name)
        return {
            "定位编号": location.get("定位编号"),
            "来源类型": location.get("来源类型"),
            "来源编号": location.get("来源编号"),
            "样本编号": location.get("样本编号"),
            "上传编号": location.get("上传编号"),
            "记录序号": location.get("记录序号"),
            "运单号": location.get("运单号"),
            "起始行": snapshot.get("起始行"),
            "结束行": snapshot.get("结束行"),
            "物理行": self._line_label(int(snapshot.get("起始行") or 0), int(snapshot.get("结束行") or 0)),
            "字段定位": snapshot,
            "记录起始行": location.get("起始行"),
            "记录结束行": location.get("结束行"),
            "解析状态": location.get("解析状态"),
            "异常说明": location.get("异常说明") or "",
        }


location_service = LocationService()
