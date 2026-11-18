"""完整报告导出包账本：按统计周期冻结报告汇总、失败明细和指标依据。

这份账本同时被「采集报告」和「采集监控」两个模块使用：

* 采集报告服务登记周期事件、创建导出包、下载与打包；
* 采集监控服务读取包里的批次版本，按报告周期核对线上指标，并把核对结果
  持久化成采集报告可以按批次回查的业务记录。

关键约束：

1. **一个包内必须同版本**：报告汇总、失败明细、指标依据都按包的 ``批次版本``
   从事件账本里切出来，任何后续事件都不会改写已生成的包。
2. **两种导出边界**：
   * ``冻结点前置``（freeze）：按发起导出时的版本立刻冻结，文件即时可用；
   * ``数据完整前置``（merge）：把发起导出时已经收到的更正事件合并进来，
     生成更高批次版本的包，保证数据完整、文件一次正确。
3. **同一版本只生成一个包**：``(统计周期, 批次版本)`` 是唯一键，并发或重复提交
   同一版本时只有一次成功，其余返回已存在的包编号。
4. **以批次版本为准重算**：``recompute`` 永远使用包自己的批次版本，不读实时
   水位，因此历史包在周期继续演进后仍然可核对。
"""
from __future__ import annotations

import copy
import hashlib
import json
import threading
import time
from datetime import datetime
from typing import Any

from app.report_data import (
    PERIOD_BASE,
    PERIOD_ORDER,
    normalize_period,
)

FREEZE_MODE = "freeze"
MERGE_MODE = "merge"

MODE_LABELS = {
    FREEZE_MODE: "冻结点前置",
    MERGE_MODE: "数据完整前置",
}

_MODE_ALIASES = {
    "冻结点前置": FREEZE_MODE,
    "冻结": FREEZE_MODE,
    "冻结快照": FREEZE_MODE,
    "导出边界前置": FREEZE_MODE,
    "freeze": FREEZE_MODE,
    "snapshot": FREEZE_MODE,
    "export_boundary": FREEZE_MODE,
    "数据完整前置": MERGE_MODE,
    "合并": MERGE_MODE,
    "合并更正": MERGE_MODE,
    "等待更正": MERGE_MODE,
    "merge": MERGE_MODE,
    "complete": MERGE_MODE,
    "data_complete": MERGE_MODE,
}


def normalize_mode(value: Any, *, default: str = FREEZE_MODE) -> str:
    """把页面上的导出边界写法归一成 freeze / merge。"""
    text = str(value or "").strip()
    if not text:
        return default
    key = text.casefold()
    return _MODE_ALIASES.get(key, _MODE_ALIASES.get(text, default))


def _now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _compact(period: str) -> str:
    return period.replace("-", "")


