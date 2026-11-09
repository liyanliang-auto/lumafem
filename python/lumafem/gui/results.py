"""Read-only Qt result browsing for actual accepted native states.

The panel never interpolates a material state, commits history, or changes the
solver's reference quantities.  Curve clicks select a real saved step.  Display
zero changes are observation operations only, and are emitted to the controller.
"""
from __future__ import annotations

from copy import deepcopy
from collections import OrderedDict
import math
from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import QByteArray, QBuffer, QIODevice, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QHBoxLayout, QLabel, QPlainTextEdit,
    QPushButton, QSpinBox, QSplitter, QTabWidget, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget, QTreeWidget, QTreeWidgetItem, QMenu,
)

from ..contracts import ContractError, same_run
from ..observations import criterion_evidence
from .formatting import full_precision, scientific
from .themes import colors, palette


# Source keys are presentation identifiers. Neither translated text nor locale
# is written into a document, selection, result payload or export schema.
_EN: dict[str, str] = {
    "结果树": "Results tree", "运行": "Run", "状态": "Status",
    "尚无已保存运行": "No saved runs", "当前运行": "Current run",
    "已保存运行 {number}": "Saved run {number}", "已接受帧": "Accepted frames",
    "已接受帧 {sequence} · {phase}": "Frame {sequence} / {phase}",
    "不可用帧 {sequence} · {phase}": "Unavailable frame {sequence} / {phase}",
    "当前结果": "Current results", "冻结结果只读": "Frozen results, read only",
    "结果属于旧模型修订": "Results belong to an earlier model revision",
    "运行中 · 历史切换已锁定": "Running / history switching locked",
    "取消已请求，等待安全边界": "Cancellation requested / waiting for a safe boundary",
    "已结束": "Finished", "未结束 / 中断": "Not concluded / interrupted",
    "常用结果": "Browse results", "详细诊断": "Detailed diagnostics",
    "打开运行": "Open run", "显示结果": "Show results",
    "检查不可用原因": "Inspect unavailable reason",
    "上一步": "Previous", "下一步": "Next", "接受步 ": "Frame ",
    "跟随最新": "Follow latest", "固定步": "Pinned frame",
    "默认零点（SG / S0）": "Default zero (SG / S0)", "累计量": "Total",
    "以所选步为零点": "Zero at selected frame",
    "尚未选择结果": "No result selected", "荷载—挠度": "Load-displacement",
    "结构判据 / 点诊断": "Structural criterion / point diagnostic",
    "结构窗口 / 终止夹逼": "Gauge window / terminal bracket",
    "截面 / 钢筋 / 边缘": "Sections / rebar / edges",
    "位移 / 反力 / NVM": "Displacements / reactions / NVM",
    "事件": "Events", "转折定位": "Onset localization",
    "观测 / 标距": "Observations / gauges", "数据对比": "Data comparison",
    "诊断 / 结束原因": "Diagnostics / termination", "日志": "Log",
    "尚无可用接受步；未创建或插值计算状态。": "No accepted frame available; no state is invented or interpolated.",
    "所选结果步缺失或损坏；未使用其他步代替。": "Selected frame is missing or damaged; no other frame is substituted.",
    "所选接受步 {sequence} 完整数组不可用：{error}；未用其他步代替。": "Full arrays for frame {sequence} are unavailable: {error}; no substitute frame.",
    "运行 {number} · {phase} · 已接受帧 {sequence} · P={P} N · {follow}": "Run {number} / {phase} / frame {sequence} / P={P} N / {follow}",
    "接受步 {sequence} 缺失 / 不可用；未插值或补零。": "Frame {sequence} is missing or unavailable; no interpolation or zero fill.",
    "S0": "Initial", "G": "Self-weight loading", "SG": "Self-weight equilibrium", "J": "Jack loading",
    "初始态": "Initial", "自重加载": "Self-weight loading", "自重平衡": "Self-weight equilibrium", "千斤顶加载": "Jack loading",
    "缺失 / 不适用": "Missing / not applicable", "无效数值": "Invalid value", "是": "Yes", "否": "No", "缺失": "Missing",
    "真实 Gauss 截面": "Actual Gauss section",
    "仅展示真实 Gauss 截面；边缘监测不参与内力积分": "Actual Gauss sections only; edge monitors do not enter force integration",
    "此步的截面数据缺失 / 不可用。": "Section data for this frame are missing or unavailable.",
    "证据": "Evidence", "字段": "Field", "实际保存值": "Actual saved value",
    "点身份": "Point identity", "类别 / 边缘": "Kind / edge", "面积 [mm²]": "Area [mm²]",
    "应变 [1]": "Strain [1]", "应力 [MPa]": "Stress [MPa]", "切线 [MPa]": "Tangent [MPa]",
    "塑性应变": "Plastic strain", "分支": "Branch", "已开裂": "Cracked", "已屈服": "Yielded",
    "原始最大压应变": "Raw maximum compressive strain", "当前参考压应变": "Current reference compression strain",
    "历史最大参考压应变": "Historical maximum reference compression strain", "参考阈值": "Reference threshold",
    "受拉截断能量 [N/mm]": "Tension cutoff energy [N/mm]", "受压截断能量 [N/mm]": "Compression cutoff energy [N/mm]",
    "节点": "Node", "Rx / 自由残差 [N]": "Rx / free residual [N]", "Ry / 自由残差 [N]": "Ry / free residual [N]",
    "Mz / 自由残差 [N.mm]": "Mz / free residual [N.mm]", "约束分量": "Constrained components", "单元": "Element", "来源": "Source",
    "真实接受步": "Accepted frame", "阶段": "Phase", "位置 x [mm]": "Position x [mm]", "截面": "Section",
    "控制增量 [mm]": "Control increment [mm]", "原始应变": "Raw strain", "参考应变": "Reference strain",
    "阈值": "Threshold", "判据来源": "Criterion source", "模式": "Mode", "边缘": "Edge",
    "窗口起点 [mm]": "Window start [mm]", "窗口终点 [mm]": "Window end [mm]",
    "有符号总均值 [1]": "Signed total mean [1]", "总压应变最大值 [1]": "Maximum total compression [1]",
    "基准": "Reference", "算子": "Operator", "夹逼证据": "Bracket evidence", "参数来源": "Parameter source",
    "非终止转折量": "Nonterminal onset quantity", "数值 / 状态": "Value / status", "含义 / 单位": "Meaning / unit",
    "观测": "Observation", "量": "Quantity", "单位": "Unit", "相对量": "Increment", "参考步": "Reference frame", "算子 / 来源": "Operator / source",
    "数据集": "Dataset", "身份 / 来源": "Identity / source", "指标": "Metric",
    "尚无导入数据。默认值与合成曲线不能认证真实 NC0 试验吻合度。": "No imported data; defaults and synthetic curves do not validate real NC0 tests.",
    "尚无导入数据。默认参数不等于真实试验验证。": "No imported data; default parameters are not experimental validation.",
    "合成数据只验证导入与对比流程；真实 NC0 试验验证保持待数据。共有区间、覆盖率、误差与事件证据分别报告；不自动平移或峰值归一。": "Synthetic data checks import/comparison workflows only; real NC0 validation still needs data. Common interval, coverage, errors and events are reported separately; no automatic shift or peak normalization.",
    "合成数据；非实测": "Synthetic / not measured", "已导入；对齐与来源需核实": "Imported / alignment and source unverified",
    "对比未完成": "Comparison incomplete", "导入 {number}": "Import {number}", " [合成，非实测]": " [synthetic, not measured]",
    " [导入，来源待核实]": " [imported, source unverified]",
    "跨中挠度 w [mm]；向下正": "Midspan deflection w [mm], downward positive",
    "两千斤顶合计荷载 P [N]": "Total jack load P [N]", "已接受计算步": "Accepted frames",
    "尚无已接受结果": "No accepted results", "实际控制位移 [mm]；保留原阶段定义": "Actual control displacement [mm], original phase definition",
    "压应变 / 阈值 [1]": "Compression strain / threshold [1]", "所选结构判据（真实接受步）": "Selected structural criterion (actual accepted frames)",
    "尚无可绘制的真实接受步 / 有效导入数据": "No actual accepted frames or valid imported data to plot",
    "（实线）": " (solid)", "；虚线: ": "; dashed: ", "点击吸附到距离最近的真实接受步": "Click to select the nearest actual accepted frame",
    "P—w | {zero} | 点击吸附到真实接受步": "P-w | {zero} | click to select an actual accepted frame",
    "接受历史窗口最大值（保留原窗口身份）": "Accepted-history maximum (original window identity retained)",
    "旧点 Route-C（仅已读取完整边缘步；缺失留空，物理模式仅诊断）": "Legacy point Route-C (loaded full-edge frames only; gaps remain; diagnostic in physical mode)",
    "physical_gauge_mean_v1 | 含自重总应变；未标定建模假设；阈值比 1.000E+00": "physical_gauge_mean_v1 | total strain including self-weight; uncalibrated assumption; threshold ratio 1.000E+00",
    "旧点 Route-C | 仅实际完整边缘历史；缺失留空；阈值比 1.000E+00": "Legacy point Route-C | actual complete edge histories only; missing values remain gaps; threshold ratio 1.000E+00",
    "所选判据": "Selected criterion", "结构窗口": "Structural window", "未保存 / 不适用": "Not saved / not applicable",
    "旧点式结果不补造窗口或总应变": "No windows or total strains invented for legacy point results",
    "定义": "Definition", "总应变基准": "Total-strain reference", "原始未变形状态，包含自重；与观测显示归零无关": "Original undeformed state including self-weight; independent of display zero",
    "适用范围": "Scope", "建模假设": "Modeling assumption", "整梁双压缘固定完整窗口；未作试验标定，不代表真实裂缝路径": "Complete fixed windows on both beam edges; uncalibrated; not a real crack path",
    "当前最大窗口": "Current maximum window", "接受历史最大窗口": "Accepted-history maximum window", "终止定位": "Terminal localization",
    "旧结果未包含此类证据": "Legacy result has no such evidence", "不补零或重新计算历史结果": "No zero fill or recomputation of history",
    "本步未定位新的首次转折": "No new first onset localized in this frame", "不表示压碎或分析终止": "Does not imply crushing or termination",
    "首次拉裂转折": "First tensile-cracking onset", "首次压缩峰值（非压碎）": "First compression peak (not crushing)",
    "类型": "Kind", "非终止接受步": "Nonterminal accepted frame", "材料点": "Material point", "真实 Gauss / 边缘身份": "Actual Gauss / edge identity",
    "应变域定义，单位 1": "Strain-domain definition, unit 1", "候选求解次数": "Candidate solves", "含定位候选，不等于已接受步数": "Includes localization candidates, not accepted-frame count",
    "下端控制位移": "Lower control displacement", "上端控制位移": "Upper control displacement", "下端归一转折量": "Lower normalized onset quantity", "上端归一转折量": "Upper normalized onset quantity",
    "下端已收敛": "Lower converged", "上端已收敛": "Upper converged", "独立收敛证据": "Independent convergence evidence", "下端": "Lower", "上端": "Upper",
    "定位试算收敛证据；未作为额外正式步保存": "Localization trial convergence evidence; not an extra accepted frame",
    "自由；反力栏为残差": "Free; reaction column shows residual", "累计量；无显示归零": "Total; no display zero",
    "达到旧点式 Route-C 参考压缘判据": "Legacy point Route-C compression criterion reached",
    "达到固定物理窗口总压应变判据（未标定建模假设）": "Fixed physical-window total-compression criterion reached (uncalibrated assumption)",
    "触发安全限值": "Safety limit", "数值不收敛": "Nonconvergence", "线性系统求解失败": "Linear-system failure",
    "事件定位失败": "Localization failure", "用户取消": "User cancelled", "输入错误": "Input error", "存储错误": "Storage error", "内部错误": "Internal error",
    "结束原因：{reason}": "Termination: {reason}", "内存最后接受步：{sequence} / {identity}": "Last accepted memory frame: {sequence} / {identity}",
    "文件最后保存步：{sequence} / {identity}": "Last persisted frame: {sequence} / {identity}", "物理判据已达到：{value}": "Physical criterion reached: {value}",
    "收到取消请求：{value}": "Cancellation requested: {value}", "验收状态（一次运行不会自动认证）：": "Validation status (one run does not confer qualification):",
    "结束诊断：": "Termination diagnostics:", "尚无已保存结束结论。": "No persisted conclusion yet.", "运行中或尚未启动": "Running or not started",
    "运行状态：{status}": "Run status: {status}", "中断工程不伪造成功或压碎结论。": "Interrupted projects do not fabricate success or crushing.",
    "所选结构判据（显示零点 / 传感器不改变此定义）：": "Selected structural criterion (display zero / sensors do not alter it):",
    "旧结果未保存显式判据描述；保留原点式语义，不补入物理窗口。": "Legacy result has no explicit criterion descriptor; original point semantics retained, no physical window inserted.",
    "缺失 / 损坏步：{steps}": "Missing / damaged frames: {steps}", "当前接受步 {sequence} / {phase} 的收敛证据：": "Convergence evidence for frame {sequence} / {phase}:",
    "线搜索实际评估次数": "Actual line-search evaluations", "回溯次数": "Backtracks", "最后实际修正因子": "Last actual correction factor", "最小实际修正因子": "Minimum actual correction factor",
    "旧结果未保存非终止转折定位证据；未重新生成。": "Legacy result has no saved nonterminal onset evidence; none regenerated.",
    "首次拉裂": "First tensile cracking", "非终止转折定位：{name}；完整夹逼与端点证据见“转折定位”页。": "Nonterminal onset: {name}; complete brackets and endpoint evidence are in Onset localization.",
    "所选结构判据终止定位证据（控制位移 mm，重力因子独立）：": "Selected terminal-criterion localization evidence (control displacement mm; gravity factor separate):",
    "首次软化转折定位（非终止）": "First softening onset localization (nonterminal)", "所选结构终止判据定位": "Structural terminal-criterion localization",
    "LumaFEM | 二维纤维梁计算结果 | 数值倍率 1.000E+00": "LumaFEM | planar fiber-beam results | numeric scale 1.000E+00",
    "合成曲线不是真实 NC0 试验数据；未认证物理验证通过。": "Synthetic curves are not real NC0 tests; no experimental validation is certified.",
    "真实 Gauss 截面。边缘为独立零面积监测；点式参考压应变与结构窗口不同。": "Actual Gauss section. Edge monitors have zero area; point reference strain differs from the structural window.",
    " 本模式的点式阈值 / crushed 标记仅作诊断，不代表结构终止。": " Point thresholds / crushed flags are diagnostic here, not structural termination.",
    "{count} 条；完整处理链保存在工程中": "{count} entries; complete processing chain saved in the project",
    "[{sequence}] {stage} | 已接受步 {accepted} | 迭代 {iteration} | 减半 {halvings} | w={w} mm | P={P} N": "[{sequence}] {stage} | accepted {accepted} | iteration {iteration} | halvings {halvings} | w={w} mm | P={P} N",
    "准备": "Preparation", "平衡": "Equilibrium", "加载": "Loading", "保存": "Saving",
    "只读计算结果": "Read-only calculation results",
    "观测零点；不改变材料历史": "Observation zero; material histories unchanged",
    "荷载—挠度曲线；点击吸附到真实接受步": "Load-displacement curve; click selects an actual accepted frame",
    "结束原因、收敛与保存位置": "Termination, convergence and persisted positions",
    "实际分析进度日志": "Actual analysis progress log",
    "物理窗口当前与历史最大值、终止夹逼证据；含自重总应变": "Current/historical physical windows and terminal brackets; total strain includes self-weight",
    "首次拉裂或压缩峰值定位证据；不是压碎判据": "First cracking/compression-peak localization evidence; not a crushing criterion",
}


