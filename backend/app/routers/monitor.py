"""采集监控 分析看板接口：周期指标、导出包接力核对与待审清单。

固定路径（``/report-packages``、``/report-checks``）必须声明在 ``/{entry_id}``
之前，否则会被当成监控面板编号。
"""
from __future__ import annotations

import csv
import io
import random
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse

from app.schemas import ActionResult, EntryPayload
from app.services.monitor import MonitorService

router = APIRouter(prefix="/api/monitor", tags=["采集监控"])
service = MonitorService()
LIST_FIELDS = ["面板编号", "采集源总数", "今日采集量", "成功率", "平均延迟", "失败重试", "异常告警", "更新时间"]
STATUSES = ["正常", "预警", "告警"]


def csv_cell(value: Any) -> str:
    text = str(value) if value is not None else ""
    if text and text[0] in "=+-@":
        text = "'" + text
    return '"' + text.replace('"', '""') + '"'


# ----------------------------------------------------------------------
# 完整报告导出包的接力
# ----------------------------------------------------------------------
@router.get("/report-packages")
def list_report_packages(period: str | None = Query(default=None)) -> dict[str, Any]:
    """采集监控视角的导出包清单：批次版本、冻结版本、线上版本与核查状态。"""
    return service.list_report_packages(period=period)


@router.get("/report-packages/{package_no}")
def get_report_package(package_no: str) -> dict[str, Any]:
    """读取采集报告冻结的完整导出包；不存在时给出可读说明。"""
    package = service.get_report_package(package_no)
    if package is None:
        raise HTTPException(status_code=404, detail=f"导出包 {package_no} 不存在或已失效")
    return package


@router.post("/report-packages/sync")
def sync_report_package(payload: EntryPayload) -> ActionResult:
    """同步重算完整报告导出包并生成待审清单。

    不传包编号时，按统计周期取最新导出包；周期还没有导出包时由监控侧
    冻结一份再核对，保证跨模块接力不会因为"报告还没点导出"而断链。
    """
    record, message = service.sync_report_package(payload.values or {})
    if record is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=record)


@router.get("/report-checks")
def list_report_checks(
    period: str | None = Query(default=None),
    package_no: str | None = Query(default=None),
    batch_version: int | None = Query(default=None),
    only_pending: bool = Query(default=False),
) -> dict[str, Any]:
    """监控核查记录（含待审清单），可被采集报告按批次回查。"""
    return service.list_checks(
        period=period, package_no=package_no,
        batch_version=batch_version, only_pending=only_pending)


@router.get("/report-checks/{record_no}")
def get_report_check(record_no: str) -> dict[str, Any]:
    record = service.get_check(record_no)
    if record is None:
        raise HTTPException(status_code=404, detail=f"监控核查记录 {record_no} 不存在")
    return record


# ----------------------------------------------------------------------
# 原有看板能力
# ----------------------------------------------------------------------
@router.get("/summary")
def get_summary() -> dict[str, Any]:
    return {"metrics": [
        {"label": "覆盖数据源", "value": random.randint(12, 45), "trend": random.randint(-12, 25)},
        {"label": "最新指标值", "value": round(random.uniform(0.72, 0.99), 3), "trend": random.randint(-8, 15)},
        {"label": "异常记录", "value": random.randint(0, 8), "trend": -random.randint(1, 10)},
    ]}


@router.get("")
def list_results() -> dict[str, Any]:
    return {"items": service.list_results()}


@router.post("/run")
def run_analysis(payload: EntryPayload) -> dict[str, Any]:
    results = service.run_analysis(payload.values)
    return {"items": results}


@router.get("/export")
def export_entries() -> StreamingResponse:
    items = service.list_results()
    buf = io.StringIO(newline="")
    writer = csv.writer(buf)
    all_keys = ["id"]
    if items:
        all_keys = list(items[0].keys())
    writer.writerow(all_keys)
    for item in items:
        writer.writerow([csv_cell(item.get(k, "")) for k in all_keys])
    buf.seek(0)
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv; charset=utf-8",
                             headers={"Content-Disposition": "attachment; filename=monitor.csv"})


@router.get("/{entry_id}")
def get_entry(entry_id: int) -> dict:
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"监控面板 {entry_id} 不存在")
    return entry
