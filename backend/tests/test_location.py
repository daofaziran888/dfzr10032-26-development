"""采集行定位与缺项处置的回归用例。

覆盖：带引号的多行字段切分、抽取失败保留位置、接入/校验共用定位记录、
按原处置号回放与按新样本定位、重投后是否覆盖与应领取的待办。
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services.dsv import parse_delimited
from app.services.location import location_service
from app.services.disposition import disposition_service


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


def reset_state() -> None:
    """每个用例从干净态重来：清掉启动期派生的定位与处置。"""
    from app.store import store

    store.rows("row_location").clear()
    store.rows("disposition").clear()
    for row in store.rows("upload"):
        for key in ("解析状态", "物理行数", "记录数", "定位编号集", "解析异常"):
            row.pop(key, None)


def test_quoted_multiline_field_is_one_record():
    text = (
        '运单号,收货人,地址,邮编\n'
        'A1,张三,"上海市浦东新区\n世纪大道100号",200120\n'
        'A2,李四,杭州市西湖区,310000\n'
    )
    parsed = parse_delimited(text)
    assert len(parsed.records) == 2
    first, second = parsed.records
    assert first.start_line == 2 and first.end_line == 3
    assert first.fields[2].value == "上海市浦东新区\n世纪大道100号"
    assert (first.fields[2].start_line, first.fields[2].end_line) == (2, 3)
    # 引号内的换行没有把邮编挤到下一条记录里。
    assert first.fields[3].value == "200120"
    assert second.start_line == 4


def test_unclosed_quote_keeps_original_position():
    parsed = parse_delimited('运单号,地址,邮编\nA1,"上海市浦东新区\n世纪大道\n')
    assert parsed.has_error
    record = parsed.records[0]
    assert record.error
    assert record.fields[0].value == "A1"
    assert record.fields[1].start_line == 2
    assert record.fields[1].end_line == 3
    assert "世纪大道" in record.fields[1].value


def test_ingestion_and_validator_share_same_location(client: TestClient):
    reset_state()
    ingestion = client.post("/api/ingestion/1/parse", json={"values": {"来源编号": "UPLD-0002"}})
    assert ingestion.status_code == 200
    payload = ingestion.json()
    assert payload["ok"], payload["message"]
    location_ids = [item["定位编号"] for item in payload["entry"]["items"]]

    check = client.post("/api/validator/1/check", json={"values": {"来源编号": "UPLD-0002"}})
    assert check.status_code == 200
    result = check.json()
    assert result["ok"], result["message"]
    referenced = {
        item["当前定位编号"]
        for item in result["entry"]["新建"] + result["entry"]["更新"]
    }
    assert referenced
    assert referenced <= set(location_ids)


def test_recheck_covers_new_position_after_claim():
    """首版产生处置并领取；重投后位置变化，应说明覆盖情况与待领取项。"""
    reset_state()
    locations = location_service.ensure_source_locations("UPLD-0001", "上传记录")[2]
    first = next(item for item in locations if item["运单号"] == "SF20260901001")
    old_line = next(
        field for field in first["字段定位"] if field["字段名"] == "邮编"
    )["起始行"]

    generated = disposition_service.generate(source_no="UPLD-0001", fields=["邮编"])
    created = next(item for item in generated["新建"] if item["运单号"] == "SF20260901001")
    assert created["当前位置"]["物理行"] == str(old_line)
    disposition_service.claim(created["处置编号"], "复核员甲")

    diagnostic = disposition_service.recheck(
        disposition_ref=created["处置编号"],
        upload_no="UPLD-0002",
        assignee="复核员甲",
    )
    assert diagnostic["can_cover"] is False
    assert diagnostic["should_claim"]
    assert any(item["建议动作"] == "更新位置" for item in diagnostic["should_claim"])

    applied = disposition_service.recheck(
        disposition_ref=created["处置编号"],
        upload_no="UPLD-0002",
        assignee="复核员甲",
        auto_claim=True,
    )
    assert applied["can_cover"] is True
    assert applied["should_claim"] == []

    # 按原处置号回到当时的物理行，按新样本得到当前位置（标题行删掉后行号会变）。
    old_view = applied["locate_by_old_disposition"][created["处置编号"]]
    assert old_view["当时位置"]["物理行"] == str(old_line)
    new_view = applied["locate_by_new_sample"]
    assert new_view["定位编号"] == applied["最新定位编号"]
    assert new_view["位置"]["物理行"] != str(old_line)


def test_line_shift_after_retitle_keeps_old_and_new_positions():
    """删掉标题行后同一票货的物理行前移；两套定位都要能查到。"""
    reset_state()
    v1 = location_service.ensure_source_locations("UPLD-0001", "上传记录")[2]
    v2 = location_service.ensure_source_locations("UPLD-0002", "上传记录")[2]
    old = next(item for item in v1 if item["运单号"] == "SF20260901001")
    new = next(item for item in v2 if item["运单号"] == "SF20260901001")
    assert old["物理行"] == "2"
    assert new["物理行"] == "1"

    generated = disposition_service.generate(source_no="UPLD-0001", fields=["邮编"])
    created = next(item for item in generated["新建"] if item["运单号"] == "SF20260901001")
    old_view = disposition_service.locate_by_disposition(created["处置编号"])
    assert old_view["当时位置"]["物理行"] == "2"
    new_view = disposition_service.locate_by_sample("UPLD-0002", "SF20260901001", "邮编")
    assert new_view["位置"]["物理行"] == "1"


def test_failed_extraction_keeps_position_and_disposition():
    """引号未闭合时抽取失败，定位与缺项处置仍带原文行号。"""
    reset_state()
    kind, source_row, locations = location_service.ensure_source_locations("UPLD-0003", "上传记录")
    assert source_row["解析状态"] == "解析异常"
    assert locations
    assert locations[0]["起始行"] == 2
    assert locations[0]["解析状态"] == "解析异常"
    assert location_service.field_snapshot(locations[0], "地址")["起始行"] == 2

    result = disposition_service.generate(source_no="UPLD-0003", fields=["邮编"])
    assert result["新建"]
    assert all(item["当前位置"]["物理行"] for item in result["新建"])


def test_quoted_address_split_over_two_lines_keeps_record_together():
    reset_state()
    locations = location_service.ensure_source_locations("UPLD-0002", "上传记录")[2]
    row = next(item for item in locations if item["运单号"] == "SF20260901002")
    assert row["起始行"] == 2 and row["结束行"] == 3
    address = location_service.field_snapshot(row, "地址")
    assert address["起始行"] == 2 and address["结束行"] == 3
    assert "\n" in address["值"]
    postcode = location_service.field_snapshot(row, "邮编")
    assert postcode["起始行"] == 3
    assert postcode["值"] == "310012"


def test_recheck_reports_other_holder():
    reset_state()
    generated = disposition_service.generate(source_no="UPLD-0002", fields=["邮编"])
    disposition_service.generate(source_no="UPLD-0001", fields=["邮编"])
    row = next(item for item in store_rows("disposition") if item["运单号"] == "SF20260901001")
    disposition_service.claim(row["处置编号"], "复核员乙")
    result = disposition_service.recheck(
        disposition_ref=row["处置编号"], upload_no="UPLD-0002", assignee="复核员甲"
    )
    field = next(item for item in result["coverage"] if item["字段名"] == "邮编")
    assert field["结论"] == "他人核查中"
    assert any(item["建议动作"] == "交接" for item in result["should_claim"])


def store_rows(module: str):
    from app.store import store

    return store.rows(module)


def test_disposition_statuses_endpoint(client: TestClient):
    response = client.get("/api/location/dispositions/statuses")
    assert response.status_code == 200
    assert "待领取" in response.json()["statuses"]