_VALUE_LABELS: dict[str, tuple[str, str]] = {
    "S0": ("初始态", "Initial"), "G": ("自重加载", "Self-weight loading"),
    "SG": ("自重平衡", "Self-weight equilibrium"), "J": ("千斤顶加载", "Jack loading"),
    "force": ("力", "Force"), "displacement": ("位移", "Displacement"),
    "strain": ("应变", "Strain"), "curvature": ("曲率", "Curvature"),
    "first_crack": ("首次受拉开裂", "First tensile cracking"),
    "first_yield": ("首次钢筋屈服", "First steel yielding"),
    "reference_compression": ("旧点式压缘终止", "Legacy point compression termination"),
    "physical_gauge_compression": ("物理标距压缘终止", "Physical-gauge compression termination"),
    "path_maximum": ("路径最大荷载", "Path maximum load"),
    "top": ("上缘", "Top"), "bottom": ("下缘", "Bottom"),
    "steel": ("钢筋", "Steel"), "concrete": ("混凝土", "Concrete"),
    "fiber": ("纤维", "Fiber"), "edge": ("边缘监测", "Edge monitor"),
    "finished": ("已结束", "Finished"), "running": ("运行中", "Running"),
    "conclusion_unsaved": ("结束结论未保存", "Conclusion not persisted"),
}


def _display(language: str, key: str, **values: Any) -> str:
    """Translate a source catalog key, never a stored data field or identity."""
    if key in _VALUE_LABELS:
        text = _VALUE_LABELS[key][1 if language == "en" else 0]
    else:
        text = _EN.get(key, key) if language == "en" else key
    return text.format(**values) if values else text


def _phase(language: str, value: Any) -> str:
    names = {"S0": "初始态", "G": "自重加载", "SG": "自重平衡", "J": "千斤顶加载"}
    return _display(language, names.get(str(value), str(value)))


def _number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _text(value: Any) -> str:
    if value is None:
        return "缺失 / 不适用"
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, float):
        return format(value, ".10g") if math.isfinite(value) else "无效数值"
    return str(value)


# Explicit semantic fields: counts, IDs, booleans and raw evidence are excluded.
_PHYSICAL_UNITS = {
    "gauge_length_mm": "mm", "threshold": "1", "eta": "mm", "x1": "mm", "x2": "mm",
    "search_domain_mm": "mm", "signed_mean": "1", "compression_maximum": "1",
    "control_displacement": "mm", "load_factor": "1", "gravity_factor": "1",
    "lower_control": "mm", "upper_control": "mm", "lower_ratio": "1", "upper_ratio": "1",
    "final_excess": "1", "lower_gravity_factor": "1", "upper_gravity_factor": "1", "bracket_width": "mm",
    "residual_force": "N", "residual_moment": "N.mm", "correction_translation": "mm",
    "correction_rotation": "rad", "control_error": "mm", "force_scale": "N", "moment_scale": "N.mm",
    "translation_scale": "mm", "rotation_scale": "rad", "target_increment": "mm", "backward_error": "1",
    "last_correction_factor": "1", "minimum_correction_factor": "1", "predictor_seed_factor": "1",
    "predictor_control_advance": "mm", "predictor_amplitude": "mm",
}


def _physical_text(value: Any, language: str, *, exact: bool = False) -> str:
    """Format an explicitly physical value without changing its saved source."""
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(_physical_text(item, language, exact=exact) for item in value) + "]"
    unavailable = _display(language, "缺失 / 不适用" if value is None else "无效数值")
    return (full_precision if exact else scientific)(value, unavailable=unavailable)


def _field_label(key: str) -> str:
    unit = _PHYSICAL_UNITS.get(key)
    return f"{key} [{unit}]" if unit else key


def _evidence_lines(value: dict[str, Any], language: str, *, exact: bool = False, prefix: str = "") -> list[str]:
    """Present known physical evidence fields while retaining semantic metadata."""
    lines = []
    for key, item in value.items():
        name = f"{prefix}.{key}" if prefix else key
        if isinstance(item, dict) and key in ("lower_evidence", "upper_evidence", "lower_gauge", "upper_gauge"):
            lines.extend(_evidence_lines(item, language, exact=exact, prefix=name))
        elif key in _PHYSICAL_UNITS:
            lines.append(f"{name} [{_PHYSICAL_UNITS[key]}]: {_physical_text(item, language, exact=exact)}")
        else:
            lines.append(f"{name}: {_display(language, _text(item))}")
    return lines


def _comparison_unit(prefix: str, units: dict[str, str]) -> str | None:
    """Classify generated comparison metrics, never raw options/source fields."""
    parts = prefix.split(".")
    root, leaf = parts[0].split("[")[0], parts[-1].split("[")[0]
    if root in ("options", "source", "diagnostics"):
        return None
    if root == "thresholds":
        return "1"
    if root in ("experimental_interval", "common_intervals"):
        return "mm"
    dimensionless = {"NRMSE", "Emax", "coverage", "relative_error", "limit", "P_error", "w_error", "P_limit", "w_limit"}
    if leaf in dimensionless:
        return "1"
    if root == "channels" and len(parts) >= 3 and leaf in ("scale", "noise_scale", "maximum_absolute_error"):
        return units.get(".".join(parts[1:-1]), "?")
    if root in ("maximum_P", "initial_stiffness") and leaf in ("simulation", "experiment"):
        return "N" if root == "maximum_P" else "N/mm"
    if root == "initial_stiffness" and leaf == "interval":
        return "mm"
    return {"Pscale": "N", "wscale": "mm", "P_interval_width": "N", "w_interval_width": "mm",
            "w": "mm", "P_sim": "N", "P_exp": "N", "error": "N"}.get(leaf)


def _table(headers: list[str], *, physical_columns: tuple[int, ...] = (), physical_key_column: int | None = None) -> QTableWidget:
    table = QTableWidget(0, len(headers))
    table.setHorizontalHeaderLabels(headers)
    table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
    table.setAlternatingRowColors(True)
    table.setSortingEnabled(False)
    table.horizontalHeader().setStretchLastSection(True)
    table.setAccessibleName("只读计算结果")
    table.setProperty("source_headers", headers)
    table.setProperty("result_language", "zh_CN")
    table.setProperty("physical_columns", list(physical_columns))
    table.setProperty("physical_key_column", physical_key_column)
    return table


