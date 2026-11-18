"""完整报告导出包的内容编排：多段 CSV、可核对明细与 ZIP 打包。

导出包是"报告汇总 + 失败明细 + 指标依据"的唯一版本快照，因此这里只负责把一个
已经冻结的包渲染成文件，不再回头读取实时数据。文件里带批次版本和来源书签，
下载后可以按版本号回到系统核对。
"""
from __future__ import annotations

import csv
import io
import json
import zipfile
from typing import Any, Iterable

REPORT_FIELDS = [
    "报告编号", "采集源", "统计周期", "总采集量", "成功量", "失败量",
    "平均耗秒", "生成时间", "批次版本", "冻结版本", "来源书签",
]
FAILURE_FIELDS = [
    "失败编号", "统计周期", "采集源", "失败环节", "失败原因",
    "发生时间", "记录数", "批次版本", "来源事件", "数据状态",
]
METRIC_FIELDS = [
    "指标名称", "指标值", "计算口径", "依据来源", "批次版本", "关联明细数",
]


def safe_cell(value: Any) -> str:
    """防 CSV 注入：公式起始字符前补单引号。"""
    text = "" if value is None else str(value)
    if text and text[0] in "=+-@":
        return "'" + text
    return text


def write_rows(writer: csv.writer, rows: Iterable[Iterable[Any]]) -> None:
    for row in rows:
        writer.writerow([safe_cell(cell) for cell in row])


def package_csv(package: dict[str, Any]) -> str:
    """把一个导出包渲染成多段 CSV：报告、失败明细、指标依据同文件可核对。"""
    buffer = io.StringIO(newline="")
    buffer.write("﻿")
    writer = csv.writer(buffer, lineterminator="\n")

    writer.writerow([f"完整报告导出包 {package['包编号']}"])
    writer.writerow(["统计周期", package["统计周期"], "周期区间", package["周期区间"]])
    writer.writerow([
        "批次版本", package["批次版本"], "冻结版本", package["冻结版本"],
        "导出边界", package["导出边界"],
    ])
    writer.writerow(["来源书签", package["来源书签"], "内容指纹", package["内容指纹"]])
    writer.writerow([])

    writer.writerow(["报告汇总"])
    writer.writerow(REPORT_FIELDS)
    report = package["报告"]
    writer.writerow([safe_cell(report.get(field, "")) for field in REPORT_FIELDS])
    writer.writerow([])

    writer.writerow(["失败明细"])
    writer.writerow(FAILURE_FIELDS)
    write_rows(writer, ([row.get(field, "") for field in FAILURE_FIELDS] for row in package["失败明细"]))
    writer.writerow([])

    writer.writerow(["指标依据"])
    writer.writerow(METRIC_FIELDS)
    write_rows(writer, ([row.get(field, "") for field in METRIC_FIELDS] for row in package["指标依据"]))
    writer.writerow([])

    writer.writerow(["核对结果", package["核对"]["结论"]])
    writer.writerow(["检查项", "结果", "说明"])
    write_rows(
        writer,
        ([item["检查项"], item["结果"], item["说明"]] for item in package["核对"]["项目"]),
    )
    return buffer.getvalue()


def package_bundle(package: dict[str, Any]) -> bytes:
    """打包成 ZIP：三个明细文件 + 清单，清单记录版本、指纹与核对结论。"""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(f"{package['包编号']}/report.csv", _section_csv(
            REPORT_FIELDS, [package["报告"]]))
        archive.writestr(f"{package['包编号']}/failures.csv", _section_csv(
            FAILURE_FIELDS, package["失败明细"]))
        archive.writestr(f"{package['包编号']}/metrics.csv", _section_csv(
            METRIC_FIELDS, package["指标依据"]))
        manifest = {
            "包编号": package["包编号"],
            "统计周期": package["统计周期"],
            "周期区间": package["周期区间"],
            "批次版本": package["批次版本"],
            "冻结版本": package["冻结版本"],
            "导出边界": package["导出边界"],
            "状态": package["状态"],
            "内容指纹": package["内容指纹"],
            "可核对": package["可核对"],
            "来源书签": package["来源书签"],
            "生成时间": package["生成时间"],
            "文件": ["report.csv", "failures.csv", "metrics.csv"],
            "核对": package["核对"],
            "指标依据": package["指标依据"],
        }
        archive.writestr(
            f"{package['包编号']}/manifest.json",
            json.dumps(manifest, ensure_ascii=False, indent=2),
        )
    return buffer.getvalue()


def _section_csv(fields: list[str], rows: list[dict[str, Any]]) -> str:
    buffer = io.StringIO(newline="")
    buffer.write("﻿")
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(fields)
    write_rows(writer, ([row.get(field, "") for field in fields] for row in rows))
    return buffer.getvalue()
