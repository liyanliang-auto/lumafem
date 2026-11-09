"""Session-only presentation language and bundled Qt button translations.

Catalog keys are original source templates. Engineering payloads, identifiers,
units and machine diagnostics are never passed through a translation process.
"""
from __future__ import annotations

from typing import Any

from PySide6.QtCore import QObject, QTranslator, Signal
from PySide6.QtWidgets import QApplication

__all__ = ["LanguageManager", "tr", "language", "source_key"]

_language = "zh_CN"
_EN = {
    "未核实": "Unverified", "已确认": "Confirmed",
    "尚无已保存运行": "No saved run available",
    "力": "Force", "位移": "Displacement", "时间": "Time", "应变": "Strain", "曲率": "Curvature",
    "工程": "File", "编辑": "Edit", "分析": "Analysis", "数据与导出": "Data and Export", "帮助": "Help", "视图": "View",
    "新建 NC0 默认工程": "New NC0 default project", "打开工程…": "Open project...", "保存": "Save", "另存为…": "Save As...",
    "创建一致备份…": "Create consistent backup...", "打开恢复副本…": "Open recovery copy...", "退出": "Exit",
    "应用表单": "Apply properties", "还原表单": "Revert properties", "撤销已应用修改": "Undo applied edit", "重做": "Redo",
    "检查模型": "Check model", "运行分析": "Submit job", "取消分析": "Cancel job", "选择历史运行…": "Choose saved run...",
    "导入试验格式 CSV…": "Import measurement CSV...", "导入合成通道示例…": "Import synthetic example...",
    "导出数值 CSV…": "Export numerical CSV...", "导出曲线 PNG…": "Export curve PNG...", "导出视口 PNG…": "Export viewport PNG...",
    "关于与能力边界": "About and scope", "常用操作": "Common actions", "模块": "Module", "界面语言": "Interface language",
    "几何": "Geometry", "材料": "Materials", "截面与配筋": "Section and Reinforcement", "分析步": "Step",
    "荷载与约束": "Loads and Boundary Conditions", "网格": "Mesh", "作业": "Job", "结果": "Results",
    "模型": "Model", "模型树 / 结果树": "Model / Results tree", "属性与检查": "Properties and checks",
    "结果与作业": "Results and job", "作业监视": "Job monitor", "消息": "Messages", "恢复默认布局": "Restore standard layout",
    "适合窗口": "Fit view", "平面视图": "Front view", "清除选择": "Clear selection", "选择与检查": "Select and inspect",
    "当前模型 · 检查预览": "Current model / checked preview", "已选运行 · 真实结果": "Selected run / actual results",
    "切换模型预览与冻结结果": "Switch current model and frozen results", "尚未打开工程": "No project open",
    "未命名工程": "Untitled project", "就绪": "Ready", "运行中": "Running", "取消待处理": "Cancellation requested",
    "结果属于旧模型修订": "Results belong to an older model revision", "未检查模型": "Model not checked",
    "当前模型已检查": "Current model checked", "冻结结果浏览": "Frozen result inspection", "历史快照只读": "Historical snapshot is analysis read-only",
    "所选帧不可用；已清除视口结果。": "Selected frame unavailable; viewport results cleared.",
    "已选择；当前显示无对应几何。": "Selected; no matching geometry in this display.",
    "自重平衡": "Self-weight equilibrium", "两点加载": "Two-point loading", "参考压缘事件定位": "Compression-event localization",
    "首次软化转折定位（非终止）": "First softening transition localization (nonterminal)", "计算": "Analysis",
    "达到旧点参考压缘判据": "Legacy point compression criterion reached", "达到固定物理压缘窗口平均应变判据": "Physical edge-gauge mean compression criterion reached",
    "已取消": "Cancelled", "存储失败": "Storage failure", "数值不收敛": "Nonconvergence", "事件定位失败": "Event localization failure",
    "达到安全限值": "Safety limit reached", "内部错误": "Internal error", "未结束 / 中断": "Unfinished / interrupted",
    "分析结束：{reason}": "Analysis ended: {reason}", "{stage} · 已接受 {accepted} 步 · 迭代 {iteration}": "{stage} / accepted {accepted} / iteration {iteration}",
    "{status} · 内存步 {memory} / 文件步 {saved}": "{status} / memory step {memory} / persisted step {saved}",
    "运行结果 · {status}": "Run results / {status}", "当前模型预览 · {status}": "Current model preview / {status}",
    "已检查，显示实际离散": "Checked; actual discretization displayed", "请先应用并检查模型": "Apply and check the model first",
    "操作未完成": "Operation incomplete", "操作未完成；原始诊断见消息详情。": "Operation incomplete; original diagnostic is available in message details.",
    "表单尚未应用": "Unapplied property edits", "先应用当前表单，还是丢弃这部分草稿？": "Apply the current properties, discard these draft edits, or cancel?",
    "保存当前工程": "Save current project", "当前工程有未保存内容。是否先保存？": "The current project has unsaved content. Save it first?",
    "打开工程": "Open project", "LumaFEM 工程 (*.lumafem)": "LumaFEM project (*.lumafem)", "另存为工程": "Save project As",
    "保存已持久化内容的备份": "Back up persisted content", "恢复工程 (*.lumafem)": "Recovery project (*.lumafem)", "选择恢复副本": "Choose recovery copy",
    "已创建一致备份；未应用草稿不在此备份中。": "Consistent backup created; unapplied draft edits are excluded.",
    "备份不能覆盖导入的原始数据。": "Backup cannot overwrite imported source data.",
    "模型检查通过；假设和来源说明见检查报告。": "Model check passed; see check report for assumptions and provenance.",
    "模型检查未通过，请定位检查报告。": "Model check failed; locate the issue in the check report.",
    "历史运行": "Saved runs", "当前工程尚无保存的运行。": "The current project has no saved runs.", "选择历史运行": "Choose saved run",
    "每个运行绑定其原始输入快照": "Each run retains its original input snapshot", "运行 {number} · {status} · 最后步 {last}": "Run {number} / {status} / last step {last}",
    "选择原始 CSV": "Choose source CSV", "CSV 数据 (*.csv *.txt *.tsv);;所有文件 (*)": "CSV data (*.csv *.txt *.tsv);;All files (*)",
    "已导入通道和处理记录；合成或未核实数据不表示物理验证通过。": "Channels and processing record imported; synthetic or unverified data do not establish physical validation.",
    "合成示例文件缺失，请先运行本地数据准备脚本。": "Synthetic example is missing; run the local input provisioning script first.",
    "荷载—挠度曲线": "Load-displacement curve", "事件": "Events", "收敛与定位诊断": "Convergence and localization diagnostics",
    "所选步纤维": "Selected-frame fibers", "所选步边缘": "Selected-frame edges", "所选步截面": "Selected-frame sections",
    "所选步单元内力": "Selected-frame element forces", "所选步观测": "Selected-frame observations", "导入曲线对比": "Imported curve comparison",
    "导出数值": "Export numerical data", "导出内容": "Export content", "选择新的 CSV 路径": "Choose new CSV path", "选择新的 PNG 路径": "Choose new PNG path",
    "请选择新的 PNG 路径，以保留已有文件。": "Choose a new PNG path to preserve existing files.", "已导出带有身份和单位的 PNG。": "PNG exported with identity and units.",
    "二维平面纤维梁计算 · Qt＋VTK 三维外观\n小变形、完全粘结、无剪切变形；箍筋仅作档案与外观。\n参考压缘判据与真实试验压碎不是同一证据。\n默认材料及合成数据未标定；A/B/C/D 验收须依据独立证据分别报告。\n保存重开支持结果浏览，不承诺任意步续算。": "Planar fiber-beam analysis / Qt + VTK three-dimensional appearance\nSmall deformation, perfect bond, no shear deformation; stirrups are appearance/archive only.\nThe compression criterion is distinct from measured crushing evidence.\nDefault materials and synthetic data are uncalibrated; report A/B/C/D separately using independent evidence.\nSaved results can be browsed; restarting from arbitrary frames is unsupported.",
    "分析仍在运行": "Analysis is running", "请求取消并在完整接受/保存边界后关闭？": "Request cancellation and close after a complete acceptance/persistence boundary?",
    "本地默认输入缺失。请先运行 scripts/provision_local_data.py 或指定 --case-dir。": "Local default input is missing. Run scripts/provision_local_data.py or provide --case-dir.",
    "默认工程必须为对象": "Default project must be an object", "未知错误": "Unknown error",
    "导入 CSV · 通道、单位与零点": "Import CSV / channels, units and zero", "重新预览原始文件": "Refresh source preview",
    "先核对原始列，再明确单位、符号和标定。导入保留原始顺序；不自动平移或峰值归一。": "Check source columns, units, signs and calibration. Original order is retained; no automatic offset or peak normalization.",
    "自动识别分隔符": "Detect delimiter", "逗号": "Comma", "制表符": "Tab", "分号": "Semicolon", "竖线": "Vertical bar", "编码": "Encoding",
    "原始预览": "Source preview", "增加应变 / 曲率通道": "Add strain / curvature channel", "通道映射": "Channel mapping",
    "压力单位必须填写力标定多项式：c0,c1,…；输出单位为 N 或 kN，并记录标定依据。": "Pressure channels require force-calibration coefficients c0,c1,..., output N or kN and a calibration reference.",
    "物理通道": "Physical channel", "原始列（留空不导入）": "Source column (blank excludes)", "原始单位": "Source unit", "符号": "Sign", "输出偏置": "Output offset",
    "压力标定系数": "Pressure calibration coefficients", "标定输出单位": "Calibration output unit", "标定依据": "Calibration reference", "物理量": "Quantity",
    "两千斤顶合计 P1＋P2": "Two-jack total P1+P2", "独立总荷载 P": "Independent total load P", "保留仪器零点": "Retain instrument zero",
    "0 为第一行数据；-1 不减去任何参考行。": "0 is the first data row; -1 subtracts no reference row.",
    "按两支点连线修正跨中位移（需两支点通道）": "Correct midspan displacement relative to the support line (both support channels required)",
    "仅在明确同步依据后填写；不会自动同步": "Enter only with an explicit synchronization reference; no automatic synchronization",
    "预先冻结的 w 下限,上限 [mm]；留空使用实际有效范围": "Predefined w lower,upper bounds [mm]; blank uses actual valid range",
    "已核对加载段、单位、零点与时间对应关系": "Loading segment, units, zero and time alignment checked",
    "此数据参与材料标定（不能充当独立预测验证）": "Data used for material calibration (not independent prediction validation)",
    "此文件为合成 / 默认演示数据": "Synthetic / default demonstration data", "默认读取同名 .metadata.json；可填写明确元数据路径": "Read matching .metadata.json by default; an explicit metadata path is allowed",
    "我已核对映射与单位，并同意保存这套处理记录": "I checked mapping and units and agree to retain the processing record",
    "总荷载来源": "Total-load source", "参考数据行（从0开始）": "Reference data row (zero-based)", "沉降处理": "Settlement correction",
    "控制点 x [mm]": "Control-point x [mm]", "两支点 x [mm]": "Support x positions [mm]", "时间倍率": "Time scale", "时间偏移 [s]": "Time offset [s]",
    "同步依据": "Synchronization reference", "比较有效位移区间": "Comparison displacement interval", "观测口径状态": "Observation-reference status", "对齐说明": "Alignment",
    "校准身份": "Calibration role", "来源分类": "Source classification", "元数据": "Metadata", "确认处理": "Confirm processing",
    "零点、沉降与同步": "Zero, settlement and synchronization", "原始行": "Source row", "P与双通道差 [N]": "P minus two-channel total [N]", "有效": "Valid",
    "处理后预览": "Processed preview", "尚未验证映射": "Mapping not validated", "保存映射并导入": "Save mapping and import", "预览转换结果": "Preview conversion",
    "原始 {rows} 行；缺失单元格 {missing}。列名建议尚未确认单位。": "{rows} source rows; {missing} missing cells. Suggested column names do not confirm units.",
    "不导入": "Exclude", "通道名称必须非空且不能重复": "Channel names must be nonempty and unique", "偏置": "Offset", "标定系数": "Calibration coefficient",
    "时间偏移": "Time offset", "时间修正需填写明确的同步依据": "Time correction requires an explicit synchronization reference", "控制点坐标": "Control-point position",
    "支点坐标": "Support position", "比较区间": "Comparison interval", "比较区间需填写递增的两个位移值": "Comparison interval requires two increasing displacements",
    "合成演示，非真实试验": "Synthetic demonstration; not a real experiment", "导入来源待核实": "Imported source unverified",
    "{source}；{rows} 行。{notes} 真实物理验证不会因导入自动通过。": "{source}; {rows} rows. {notes} Import does not establish physical validation.",
    "无法导入：{error}": "Cannot import: {error}", "请修正导入设置": "Correct import settings", "核对处理记录": "Check processing record",
    "请在“零点、沉降与同步”页确认映射和单位。": "Confirm mapping and units on the Zero, settlement and synchronization page.",
    "{label}必须是有限数值": "{label} must be finite", "缺失": "Missing",
    "选择梁对象并编辑尺寸；应用后检查模型。": "Select the beam and edit dimensions; apply and check the model.",
    "编辑材料物理参数并保留来源与假设标记。": "Edit physical material parameters while retaining source and assumption marks.",
    "编辑截面与纵向钢筋；箍筋仅作外观与档案。": "Edit section and longitudinal reinforcement; stirrups are appearance/archive only.",
    "检查自重后两点加载、控制与终止判据；高级求解参数在高级页。": "Inspect self-weight then two-point loading, control and termination; solver controls are on Advanced.",
    "选择真实支座、两点荷载或自重对象。": "Select actual supports, two-point loads or self-weight.",
    "编辑离散设置并检查实际节点、单元和截面。": "Edit discretization and inspect actual nodes, elements and sections.",
    "提交一个后台作业；取消在安全接受与保存边界生效。": "Submit one background job; cancellation takes effect at a safe acceptance/persistence boundary.",
    "选择真实运行和已接受帧；曲线与视口联动。": "Select a real run and accepted frame; curve and viewport selection are linked.",
    "原始诊断与身份详情": "Original diagnostics and identity details", "尚无作业进度": "No job progress", "阶段": "Phase", "已接受步": "Accepted step",
    "已保存步": "Persisted step", "作业状态": "Job status", "终止原因": "Termination", "提交作业": "Submit job", "取消作业": "Cancel job",
    "默认参数未标定；原严格科学标准仍失败，真实试验 C 未验证。": "Default parameters are uncalibrated; original STRICT scientific gates remain failed and real experiments C remain unverified.",
}


