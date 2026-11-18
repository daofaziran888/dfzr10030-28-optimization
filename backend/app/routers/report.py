"""采集报告 看板接口：周期指标、完整报告导出包与监控回查。

路由顺序很重要：``/periods``、``/export-packages/...`` 这类固定路径必须声明在
``/{entry_id}`` 之前，否则会被当成明细编号。
"""
from __future__ import annotations

import random
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response, StreamingResponse

from app.schemas import ActionResult, EntryPayload
from app.report_export import package_bundle, package_csv
from app.services.report import ReportService

router = APIRouter(prefix="/api/report", tags=["采集报告"])
service = ReportService()
LIST_FIELDS = ["报告编号", "采集源", "统计周期", "总采集量", "成功量", "失败量", "平均耗秒", "生成时间"]
STATUSES = ["生成中", "已生成", "已发送"]


def require_package(package_no: str) -> dict[str, Any]:
    package = service.get_package(package_no)
    if package is None:
        raise HTTPException(status_code=404, detail=f"导出包 {package_no} 不存在或已失效")
    return package


def attachment_headers(filename: str) -> dict[str, str]:
    return {"Content-Disposition": f'attachment; filename="{filename}"'}


# ----------------------------------------------------------------------
# 周期与版本
# ----------------------------------------------------------------------
@router.get("/periods")
def list_periods() -> dict[str, Any]:
    """统计周期下拉：列出每个周期的区间、最新版本与已有导出包。"""
    return {"items": service.periods(), "periods": service.period_options()}


@router.get("/periods/{period}")
def get_period(period: str, version: int | None = Query(default=None, ge=1)) -> dict[str, Any]:
    """周期实时视图：同一版本下的报告、失败明细与指标依据。"""
    try:
        return service.period_detail(period) if version is None else service.get_period_version(period, version)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@router.get("/periods/{period}/events")
def list_period_events(period: str) -> dict[str, Any]:
    """周期事件流水：失败新增与更正事件，事件会推进批次版本。"""
    return {"统计周期": service.resolve_period(period), "items": service.list_events(period)}


@router.post("/periods/{period}/events")
def create_period_event(period: str, payload: EntryPayload) -> ActionResult:
    """登记周期事件；已冻结的导出包不会被改写，新事件只进入新的批次版本。"""
    event, message = service.append_event(period, payload.values)
    if event is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=event)


# ----------------------------------------------------------------------
# 完整报告导出包
# ----------------------------------------------------------------------
@router.get("/export-packages")
def list_export_packages(
    period: str | None = Query(default=None),
    batch_version: int | None = Query(default=None),
    status: str | None = Query(default=None),
) -> dict[str, Any]:
    items = service.list_packages(period=period, batch_version=batch_version, status=status)
    return {"items": items}


@router.post("/export-packages")
def create_export_package(payload: EntryPayload) -> ActionResult:
    """按统计周期生成完整报告导出包；同一版本同时提交只会有一次成功。"""
    package, message, existing = service.create_package(payload.values)
    if package is None:
        if existing is not None:
            return ActionResult(ok=False, message=message, entry=existing)
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=package)


@router.get("/export-packages/{package_no}")
def get_export_package(package_no: str) -> dict[str, Any]:
    """按包编号回查导出包；包含冻结版本、内容指纹与待合并更正。"""
    return require_package(package_no)


@router.get("/export-packages/{package_no}/download")
def download_export_package(package_no: str) -> StreamingResponse:
    """下载可核对的 CSV：报告汇总、失败明细、指标依据同文件同版本。"""
    package = require_package(package_no)
    content = package_csv(package)
    filename = f"{package['包编号']}-{package['统计周期']}.csv"
    return StreamingResponse(
        iter([content]), media_type="text/csv; charset=utf-8",
        headers=attachment_headers(filename))


@router.get("/export-packages/{package_no}/bundle")
def bundle_export_package(package_no: str) -> Response:
    """下载 ZIP 打包：report.csv / failures.csv / metrics.csv / manifest.json。"""
    package = require_package(package_no)
    filename = f"{package['包编号']}-{package['统计周期']}.zip"
    return Response(
        content=package_bundle(package), media_type="application/zip",
        headers=attachment_headers(filename))


@router.get("/export-packages/{package_no}/recompute")
def recompute_export_package(package_no: str) -> dict[str, Any]:
    """以包自己的批次版本重算，不读实时水位，验证历史包仍然可核对。"""
    result, message = service.recompute(package_no)
    if result is None:
        raise HTTPException(status_code=404, detail=message)
    return result


@router.post("/export-packages/{package_no}/merge")
def merge_export_package(package_no: str, payload: EntryPayload) -> ActionResult:
    """等待导出期间的更正事件合并后，按最新版本重新生成导出包。"""
    values = payload.values or {}
    package, message, existing = service.merge_package(
        package_no,
        bookmark=values.get("来源书签"),
        operator=values.get("操作人"),
    )
    if package is None:
        if existing is not None:
            return ActionResult(ok=False, message=message, entry=existing)
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=package)


@router.post("/export-packages/{package_no}/verify")
def verify_export_package(package_no: str, payload: EntryPayload) -> ActionResult:
    """采集报告侧触发采集监控回查：重算完整包并生成待审清单。"""
    package = require_package(package_no)
    record, message = service.verify_package(package_no, payload.values or {})
    if record is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=record)


# ----------------------------------------------------------------------
# 采集监控回写的业务记录（采集报告按批次回查）
# ----------------------------------------------------------------------
@router.get("/monitor-records")
def list_monitor_records(
    period: str | None = Query(default=None),
    package_no: str | None = Query(default=None),
    batch_version: int | None = Query(default=None),
) -> dict[str, Any]:
    items = service.monitor_records(
        period=period, package_no=package_no, batch_version=batch_version)
    return {"items": items}


@router.get("/monitor-records/{record_no}")
def get_monitor_record(record_no: str) -> dict[str, Any]:
    record = service.monitor_record(record_no)
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
def export_entries(period: str | None = Query(default=None), mode: str | None = Query(default=None)) -> StreamingResponse:
    """报告导出：按统计周期冻结成完整导出包并直接下载 CSV。

    与旧版"只拼接当前列表字段"不同，这里下载的是报告汇总 + 失败明细 +
    指标依据的多段文件，三部分来自同一个批次版本，可以相互核对。
    """
    package, message = service.period_export(
        period=period, mode=mode, operator="值班管理员",
    )
    if package is None:
        raise HTTPException(status_code=409, detail=message)
    content = package_csv(package)
    return StreamingResponse(
        iter([content]), media_type="text/csv; charset=utf-8",
        headers={
            **attachment_headers("report.csv"),
            "X-Report-Package": package["包编号"],
            "X-Report-Version": str(package["批次版本"]),
            "X-Report-Period": package["统计周期"],
        })


@router.get("/{entry_id}")
def get_entry(entry_id: int) -> dict:
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"采集报告 {entry_id} 不存在")
    return entry
