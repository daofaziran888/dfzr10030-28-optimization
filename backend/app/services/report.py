"""采集报告 分析看板服务：指标汇总、周期导出包与监控回查。

列表/分析部分保留原有看板口径；报告导出升级为「按统计周期打包」：
报告汇总、失败明细、指标依据来自同一批次版本，导出的 CSV 与 ZIP 可以互相核对。
所有版本、冻结与唯一性规则都委托给 :mod:`app.report_ledger`，路由层只做编排。
"""
from __future__ import annotations

import random
from typing import Any

from app.report_data import DEFAULT_PERIOD, PERIOD_BASE, normalize_period
from app.report_ledger import (
    FREEZE_MODE,
    MERGE_MODE,
    MODE_LABELS,
    ledger,
    normalize_mode,
)
from app.store import store

MODULE = "report"
REQUIRED_FIELDS = ["报告编号", "采集源", "统计周期"]
STATUS_ORDER = ["生成中", "已生成", "已发送"]
ACTION_RULES = {"重新生成": "已生成"}
NEGATIVE_ACTIONS = []


class ReportService:
    # ------------------------------------------------------------------
    # 原有看板能力
    # ------------------------------------------------------------------
    def list_results(self) -> list[dict[str, Any]]:
        rows = store.rows(MODULE)
        results: list[dict] = []
        for row in rows:
            d = dict(row)
            d["指标值"] = round(random.uniform(0.65, 0.98), 3)
            d["对比基准"] = round(random.uniform(0.60, 0.92), 3)
            d["差异"] = round(d["指标值"] - d["对比基准"], 3)
            results.append(d)
        return results

    def run_analysis(self, params: dict[str, Any]) -> list[dict[str, Any]]:
        count = int(params.get("样本范围", params.get("并行数量", "6")))
        results: list[dict] = []
        for i in range(count):
            results.append({
                "id": i + 1,
                "分组": f"分组{(i%3)+1}",
                "指标值": round(random.uniform(0.62, 1.02), 3),
                "对比基准": round(random.uniform(0.58, 0.95), 3),
                "差异": round(random.uniform(-0.2, 0.3), 3),
            })
        return results

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        return store.find(MODULE, entry_id)

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"采集报告 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于采集报告可执行范围"
        entry["status"] = ACTION_RULES[action]
        entry["pending"] = False
        entry["abnormal"] = False
        return entry, f"采集报告已{action}"

    # ------------------------------------------------------------------
    # 统计周期与版本
    # ------------------------------------------------------------------
    def periods(self) -> list[dict[str, Any]]:
        return ledger.periods()

    def resolve_period(self, period: Any = None) -> str:
        return normalize_period(period)

    def resolve_mode(self, mode: Any = None) -> str:
        return normalize_mode(mode)

    def mode_label(self, mode: Any = None) -> str:
        return MODE_LABELS[self.resolve_mode(mode)]

    def period_detail(self, period: Any = None) -> dict[str, Any]:
        """周期实时视图：报告、失败明细、指标依据与核对结论（未冻结）。"""
        period = self.resolve_period(period)
        snapshot = ledger.snapshot(period)
        snapshot["周期选项"] = [
            {"统计周期": item["统计周期"], "周期区间": item["周期区间"], "最新版本": item["最新版本"]}
            for item in ledger.periods()
        ]
        snapshot["导出边界选项"] = [
            {"值": FREEZE_MODE, "标签": MODE_LABELS[FREEZE_MODE], "说明": "冻结起始快照，文件即时可用"},
            {"值": MERGE_MODE, "标签": MODE_LABELS[MERGE_MODE], "说明": "等待更正合并后重新生成，数据一次正确"},
        ]
        return snapshot

    def get_period_version(self, period: Any, version: int) -> dict[str, Any]:
        """按指定批次版本查看周期视图，用于历史版本核对。"""
        period = self.resolve_period(period)
        latest = ledger.latest_version(period)
        if version < 1 or version > latest:
            raise ValueError(f"批次版本 {version} 不在 1~{latest} 范围内")
        snapshot = ledger.snapshot(period, version)
        snapshot["周期选项"] = [
            {"统计周期": item["统计周期"], "周期区间": item["周期区间"], "最新版本": item["最新版本"]}
            for item in ledger.periods()
        ]
        return snapshot

    def list_events(self, period: Any = None) -> list[dict[str, Any]]:
        return ledger.list_events(self.resolve_period(period))

    def append_event(
        self, period: Any, values: dict[str, Any], *, operator: str | None = None,
    ) -> tuple[dict[str, Any] | None, str]:
        return ledger.append_event(self.resolve_period(period), values, operator=operator)

    # ------------------------------------------------------------------
    # 导出包
    # ------------------------------------------------------------------
    def period_export(
        self,
        *,
        period: Any = None,
        mode: Any = None,
        version: int | None = None,
        bookmark: str | None = None,
        operator: str | None = None,
    ) -> tuple[dict[str, Any] | None, str]:
        """页面上的"导出"：返回当前周期已冻结的包，没有包时才新建。

        * 默认（冻结点前置）：直接复用该周期最新的冻结包，导出响应快、版本可回查，
          周期后来新增的事件不会混进已冻结的文件；
        * 数据完整前置（merge）：如果已有包落后于线上版本，就按线上版本重新生成，
          把期间收到的更正合并进来，文件一次正确。
        """
        period = self.resolve_period(period)
        mode = self.resolve_mode(mode)
        latest = ledger.latest_version(period)

        if version is not None:
            target = int(version)
            existing = ledger.latest_package(period, package_no=f"PKG-{period.replace('-', '')}-V{target}")
            if existing is not None:
                existing["本次生成"] = False
                return existing, f"复用已冻结的导出包 {existing['包编号']}"
            package, message, _ = ledger.create_package(
                period, mode=mode, version=target, bookmark=bookmark, operator=operator,
            )
            if package is not None:
                package["本次生成"] = True
            return package, message

        current = ledger.latest_package(period)
        if current is not None and (mode != MERGE_MODE or current["批次版本"] >= latest):
            current["本次生成"] = False
            return current, f"复用已冻结的导出包 {current['包编号']}"

        package, message, existing = ledger.create_package(
            period, mode=mode, version=latest, bookmark=bookmark, operator=operator,
            source_package=current["包编号"] if current is not None and mode == MERGE_MODE else None,
        )
        if package is None and existing is not None:
            existing["本次生成"] = False
            return existing, message
        if package is not None:
            package["本次生成"] = True
        return package, message

    def create_package(
        self,
        values: dict[str, Any],
        *,
        operator: str | None = None,
    ) -> tuple[dict[str, Any] | None, str, dict[str, Any] | None]:
        period = values.get("统计周期") or values.get("period") or DEFAULT_PERIOD
        mode = values.get("导出边界") or values.get("mode") or FREEZE_MODE
        version = values.get("批次版本") or values.get("version")
        bookmark = values.get("来源书签") or values.get("bookmark")
        if version not in (None, ""):
            try:
                version = int(version)
            except (TypeError, ValueError):
                return None, "批次版本必须是整数", None
        else:
            version = None
        return ledger.create_package(
            period,
            mode=mode,
            version=version,
            bookmark=str(bookmark) if bookmark else None,
            operator=operator,
        )

    def merge_package(
        self,
        package_no: str,
        *,
        bookmark: str | None = None,
        operator: str | None = None,
    ) -> tuple[dict[str, Any] | None, str, dict[str, Any] | None]:
        """等待更正事件合并后重新生成：以线上最新版本生成新的导出包。"""
        package = ledger.get_package(package_no)
        if package is None:
            return None, f"导出包 {package_no} 不存在", None
        if not package.get("待合并更正"):
            return package, f"导出包 {package_no} 已是周期最新版本，无需合并更正", None
        new_package, message, existing = ledger.create_package(
            package["统计周期"],
            mode=MERGE_MODE,
            version=package["线上版本"],
            bookmark=bookmark or package["来源书签"],
            operator=operator,
            owner="report",
            source_package=package_no,
        )
        return new_package, message, existing

    def list_packages(
        self,
        *,
        period: Any = None,
        batch_version: int | None = None,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        return ledger.list_packages(
            period=period, batch_version=batch_version, status=status)

    def get_package(self, package_no: str) -> dict[str, Any] | None:
        return ledger.get_package(package_no)

    def recompute(self, package_no: str) -> tuple[dict[str, Any] | None, str]:
        return ledger.recompute(package_no)

    # ------------------------------------------------------------------
    # 采集监控回写业务记录（采集报告按批次回查）
    # ------------------------------------------------------------------
    def monitor_records(
        self,
        *,
        period: Any = None,
        package_no: str | None = None,
        batch_version: int | None = None,
    ) -> list[dict[str, Any]]:
        return ledger.list_checks(
            period=period, package_no=package_no, batch_version=batch_version)

    def monitor_record(self, record_no: str) -> dict[str, Any] | None:
        return ledger.get_check(record_no)

    def verify_package(
        self, package_no: str, values: dict[str, Any], *, operator: str | None = None,
    ) -> tuple[dict[str, Any] | None, str]:
        package = ledger.get_package(package_no)
        if package is None:
            return None, f"导出包 {package_no} 不存在"
        return ledger.verify_package(
            package["统计周期"],
            package_no=package_no,
            operator=values.get("操作人") or operator,
            bookmark=values.get("来源书签"),
        )

    def period_options(self) -> list[dict[str, Any]]:
        return [
            {
                "统计周期": period,
                "周期区间": PERIOD_BASE[period]["周期区间"],
                "最新版本": ledger.latest_version(period),
            }
            for period in PERIOD_BASE
        ]
