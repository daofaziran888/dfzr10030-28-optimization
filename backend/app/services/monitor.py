"""采集监控 分析看板服务：指标汇总、导出包接力核对与待审清单。

采集监控不再只提供随机看板指标，它还承担"报告导出包"的接力职责：

* 读取采集报告生成的导出包版本，按报告周期重算并核对线上指标；
* 把完整报告导出包持久化一份监控侧业务记录，供采集报告服务按批次回查；
* 核对产生的差异（包括"包生成后周期又来了新事件"）进入待审清单。

所有版本规则仍在 :mod:`app.report_ledger` 内，这里只做监控视角的编排。
"""
from __future__ import annotations

import random
from typing import Any

from app.report_data import PERIOD_BASE, normalize_period
from app.report_ledger import ledger
from app.store import store

MODULE = "monitor"
REQUIRED_FIELDS = ["面板编号", "采集源总数", "今日采集量"]
STATUS_ORDER = ["正常", "预警", "告警"]
ACTION_RULES = {"查看详情": "正常"}
NEGATIVE_ACTIONS = []


class MonitorService:
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
            return None, f"监控面板 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于采集监控可执行范围"
        entry["status"] = ACTION_RULES[action]
        entry["pending"] = False
        entry["abnormal"] = False
        return entry, f"监控面板已{action}"

    # ------------------------------------------------------------------
    # 完整报告导出包的接力
    # ------------------------------------------------------------------
    def period_options(self) -> list[dict[str, Any]]:
        return [
            {
                "统计周期": period,
                "周期区间": data["周期区间"],
                "最新版本": ledger.latest_version(period),
                "导出包数": len(ledger.list_packages(period=period)),
            }
            for period, data in PERIOD_BASE.items()
        ]

    def list_report_packages(self, *, period: Any = None) -> dict[str, Any]:
        """监控侧看到的导出包清单，标注"监控已持久化"的业务记录。"""
        period_value = normalize_period(period) if period else None
        packages = ledger.list_packages(period=period_value)
        checks = ledger.list_checks(period=period_value)
        by_package: dict[str, list[dict[str, Any]]] = {}
        for record in checks:
            by_package.setdefault(record["包编号"], []).append(record)

        items: list[dict[str, Any]] = []
        for package in packages:
            records = by_package.get(package["包编号"], [])
            latest = records[0] if records else None
            items.append({
                "包编号": package["包编号"],
                "统计周期": package["统计周期"],
                "周期区间": package["周期区间"],
                "批次版本": package["批次版本"],
                "冻结版本": package["冻结版本"],
                "线上版本": package["线上版本"],
                "状态": package["状态"],
                "导出边界": package["导出边界"],
                "可核对": package["可核对"],
                "内容指纹": package["内容指纹"],
                "来源书签": package["来源书签"],
                "待合并更正": package["待合并更正"],
                "待合并条数": package["待合并条数"],
                "监控已持久化": bool(records),
                "核查次数": len(records),
                "最近核查编号": latest["核查编号"] if latest else "",
                "最近核查结论": latest["结论"] if latest else "未核查",
                "最近待审条数": len(latest.get("待审清单") or []) if latest else 0,
                "回查": {
                    "采集监控查看包": f"/api/monitor/report-packages/{package['包编号']}",
                    "采集报告回查包": f"/api/report/export-packages/{package['包编号']}",
                },
            })
        items.sort(key=lambda row: (row["统计周期"], row["批次版本"]), reverse=True)
        return {"items": items, "periods": self.period_options()}

    def get_report_package(self, package_no: str) -> dict[str, Any] | None:
        package = ledger.get_package(package_no)
        if package is None:
            return None
        records = [r for r in ledger.list_checks(package_no=package_no)]
        package["监控核查记录"] = records
        package["监控已持久化"] = bool(records)
        package["回查"] = {
            "采集报告回查包": f"/api/report/export-packages/{package_no}",
            "采集监控核查记录": [r["回查"]["采集监控查看记录"] for r in records],
        }
        return package

    def sync_report_package(
        self,
        values: dict[str, Any],
        *,
        operator: str | None = None,
    ) -> tuple[dict[str, Any] | None, str]:
        """接力导出包：没有包时按周期冻结一份，再同步重算并生成待审清单。

        ``冻结后重算`` 表示本次调用是否顺手创建了新的导出包；无论是否新建，
        都会读取包的批次版本与线上周期逐项核对。
        """
        period = normalize_period(values.get("统计周期") or values.get("period"))
        package_no = values.get("包编号") or values.get("package_no")
        record, message = ledger.verify_package(
            period,
            package_no=str(package_no) if package_no else None,
            operator=values.get("操作人") or operator,
            bookmark=values.get("来源书签"),
        )
        return record, message

    def list_checks(
        self,
        *,
        period: Any = None,
        package_no: str | None = None,
        batch_version: int | None = None,
        only_pending: bool = False,
    ) -> dict[str, Any]:
        records = ledger.list_checks(
            period=period, package_no=package_no, batch_version=batch_version)
        if only_pending:
            records = [record for record in records if record["待审清单"]]
        pending_total = sum(len(record.get("待审清单") or []) for record in records)
        return {
            "items": records,
            "total": len(records),
            "待审条数合计": pending_total,
        }

    def get_check(self, record_no: str) -> dict[str, Any] | None:
        return ledger.get_check(record_no)