def _canonical(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _fingerprint(payload: Any) -> str:
    return hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()


def _as_int(value: Any, field: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        raise ValueError(f"字段「{field}」必须是整数") from None


def _as_float(value: Any, field: str) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        raise ValueError(f"字段「{field}」必须是数字") from None


class ReportLedger:
    """周期事件账本 + 导出包仓库 + 监控核查业务记录。"""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._events: dict[str, list[dict[str, Any]]] = {p: [] for p in PERIOD_ORDER}
        self._packages: dict[str, dict[str, Any]] = {}
        self._package_keys: dict[tuple[str, int], str] = {}
        self._checks: dict[str, dict[str, Any]] = {}
        self._check_seq: dict[str, int] = {}
        self._seed_events()

    # ------------------------------------------------------------------
    # 周期事件
    # ------------------------------------------------------------------
    def _seed_events(self) -> None:
        """预置少量周期事件，让"周期继续演进"在起服务后即可复现。"""
        self._append_seed(
            "2026-W36",
            {
                "事件类型": "失败事件",
                "说明": "周末补采批次新增失败记录",
                "记录时间": "2026-09-08 09:12:00",
                "来源": "调度补采",
                "总采集量增量": 30,
                "失败明细": [
                    {
                        "失败编号": "FAIL-2026W36-09",
                        "采集源": "价格监测源",
                        "失败环节": "页面解析",
                        "失败原因": "补采页面临时改版",
                        "发生时间": "2026-09-07 22:40:00",
                        "记录数": 2,
                    }
                ],
            },
        )
        self._append_seed(
            "2026-W37",
            {
                "事件类型": "更正事件",
                "说明": "接口限流失败复核后更正",
                "记录时间": "2026-09-16 10:05:00",
                "来源": "采集监控",
                "总采集量增量": 20,
                "平均耗秒": 1.58,
                "更正明细": [
                    {"失败编号": "FAIL-2026W37-02", "失败原因": "触发接口限流后重试耗尽（已复核）"}
                ],
                "失败明细": [
                    {
                        "失败编号": "FAIL-2026W37-07",
                        "采集源": "官网商品页",
                        "失败环节": "页面解析",
                        "失败原因": "图片懒加载超时",
                        "发生时间": "2026-09-14 15:20:00",
                        "记录数": 1,
                    }
                ],
            },
        )
        self._append_seed(
            "2026-W38",
            {
                "事件类型": "失败事件",
                "说明": "周末补采批次新增失败记录",
                "记录时间": "2026-09-23 09:12:00",
                "来源": "调度补采",
                "总采集量增量": 60,
                "失败明细": [
                    {
                        "失败编号": "FAIL-2026W38-08",
                        "采集源": "社媒公开页",
                        "失败环节": "页面解析",
                        "失败原因": "验证码导致列表页未展开",
                        "发生时间": "2026-09-22 21:40:00",
                        "记录数": 2,
                    }
                ],
            },
        )
        self._append_seed(
            "2026-W38",
            {
                "事件类型": "更正事件",
                "说明": "供应商目录批次主键冲突复核，并补录失败记录",
                "记录时间": "2026-09-24 10:05:00",
                "来源": "采集监控",
                "总采集量增量": 40,
                "平均耗秒": 1.52,
                "更正明细": [
                    {"失败编号": "FAIL-2026W38-05", "失败原因": "批次主键冲突（已复核）"}
                ],
                "失败明细": [
                    {
                        "失败编号": "FAIL-2026W38-09",
                        "采集源": "供应商目录",
                        "失败环节": "落库写入",
                        "失败原因": "补录批次主键冲突",
                        "发生时间": "2026-09-23 20:15:00",
                        "记录数": 3,
                    }
                ],
            },
        )

    def _append_seed(self, period: str, payload: dict[str, Any]) -> None:
        version = len(self._events[period]) + 2
        event = {
            "事件编号": f"EVT-{_compact(period)}-{version:03d}",
            "统计周期": period,
            "批次版本": version,
            "事件类型": str(payload.get("事件类型", "失败事件")),
            "说明": str(payload.get("说明", "")),
            "来源": str(payload.get("来源", "系统")),
            "记录时间": str(payload.get("记录时间") or _now_text()),
            "操作人": "system",
            "总采集量增量": int(payload.get("总采集量增量", 0) or 0),
            "平均耗秒": payload.get("平均耗秒"),
            "失败明细": copy.deepcopy(payload.get("失败明细") or []),
            "更正明细": copy.deepcopy(payload.get("更正明细") or []),
        }
        self._events[period].append(event)

    def latest_version(self, period: str) -> int:
        period = normalize_period(period)
        with self._lock:
            events = self._events.get(period) or []
            return events[-1]["批次版本"] if events else 1

    def list_events(self, period: str) -> list[dict[str, Any]]:
        period = normalize_period(period)
        with self._lock:
            return copy.deepcopy(self._events.get(period) or [])

    def append_event(
        self,
        period: str,
        payload: dict[str, Any],
        *,
        operator: str | None = None,
    ) -> tuple[dict[str, Any] | None, str]:
        """登记一条周期事件；事件会推进批次版本，但不会改写已冻结的包。"""
        period = normalize_period(period)
        event_type = str(payload.get("事件类型") or "失败事件").strip()
        if event_type in {"失败", "新增失败", "failure"}:
            event_type = "失败事件"
        elif event_type in {"更正", "correction", "指标更正"}:
            event_type = "更正事件"
        if event_type not in {"失败事件", "更正事件"}:
            return None, f"事件类型「{event_type}」不在允许范围内"

        additions = payload.get("失败明细") or []
        patches = payload.get("更正明细") or []
        if not isinstance(additions, list) or not isinstance(patches, list):
            return None, "失败明细与更正明细必须是数组"
        if not additions and not patches:
            return None, "事件至少需要一条失败明细或更正明细"

        with self._lock:
            latest = self.latest_version(period)
            version = latest + 1
            event = {
                "事件编号": f"EVT-{_compact(period)}-{version:03d}",
                "统计周期": period,
                "批次版本": version,
                "事件类型": event_type,
                "说明": str(payload.get("说明") or "").strip(),
                "来源": str(payload.get("来源") or "采集报告"),
                "记录时间": str(payload.get("记录时间") or _now_text()),
                "操作人": operator or "值班管理员",
                "总采集量增量": _as_int(payload.get("总采集量增量", 0) or 0, "总采集量增量"),
                "平均耗秒": payload.get("平均耗秒"),
                "失败明细": [],
                "更正明细": [],
            }
            if event["平均耗秒"] is not None:
                event["平均耗秒"] = _as_float(event["平均耗秒"], "平均耗秒")

            current_ids = {row["失败编号"] for row in self._snapshot_locked(period)["失败明细"]}
            seen: set[str] = set()
            for raw in additions:
                if not isinstance(raw, dict):
                    return None, "失败明细的每一项都必须是对象"
                failure_id = str(raw.get("失败编号") or "").strip()
                if not failure_id:
                    return None, "失败明细缺少「失败编号」"
                if failure_id in current_ids or failure_id in seen:
                    return None, f"失败编号「{failure_id}」已存在，请改用更正明细"
                seen.add(failure_id)
                records = _as_int(raw.get("记录数", 1), "记录数")
                if records <= 0:
                    return None, f"失败编号「{failure_id}」的记录数必须大于 0"
                event["失败明细"].append(
                    {
                        "失败编号": failure_id,
                        "统计周期": period,
                        "采集源": str(raw.get("采集源") or "").strip(),
                        "失败环节": str(raw.get("失败环节") or "").strip(),
                        "失败原因": str(raw.get("失败原因") or "").strip(),
                        "发生时间": str(raw.get("发生时间") or "").strip(),
                        "记录数": records,
                    }
                )
            for raw in patches:
                if not isinstance(raw, dict):
                    return None, "更正明细的每一项都必须是对象"
                failure_id = str(raw.get("失败编号") or "").strip()
                if failure_id not in current_ids:
                    return None, f"更正明细引用的失败编号「{failure_id}」不存在"
                patch = {"失败编号": failure_id}
                if "记录数" in raw and raw.get("记录数") is not None:
                    records = _as_int(raw.get("记录数"), "记录数")
                    if records <= 0:
                        return None, f"失败编号「{failure_id}」的记录数必须大于 0"
                    patch["记录数"] = records
                for field in ("采集源", "失败环节", "失败原因", "发生时间"):
                    if raw.get(field) is not None:
                        patch[field] = str(raw.get(field))
                event["更正明细"].append(patch)

            self._events[period].append(event)
            try:
                self._snapshot_locked(period, version)
            except ValueError as error:
                self._events[period].pop()
                return None, str(error)
            return copy.deepcopy(event), "周期事件已登记"

    # ------------------------------------------------------------------
    # 快照
    # ------------------------------------------------------------------
    def _snapshot_locked(self, period: str, version: int | None = None) -> dict[str, Any]:
        base = PERIOD_BASE[period]
        events = self._events[period]
        latest = events[-1]["批次版本"] if events else 1
        target = latest if version is None else max(1, min(int(version), latest))

        failures: dict[str, dict[str, Any]] = {}
        for row in base["失败明细"]:
            failures[row["失败编号"]] = {
                **row,
                "批次版本": target,
                "来源事件": f"BASE-{_compact(period)}",
                "数据状态": "基线",
            }

        applied: list[dict[str, Any]] = []
        total = int(base["总采集量"])
        average = float(base["平均耗秒"])
        for event in events:
            if event["批次版本"] > target:
                break
            applied.append(event)
            for row in event.get("失败明细") or []:
                failure_id = row["失败编号"]
                failures[failure_id] = {
                    **row,
                    "统计周期": period,
                    "批次版本": target,
                    "来源事件": event["事件编号"],
                    "数据状态": "新增",
                }
            for patch in event.get("更正明细") or []:
                row = failures.get(patch["失败编号"])
                if row is None:
                    continue
                for key, value in patch.items():
                    if key != "失败编号":
                        row[key] = value
                row["批次版本"] = target
                row["来源事件"] = event["事件编号"]
                row["数据状态"] = "已更正"
            total += int(event.get("总采集量增量") or 0)
            if event.get("平均耗秒") is not None:
                average = float(event["平均耗秒"])

        failure_rows = sorted(
            (copy.deepcopy(row) for row in failures.values()),
            key=lambda item: str(item.get("失败编号", "")),
        )
        failure_count = sum(int(row.get("记录数") or 0) for row in failure_rows)
        success = total - failure_count
        if success < 0:
            raise ValueError(
                f"统计周期 {period} 的失败明细合计 {failure_count} 超过总采集量 {total}，数据不一致"
            )
        success_rate = round(success / total * 100, 2) if total else 0.0
        sources = list(base["采集源"])
        for row in failure_rows:
            source = str(row.get("采集源") or "").strip()
            if source and source not in sources:
                sources.append(source)

        report = {
            "报告编号": f"REPO-{_compact(period)}-V{target}",
            "采集源": "、".join(sources),
            "统计周期": period,
            "总采集量": total,
            "成功量": success,
            "失败量": failure_count,
            "平均耗秒": round(average, 2),
            "生成时间": base["生成时间"],
            "批次版本": target,
            "冻结版本": target,
            "来源书签": f"report://{period}/version/{target}",
        }

        events_text = "；".join(event["事件编号"] for event in applied) or "无（基线快照）"
        metrics = [
            {
                "指标名称": "总采集量",
                "指标值": total,
                "数值": total,
                "计算口径": "周期基线总采集量 + 已合并事件的总采集量增量",
                "依据来源": f"BASE-{_compact(period)}；{events_text}",
                "批次版本": target,
                "关联明细数": 0,
            },
            {
                "指标名称": "成功量",
                "指标值": success,
                "数值": success,
                "计算口径": "总采集量 - 失败量",
                "依据来源": "报告汇总字段；与失败明细合计核对",
                "批次版本": target,
                "关联明细数": len(failure_rows),
            },
            {
                "指标名称": "失败量",
                "指标值": failure_count,
                "数值": failure_count,
                "计算口径": "失败明细「记录数」求和",
                "依据来源": f"失败明细 {len(failure_rows)} 条",
                "批次版本": target,
                "关联明细数": len(failure_rows),
            },
            {
                "指标名称": "成功率",
                "指标值": f"{success_rate:.2f}%",
                "数值": success_rate,
                "计算口径": "成功量 / 总采集量 × 100%",
                "依据来源": f"{success} / {total}",
                "批次版本": target,
                "关联明细数": len(failure_rows),
            },
            {
                "指标名称": "平均耗秒",
                "指标值": round(average, 2),
                "数值": round(average, 2),
                "计算口径": "周期基线平均耗时，按更正事件覆盖",
                "依据来源": events_text,
                "批次版本": target,
                "关联明细数": 0,
            },
            {
                "指标名称": "失败明细条数",
                "指标值": len(failure_rows),
                "数值": len(failure_rows),
                "计算口径": "失败明细去重后的条目数",
                "依据来源": "、".join(str(row.get("失败编号")) for row in failure_rows) or "无",
                "批次版本": target,
                "关联明细数": len(failure_rows),
            },
            {
                "指标名称": "覆盖采集源",
                "指标值": len(sources),
                "数值": len(sources),
                "计算口径": "周期基础采集源与失败明细采集源去重计数",
                "依据来源": "、".join(sources),
                "批次版本": target,
                "关联明细数": 0,
            },
        ]

        checks = self._build_checks(period, target, report, failure_rows, metrics, success_rate)
        return {
            "统计周期": period,
            "周期区间": base["周期区间"],
            "批次版本": target,
            "最新版本": latest,
            "报告": report,
            "失败明细": failure_rows,
            "指标依据": metrics,
            "核对": checks,
            "已应用事件": copy.deepcopy(applied),
            "待应用事件": copy.deepcopy([e for e in events if e["批次版本"] > target]),
        }

    def _build_checks(
        self,
        period: str,
        version: int,
        report: dict[str, Any],
        failures: list[dict[str, Any]],
        metrics: list[dict[str, Any]],
        success_rate: float,
    ) -> dict[str, Any]:
        failure_total = sum(int(row.get("记录数") or 0) for row in failures)
        metric_map = {row["指标名称"]: row for row in metrics}
        items = [
            ("报告与明细同版本", all(row.get("批次版本") == version for row in failures),
             f"报告批次版本 {version}，明细批次版本 {sorted({row.get('批次版本') for row in failures}) or [version]}"),
            ("报告与依据同版本", all(row.get("批次版本") == version for row in metrics),
             f"指标依据共 {len(metrics)} 项，均按批次版本 {version} 重算"),
            ("失败量等于明细记录数合计", failure_total == report["失败量"],
             f"失败量 {report['失败量']}，明细记录数合计 {failure_total}"),
            ("成功量加失败量等于总采集量",
             report["成功量"] + report["失败量"] == report["总采集量"],
             f"{report['成功量']} + {report['失败量']} = {report['总采集量']}"),
            ("成功率与成功量一致", abs(float(metric_map["成功率"]["数值"]) - success_rate) < 0.01,
             f"成功率 {metric_map['成功率']['指标值']}"),
            ("明细统计周期一致", all(row.get("统计周期") == period for row in failures),
             f"统计周期 {period}"),
            ("指标依据可追溯", all(str(row.get("依据来源") or "").strip() for row in metrics),
             f"{len(metrics)} 项指标均记录了依据来源"),
        ]
        checks = [
            {"检查项": name, "结果": "通过" if passed else "未通过", "说明": detail}
            for name, passed, detail in items
        ]
        passed_all = all(item["结果"] == "通过" for item in checks)
        return {
            "结论": "通过" if passed_all else "未通过",
            "可核对": passed_all,
            "项目": checks,
            "失败明细条数": len(failures),
            "失败量合计": failure_total,
            "指标项数": len(metrics),
        }

    def snapshot(self, period: str, version: int | None = None) -> dict[str, Any]:
        period = normalize_period(period)
        with self._lock:
            return copy.deepcopy(self._snapshot_locked(period, version))

    # ------------------------------------------------------------------
    # 导出包
    # ------------------------------------------------------------------
    def _build_payload(self, period: str, version: int) -> dict[str, Any]:
        snapshot = self._snapshot_locked(period, version)
        payload = {
            "报告": snapshot["报告"],
            "失败明细": snapshot["失败明细"],
            "指标依据": snapshot["指标依据"],
            "核对": snapshot["核对"],
        }
        return payload

    def create_package(
        self,
        period: Any = None,
        *,
        mode: Any = FREEZE_MODE,
        version: int | None = None,
        bookmark: str | None = None,
        operator: str | None = None,
        owner: str = "report",
        source_package: str | None = None,
    ) -> tuple[dict[str, Any] | None, str, dict[str, Any] | None]:
        """创建一个完整报告导出包。

        返回 ``(package, message, existing)``：成功时 existing 为 None；同一
        ``(统计周期, 批次版本)`` 已存在时 package 为 None、existing 为已有包。
        整个"检查唯一键 -> 落库"在锁内完成，并发提交同一版本只有一次成功。
        """
        started = time.perf_counter()
        period = normalize_period(period)
        mode = normalize_mode(mode)
        with self._lock:
            latest = self.latest_version(period)
            target = latest if version is None else _as_int(version, "批次版本")
            if target < 1 or target > latest:
                return None, f"批次版本 {target} 不在 1~{latest} 范围内", None
            key = (period, target)
            existing_no = self._package_keys.get(key)
            if existing_no:
                existing = self._packages.get(existing_no)
                return (
                    None,
                    f"统计周期 {period} 的批次版本 {target} 已由导出包 {existing_no} 占用，"
                    f"同一版本只允许提交一次",
                    copy.deepcopy(existing),
                )

            payload = self._build_payload(period, target)
            package_no = f"PKG-{_compact(period)}-V{target}"
            fingerprint = _fingerprint(payload)
            label = MODE_LABELS[mode]
            package = {
                "包编号": package_no,
                "统计周期": period,
                "周期区间": snapshot_period_range(period),
                "导出边界": label,
                "导出模式": mode,
                "批次版本": target,
                "冻结版本": target,
                "起始版本": target,
                "状态": "已冻结" if mode == FREEZE_MODE else "已合并更正",
                "可核对": payload["核对"]["可核对"],
                "内容指纹": fingerprint,
                "生成时间": _now_text(),
                "生成耗时毫秒": max(1, int((time.perf_counter() - started) * 1000)),
                "来源书签": (bookmark or "").strip() or f"report://{period}/packages/{package_no}",
                "操作人": operator or "值班管理员",
                "归属": owner,
                "报告": payload["报告"],
                "失败明细": payload["失败明细"],
                "指标依据": payload["指标依据"],
                "核对": payload["核对"],
                "回查起点": {
                    "包查询": f"/api/report/export-packages/{package_no}",
                    "下载": f"/api/report/export-packages/{package_no}/download",
                    "打包": f"/api/report/export-packages/{package_no}/bundle",
                    "重算": f"/api/report/export-packages/{package_no}/recompute",
                },
            }
            if source_package:
                package["来源包编号"] = source_package
                package["合并条数"] = len(self._events_since(period, target, source=source_package))
            self._packages[package_no] = package
            self._package_keys[key] = package_no
            return self._package_view(package), f"完整报告导出包 {package_no} 已生成", None

    def _events_since(
        self, period: str, version: int, *, source: str | None = None,
    ) -> list[dict[str, Any]]:
        """版本之后尚未进入该包的事件；用于记录"合并了哪些更正"。"""
        source_version = 0
        if source:
            source_package = self._packages.get(source)
            if source_package is not None:
                source_version = source_package["批次版本"]
        return [
            event
            for event in self._events.get(period, [])
            if source_version < event["批次版本"] <= version
        ]

    def _package_view(self, package: dict[str, Any]) -> dict[str, Any]:
        view = copy.deepcopy(package)
        pending = [
            copy.deepcopy(event)
            for event in self._events.get(package["统计周期"], [])
            if event["批次版本"] > package["批次版本"]
        ]
        view["待合并更正"] = pending
        view["可合并更正"] = bool(pending)
        view["线上版本"] = self.latest_version(package["统计周期"])
        view["待合并条数"] = len(pending)
        view["监控核查记录"] = [
            {
                "核查编号": record["核查编号"],
                "核对时间": record["核对时间"],
                "结论": record["结论"],
                "待审条数": len(record.get("待审清单") or []),
            }
            for record in self._checks.values()
            if record.get("包编号") == package["包编号"]
        ]
        return view

    def list_packages(
        self,
        *,
        period: Any = None,
        batch_version: int | None = None,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        with self._lock:
            rows = []
            for package in self._packages.values():
                if period and package["统计周期"] != normalize_period(period):
                    continue
                if batch_version is not None and package["批次版本"] != int(batch_version):
                    continue
                if status and package["状态"] != status:
                    continue
                rows.append(self._package_view(package))
        rows.sort(key=lambda row: (row["统计周期"], row["批次版本"]), reverse=True)
        return rows

    def get_package(self, package_no: str) -> dict[str, Any] | None:
        with self._lock:
            package = self._packages.get(str(package_no).strip())
            return self._package_view(package) if package else None

    def recompute(self, package_no: str) -> tuple[dict[str, Any] | None, str]:
        """以包自己的批次版本重算，验证历史包没有被后续事件污染。"""
        with self._lock:
            package = self._packages.get(str(package_no).strip())
            if package is None:
                return None, f"导出包 {package_no} 不存在"
            payload = self._build_payload(package["统计周期"], package["批次版本"])
            fingerprint = _fingerprint(payload)
            same = fingerprint == package["内容指纹"]
            result = {
                "包编号": package["包编号"],
                "统计周期": package["统计周期"],
                "批次版本": package["批次版本"],
                "冻结版本": package["冻结版本"],
                "线上版本": self.latest_version(package["统计周期"]),
                "重算版本": payload["报告"]["批次版本"],
                "内容指纹": package["内容指纹"],
                "重算指纹": fingerprint,
                "一致": same,
                "报告": payload["报告"],
                "失败明细条数": len(payload["失败明细"]),
                "失败量合计": payload["核对"]["失败量合计"],
                "指标项数": len(payload["指标依据"]),
                "核对": payload["核对"],
                "说明": (
                    "按批次版本重算结果与包内内容一致"
                    if same
                    else "按批次版本重算结果与包内内容不一致，请重新生成导出包"
                ),
            }
            return result, result["说明"]

    # ------------------------------------------------------------------
    # 采集监控：回查 + 重算核对 + 待审清单
    # ------------------------------------------------------------------
    def latest_package(self, period: Any, package_no: str | None = None) -> dict[str, Any] | None:
        with self._lock:
            period = normalize_period(period)
            if package_no:
                package = self._packages.get(str(package_no).strip())
                return self._package_view(package) if package else None
            candidates = [p for p in self._packages.values() if p["统计周期"] == period]
            if not candidates:
                return None
            return self._package_view(max(candidates, key=lambda p: p["批次版本"]))

    def verify_package(
        self,
        period: Any = None,
        *,
        package_no: str | None = None,
        operator: str | None = None,
        bookmark: str | None = None,
    ) -> tuple[dict[str, Any] | None, str]:
        """采集监控接力导出包：按包版本重算，再和线上周期指标逐项核对。

        线上指标取周期的最新版本；如果包生成后周期又收到了事件，待审清单会
        明确列出"版本滞后"和各项指标差异，供人工确认后重新导出。
        """
        period = normalize_period(period)
        with self._lock:
            package = self.latest_package(period, package_no)
            if package is not None:
                # 显式指定包编号时以包自身的周期为准，避免跨周期错配核对口径。
                period = package["统计周期"]
            created = False
            if package is None:
                package, message, _ = self.create_package(
                    period, mode=FREEZE_MODE, owner="monitor", operator=operator,
                )
                if package is None:
                    return None, message
                created = True

            recomputed, _ = self.recompute(package["包编号"])
            if recomputed is None:
                return None, "导出包重算失败"

            online = self._snapshot_locked(period)
            online_report = online["报告"]
            package_report = package["报告"]

            fields = [
                ("总采集量", "总采集量"),
                ("成功量", "成功量"),
                ("失败量", "失败量"),
                ("成功率", "成功率"),
                ("平均耗秒", "平均耗秒"),
                ("失败明细条数", "失败明细条数"),
                ("覆盖采集源", "覆盖采集源"),
            ]
            package_metrics = {row["指标名称"]: row for row in package["指标依据"]}
            online_metrics = {row["指标名称"]: row for row in online["指标依据"]}
            review_items: list[dict[str, Any]] = []
            checklist: list[dict[str, Any]] = []

            version_gap = online["批次版本"] - package["批次版本"]
            version_item = {
                "核对项": "批次版本",
                "包内值": package["批次版本"],
                "线上值": online["批次版本"],
                "差异": version_gap,
                "结果": "一致" if version_gap == 0 else "待审核",
                "处置建议": "版本一致，无需处理" if version_gap == 0
                else f"线上已推进到版本 {online['批次版本']}，请按新版本重新生成导出包",
            }
            checklist.append(version_item)
            if version_gap != 0:
                review_items.append({**version_item, "序号": len(review_items) + 1, "状态": "待审核"})

            for display, metric_name in fields:
                package_metric = package_metrics.get(metric_name, {})
                online_metric = online_metrics.get(metric_name, {})
                package_value = package_metric.get("数值")
                online_value = online_metric.get("数值")
                if display == "成功率":
                    package_value = package_metric.get("指标值", package_value)
                    online_value = online_metric.get("指标值", online_value)
                difference = _difference(package_value, online_value)
                same = difference == 0 or (
                    isinstance(package_value, str) and package_value == online_value
                )
                item = {
                    "核对项": display,
                    "包内值": package_value,
                    "线上值": online_value,
                    "差异": difference,
                    "结果": "一致" if same else "待审核",
                    "处置建议": "无需处理" if same else "确认线上指标后重新生成导出包并复核",
                }
                checklist.append(item)
                if not same:
                    review_items.append({**item, "序号": len(review_items) + 1, "状态": "待审核"})

            local_consistent = recomputed["一致"] and package["可核对"]
            consistency_item = {
                "核对项": "包内报告与明细同版本",
                "包内值": "一致" if local_consistent else "不一致",
                "线上值": "—",
                "差异": 0 if local_consistent else 1,
                "结果": "一致" if local_consistent else "待审核",
                "处置建议": "无需处理" if local_consistent else "包内容与批次版本重算结果不符，作废后重新导出",
            }
            checklist.append(consistency_item)
            if not local_consistent:
                review_items.append(
                    {**consistency_item, "序号": len(review_items) + 1, "状态": "待审核"}
                )

            conclusion = "一致" if not review_items else "存在差异，待人工复核"
            self._check_seq[period] = self._check_seq.get(period, 0) + 1
            record_no = f"MONI-CHK-{_compact(period)}-{self._check_seq[period]:03d}"
            record = {
                "核查编号": record_no,
                "统计周期": period,
                "周期区间": package["周期区间"],
                "包编号": package["包编号"],
                "批次版本": package["批次版本"],
                "冻结版本": package["冻结版本"],
                "线上版本": online["批次版本"],
                "包状态": package["状态"],
                "包可核对": package["可核对"],
                "重算一致": recomputed["一致"],
                "结论": conclusion,
                "待审条数": len(review_items),
                "待审清单": review_items,
                "核对项目": checklist,
                "包摘要": {
                    "报告编号": package_report["报告编号"],
                    "总采集量": package_report["总采集量"],
                    "成功量": package_report["成功量"],
                    "失败量": package_report["失败量"],
                    "平均耗秒": package_report["平均耗秒"],
                    "失败明细条数": len(package["失败明细"]),
                },
                "包快照": {
                    "包编号": package["包编号"],
                    "统计周期": package["统计周期"],
                    "批次版本": package["批次版本"],
                    "冻结版本": package["冻结版本"],
                    "导出边界": package["导出边界"],
                    "状态": package["状态"],
                    "内容指纹": package["内容指纹"],
                    "可核对": package["可核对"],
                    "来源书签": package["来源书签"],
                    "报告": copy.deepcopy(package["报告"]),
                    "失败明细": copy.deepcopy(package["失败明细"]),
                    "指标依据": copy.deepcopy(package["指标依据"]),
                    "核对": copy.deepcopy(package["核对"]),
                },
                "线上摘要": {
                    "报告编号": online_report["报告编号"],
                    "总采集量": online_report["总采集量"],
                    "成功量": online_report["成功量"],
                    "失败量": online_report["失败量"],
                    "平均耗秒": online_report["平均耗秒"],
                    "失败明细条数": len(online["失败明细"]),
                },
                "来源书签": (bookmark or "").strip() or package["来源书签"],
                "包来源书签": package["来源书签"],
                "操作人": operator or "值班管理员",
                "核对时间": _now_text(),
                "新建导出包": created,
                "回查": {
                    "采集报告回查包": f"/api/report/export-packages/{package['包编号']}",
                    "采集报告回查记录": f"/api/report/monitor-records/{record_no}",
                    "采集监控查看记录": f"/api/monitor/report-checks/{record_no}",
                },
            }
            self._checks[record_no] = record
            return copy.deepcopy(record), "已按周期重算导出包并生成待审清单"

    def list_checks(
        self,
        *,
        period: Any = None,
        package_no: str | None = None,
        batch_version: int | None = None,
    ) -> list[dict[str, Any]]:
        with self._lock:
            rows = []
            for record in self._checks.values():
                if period and record["统计周期"] != normalize_period(period):
                    continue
                if package_no and record["包编号"] != package_no:
                    continue
                if batch_version is not None and record["批次版本"] != int(batch_version):
                    continue
                rows.append(copy.deepcopy(record))
        rows.sort(key=lambda row: row["核对时间"], reverse=True)
        return rows

    def get_check(self, record_no: str) -> dict[str, Any] | None:
        with self._lock:
            record = self._checks.get(str(record_no).strip())
            return copy.deepcopy(record) if record else None

    # ------------------------------------------------------------------
    # 周期概览
    # ------------------------------------------------------------------
    def periods(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = []
            for period in PERIOD_ORDER:
                snapshot = self._snapshot_locked(period)
                packages = [p for p in self._packages.values() if p["统计周期"] == period]
                latest_package = max(packages, key=lambda p: p["批次版本"]) if packages else None
                rows.append(
                    {
                        "统计周期": period,
                        "周期区间": snapshot["周期区间"],
                        "最新版本": snapshot["最新版本"],
                        "报告编号": snapshot["报告"]["报告编号"],
                        "总采集量": snapshot["报告"]["总采集量"],
                        "失败量": snapshot["报告"]["失败量"],
                        "失败明细条数": len(snapshot["失败明细"]),
                        "事件数": len(self._events.get(period) or []),
                        "导出包数": len(packages),
                        "最新包编号": latest_package["包编号"] if latest_package else "",
                        "最新包版本": latest_package["批次版本"] if latest_package else 0,
                    }
                )
            return rows


def snapshot_period_range(period: str) -> str:
    return str(PERIOD_BASE[normalize_period(period)]["周期区间"])


def _difference(left: Any, right: Any) -> int | float:
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return round(float(left) - float(right), 4)
    return 0 if left == right else 1


ledger = ReportLedger()