def language() -> str:
    """Return the current session presentation language."""
    return _language


def tr(source: str, **values: Any) -> str:
    """Translate a declared source template, never a stored payload."""
    template = _EN.get(source, source) if _language == "en" else source
    return template.format(**values) if values else template


def source_key(caption: str) -> str | None:
    """Resolve only a declared static catalog caption when binding a dialog.

    This is used once at construction to retain source keys for static Qt
    controls. Unknown source columns, user text and diagnostics are excluded.
    Dynamic text uses explicit source templates and arguments instead.
    """
    if caption in _EN:
        return caption
    return next((key for key, value in _EN.items() if caption == value), None)


class _QtButtons(QTranslator):
    """Small bundled standard-control catalog, independent of Qt .qm assets."""

    _ZH = {"OK": "确定", "&OK": "确定", "Cancel": "取消", "&Cancel": "取消", "Apply": "应用", "&Apply": "应用",
           "Discard": "放弃", "&Discard": "放弃", "Save": "保存", "&Save": "保存", "Open": "打开", "&Open": "打开",
           "Yes": "是", "&Yes": "是", "No": "否", "&No": "否", "Close": "关闭", "&Close": "关闭", "Reset": "重置",
           "Help": "帮助", "Restore Defaults": "恢复默认值", "Save All": "全部保存", "Abort": "中止", "Retry": "重试", "Ignore": "忽略",
           "Look in:": "查找范围：", "File &name:": "文件名：", "Files of type:": "文件类型：", "File name:": "文件名：",
           "All Files (*)": "所有文件 (*)", "Back": "返回", "Forward": "前进", "Parent Directory": "上级目录", "Create New Folder": "新建文件夹",
           "List View": "列表视图", "Detail View": "详细视图", "Name": "名称", "Size": "大小", "Type": "类型", "Date Modified": "修改日期",
           "Show ": "显示 ", "Hide ": "隐藏 ", "Show Details...": "显示详情…", "Hide Details...": "隐藏详情…"}

    def translate(self, context: str, sourceText: str, disambiguation: str | None = None, n: int = -1) -> str:
        return self._ZH.get(sourceText, sourceText) if _language == "zh_CN" else sourceText


class LanguageManager(QObject):
    """Coordinate session language without touching engineering preferences."""

    languageChanged = Signal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._translator = _QtButtons(self)
        app = QApplication.instance()
        if app is not None:
            app.installTranslator(self._translator)
        self.set_language("zh_CN")

    @property
    def language(self) -> str:
        return language()

    def set_language(self, value: str) -> None:
        global _language
        if value not in ("zh_CN", "en"):
            raise ValueError("Unsupported interface language: " + str(value))
        _language = value
        app = QApplication.instance()
        if app is not None:
            app.removeTranslator(self._translator)
            app.installTranslator(self._translator)
        self.languageChanged.emit(value)