def _rows(table: QTableWidget, rows: list[list[Any]], *, physical_cells: tuple[tuple[int, int], ...] = ()) -> None:
    table.setProperty("source_rows", deepcopy(rows))
    table.setProperty("physical_cells", list(physical_cells))
    language = table.property("result_language") or "zh_CN"
    columns = set(table.property("physical_columns") or [])
    cells = {tuple(cell) for cell in physical_cells}
    key_column = table.property("physical_key_column")
    table.setUpdatesEnabled(False)
    try:
        table.setRowCount(len(rows))
        for row_index, row in enumerate(rows):
            for column, value in enumerate(row):
                physical = column in columns or (row_index, column) in cells
                text = _physical_text(value, language) if physical else _display(language, _text(value))
                if column == key_column and isinstance(value, str) and value in _PHYSICAL_UNITS:
                    text = _field_label(value)
                item = QTableWidgetItem(text)
                item.setToolTip(_physical_text(value, language, exact=True) if physical else _text(value))
                item.setData(Qt.ItemDataRole.UserRole, deepcopy(value))
                table.setItem(row_index, column, item)
    finally:
        table.setUpdatesEnabled(True)


class _Curve(QWidget):
    """Paint ordered physical P-w samples, retaining each real step identity."""

    pointRequested = Signal(dict)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumSize(360, 150)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName("荷载挠度曲线；点击吸附到真实接受步")
        self.records: list[dict[str, Any]] = []
        self.overlays: list[tuple[str, list[dict[str, Any]]]] = []
        self.selected_id = ""
        self.caption = "尚无已接受结果"
        self.x_label = "跨中挠度 w [mm]；向下正"
        self.y_label = "两千斤顶合计荷载 P [N]"
        self.series_label = "已接受计算步"
        self._hit_points: list[tuple[QPointF, dict[str, Any]]] = []
        self.language = "zh_CN"
        self._theme = "dark"

    def set_theme(self, name: str) -> None:
        colors(name)
        self._theme = name
        self.update()

    def paintEvent(self, event: Any) -> None:
        painter = QPainter(self)
        self.draw(painter, QRectF(self.rect()))

    def draw(self, painter: QPainter, rect: QRectF, *, interactive: bool = True) -> None:
        painter.save()
        c = colors(self._theme)
        painter.setFont(QFont("Microsoft YaHei", 9))
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(rect, QColor(c["plot"]))
        plot = rect.adjusted(72, 32, -24, -62)
        painter.setPen(QColor(c["text"]))
        metrics = painter.fontMetrics()
        caption_rect = metrics.boundingRect(rect.adjusted(12, 2, -12, 0).toRect(),
                                           Qt.TextFlag.TextWordWrap, self.caption)
        painter.drawText(rect.adjusted(12, 2, -12, 0), Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextWordWrap, self.caption)
        hit_points: list[tuple[QPointF, dict[str, Any]]] = []
        if interactive:
            self._hit_points = []
        all_points = self.records + [point for _, points in self.overlays for point in points]
        valid = [point for point in all_points if _number(point.get("w")) and _number(point.get("P"))]
        if not valid:
            painter.drawText(plot, Qt.AlignmentFlag.AlignCenter, _display(self.language, "尚无可绘制的真实接受步 / 有效导入数据"))
            painter.restore()
            return
        xmin, xmax = min(p["w"] for p in valid), max(p["w"] for p in valid)
        ymin, ymax = min(p["P"] for p in valid), max(p["P"] for p in valid)
        dx, dy = xmax - xmin, ymax - ymin
        dx = dx if dx > 0 else max(abs(xmax) * .1, 1.)
        dy = dy if dy > 0 else max(abs(ymax) * .1, 1.)
        xmin, xmax = xmin - dx * .04, xmax + dx * .04
        ymin, ymax = ymin - dy * .06, ymax + dy * .06
        x_labels = [scientific(xmin + index / 5 * (xmax - xmin)) for index in range(6)]
        y_labels = [scientific(ymin + index / 5 * (ymax - ymin)) for index in range(6)]
        x_width = max(metrics.horizontalAdvance(text) for text in x_labels)
        y_width = max(metrics.horizontalAdvance(text) for text in y_labels)
        # Reserve actual font bounds, including a half end-label on the right.
        plot = rect.adjusted(y_width + 38, max(32, caption_rect.height() + 8),
                             -max(16, x_width / 2 + 8), -62)
        x_count = max(2, min(6, int(max(1, plot.width()) / (x_width + 14)) + 1))
        y_count = max(2, min(6, int(max(1, plot.height()) / (metrics.height() + 8)) + 1))

        def xy(point: dict[str, Any]) -> QPointF:
            return QPointF(plot.left() + (point["w"] - xmin) / (xmax - xmin) * plot.width(),
                           plot.bottom() - (point["P"] - ymin) / (ymax - ymin) * plot.height())

        for index in range(x_count):
            fraction = index / (x_count - 1)
            x = plot.left() + fraction * plot.width()
            painter.setPen(QPen(QColor(c["grid"]), 1))
            painter.drawLine(QPointF(x, plot.top()), QPointF(x, plot.bottom()))
            painter.setPen(QColor(c["secondary"]))
            painter.drawText(QRectF(x - x_width / 2 - 4, plot.bottom() + 5, x_width + 8, metrics.height() + 2),
                             Qt.AlignmentFlag.AlignCenter, scientific(xmin + fraction * (xmax - xmin)))
        for index in range(y_count):
            fraction = index / (y_count - 1)
            y = plot.bottom() - fraction * plot.height()
            painter.setPen(QPen(QColor(c["grid"]), 1))
            painter.drawLine(QPointF(plot.left(), y), QPointF(plot.right(), y))
            painter.setPen(QColor(c["secondary"]))
            painter.drawText(QRectF(rect.left() + 28, y - metrics.height() / 2, y_width + 4, metrics.height() + 2),
                             Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                             scientific(ymin + fraction * (ymax - ymin)))
        painter.drawRect(plot)
        painter.drawText(QRectF(plot.left(), plot.bottom() + 28, plot.width(), 22),
                         Qt.AlignmentFlag.AlignCenter, self.x_label)
        painter.save()
        painter.translate(rect.left() + 14, plot.center().y())
        painter.rotate(-90)
        painter.drawText(QRectF(-plot.height() / 2, -12, plot.height(), 22),
                         Qt.AlignmentFlag.AlignCenter, self.y_label)
        painter.restore()
        painter.save()
        painter.setClipRect(plot.adjusted(-5, -5, 5, 5))
        series = [(self.series_label, self.records, QColor(c["line"]), Qt.PenStyle.SolidLine)]
        overlay_colors = c["overlays"]
        for index, (label, points) in enumerate(self.overlays):
            series.append((label, points, QColor(overlay_colors[index % len(overlay_colors)]), Qt.PenStyle.DashLine))
        for series_index, (_, points, color, style) in enumerate(series):
            path = QPainterPath()
            active = False
            previous_branch: Any = None
            for point in points:
                if not (_number(point.get("w")) and _number(point.get("P"))) or point.get("valid") is False:
                    active = False
                    continue
                position = xy(point)
                branch = point.get("branch")
                if not active or (branch is not None and branch != previous_branch):
                    path.moveTo(position)
                else:
                    path.lineTo(position)
                active, previous_branch = True, branch
                if series_index == 0:
                    hit_points.append((position, point))
            painter.setPen(QPen(color, 1.8, style))
            painter.drawPath(path)
        for position, point in hit_points:
            if point.get("identity", {}).get("step_id") == self.selected_id:
                painter.setPen(QPen(QColor(c["marker"]), 1.3))
                painter.drawLine(QPointF(position.x(), plot.top()), QPointF(position.x(), plot.bottom()))
                painter.setBrush(QColor(c["marker"]))
                painter.drawEllipse(position, 4, 4)
        painter.restore()
        painter.setPen(QColor(c["line"]))
        legend = self.series_label + _display(self.language, "（实线）")
        if self.overlays:
            legend += _display(self.language, "；虚线: ") + " / ".join(label for label, _ in self.overlays)
        painter.drawText(QRectF(plot.left(), plot.top() + 4, plot.width(), 38),
                         Qt.AlignmentFlag.AlignLeft | Qt.TextFlag.TextWordWrap, legend)
        if interactive:
            self._hit_points = hit_points
        painter.restore()

    def mousePressEvent(self, event: Any) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self._hit_points:
            position = event.position()
            _, record = min(self._hit_points, key=lambda pair: (pair[0].x() - position.x()) ** 2 +
                           (pair[0].y() - position.y()) ** 2)
            self.pointRequested.emit(deepcopy(record))
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: Any) -> None:
        if self._hit_points:
            position = event.position()
            pixel, record = min(self._hit_points, key=lambda pair: (pair[0].x() - position.x()) ** 2 +
                                (pair[0].y() - position.y()) ** 2)
            if (pixel - position).manhattanLength() < 35:
                self.setToolTip(f"{_display(self.language, '真实接受步')} {record.get('sequence')} / {_phase(self.language, record.get('phase'))}\n"
                                f"{self.x_label}: {_physical_text(record.get('w'), self.language, exact=True)}\n{self.y_label}: {_physical_text(record.get('P'), self.language, exact=True)}\n"
                                f"{_text(record.get('gauge', {}).get('current')) if 'gauge' in record else ''}\n"
                                f"{record.get('identity', {}).get('step_id', '')}")
            else:
                self.setToolTip(_display(self.language, "点击吸附到距离最近的真实接受步"))
        super().mouseMoveEvent(event)


class ResultsPanel(QWidget):
    """Linked read-only result panels consuming complete copied wire snapshots.

    ``selectionRequested`` carries run/step/phase/sequence and optional section,
    point or object IDs. ``zeroChanged`` carries a complete observation definition.
    External ``set_selection`` updates the panel without echoing selection signals.
    """

    selectionRequested = Signal(dict)
    zeroChanged = Signal(dict)
    runRequested = Signal(str)
    viewRequested = Signal()
    frameAvailabilityChanged = Signal(bool)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._language = "zh_CN"
        self._theme = "dark"
        self._available_runs: list[dict[str, Any]] = []
        self._context_state: dict[str, Any] = {}
        self._frame_available = False
        self._unavailable_sequence: Any = None
        self._navigation = QTreeWidget()
        self._navigation.setObjectName("resultsTree")
        self._navigation.setHeaderLabels(["运行", "状态"])
        self._navigation.setAccessibleName("结果树")
        self._navigation.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._navigation.itemClicked.connect(self._tree_requested)
        self._navigation.itemActivated.connect(self._tree_requested)
        self._navigation.customContextMenuRequested.connect(self._tree_menu)
        self._frame_items: dict[str, QTreeWidgetItem] = {}
        self._run: dict[str, Any] = {}
        self._prepared: dict[str, Any] = {}
        self._steps: list[dict[str, Any]] = []
        self._by_id: dict[str, dict[str, Any]] = {}
        self._by_sequence: dict[int, dict[str, Any]] = {}
        self._imports: list[dict[str, Any]] = []
        self._selection: dict[str, Any] = {}
        self._section_id = ""
        self._reference_id = ""
        self._updating = False
        self._event_rows: list[tuple[dict[str, Any], dict[str, Any]]] = []
        self._point_rows: list[dict[str, Any]] = []
        self._step_provider: Callable[[dict[str, Any]], dict[str, Any]] | None = None
        self._full_steps: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self._log_lines: list[str] = []
        self._progress_records: list[dict[str, Any]] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        controls = QHBoxLayout()
        self.previous_button, self.next_button = QPushButton("上一步"), QPushButton("下一步")
        self.step_spin = QSpinBox()
        self.step_spin.setRange(0, 0)
        self.step_spin.setPrefix("接受步 ")
        self.step_spin.setAccessibleName("选择实际接受步序号")
        self.follow = QCheckBox("跟随最新")
        self.follow.setChecked(True)
        self.zero_combo = QComboBox()
        self.zero_combo.addItems(["默认零点（SG / S0）", "累计量", "以所选步为零点"])
        self.zero_combo.setAccessibleName("观测零点；不改变材料历史")
        for widget in (self.previous_button, self.step_spin, self.next_button, self.follow, self.zero_combo):
            controls.addWidget(widget)
        controls.addStretch()
        self.browse_mode = QComboBox()
        self.browse_mode.setObjectName("resultsBrowseMode")
        self.browse_mode.addItems(["常用结果", "详细诊断"])
        self.browse_mode.currentIndexChanged.connect(self._browse_mode_changed)
        controls.addWidget(self.browse_mode)
        layout.addLayout(controls)
        self.identity_label = QLabel("尚未选择结果")
        self.identity_label.setWordWrap(True)
        self.identity_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.identity_label)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs, 1)

        self.curve = _Curve()
        self.tabs.addTab(self.curve, "荷载—挠度")
        self.criterion_curve = _Curve()
        self.criterion_curve.x_label = "实际控制位移 [mm]；保留原阶段定义"
        self.criterion_curve.y_label = "压应变 / 阈值 [1]"
        self.criterion_curve.series_label = "所选结构判据（真实接受步）"
        self.tabs.addTab(self.criterion_curve, "结构判据 / 点诊断")
        self.gauge_table = _table(["证据", "字段", "实际保存值"], physical_key_column=1)
        self.gauge_table.setAccessibleName("物理窗口当前与历史最大值、终止夹逼证据；含自重总应变")
        self.tabs.addTab(self.gauge_table, "结构窗口 / 终止夹逼")
        section_page = QWidget()
        section_layout = QVBoxLayout(section_page)
        self.section_combo = QComboBox()
        self.section_combo.setAccessibleName("真实 Gauss 截面")
        section_layout.addWidget(self.section_combo)
        self.section_label = QLabel("仅展示真实 Gauss 截面；边缘监测不参与内力积分")
        self.section_label.setWordWrap(True)
        section_layout.addWidget(self.section_label)
        self.points_table = _table(["点身份", "类别 / 边缘", "η [mm]", "面积 [mm²]", "应变 [1]",
                                    "应力 [MPa]", "切线 [MPa]", "塑性应变", "分支", "已开裂", "已屈服",
                                    "原始最大压应变", "当前参考压应变", "历史最大参考压应变",
                                    "参考阈值", "δc [mm]", "lch,t [mm]", "lch,c [mm]", "Lg [mm]",
                                    "受拉截断能量 [N/mm]", "受压截断能量 [N/mm]"],
                                   physical_columns=(2, 3, 4, 5, 6, 7, *range(11, 21)))
        section_layout.addWidget(self.points_table)
        self.tabs.addTab(section_page, "截面 / 钢筋 / 边缘")

        force_split = QSplitter(Qt.Orientation.Vertical)
        self.node_table = _table(["节点", "x [mm]", "ux [mm]", "uy [mm]", "θ [rad]",
                                 "Rx / 自由残差 [N]", "Ry / 自由残差 [N]", "Mz / 自由残差 [N.mm]", "约束分量"],
                                 physical_columns=tuple(range(1, 8)))
        self.force_table = _table(["单元", "N1 [N]", "V1 [N]", "M1 [N.mm]", "N2 [N]", "V2 [N]",
                                  "M2 [N.mm]", "qx [N/mm]", "qy [N/mm]", "来源"],
                                  physical_columns=tuple(range(1, 9)))
        force_split.addWidget(self.node_table)
        force_split.addWidget(self.force_table)
        self.tabs.addTab(force_split, "位移 / 反力 / NVM")
        self.event_table = _table(["事件", "真实接受步", "阶段", "位置 x [mm]", "η [mm]", "截面", "点身份",
                                  "P [N]", "控制增量 [mm]", "原始应变", "参考应变", "阈值", "判据来源",
                                  "模式", "边缘", "窗口起点 [mm]", "窗口终点 [mm]", "有符号总均值 [1]",
                                  "总压应变最大值 [1]", "基准", "算子", "夹逼证据", "参数来源"],
                                  physical_columns=(3, 4, 7, 8, 9, 10, 11, 15, 16, 17, 18))
        self.tabs.addTab(self.event_table, "事件")
        self.transition_table = _table(["非终止转折量", "数值 / 状态", "含义 / 单位"])
        self.transition_table.setAccessibleName("首次拉裂或压缩峰值定位证据；不是压碎判据")
        self.tabs.addTab(self.transition_table, "转折定位")
        self.observation_table = _table(["观测", "量", "单位", "累计量", "相对量", "参考步", "算子 / 来源"],
                                        physical_columns=(3, 4))
        self.tabs.addTab(self.observation_table, "观测 / 标距")

        comparison = QWidget()
        comparison_layout = QVBoxLayout(comparison)
        self.comparison_label = QLabel("尚无导入数据。默认值与合成曲线不能认证真实 NC0 试验吻合度。")
        self.comparison_label.setWordWrap(True)
        comparison_layout.addWidget(self.comparison_label)
        self.comparison_table = _table(["数据集", "身份 / 来源", "指标", "数值 / 状态"])
        comparison_layout.addWidget(self.comparison_table)
        self.tabs.addTab(comparison, "数据对比")
        self.summary_text = QPlainTextEdit()
        self.summary_text.setReadOnly(True)
        self.summary_text.setAccessibleName("结束原因、收敛与保存位置")
        self.tabs.addTab(self.summary_text, "诊断 / 结束原因")
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(5000)
        self.log.setAccessibleName("实际分析进度日志")
        self.tabs.addTab(self.log, "日志")

        self.previous_button.clicked.connect(lambda: self._navigate(-1))
        self.next_button.clicked.connect(lambda: self._navigate(1))
        self.step_spin.valueChanged.connect(self._sequence_requested)
        self.follow.toggled.connect(self._follow_changed)
        self.zero_combo.currentIndexChanged.connect(self._zero_changed)
        self.curve.pointRequested.connect(self._curve_requested)
        self.criterion_curve.pointRequested.connect(self._curve_requested)
        self.section_combo.currentIndexChanged.connect(self._section_requested)
        self.points_table.cellClicked.connect(self._point_requested)
        self.event_table.cellClicked.connect(self._event_requested)
        self.node_table.cellClicked.connect(self._node_requested)
        self.force_table.cellClicked.connect(self._element_requested)
        self._tab_keys = [self.tabs.tabText(index) for index in range(self.tabs.count())]
        self._browse_mode_changed(0)
        self.retranslate_ui("zh_CN")

    @property
    def language(self) -> str:
        """Current presentation language; engineering data never contain it."""
        return self._language

    def set_theme(self, name: str) -> None:
        """Recolor plots and tables without selecting or resolving a frame."""
        colors(name)
        self._theme = name
        self.setPalette(palette(name))
        self._navigation.setPalette(palette(name))
        for curve in (self.curve, self.criterion_curve):
            curve.set_theme(name)

    def _tr(self, key: str, **values: Any) -> str:
        return _display(self._language, key, **values)

    def navigation_widget(self) -> QWidget:
        """Return persistent real-run navigation for the workbench's left dock."""
        return self._navigation

    def set_available_runs(self, runs: list[dict[str, Any]]) -> None:
        """Accept lightweight controller records, without reading material arrays."""
        self._available_runs = deepcopy(runs or [])
        self._rebuild_navigation()

    def set_context_state(self, state: dict[str, Any]) -> None:
        """Display actual running/stale state; never alter run or input data."""
        previous_status = tuple(bool(self._context_state.get(key)) for key in ("running", "cancel_requested", "results_stale"))
        self._context_state = deepcopy(state or {})
        if previous_status != tuple(bool(self._context_state.get(key)) for key in ("running", "cancel_requested", "results_stale")):
            self._rebuild_navigation()
        self._refresh_identity()

    def _browse_mode_changed(self, mode: int) -> None:
        diagnostics = {2, 6, 9, 10}
        for index in range(self.tabs.count()):
            self.tabs.setTabVisible(index, index not in diagnostics if mode == 0 else True)

    def _run_id(self) -> str:
        return str(self._prepared.get("identity", {}).get("run_id", ""))

    def _run_number(self, run_id: str) -> int:
        identifiers = [str(item.get("run_id", "")) for item in self._available_runs]
        return identifiers.index(run_id) + 1 if run_id in identifiers else len(identifiers) + 1

    def _status_text(self) -> str:
        labels = []
        if self._run.get("analysis_read_only"):
            labels.append(self._tr("冻结结果只读"))
        if self._context_state.get("results_stale"):
            labels.append(self._tr("结果属于旧模型修订"))
        if self._context_state.get("running"):
            labels.append(self._tr("取消已请求，等待安全边界") if self._context_state.get("cancel_requested") else self._tr("运行中 · 历史切换已锁定"))
        return " / ".join(labels)

    def _refresh_identity(self) -> None:
        step = self._by_id.get(self._selection.get("step_id", ""))
        if step is None:
            return
        if not self._frame_available:
            return
        self.identity_label.setText(self._tr("运行 {number} · {phase} · 已接受帧 {sequence} · P={P} N · {follow}",
            number=self._run_number(self._run_id()), phase=_phase(self._language, step.get("phase")), sequence=step.get("sequence"),
            P=_physical_text(step.get("P"), self._language), follow=self._tr("跟随最新" if self.follow.isChecked() else "固定步")) +
            ("\n" + self._status_text() if self._status_text() else ""))
        self.identity_label.setToolTip("\n".join(f"{key}={value}" for key, value in step.get("identity", {}).items()) +
                                      f"\nP [N]={_physical_text(step.get('P'), self._language, exact=True)}")

    def _rebuild_navigation(self) -> None:
        tree = self._navigation
        current_data = tree.currentItem().data(0, Qt.ItemDataRole.UserRole) if tree.currentItem() else None
        expanded = {tree.topLevelItem(i).data(0, Qt.ItemDataRole.UserRole).get("run_id")
                    for i in range(tree.topLevelItemCount()) if tree.topLevelItem(i).isExpanded() and isinstance(tree.topLevelItem(i).data(0, Qt.ItemDataRole.UserRole), dict)}
        tree.blockSignals(True)
        try:
            tree.clear()
            self._frame_items = {}
            records = deepcopy(self._available_runs)
            current_id = self._run_id()
            if current_id and not any(item.get("run_id") == current_id for item in records):
                records.append({"run_id": current_id, "status": self._run.get("run_status")})
            if not records:
                item = QTreeWidgetItem([self._tr("尚无已保存运行")])
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEnabled)
                tree.addTopLevelItem(item)
            for number, record in enumerate(records, 1):
                run_id = str(record.get("run_id", ""))
                if not run_id:
                    continue
                is_current = run_id == current_id
                status = self._status_text() if is_current else ""
                status = status or self._tr("已结束" if record.get("status") == "finished" or (is_current and self._run.get("summary")) else "未结束 / 中断")
                item = QTreeWidgetItem([self._tr("当前运行") if is_current else self._tr("已保存运行 {number}", number=number), status])
                item.setData(0, Qt.ItemDataRole.UserRole, {"kind": "run", "run_id": run_id})
                item.setToolTip(0, "\n".join(f"{key}={value}" for key, value in record.items()))
                tree.addTopLevelItem(item)
                if not is_current:
                    item.setDisabled(bool(self._context_state.get("running")))
                    continue
                frames = QTreeWidgetItem(item, [self._tr("已接受帧"), str(len(self._steps))])
                frames.setData(0, Qt.ItemDataRole.UserRole, {"kind": "category", "run_id": run_id, "tab": 0})
                entries = [(step["sequence"], step, True) for step in self._steps]
                entries.extend((entry.get("sequence", -1), entry, False) for entry in self._run.get("unavailable_steps", []))
                for sequence, entry, available in sorted(entries, key=lambda value: value[0]):
                    key = "已接受帧 {sequence} · {phase}" if available else "不可用帧 {sequence} · {phase}"
                    child = QTreeWidgetItem(frames, [self._tr(key, sequence=sequence, phase=_phase(self._language, entry.get("phase")))])
                    selection = self._selection_for(entry) if available else {"run_id": run_id, "step_id": entry.get("step_id"), "sequence": sequence, "phase": entry.get("phase")}
                    child.setData(0, Qt.ItemDataRole.UserRole, {"kind": "frame" if available else "unavailable", "selection": selection})
                    child.setToolTip(0, str(entry.get("diagnostics") or selection))
                    if selection.get("step_id"):
                        self._frame_items[selection["step_id"]] = child
                for tab in (0, 1, 3, 4, 5, 7, 8, 9):
                    category = QTreeWidgetItem(item, [self._tr(self._tab_keys[tab])])
                    category.setData(0, Qt.ItemDataRole.UserRole, {"kind": "category", "run_id": run_id, "tab": tab})
                item.setExpanded(is_current or run_id in expanded)
                frames.setExpanded(len(entries) <= 20)
            selected_id = self._selection.get("step_id")
            if not selected_id and isinstance(current_data, dict):
                selected_id = (current_data.get("selection") or {}).get("step_id")
            if selected_id in self._frame_items:
                tree.setCurrentItem(self._frame_items[selected_id])
                self._frame_items[selected_id].parent().setExpanded(True)
        finally:
            tree.blockSignals(False)

    def _tree_requested(self, item: QTreeWidgetItem, column: int = 0) -> None:
        data = item.data(0, Qt.ItemDataRole.UserRole) or {}
        kind = data.get("kind")
        if kind == "run" and data.get("run_id") != self._run_id():
            if not self._context_state.get("running"):
                self.runRequested.emit(data["run_id"])
        elif kind == "frame":
            step = self._by_id.get(data["selection"].get("step_id"))
            if step:
                self._request(step)
        elif kind == "unavailable":
            self._show_unavailable(data["selection"].get("sequence"))
            self.browse_mode.setCurrentIndex(1)
            self.tabs.setCurrentIndex(9)
        elif kind == "category":
            tab = int(data.get("tab", 0))
            if tab in (2, 6, 9, 10):
                self.browse_mode.setCurrentIndex(1)
            self.tabs.setCurrentIndex(tab)
        self.viewRequested.emit()

    def _tree_menu(self, position: Any) -> None:
        item = self._navigation.itemAt(position)
        if item is None and position.x() < 0 and position.y() < 0:
            item = self._navigation.currentItem()
            if item:
                position = self._navigation.visualItemRect(item).center()
        if item is None:
            return
        self._navigation.setCurrentItem(item)
        data = item.data(0, Qt.ItemDataRole.UserRole) or {}
        kind = data.get("kind")
        if not kind:
            return
        menu = QMenu(self._navigation)
        title = "打开运行" if kind == "run" else "检查不可用原因" if kind == "unavailable" else "显示结果"
        action = menu.addAction(self._tr(title))
        action.setEnabled(not (kind == "run" and self._context_state.get("running") and data.get("run_id") != self._run_id()))
        action.triggered.connect(lambda: self._tree_requested(item))
        menu.exec(self._navigation.viewport().mapToGlobal(position))

    def _show_unavailable(self, sequence: Any) -> None:
        self._unavailable_sequence = sequence
        self._set_frame_availability(False)
        self._selection = {}
        self._clear_step_views()
        self.curve.selected_id = self.criterion_curve.selected_id = ""
        self.curve.update()
        self.criterion_curve.update()
        self.identity_label.setText(self._tr("接受步 {sequence} 缺失 / 不可用；未插值或补零。", sequence=sequence))

    def _set_frame_availability(self, available: bool) -> None:
        # Emitted only on an availability transition. True is informational;
        # composition must not reload a provider or echo a selection from it.
        if self._frame_available != available:
            self._frame_available = available
            self.frameAvailabilityChanged.emit(available)

    def retranslate_ui(self, language: str) -> None:
        """Retranslate source catalog keys while retaining all data/view choices."""
        if language not in ("zh_CN", "en"):
            raise ValueError("Unsupported result language")
        self._language = language
        updating = self._updating
        self._updating = True
        try:
            self.previous_button.setText(self._tr("上一步"))
            self.next_button.setText(self._tr("下一步"))
            self.step_spin.setPrefix(self._tr("接受步 "))
            self.step_spin.setAccessibleName(self._tr("真实接受步"))
            self.follow.setText(self._tr("跟随最新"))
            self.zero_combo.setAccessibleName(self._tr("观测零点；不改变材料历史"))
            self.section_combo.setAccessibleName(self._tr("真实 Gauss 截面"))
            self.summary_text.setAccessibleName(self._tr("结束原因、收敛与保存位置"))
            self.log.setAccessibleName(self._tr("实际分析进度日志"))
            for index, key in enumerate(("默认零点（SG / S0）", "累计量", "以所选步为零点")):
                self.zero_combo.setItemText(index, self._tr(key))
            for index, key in enumerate(("常用结果", "详细诊断")):
                self.browse_mode.setItemText(index, self._tr(key))
            for index, key in enumerate(self._tab_keys):
                self.tabs.setTabText(index, self._tr(key))
            self._navigation.setHeaderLabels([self._tr("运行"), self._tr("状态")])
            self._navigation.setAccessibleName(self._tr("结果树"))
            for table in self.findChildren(QTableWidget):
                table.setProperty("result_language", language)
                table.setHorizontalHeaderLabels([self._tr(key) for key in table.property("source_headers") or []])
                table.setAccessibleName(self._tr("只读计算结果"))
                _rows(table, table.property("source_rows") or [],
                      physical_cells=tuple(table.property("physical_cells") or []))
            for curve in (self.curve, self.criterion_curve):
                curve.language = language
                curve.setAccessibleName(self._tr("荷载—挠度曲线；点击吸附到真实接受步"))
            self.gauge_table.setAccessibleName(self._tr("物理窗口当前与历史最大值、终止夹逼证据；含自重总应变"))
            self.transition_table.setAccessibleName(self._tr("首次拉裂或压缩峰值定位证据；不是压碎判据"))
            self.curve.x_label = self._tr("跨中挠度 w [mm]；向下正")
            self.curve.y_label = self._tr("两千斤顶合计荷载 P [N]")
            self.curve.series_label = self._tr("已接受计算步")
            self.criterion_curve.x_label = self._tr("实际控制位移 [mm]；保留原阶段定义")
            self.criterion_curve.y_label = self._tr("压应变 / 阈值 [1]")
            self.criterion_curve.series_label = self._tr("所选结构判据（真实接受步）")
            step = self._by_id.get(self._selection.get("step_id", ""))
            if step and self._frame_available:
                self._show_step(step)
            elif self._unavailable_sequence is not None:
                self.identity_label.setText(self._tr("接受步 {sequence} 缺失 / 不可用；未插值或补零。", sequence=self._unavailable_sequence))
            elif not self._steps:
                self._clear_step_views()
            self._refresh_curve()
            self._refresh_summary()
            self._refresh_comparison()
            self.log.setPlainText("\n".join(self._progress_line(value) for value in self._progress_records))
            if self._progress_records:
                self.log.setToolTip(self._progress_tooltip(self._progress_records[-1]))
            self._log_lines = [self._progress_line(value) for value in self._progress_records]
            self._rebuild_navigation()
        finally:
            self._updating = updating

    def set_run(self, run: dict[str, Any]) -> None:
        """Replace displayed run with its immutable snapshot and saved accepted steps."""
        self._run = deepcopy(run or {})
        self._prepared = self._run.get("prepared") or {}
        self._steps, self._by_id, self._by_sequence = [], {}, {}
        self._full_steps.clear()
        self._selection, self._section_id, self._reference_id = {}, "", ""
        self._frame_available = False
        self._unavailable_sequence = None
        self._log_lines = []
        self._progress_records = []
        self.log.clear()
        self.log.setToolTip("")
        steps = self._run.pop("steps", []) or []
        self._updating = True
        try:
            self.zero_combo.setCurrentIndex(0)
            for step in steps:
                self._store_step(step)
            self.step_spin.setRange(min(self._by_sequence, default=0), max(self._by_sequence, default=0))
        finally:
            self._updating = False
        self.set_summary(self._run.get("summary"))
        if self._steps:
            self.set_selection(self._selection_for(self._steps[-1]))
        else:
            self._clear_step_views()
        self._refresh_curve()
        self._refresh_events()
        self._refresh_comparison()
        self._rebuild_navigation()

    def _store_step(self, step: dict[str, Any]) -> bool:
        identity = step.get("identity", {})
        step_id, sequence = identity.get("step_id"), step.get("sequence")
        owner = self._prepared.get("identity", {})
        if owner and not same_run(owner, identity):
            raise ContractError("stale_result", "Result does not belong to the displayed frozen snapshot", stage="display")
        if not step_id or not isinstance(sequence, int) or isinstance(sequence, bool):
            raise ContractError("invalid_result_identity", "Accepted result requires a step ID and integer sequence", stage="display")
        if step_id in self._by_id:
            return False
        if sequence in self._by_sequence or (self._steps and sequence <= self._steps[-1]["sequence"]):
            raise ContractError("result_order", "Accepted result sequence is duplicated or out of order", stage="display")
        if not step.get("_bulk_unloaded"):
            self._full_steps[step_id] = deepcopy(step)
            self._full_steps.move_to_end(step_id)
            while len(self._full_steps) > 4:
                self._full_steps.popitem(last=False)
        value = {key: deepcopy(item) for key, item in step.items()
                 if key not in ("sections", "fibers", "edges")}
        projection = criterion_evidence(self._prepared, step)
        if "point_route_c_diagnostic" in projection:
            value["_point_route_c_diagnostic"] = projection["point_route_c_diagnostic"]
        value["_bulk_unloaded"] = True
        self._steps.append(value)
        self._by_id[step_id], self._by_sequence[sequence] = value, value
        return True

    def append_step(self, step: dict[str, Any]) -> None:
        """Append one actual accepted snapshot; pinned browsing remains at its step."""
        if not self._store_step(step):
            return
        self._updating = True
        self.step_spin.setRange(min(self._by_sequence), max(self._by_sequence))
        self._updating = False
        if self.follow.isChecked() or not self._selection:
            selection = self._selection_for(self._steps[-1])
            self.set_selection(selection)
            self.selectionRequested.emit(deepcopy(selection))
        self._refresh_curve()
        self._refresh_events()
        self._refresh_comparison()
        self._rebuild_navigation()

    def _selection_for(self, step: dict[str, Any], **extra: Any) -> dict[str, Any]:
        return {"run_id": step.get("identity", {}).get("run_id"),
                "step_id": step.get("identity", {}).get("step_id"),
                "sequence": step.get("sequence"), "phase": step.get("phase"), **extra}

    def set_selection(self, selection: dict[str, Any]) -> None:
        """Display exactly an available accepted step, without emitting a feedback loop."""
        if not selection:
            return
        expected_run = self._prepared.get("identity", {}).get("run_id")
        if expected_run and selection.get("run_id", expected_run) != expected_run:
            return
        step = self._by_id.get(selection.get("step_id", ""))
        if step is None and "sequence" in selection and not selection.get("step_id"):
            step = self._by_sequence.get(selection["sequence"])
        if step is None:
            self._show_unavailable(selection.get("sequence", "?"))
            return
        self._selection = self._selection_for(step, **{key: deepcopy(value) for key, value in selection.items()
                                                    if key not in ("run_id", "step_id", "sequence", "phase")})
        if selection.get("section_id"):
            self._section_id = selection["section_id"]
        self._updating = True
        try:
            self.step_spin.setValue(step["sequence"])
            self._show_step(step)
        finally:
            self._updating = False
        self.curve.selected_id = step["identity"]["step_id"] if self._frame_available else ""
        self.curve.update()
        self.criterion_curve.selected_id = step["identity"]["step_id"] if self._frame_available else ""
        self._refresh_criterion_curve()
        self._refresh_summary()
        selected_item = self._frame_items.get(step["identity"]["step_id"])
        if selected_item:
            self._navigation.setCurrentItem(selected_item)
            selected_item.parent().setExpanded(True)

    def _clear_step_views(self) -> None:
        self.identity_label.setText(self._tr("尚无可用接受步；未创建或插值计算状态。"))
        self.identity_label.setToolTip("")
        for table in (self.points_table, self.node_table, self.force_table, self.observation_table, self.transition_table, self.gauge_table):
            table.setRowCount(0)
        self.section_combo.clear()
        self._point_rows = []
        self.section_label.setText(self._tr("此步的截面数据缺失 / 不可用。"))
        self.section_label.setToolTip("")

    def set_step_provider(self, provider: Callable[[dict[str, Any]], dict[str, Any]] | None) -> None:
        """Load exact selected material arrays on demand, retaining at most four steps."""
        if provider is not None and not callable(provider):
            raise ContractError("invalid_step_provider", "Step provider must be callable", stage="display")
        self._step_provider = provider
        self._full_steps.clear()

    def _resolve_step(self, step: dict[str, Any]) -> dict[str, Any]:
        key = step["identity"]["step_id"]
        if key not in self._full_steps:
            if self._step_provider is None:
                raise ContractError("step_unavailable", "Full selected step needs its project reader", stage="display")
            value = self._step_provider(self._selection_for(step))
            if not isinstance(value, dict) or value.get("identity") != step["identity"] or value.get("sequence") != step["sequence"]:
                raise ContractError("step_identity_mismatch", "Loaded step differs from selected accepted identity", stage="display")
            if any(not isinstance(value.get(field), list) for field in ("sections", "fibers", "edges")):
                raise ContractError("step_unavailable", "Selected material arrays are missing", stage="display")
            self._full_steps[key] = deepcopy(value)
            while len(self._full_steps) > 4:
                self._full_steps.popitem(last=False)
        self._full_steps.move_to_end(key)
        projection = criterion_evidence(self._prepared, self._full_steps[key])
        if "point_route_c_diagnostic" in projection:
            step["_point_route_c_diagnostic"] = projection["point_route_c_diagnostic"]
        return self._full_steps[key]

    def _show_step(self, step: dict[str, Any]) -> None:
        try:
            step = self._resolve_step(step)
        except Exception as error:
            self._unavailable_sequence = step.get("sequence")
            self._set_frame_availability(False)
            self._clear_step_views()
            self._point_rows = []
            self.identity_label.setText(self._tr("所选接受步 {sequence} 完整数组不可用：{error}；未用其他步代替。", sequence=step.get("sequence"), error=error))
            self.identity_label.setToolTip(str(error))
            return
        identity = step.get("identity", {})
        self._unavailable_sequence = None
        self._set_frame_availability(True)
        self._refresh_identity()
        sections = step.get("sections") or []
        self.section_combo.clear()
        for section in sections:
            self.section_combo.addItem(f"x={_physical_text(section.get('x'), self._language)} mm | Gauss {section.get('gauss_index')}", section.get("id"))
            self.section_combo.setItemData(self.section_combo.count() - 1,
                str(section.get("id")) + f"\nx [mm]={_physical_text(section.get('x'), self._language, exact=True)}", Qt.ItemDataRole.ToolTipRole)
        section_index = next((index for index, section in enumerate(sections) if section.get("id") == self._section_id), 0)
        if sections:
            self.section_combo.setCurrentIndex(section_index)
            self._section_id = sections[section_index].get("id", "")
            self._show_section(sections[section_index])
        else:
            self.points_table.setRowCount(0)
            self.section_label.setText(self._tr("此步的截面数据缺失 / 不可用。"))
            self.section_label.setToolTip("")
        self._show_forces(step)
        self._show_observations(step)
        self._show_transition(step)
        self._show_gauge(step)

    def _show_gauge(self, step: dict[str, Any]) -> None:
        """Display saved windows and each actual bracket end without point aliases."""
        rows: list[list[Any]] = []
        descriptor = self._prepared.get("compression_criterion")
        if descriptor is not None:
            rows.extend(["所选判据", key, value] for key, value in descriptor.items())
        gauge = step.get("gauge")
        if gauge is None:
            rows.append(["结构窗口", "未保存 / 不适用", "旧点式结果不补造窗口或总应变"])
        else:
            rows.append(["定义", "总应变基准", "原始未变形状态，包含自重；与观测显示归零无关"])
            rows.append(["适用范围", "建模假设", "整梁双压缘固定完整窗口；未作试验标定，不代表真实裂缝路径"])
            for status, label in (("current", "当前最大窗口"), ("historical", "接受历史最大窗口")):
                rows.extend([label, key, value] for key, value in (gauge.get(status) or {}).items())
        localization = step.get("localization")
        if localization is not None:
            for key, value in localization.items():
                if isinstance(value, dict):
                    rows.extend([key, field, item] for field, item in value.items())
                else:
                    rows.append(["终止定位", key, value])
        _rows(self.gauge_table, rows, physical_cells=tuple((index, 2) for index, row in enumerate(rows)
                                                         if row[1] in _PHYSICAL_UNITS))

    def _show_transition(self, step: dict[str, Any]) -> None:
        """Show actual onset evidence without inventing it for archived steps."""
        transition = step.get("transition")
        if transition is None:
            _rows(self.transition_table, [["旧结果未包含此类证据", None, "不补零或重新计算历史结果"]])
            return
        if not transition.get("active"):
            _rows(self.transition_table, [["本步未定位新的首次转折", False, "不表示压碎或分析终止"]])
            return
        names = {"tensile_onset": "首次拉裂转折", "compression_peak": "首次压缩峰值（非压碎）"}
        rows = [["类型", names.get(transition.get("kind"), transition.get("kind")), "非终止接受步"],
                ["材料点", transition.get("point_id"), "真实 Gauss / 边缘身份"],
                ["阈值", transition.get("threshold"), "应变域定义，单位 1"],
                ["候选求解次数", transition.get("candidate_solves"), "含定位候选，不等于已接受步数"]]
        physical_cells = [(2, 1)]
        for key, label, unit in (("lower_control", "下端控制位移", "mm"),
                                  ("upper_control", "上端控制位移", "mm"),
                                  ("lower_ratio", "下端归一转折量", "1"),
                                  ("upper_ratio", "上端归一转折量", "1"),
                                  ("lower_converged", "下端已收敛", "独立收敛证据"),
                                  ("upper_converged", "上端已收敛", "独立收敛证据")):
            rows.append([label, transition.get(key), unit])
            if key in _PHYSICAL_UNITS:
                physical_cells.append((len(rows) - 1, 1))
        for key, title in (("lower_evidence", "下端"), ("upper_evidence", "上端")):
            for field, value in (transition.get(key) or {}).items():
                unit = _PHYSICAL_UNITS.get(field)
                rows.append([self._tr(title) + " / " + field, value,
                             (f"{unit} | " if unit else "") + self._tr("定位试算收敛证据；未作为额外正式步保存")])
                if unit:
                    physical_cells.append((len(rows) - 1, 1))
        _rows(self.transition_table, rows, physical_cells=tuple(physical_cells))

    def _show_section(self, section: dict[str, Any]) -> None:
        self.section_label.setText(f"N={_physical_text(section.get('N'), self._language)} N；M={_physical_text(section.get('M'), self._language)} N.mm；"
                                  f"ε0={_physical_text(section.get('axial_strain'), self._language)} [1]；κ={_physical_text(section.get('curvature'), self._language)} 1/mm\n" +
                                  self._tr("真实 Gauss 截面。边缘为独立零面积监测；点式参考压应变与结构窗口不同。") +
                                  (self._tr(" 本模式的点式阈值 / crushed 标记仅作诊断，不代表结构终止。")
                                   if self._prepared.get("compression_criterion", {}).get("mode") == "physical_gauge_mean_v1" else ""))
        self.section_label.setToolTip("\n".join(f"{key} [{unit}]={_physical_text(section.get(key), self._language, exact=True)}"
            for key, unit in (("N", "N"), ("M", "N.mm"), ("axial_strain", "1"), ("curvature", "1/mm"))))
        self._point_rows = list(section.get("fibers") or []) + list(section.get("edges") or [])
        rows = []
        for point in self._point_rows:
            state = point.get("state") or {}
            minimum = state.get("min_strain")
            rows.append([point.get("id"), point.get("side") or point.get("role"), point.get("eta"), point.get("area"),
                         point.get("strain"), point.get("stress"), point.get("tangent"), state.get("plastic_strain"),
                         state.get("branch"), state.get("cracked"), state.get("yielded"),
                         max(0., -minimum) if _number(minimum) else None,
                         point.get("reference_compression_strain"), state.get("max_reference_compression_strain"),
                         point.get("reference_threshold"), point.get("delta_c"), point.get("lch_t"), point.get("lch_c"),
                         point.get("reference_length"), state.get("tension_energy"), state.get("compression_energy")])
        _rows(self.points_table, rows)
        selected = self._selection.get("point_id")
        for index, point in enumerate(self._point_rows):
            if point.get("id") == selected:
                self.points_table.selectRow(index)
                break

    def _show_forces(self, step: dict[str, Any]) -> None:
        nodes = self._prepared.get("nodes") or []
        displacement, reactions = step.get("displacement") or [], step.get("reactions") or []
        constraints = set(self._prepared.get("constrained_dofs") or [])
        rows = []
        for node in nodes:
            dofs = node.get("dofs") or []
            values = [displacement[dof] if isinstance(dof, int) and 0 <= dof < len(displacement) else None for dof in dofs]
            forces = [reactions[dof] if isinstance(dof, int) and 0 <= dof < len(reactions) else None for dof in dofs]
            values, forces = (values + [None] * 3)[:3], (forces + [None] * 3)[:3]
            fixed = ",".join(name for name, dof in zip(("ux", "uy", "theta"), dofs) if dof in constraints)
            rows.append([node.get("id"), node.get("x"), *values, *forces, fixed or "自由；反力栏为残差"])
        _rows(self.node_table, rows)
        force_rows = []
        for item in step.get("element_forces") or []:
            components = (list(item.get("end_forces") or []) + [None] * 6)[:6]
            force_rows.append([item.get("id"), *components, item.get("distributed_axial"),
                               item.get("distributed_transverse"), item.get("source")])
        _rows(self.force_table, force_rows)

    def _reference_step(self) -> dict[str, Any] | None:
        if self.zero_combo.currentIndex() == 2:
            return self._by_id.get(self._reference_id)
        phase = "SG" if self._prepared.get("document", {}).get("model", {}).get("gravity", {}).get("enabled") else "S0"
        return next((step for step in self._steps if step.get("phase") == phase), None)

    def _observation_values(self, step: dict[str, Any]) -> list[dict[str, Any]]:
        values = deepcopy(step.get("observations") or [])
        if self.zero_combo.currentIndex() == 0:
            return values
        reference = self._reference_step()
        reference_values = {item.get("id"): item for item in (reference or {}).get("observations") or []}
        for item in values:
            if self.zero_combo.currentIndex() == 1:
                item["increment"], item["reference_step_id"] = None, "累计量；无显示归零"
            else:
                base = reference_values.get(item.get("id"), {}).get("total")
                value = item.get("total")
                item["increment"] = value - base if _number(value) and _number(base) else None
                item["reference_step_id"] = self._reference_id or "缺失"
        return values

    def _show_observations(self, step: dict[str, Any]) -> None:
        _rows(self.observation_table, [[item.get(key) for key in ("id", "quantity", "unit", "total", "increment", "reference_step_id", "source")]
                                      for item in self._observation_values(step)])

    def _curve_records(self) -> list[dict[str, Any]]:
        records = []
        reference = self._reference_step()
        for step in self._steps:
            observation = next((item for item in self._observation_values(step) if item.get("id") == "midspan_deflection"), {})
            w = observation.get("total" if self.zero_combo.currentIndex() == 1 else "increment")
            P = step.get("P")
            if self.zero_combo.currentIndex() == 2:
                base = (reference or {}).get("P")
                P = P - base if _number(P) and _number(base) else None
            records.append({"w": w, "P": P, "identity": step.get("identity", {}),
                            "sequence": step.get("sequence"), "phase": step.get("phase"),
                            **criterion_evidence(self._prepared, step)})
        return records

    def _refresh_curve(self) -> None:
        self.curve.records = self._curve_records()
        self.curve.overlays = []
        for index, dataset in enumerate(self._imports):
            label = dataset.get("name") or dataset.get("id") or self._tr("导入 {number}", number=index + 1)
            label += self._tr(" [合成，非实测]" if dataset.get("synthetic") else " [导入，来源待核实]")
            self.curve.overlays.append((str(label), deepcopy(dataset.get("rows") or [])))
        self.curve.caption = self._tr("P—w | {zero} | 点击吸附到真实接受步", zero=self.zero_combo.currentText())
        self.curve.update()
        self._refresh_criterion_curve()

    def _refresh_criterion_curve(self) -> None:
        """Plot saved criterion ratios with real identities, leaving unavailable gaps."""
        physical = self._prepared.get("compression_criterion", {}).get("mode") == "physical_gauge_mean_v1"
        records, point_records, history_records = [], [], []
        for step in self._steps:
            current = (step.get("gauge") or {}).get("current") or {}
            historical = (step.get("gauge") or {}).get("historical") or {}
            point = step.get("_point_route_c_diagnostic") or {}

            def ratio(window: dict[str, Any]) -> float | None:
                value, limit = window.get("compression_maximum"), window.get("threshold")
                return value / limit if _number(value) and _number(limit) and limit > 0 else None

            base = {"w": step.get("control_displacement"), "identity": step.get("identity", {}),
                    "sequence": step.get("sequence"), "phase": step.get("phase"),
                    "branch": step.get("phase")}
            records.append({**base, "P": ratio(current) if physical else point.get("ratio"),
                            **({"gauge": deepcopy(step["gauge"])} if "gauge" in step else {})})
            point_records.append({**base, "P": point.get("ratio")})
            history_records.append({**base, "P": ratio(historical)})
        self.criterion_curve.records = records
        self.criterion_curve.overlays = ([(self._tr('接受历史窗口最大值（保留原窗口身份）'), history_records),
                                          (self._tr('旧点 Route-C（仅已读取完整边缘步；缺失留空，物理模式仅诊断）'), point_records)]
                                         if physical else [])
        self.criterion_curve.caption = self._tr("physical_gauge_mean_v1 | 含自重总应变；未标定建模假设；阈值比 1.000E+00"
                                        if physical else "旧点 Route-C | 仅实际完整边缘历史；缺失留空；阈值比 1.000E+00")
        if not self._prepared:
            self.criterion_curve.caption = self._tr("尚无已接受结果")
        self.criterion_curve.update()

    def _refresh_events(self) -> None:
        self._event_rows = [(event, step) for step in self._steps for event in step.get("events") or []]
        known = {event.get("id") for event, _ in self._event_rows}
        for event in (self._run.get("summary") or {}).get("physical_evidence") or []:
            if event.get("id") not in known:
                step = self._by_id.get(event.get("step_id"))
                if step:
                    self._event_rows.append((event, step))
        _rows(self.event_table, [[event.get("kind"), step.get("sequence"), step.get("phase"), event.get("x"), event.get("eta"),
                                 event.get("section_id"), event.get("point_id"), event.get("P"), event.get("control_displacement"),
                                 event.get("strain"), event.get("reference_strain"), event.get("threshold"), event.get("criterion"),
                                 *[( (event.get("gauge") or {}).get("current") or {}).get(key)
                                   for key in ("mode", "side", "x1", "x2", "signed_mean", "compression_maximum", "reference", "operator_version")],
                                 event.get("localization"), event.get("source")]
                                for event, step in self._event_rows])

    def set_imports(self, datasets: list[dict[str, Any]]) -> None:
        """Display stored imported curves even when their original files are missing."""
        self._imports = deepcopy(datasets or [])
        self._refresh_curve()
        self._refresh_comparison()

    def _refresh_comparison(self) -> None:
        rows: list[list[Any]] = []
        physical_cells: list[tuple[int, int]] = []
        records = [record for record in self._curve_records() if record.get("phase") in ("S0", "SG", "J")]
        for index, dataset in enumerate(self._imports):
            name = dataset.get("name") or dataset.get("id") or self._tr("导入 {number}", number=index + 1)
            source = "合成数据；非实测" if dataset.get("synthetic") else "已导入；对齐与来源需核实"
            try:
                from ..observations import compare_curves
                comparison = compare_curves(deepcopy(records), deepcopy(dataset))
                self._metric_rows(rows, str(name), source, comparison, units=dataset.get("units") or {},
                                  physical_cells=physical_cells)
            except (ImportError, ValueError, TypeError, KeyError, ArithmeticError) as error:
                rows.append([name, source, "对比未完成", str(error)])
        _rows(self.comparison_table, rows, physical_cells=tuple(physical_cells))
        self.comparison_label.setText(self._tr("合成数据只验证导入与对比流程；真实 NC0 试验验证保持待数据。"
                                      "共有区间、覆盖率、误差与事件证据分别报告；不自动平移或峰值归一。"
                                      if self._imports else "尚无导入数据。默认参数不等于真实试验验证。"))

    def _metric_rows(self, rows: list[list[Any]], name: str, source: str, value: Any, prefix: str = "", *,
                     units: dict[str, str] | None = None, physical_cells: list[tuple[int, int]] | None = None) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                self._metric_rows(rows, name, source, item, f"{prefix}.{key}" if prefix else str(key),
                                  units=units, physical_cells=physical_cells)
        elif isinstance(value, list):
            if len(value) <= 12:
                for index, item in enumerate(value):
                    self._metric_rows(rows, name, source, item, f"{prefix}[{index}]", units=units, physical_cells=physical_cells)
            else:
                rows.append([name, source, prefix, self._tr("{count} 条；完整处理链保存在工程中", count=len(value))])
        else:
            unit = _comparison_unit(prefix, units or {})
            rows.append([name, source, f"{prefix} [{unit}]" if unit else prefix, value])
            if unit and physical_cells is not None:
                physical_cells.append((len(rows) - 1, 3))

    def set_summary(self, summary: dict[str, Any] | None) -> None:
        """Preserve actual end reason and separate memory/persisted positions."""
        self._run["summary"] = deepcopy(summary)
        self._refresh_summary()
        self._refresh_events()
        self._rebuild_navigation()

    def _refresh_summary(self) -> None:
        summary = self._run.get("summary")
        lines = []
        exact_lines: list[str] = []
        if summary:
            reasons = {"reference_compression": "达到旧点式 Route-C 参考压缘判据",
                       "physical_gauge_compression": "达到固定物理窗口总压应变判据（未标定建模假设）", "safety_limit": "触发安全限值",
                       "non_convergence": "数值不收敛", "linear_failure": "线性系统求解失败",
                       "localization_failure": "事件定位失败", "cancelled": "用户取消",
                       "input_error": "输入错误", "storage_error": "存储错误", "internal_error": "内部错误"}
            termination = summary.get("termination")
            lines = [self._tr("结束原因：{reason}", reason=self._tr(reasons.get(termination, str(termination)))), str(summary.get("message", "")),
                     self._tr("内存最后接受步：{sequence} / {identity}", sequence=summary.get('memory_last_step'), identity=summary.get('memory_last_step_id')),
                     self._tr("文件最后保存步：{sequence} / {identity}", sequence=summary.get('persisted_last_step'), identity=summary.get('persisted_last_step_id')),
                     self._tr("物理判据已达到：{value}", value=self._tr(_text(summary.get('physical_criterion_reached')))),
                     self._tr("收到取消请求：{value}", value=self._tr(_text(summary.get('cancellation_requested')))), "", self._tr("验收状态（一次运行不会自动认证）：")]
            lines += [f"{key}: {value}" for key, value in (summary.get("validation_status") or {}).items()]
            lines += ["", self._tr("结束诊断：")]
            for diagnostic in summary.get("diagnostics") or []:
                lines.append(self._diagnostic_line(diagnostic))
        else:
            lines = [self._tr("尚无已保存结束结论。"), self._tr("运行状态：{status}", status=self._tr(str(self._run.get("run_status", "运行中或尚未启动")))),
                     self._tr("中断工程不伪造成功或压碎结论。")]
        for diagnostic in self._run.get("diagnostics") or []:
            lines.append(self._diagnostic_line(diagnostic))
        descriptor = self._prepared.get("compression_criterion")
        if descriptor is not None:
            lines += ["", self._tr("所选结构判据（显示零点 / 传感器不改变此定义）："),
                      *_evidence_lines(descriptor, self._language)]
            exact_lines += _evidence_lines(descriptor, self._language, exact=True)
        else:
            lines += ["", self._tr("旧结果未保存显式判据描述；保留原点式语义，不补入物理窗口。")]
        if self._run.get("unavailable_steps"):
            lines.append(self._tr("缺失 / 损坏步：{steps}", steps=self._run["unavailable_steps"]))
        step = self._by_id.get(self._selection.get("step_id", ""))
        if step:
            lines += ["", self._tr("当前接受步 {sequence} / {phase} 的收敛证据：", sequence=step.get('sequence'), phase=_phase(self._language, step.get('phase')))]
            search_labels = {"line_search_evaluations": "线搜索实际评估次数",
                             "line_search_backtracks": "回溯次数", "last_correction_factor": "最后实际修正因子",
                             "minimum_correction_factor": "最小实际修正因子"}
            for key, value in (step.get("convergence") or {}).items():
                label = self._tr(search_labels.get(key, key))
                if key in _PHYSICAL_UNITS:
                    label += f" [{_PHYSICAL_UNITS[key]}]"
                    lines.append(f"{label}: {_physical_text(value, self._language)}")
                    exact_lines.append(f"{label}: {_physical_text(value, self._language, exact=True)}")
                else:
                    lines.append(f"{label}: {self._tr(_text(value))}")
            transition = step.get("transition")
            if transition is None:
                lines += ["", self._tr("旧结果未保存非终止转折定位证据；未重新生成。")]
            elif transition.get("active"):
                name = {"tensile_onset": "首次拉裂", "compression_peak": "首次压缩峰值（非压碎）"}.get(
                    transition.get("kind"), transition.get("kind"))
                lines += ["", self._tr("非终止转折定位：{name}；完整夹逼与端点证据见“转折定位”页。", name=self._tr(str(name)))]
            localization = step.get("localization") or {}
            if localization.get("active"):
                lines += ["", self._tr("所选结构判据终止定位证据（控制位移 mm，重力因子独立）：")]
                lines += _evidence_lines(localization, self._language)
                exact_lines += _evidence_lines(localization, self._language, exact=True)
        self.summary_text.setPlainText("\n".join(lines))
        self.summary_text.setToolTip("\n".join(exact_lines))

    @staticmethod
    def _diagnostic_line(diagnostic: dict[str, Any]) -> str:
        return f"[{diagnostic.get('severity', 'unknown')}] {diagnostic.get('code', '')} " \
               f"{diagnostic.get('stage', '')} / {diagnostic.get('object_id', '')} / " \
               f"{diagnostic.get('field', '')}: {diagnostic.get('message', '')}"

    def append_progress(self, progress: dict[str, Any]) -> None:
        """Append genuine phases/iterations; never infer a final step count or percent."""
        owner = self._prepared.get("identity", {})
        identity = progress.get("identity", {})
        if owner and identity and not same_run(owner, identity):
            return
        self._progress_records.append(deepcopy(progress))
        if len(self._progress_records) > 5000:
            del self._progress_records[:-5000]
        line = self._progress_line(progress)
        self.log.appendPlainText(line)
        self.log.setToolTip(self._progress_tooltip(progress))
        self._log_lines.append(line)
        if len(self._log_lines) > 5000:
            del self._log_lines[:-5000]

    def _progress_line(self, progress: dict[str, Any]) -> str:
        stages = {"transition": "首次软化转折定位（非终止）", "localization": "所选结构终止判据定位",
                  "preparation": "准备", "prepare": "准备", "gravity": "自重加载", "jack": "千斤顶加载",
                  "equilibrium": "平衡", "loading": "加载", "persistence": "保存"}
        stage = str(progress.get("stage", ""))
        return self._tr("[{sequence}] {stage} | 已接受步 {accepted} | 迭代 {iteration} | 减半 {halvings} | w={w} mm | P={P} N",
                        sequence=progress.get('sequence', '?'), stage=self._tr(stages.get(stage, stage)), accepted=progress.get('accepted_steps', '?'),
                        iteration=progress.get('iteration', '?'), halvings=progress.get('halvings', '?'),
                        w=_physical_text(progress.get('control_displacement'), self._language),
                        P=_physical_text(progress.get('P'), self._language)) + f" | {progress.get('message', '')}"

    def _progress_tooltip(self, progress: dict[str, Any]) -> str:
        return (f"w [mm]={_physical_text(progress.get('control_displacement'), self._language, exact=True)}\n"
                f"P [N]={_physical_text(progress.get('P'), self._language, exact=True)}")

    def _request(self, step: dict[str, Any], **extra: Any) -> None:
        self.follow.setChecked(False)
        selection = self._selection_for(step, **extra)
        self.set_selection(selection)
        self.selectionRequested.emit(deepcopy(selection))

    def _navigate(self, direction: int) -> None:
        if not self._steps:
            return
        current = self._by_id.get(self._selection.get("step_id", ""))
        index = next((i for i, step in enumerate(self._steps) if step is current), len(self._steps) - 1)
        self._request(self._steps[max(0, min(len(self._steps) - 1, index + direction))])

    def _sequence_requested(self, sequence: int) -> None:
        if self._updating:
            return
        step = self._by_sequence.get(sequence)
        if step:
            self._request(step)
        else:
            self._show_unavailable(sequence)

    def _follow_changed(self, checked: bool) -> None:
        if checked and self._steps and not self._updating:
            selection = self._selection_for(self._steps[-1])
            self.set_selection(selection)
            self.selectionRequested.emit(selection)

    def _curve_requested(self, record: dict[str, Any]) -> None:
        step = self._by_id.get(record.get("identity", {}).get("step_id", ""))
        if step:
            self._request(step)

    def _section_requested(self, index: int) -> None:
        if self._updating or index < 0:
            return
        step = self._by_id.get(self._selection.get("step_id", ""))
        if step:
            section_id = self.section_combo.itemData(index)
            self._request(step, section_id=section_id, object_id=section_id)

    def _point_requested(self, row: int, column: int) -> None:
        step = self._by_id.get(self._selection.get("step_id", ""))
        if step and 0 <= row < len(self._point_rows):
            point = self._point_rows[row]
            self._request(step, section_id=self._section_id, point_id=point.get("id"), object_id=point.get("id"))

    def _event_requested(self, row: int, column: int) -> None:
        if 0 <= row < len(self._event_rows):
            event, step = self._event_rows[row]
            self._request(step, section_id=event.get("section_id"), point_id=event.get("point_id"), object_id=event.get("point_id"))

    def _node_requested(self, row: int, column: int) -> None:
        step = self._by_id.get(self._selection.get("step_id", ""))
        nodes = self._prepared.get("nodes") or []
        if step and 0 <= row < len(nodes):
            self._request(step, object_id=nodes[row].get("id"))

    def _element_requested(self, row: int, column: int) -> None:
        step = self._by_id.get(self._selection.get("step_id", ""))
        items = (step or {}).get("element_forces") or []
        if step and 0 <= row < len(items):
            self._request(step, object_id=items[row].get("id"))

    def _zero_changed(self, index: int) -> None:
        if self._updating:
            return
        if index == 2:
            self._reference_id = self._selection.get("step_id", "")
        definition = deepcopy(self._prepared.get("document", {}).get("observations") or {})
        definition["display_mode"] = ("increment", "total", "reference")[index]
        if index == 2:
            definition["reference_phase"] = "Sref"
            definition["reference_step_id"] = self._reference_id
        elif index == 0:
            definition["reference_phase"] = "SG" if self._prepared.get("document", {}).get("model", {}).get("gravity", {}).get("enabled") else "S0"
            definition["reference_step_id"] = ""
        self.zeroChanged.emit(definition)
        step = self._by_id.get(self._selection.get("step_id", ""))
        if step:
            self._show_observations(step)
        self._refresh_curve()
        self._refresh_comparison()

    def export_png(self, path: str) -> None:
        """Export plotted physical data with selected identity, units and display zero."""
        target = Path(path)
        if target.exists():
            raise ContractError("export_exists", "Choose a new PNG path; existing files are preserved", stage="export")
        if target.suffix.lower() != ".png":
            raise ContractError("export_path", "PNG output requires a .png suffix", stage="export")
        if not target.parent.is_dir():
            raise ContractError("export_directory", "PNG parent directory does not exist", stage="export")
        image = QImage(1500, 1000, QImage.Format.Format_ARGB32)
        image.fill(QColor(colors(self._theme)["plot"]))
        painter = QPainter(image)
        try:
            self.curve.draw(painter, QRectF(0, 0, 1500, 780), interactive=False)
            painter.setPen(QColor(colors(self._theme)["text"]))
            identity = self._prepared.get("identity", {})
            lines = [self._tr("LumaFEM | 二维纤维梁计算结果 | 数值倍率 1.000E+00"),
                     f"project={identity.get('project_id')}  revision={identity.get('revision_id')}",
                     f"snapshot={identity.get('snapshot_id')}  run={identity.get('run_id')}",
                     f"step={self._selection.get('step_id')} | sequence={self._selection.get('sequence')} | phase={self._selection.get('phase')}",
                     f"P [N], w [mm] | {self.zero_combo.currentText()} | reference={self._reference_id or 'SG/S0'}",
                     self._tr("合成曲线不是真实 NC0 试验数据；未认证物理验证通过。")]
            painter.drawText(QRectF(35, 805, 1430, 180), Qt.TextFlag.TextWordWrap, "\n".join(lines))
        finally:
            painter.end()
        encoded = QByteArray()
        buffer = QBuffer(encoded)
        if not buffer.open(QIODevice.OpenModeFlag.WriteOnly):
            raise ContractError("export_png_failed", "Could not allocate PNG buffer", stage="export")
        try:
            if not image.save(buffer, "PNG"):
                raise ContractError("export_png_failed", "Could not encode result PNG", stage="export")
        finally:
            buffer.close()
        try:
            with target.open("xb") as destination:
                destination.write(bytes(encoded))
        except OSError as exc:
            raise ContractError("export_png_failed", str(exc), stage="export") from exc


__all__ = ["ResultsPanel"]
