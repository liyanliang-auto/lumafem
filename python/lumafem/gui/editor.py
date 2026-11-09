"""Value-owned model draft editing and read-only preparation inspection.

ModelEditor never prepares a model or changes a result history. Apply publishes a
deep copy of the entire editable document; the controller validates and revises it.
Numeric text uses round-trip precision rather than a display spin-box precision.
"""
from __future__ import annotations

from copy import deepcopy
from math import isfinite
import re
from typing import Any

from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QFormLayout, QGroupBox,
    QHBoxLayout, QHeaderView, QInputDialog, QLabel, QLineEdit, QMenu, QMessageBox,
    QPushButton, QScrollArea, QSplitter, QTabWidget, QTreeWidget,
    QTreeWidgetItem, QVBoxLayout, QWidget,
)

from ..contracts import (DEFAULT_LENGTH_MAPPING, LEGACY_LENGTH_MAPPING,
                         REVISED_ANALYSIS_DEFAULTS, CR_NUM_002_COMPRESSION_CRITERION, new_id, validate_finite)
from .themes import colors, palette

_ROLE = Qt.ItemDataRole.UserRole
_LABELS = {
    "numerical_revision": "数值修订记录",
    "compression_criterion": "压缘终止判据", "gauge_length_mm": "固定物理窗口长度",
    "threshold": "压缩平均应变阈值（正幅值）",
    "name": "工程名称", "schema_version": "文件格式版本", "project_id": "工程身份",
    "revision_id": "模型修订身份", "model": "梁模型", "version": "版本",
    "geometry": "梁几何", "length": "全长", "width": "截面宽度", "height": "截面高度",
    "angle": "梁轴倾角", "concrete_material_id": "混凝土材料",
    "reinforcement": "纵筋与箍筋", "mode": "配筋输入方式", "cover_definition": "保护层口径",
    "cover": "保护层厚度", "stirrup_diameter": "箍筋直径", "stirrup_spacing": "箍筋间距",
    "top_count": "上纵筋数量", "bottom_count": "下纵筋数量", "top_diameter": "上纵筋直径",
    "bottom_diameter": "下纵筋直径", "top_material_id": "上纵筋材料", "bottom_material_id": "下纵筋材料",
    "bars": "直接坐标纵筋", "diameter": "直径", "area": "面积", "eta": "截面竖坐标 η",
    "zeta": "截面横坐标 ζ", "group": "所属纵筋组", "material_id": "材料",
    "supports": "支座", "loads": "两点比例荷载", "x": "沿梁位置", "constrained": "约束自由度",
    "values": "约束规定值", "reference": "参考荷载 / 来源说明", "control_x": "控制点位置",
    "control_component": "控制自由度", "control_direction": "控制正向",
    "gravity": "自重", "enabled": "计入自重", "density": "整体等效密度",
    "acceleration": "重力加速度", "discretization": "网格与积分", "mesh": "网格方案",
    "target_length": "目标单元长度", "gauss_points": "沿梁积分点数", "layers": "混凝土纤维层数",
    "length_mapping": "特征长度映射版本", "analysis": "求解与容差",
    "initial_step": "初始位移步长", "minimum_step": "最小位移步长", "maximum_step": "最大位移步长",
    "maximum_iterations": "每步最大迭代数", "maximum_halvings": "最大减半次数",
    "growth_consecutive": "增步前连续快收敛步数", "growth_iteration_limit": "快收敛迭代上限",
    "growth_factor": "增步系数", "gravity_steps": "自重名义步数",
    "maximum_accepted_steps": "最大接受步数", "maximum_control_displacement": "控制位移安全限值",
    "maximum_localization_solves": "事件定位候选上限", "reference_length": "参考长度",
    "line_search_enabled": "启用牛顿回溯线搜索", "maximum_line_search_trials": "每次修正的线搜索试算上限",
    "line_search_reduction": "线搜索缩减因子", "line_search_sufficient_decrease": "线搜索充分下降系数",
    "minimum_line_search_factor": "最小修正因子", "transition_resolution_enabled": "分辨首次软化转折",
    "maximum_transition_solves": "首次转折定位候选上限", "transition_displacement_tolerance": "首次转折位移夹逼容差",
    "transition_ratio_tolerance": "首次转折归一超越容差", "transition_minimum_step": "转折定位专用最小增量",
    "integration_length": "高斯积分代表长度", "lch_t": "受拉材料特征长度", "lch_c": "受压材料特征长度",
    "numerics_contract": "数值契约版本", "kernel_version": "核心版本",
    "reference_displacement": "位移缩放参考值", "reference_force": "力缩放参考值",
    "residual_absolute": "力残差绝对容差", "residual_relative": "残差相对容差",
    "correction_absolute": "位移修正绝对容差", "correction_relative": "位移修正相对容差",
    "control_absolute": "控制误差绝对容差", "control_relative": "控制误差相对容差",
    "backward_tolerance": "线性后向误差容限", "event_displacement_tolerance": "事件夹逼位移容差",
    "event_strain_absolute": "事件应变绝对容差", "event_strain_relative": "事件应变相对容差",
    "materials": "材料包", "id": "对象身份", "kind": "本构类型", "source": "来源与质量标记",
    "category": "来源类别", "quality": "质量 / 不确定性", "fc": "抗压峰值强度（正幅值）",
    "e0": "压缩峰值应变（正幅值）", "fr": "残余压应力（正幅值）", "ft": "抗拉强度",
    "Gft": "受拉断裂能", "Gcp": "受压峰值至残余点能量", "unload_ratio": "混凝土卸载比",
    "crush_reference_strain": "参考压缘终止应变", "E": "弹性模量", "fy": "钢筋屈服强度",
    "b": "钢筋屈服后切线比", "observations": "观测定义", "reference_phase": "观测参考阶段",
    "reference_step_id": "指定参考步身份", "downward_positive": "挠度向下为正",
    "settlement_correction": "相对支座连线修正", "gauges": "虚拟引伸计",
    "x1": "标距起点", "x2": "标距终点", "sources": "工程来源与建模假设",
}
_ENUMS = {
    "mode": [("保护层驱动", "cover"), ("直接钢筋坐标", "direct")],
    "cover_definition": [("至箍筋外缘", "stirrup_outer")],
    "mesh": [("M100", "M100"), ("M50", "M50"), ("M25", "M25"), ("自定义", "custom")],
    "gauss_points": [("2", 2), ("3", 3), ("5", 5)],
    "layers": [("20", 20), ("40", 40), ("80", 80)],
    "length_mapping": [("单元长度 v2（CR-NUM-001）", DEFAULT_LENGTH_MAPPING),
                       ("旧 Gauss 权重长度 v1（历史对照）", LEGACY_LENGTH_MAPPING)],
    "control_component": [("竖向位移 uy", 1), ("轴向位移 ux", 0), ("转角 θ", 2)],
    "control_direction": [("负方向（水平梁向下）", -1.0), ("正方向", 1.0)],
    "kind": [("Concrete02 型混凝土", "concrete02"), ("双线性随动硬化钢筋", "steel_bilinear"), ("线弹性校核", "elastic")],
    "category": [("工程假设", "assumption"), ("论文记录", "paper"), ("标定", "calibrated"),
                 ("实测", "measured"), ("合成", "synthetic"), ("派生", "derived")],
    "reference_phase": [("自重平衡态 SG", "SG"), ("初始态 S0", "S0")],
    "group": [("上纵筋", "top"), ("下纵筋", "bottom")],
}
_HINTS = {
    "compression_criterion": "固定物理窗口模式在整梁两侧压缘搜索完整窗口，先平均有符号总应变，再取压缩幅值；包含自重，与显示标距及归零无关。100 mm / 0.0035 是助手建模假设，未经试验标定。旧点模式保留材料参考长度与参考应变；局部压碎在窗口模式中仅作诊断。修改后应用为新修订并重新分析。",
    "reinforcement": "保护层到箍筋外缘；箍筋只作档案和外观显示，不提供剪切或约束增强计算。",
    "bars": "直接坐标模式使用本表。η 向上，ζ 为截面横向；每根钢筋单独保留身份。保护层模式下以实际准备结果为准。",
    "materials": "默认参数仍为待验证基准。先复制材料包再修改标定参数，保留旧结果的输入依据。",
    "geometry": "长度使用 mm，角度使用 rad。二维计算自由度与三维外观显示相互独立。",
    "gravity": "密度为全截面整体等效密度，不再叠加钢筋重量。正式案例先平衡自重，再固定恒载。",
    "loads": "参考向量依次为 Fx、Fy、Mz。P 是两个千斤顶的外加总力，自重不计入 P。",
    "supports": "自由度顺序为 ux、uy、θ；真实支座约束，不增加镜像或虚拟支座。",
    "analysis": "容差和安全限值随分析快照保存。首次拉裂/压缩峰值是非终止转折，不是压碎。旧文件缺失的新选项按旧默认值显示，只有显式修改才写入草稿。",
    "length_mapping": "v2 将单元长度用于材料软化映射；Gauss 权重仍只用于积分。v1 保留历史计算含义，未通过原网格客观性验收。修改后重新生成修订和材料历史，不重映射旧结果。",
    "line_search_enabled": "每次 Newton 仍更新切线；仅对完整位移/荷载系数修正回溯。不放宽平衡、修正和控制收敛容差。",
    "transition_resolution_enabled": "定位全结构首次拉裂和首次压缩峰值，接受并保存真实近阈值步后继续；不为每根纤维生成转折步骤。",
    "transition_minimum_step": "只用于首次转折定位，单位 mm；不替代正常最小位移步长。",
    "observations": "100mm 虚拟标距是观测算子，与路线 C 的参考压应变字段不同。改变零点不改变材料历史。",
    "discretization": "每次修改网格重新分析，不插值继承材料损伤；特征长度与物理标距分开保存。",
    "kind": "切换本构类型后请检查该类型的所有必需参数；未填参数会在模型检查中阻止运行。",
    "E": "混凝土初始模量由 fc、e0 派生，不作为独立混凝土输入。论文钢筋异常模量保留质量标记。",
    "area": "直接坐标钢筋的面积为独立明确输入；改变直径后请同步确认面积，不静默改写。",
    "mesh": "M100/M50/M25 为嵌套基准；自定义方案使用目标单元长度。",
}
_READONLY = {"schema_version", "project_id", "revision_id", "E0", "Ets", "etu", "delta_u",
             "eu", "compression_slope", "delta_critical", "H", "lch_t", "lch_c"}
_ARRAY_LABELS = {
    "compression_criterion": "压缘终止判据", "gauge_length_mm": "固定物理窗口长度",
    "threshold": "压缩平均应变阈值（正幅值）","constrained": ("约束 ux", "约束 uy", "约束 θ"),
                 "values": ("规定 ux", "规定 uy", "规定 θ"),
                 "reference": ("Fx", "Fy", "Mz")}


def _label(key: Any) -> str:
    return _LABELS.get(str(key), str(key))


def _path_text(path: tuple[Any, ...]) -> str:
    return ".".join(str(part) for part in path)


def _unit(path: tuple[Any, ...]) -> str:
    key = str(path[-1]) if path else ""
    if len(path) > 1 and path[-2] == "reference" and isinstance(path[-1], int):
        return "N.mm" if path[-1] == 2 else "N"
    if len(path) > 1 and path[-2] == "values" and isinstance(path[-1], int):
        return "rad" if path[-1] == 2 else "mm"
    if key in {"E", "fc", "fr", "ft", "fy", "E0", "Ets", "H"}:
        return "MPa"
    if key in {"Gft", "Gcp"}:
        return "N/mm"
    if key == "area":
        return "mm²"
    if key == "angle":
        return "rad"
    if key == "density":
        return "kg/m³"
    if key == "acceleration":
        return "m/s²"
    if key in {"reference_force", "residual_absolute"}:
        return "N"
    if key in {"length", "width", "height", "x", "x1", "x2", "eta", "zeta", "cover", "diameter",
               "stirrup_diameter", "stirrup_spacing", "top_diameter", "bottom_diameter", "control_x",
               "target_length", "reference_length", "reference_displacement", "initial_step", "minimum_step",
               "maximum_step", "maximum_control_displacement", "correction_absolute", "control_absolute",
               "event_displacement_tolerance", "transition_displacement_tolerance", "transition_minimum_step",
               "lch_t", "lch_c", "integration_length", "gauge_length_mm"}:
        return "mm"
    return ""


_LABELS_EN = {
    "numerical_revision": "Numerical revision record",
    'compression_criterion': 'Compression criterion',
    'gauge_length_mm': 'Physical gauge length',
    'threshold': 'Mean compression strain threshold',
    'name': 'Project name',
    'schema_version': 'File format',
    'project_id': 'Project identity',
    'revision_id': 'Revision identity',
    'model': 'Beam model',
    'version': 'Version',
    'geometry': 'Beam geometry',
    'length': 'Length',
    'width': 'Section width',
    'height': 'Section height',
    'angle': 'Beam angle',
    'concrete_material_id': 'Concrete material',
    'reinforcement': 'Reinforcement',
    'mode': 'Reinforcement input',
    'cover_definition': 'Cover definition',
    'cover': 'Cover thickness',
    'stirrup_diameter': 'Stirrup diameter',
    'stirrup_spacing': 'Stirrup spacing',
    'top_count': 'Top bar count',
    'bottom_count': 'Bottom bar count',
    'top_diameter': 'Top bar diameter',
    'bottom_diameter': 'Bottom bar diameter',
    'top_material_id': 'Top bar material',
    'bottom_material_id': 'Bottom bar material',
    'bars': 'Direct-coordinate bars',
    'diameter': 'Diameter',
    'area': 'Area',
    'eta': 'Section eta',
    'zeta': 'Section zeta',
    'group': 'Bar group',
    'material_id': 'Material',
    'supports': 'Supports',
    'loads': 'Proportional point loads',
    'x': 'Position along beam',
    'constrained': 'Constrained DOFs',
    'values': 'Prescribed values',
    'reference': 'Reference / source',
    'control_x': 'Control position',
    'control_component': 'Control DOF',
    'control_direction': 'Control direction',
    'gravity': 'Self-weight',
    'enabled': 'Include self-weight',
    'density': 'Equivalent density',
    'acceleration': 'Gravity acceleration',
    'discretization': 'Mesh and integration',
    'mesh': 'Mesh',
    'target_length': 'Target element length',
    'gauss_points': 'Gauss points',
    'layers': 'Concrete fiber layers',
    'length_mapping': 'Characteristic length mapping',
    'analysis': 'Solver and tolerances',
    'initial_step': 'Initial displacement increment',
    'minimum_step': 'Minimum displacement increment',
    'maximum_step': 'Maximum displacement increment',
    'maximum_iterations': 'Maximum iterations per step',
    'maximum_halvings': 'Maximum halvings',
    'growth_consecutive': 'Consecutive fast steps',
    'growth_iteration_limit': 'Fast-iteration limit',
    'growth_factor': 'Increment growth factor',
    'gravity_steps': 'Gravity steps',
    'maximum_accepted_steps': 'Maximum accepted steps',
    'maximum_control_displacement': 'Control displacement safety limit',
    'maximum_localization_solves': 'Event candidate limit',
    'reference_length': 'Reference length',
    'line_search_enabled': 'Newton backtracking',
    'maximum_line_search_trials': 'Line-search trial limit',
    'line_search_reduction': 'Line-search reduction',
    'line_search_sufficient_decrease': 'Sufficient decrease',
    'minimum_line_search_factor': 'Minimum correction factor',
    'transition_resolution_enabled': 'Resolve first softening transitions',
    'maximum_transition_solves': 'Transition candidate limit',
    'transition_displacement_tolerance': 'Transition displacement tolerance',
    'transition_ratio_tolerance': 'Transition ratio tolerance',
    'transition_minimum_step': 'Transition minimum increment',
    'integration_length': 'Gauss integration length',
    'lch_t': 'Tensile characteristic length',
    'lch_c': 'Compression characteristic length',
    'numerics_contract': 'Numerical contract',
    'kernel_version': 'Kernel version',
    'reference_displacement': 'Displacement scale',
    'reference_force': 'Force scale',
    'residual_absolute': 'Absolute force residual tolerance',
    'residual_relative': 'Relative residual tolerance',
    'correction_absolute': 'Absolute correction tolerance',
    'correction_relative': 'Relative correction tolerance',
    'control_absolute': 'Absolute control tolerance',
    'control_relative': 'Relative control tolerance',
    'backward_tolerance': 'Linear backward-error tolerance',
    'event_displacement_tolerance': 'Event displacement tolerance',
    'event_strain_absolute': 'Absolute event strain tolerance',
    'event_strain_relative': 'Relative event strain tolerance',
    'materials': 'Materials',
    'id': 'Object identity',
    'kind': 'Constitutive model',
    'source': 'Source and quality',
    'category': 'Source category',
    'quality': 'Quality / uncertainty',
    'fc': 'Peak compression strength',
    'e0': 'Peak compression strain',
    'fr': 'Residual compression stress',
    'ft': 'Tensile strength',
    'Gft': 'Tensile fracture energy',
    'Gcp': 'Compression softening energy',
    'unload_ratio': 'Concrete unloading ratio',
    'crush_reference_strain': 'Reference compression strain',
    'E': 'Elastic modulus',
    'fy': 'Steel yield strength',
    'b': 'Post-yield tangent ratio',
    'observations': 'Observations',
    'reference_phase': 'Observation reference phase',
    'reference_step_id': 'Reference step identity',
    'downward_positive': 'Downward-positive deflection',
    'settlement_correction': 'Support-line correction',
    'gauges': 'Virtual gauges',
    'x1': 'Gauge start',
    'x2': 'Gauge end',
    'sources': 'Sources and assumptions',
}
_MODULES = (
    ("geometry", "几何", "Geometry"), ("materials", "材料", "Materials"),
    ("section", "截面与配筋", "Section and reinforcement"), ("step", "分析步", "Step"),
    ("loads", "荷载与约束", "Loads and boundary conditions"), ("mesh", "网格", "Mesh"),
    ("job", "作业", "Job"), ("results", "结果", "Results"),
)
_HINTS_EN = {
    "compression_criterion": "Full physical windows are searched on both edges using signed total strain including self-weight. The 100 mm / 0.0035 default is an uncalibrated assistant assumption. Legacy point crushing is diagnostic in physical-window mode. Apply and re-analyze for changed inputs.",
    "reinforcement": "Cover is measured to the outside of stirrups. Stirrups are appearance/archive only; no shear or confinement enhancement is calculated.",
    "bars": "Direct-coordinate input. Eta is upward and zeta across the section. Cover mode uses actual prepared coordinates.",
    "materials": "Default parameters are assumptions, not calibration. Clone before editing calibrated parameters; old results retain their original input.",
    "geometry": "Lengths in mm; angles in rad. Planar calculation is distinct from three-dimensional appearance.",
    "gravity": "Equivalent full-section density includes reinforcement weight. Self-weight is equilibrated then held constant.",
    "loads": "Reference components are Fx, Fy, Mz. P is the sum of external jack forces and excludes self-weight.",
    "supports": "DOF order: ux, uy, theta. Actual supports only; no mirror or virtual supports.",
    "analysis": "Tolerances and safety limits belong to the snapshot. First cracking/compression peak are nonterminal transitions. Missing legacy options remain absent until explicitly edited.",
    "length_mapping": "v2 maps softening by element length; Gauss weights integrate only. Legacy v1 retains its historical meaning. Changing mapping requires a new analysis, not history remapping.",
    "line_search_enabled": "Each Newton correction updates the tangent. Backtracking does not relax equilibrium, correction or control tolerances.",
    "transition_resolution_enabled": "Locate first structural tensile cracking and compression peak, save actual accepted states and continue. No fabricated fiber steps.",
    "transition_minimum_step": "Minimum increment for transition localization only; not the normal minimum step.",
    "observations": "Virtual gauges are observation operators. Changing the display zero never changes material history.",
    "discretization": "A changed mesh requires re-analysis; material damage is never interpolated. Characteristic length differs from physical gauge length.",
    "kind": "Check all required parameters after changing constitutive model. Missing parameters block preparation.",
    "E": "Concrete initial modulus is derived from fc and e0. Reported unusual steel modulus retains its quality marker.",
    "area": "Direct-bar area is an explicit independent input. Diameter edits do not silently rewrite area.",
    "mesh": "M100/M50/M25 are nested benchmarks. Custom meshes use target element length.",
}
_ENUMS_EN = {
    "mode": ["Cover-driven", "Direct coordinates"], "cover_definition": ["Outside stirrups"],
    "mesh": ["M100", "M50", "M25", "Custom"], "length_mapping": ["Element length v2", "Legacy Gauss weight v1"],
    "control_component": ["Vertical displacement uy", "Axial displacement ux", "Rotation theta"],
    "control_direction": ["Negative (downward for horizontal beam)", "Positive"],
    "kind": ["Concrete02-type concrete", "Bilinear kinematic steel", "Elastic check"],
    "category": ["Assumption", "Paper", "Calibrated", "Measured", "Synthetic", "Derived"],
    "reference_phase": ["Self-weight equilibrium SG", "Initial S0"], "group": ["Top bars", "Bottom bars"],
}


class ModelEditor(QWidget):
    """Chinese model tree, typed property forms, preparation and error inspection.

    Signals carry copied draft values or stable object identities. ``set_locked``
    disables all editing while retaining inspection and selection during analysis.
    """

    applyRequested = Signal(dict)
    revertRequested = Signal()
    objectSelected = Signal(str)
    draftChanged = Signal(dict)
    moduleRequested = Signal(str)
    propertiesRequested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._language = "zh_CN"
        self._theme = "dark"
        self._module = "geometry"
        self._current_kind = ""
        self._module_items: dict[str, QTreeWidgetItem] = {}
        self._raw_text: dict[tuple[Any, ...], str] = {}
        self._navigation = QWidget(self)
        self._navigation.setObjectName("modelNavigation")
        self._document: dict[str, Any] = {}
        self._draft: dict[str, Any] = {}
        self._prepared: dict[str, Any] | None = None
        self._locked = False
        self._lock_reason = ""
        self._path_items: dict[tuple[Any, ...], QTreeWidgetItem] = {}
        self._id_items: dict[str, QTreeWidgetItem] = {}
        self._inputs: dict[tuple[Any, ...], QWidget] = {}
        self._invalid: dict[tuple[Any, ...], str] = {}
        self._invalid_text: dict[tuple[Any, ...], str] = {}
        self._current_path: tuple[Any, ...] = ()
        self._diagnostics: list[dict[str, Any]] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        self.status = QLabel("尚未打开工程", self)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.tree = QTreeWidget(self)
        self.tree.setObjectName("modelTree")
        self.tree.setHeaderLabels(["工程对象"])
        self.tree.setAccessibleName("模型树")
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.tree.itemSelectionChanged.connect(self._tree_selected)
        self.tree.itemClicked.connect(lambda *_: self._emit_selected_object())
        self.tree.itemDoubleClicked.connect(lambda *_: self._edit_selected())
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._context_menu)
        self.tree.installEventFilter(self)
        navigation_layout = QVBoxLayout(self._navigation)
        navigation_layout.setContentsMargins(2, 2, 2, 2)
        navigation_layout.addWidget(self.tree)

        self.tabs = QTabWidget(self)
        self.form_scroll = QScrollArea(self)
        self.form_scroll.setWidgetResizable(True)
        self.tabs.addTab(self.form_scroll, "属性")
        self.inspection = QTreeWidget(self)
        self.inspection.setObjectName("preparedInspection")
        self.inspection.setHeaderLabels(["实际离散 / 派生量", "值", "单位"])
        self.inspection.itemExpanded.connect(self._expand_inspection)
        self.inspection.itemSelectionChanged.connect(self._inspection_selected)
        self.tabs.addTab(self.inspection, "离散检查")
        self.report = QTreeWidget(self)
        self.report.setObjectName("modelDiagnostics")
        self.report.setHeaderLabels(["级别", "对象", "字段", "说明"])
        self.report.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.report.itemActivated.connect(self._locate_diagnostic)
        self.report.itemClicked.connect(self._locate_diagnostic)
        self.tabs.addTab(self.report, "检查报告")
        self.advanced_scroll = QScrollArea(self)
        self.advanced_scroll.setWidgetResizable(True)
        self.details_scroll = QScrollArea(self)
        self.details_scroll.setWidgetResizable(True)
        self.tabs.addTab(self.advanced_scroll, "高级")
        self.tabs.addTab(self.details_scroll, "详情与来源")
        layout.addWidget(self.tabs, 1)

        actions = QHBoxLayout()
        self.add_button = QPushButton("新增条目", self)
        self.remove_button = QPushButton("删除条目", self)
        self.clone_button = QPushButton("复制材料包", self)
        self.apply_button = QPushButton("应用", self)
        self.revert_button = QPushButton("还原", self)
        self.add_button.clicked.connect(self._add_entry)
        self.remove_button.clicked.connect(self._remove_entry)
        self.clone_button.clicked.connect(self._clone_material)
        self.apply_button.clicked.connect(self._apply)
        self.revert_button.clicked.connect(self._revert)
        for button in (self.add_button, self.remove_button, self.clone_button):
            button.setVisible(False)
        for button in (self.apply_button, self.revert_button):
            actions.addWidget(button)
        layout.addLayout(actions)
        self._show_form(())

    @property
    def language(self) -> str:
        """Current presentation language; engineering values remain unchanged."""
        return self._language

    def set_theme(self, name: str) -> None:
        """Recolor existing widgets without flushing or rebuilding the draft."""
        c = colors(name)
        self._theme = name
        self.setPalette(palette(name))
        self._navigation.setPalette(palette(name))
        for path, widget in self._inputs.items():
            if path in self._invalid:
                widget.setStyleSheet("border:2px solid " + c["error"])
        for index in range(self.report.topLevelItemCount()):
            item = self.report.topLevelItem(index)
            severity = (item.data(0, _ROLE) or {}).get("severity")
            if severity in {"error", "warning"}:
                item.setForeground(0, QColor(c["error" if severity == "error" else "warning"]))

    def _t(self, chinese: str, english: str) -> str:
        return english if self._language == "en" else chinese

    def _label(self, key: Any) -> str:
        return (_LABELS_EN if self._language == "en" else _LABELS).get(str(key), str(key))

    def _hint(self, key: str) -> str:
        return (_HINTS_EN if self._language == "en" else _HINTS).get(key, "")

    def navigation_widget(self) -> QWidget:
        """Return the persistent model tree for the workbench navigation dock."""
        return self._navigation

    def retranslate_ui(self, language: str) -> None:
        """Translate presentation without applying, reverting or discarding text."""
        if language not in {"zh_CN", "en"}:
            raise ValueError("Unsupported editor language: " + str(language))
        focus = self.focusWidget()
        focus_path = next((p for p, w in self._inputs.items() if w is focus), None)
        scroll = [page.verticalScrollBar().value() for page in
                  (self.form_scroll, self.advanced_scroll, self.details_scroll)]
        tab = self.tabs.currentIndex()
        inspection_selection = self.inspection.currentItem()
        inspection_data = inspection_selection.data(0, _ROLE) if inspection_selection is not None else None
        inspected_id = (inspection_data.get("value", {}).get("id")
                        if isinstance(inspection_data, dict) and isinstance(inspection_data.get("value"), dict) else None)
        self._flush_inputs()
        self._language = language
        self._flush_inputs()
        self.tree.setHeaderLabels([self._t("工程对象", "Engineering objects")])
        self.tree.setAccessibleName(self._t("模型树", "Model tree"))
        for index, labels in enumerate((("属性", "Properties"), ("离散检查", "Prepared model"),
                                        ("检查报告", "Check report"), ("高级", "Advanced"),
                                        ("详情与来源", "Details and sources"))):
            self.tabs.setTabText(index, self._t(*labels))
        self.inspection.setHeaderLabels([self._t("实际离散 / 派生量", "Actual discretization / derived values"),
                                         self._t("值", "Value"), self._t("单位", "Unit")])
        self.report.setHeaderLabels([self._t("级别", "Severity"), self._t("对象", "Object"),
                                    self._t("字段", "Field"), self._t("说明", "Message")])
        for button, zh, en in ((self.add_button, "新增条目", "Add item"),
                               (self.remove_button, "删除条目", "Delete item"),
                               (self.clone_button, "复制材料包", "Clone material"),
                               (self.apply_button, "应用", "Apply"), (self.revert_button, "还原", "Revert")):
            button.setText(self._t(zh, en))
        self._rebuild_tree(self._current_path)
        self.set_prepared(self._prepared)
        if inspected_id:
            self._select_prepared_object(str(inspected_id), notify=False)
        self.set_diagnostics(self._diagnostics)
        self.tabs.setCurrentIndex(tab)
        for page, value in zip((self.form_scroll, self.advanced_scroll, self.details_scroll), scroll):
            page.verticalScrollBar().setValue(value)
        if focus_path in self._inputs:
            self._inputs[focus_path].setFocus(Qt.FocusReason.OtherFocusReason)

    def set_module(self, module_key: str) -> bool:
        """Reveal an engineering context without modifying the pending draft."""
        if module_key not in {item[0] for item in _MODULES}:
            return False
        self._flush_inputs()
        self._module = module_key
        item = self._module_items.get(module_key)
        if item is None:
            return False
        current = self.tree.currentItem()
        if current is None or current.data(0, _ROLE + 1) != module_key:
            self.tree.setCurrentItem(item.child(0) if item.childCount() else item)
        item.setExpanded(True)
        self.tree.scrollToItem(item)
        return True

    def eventFilter(self, watched: Any, event: Any) -> bool:
        if watched is self.tree and event.type() == QEvent.Type.KeyPress:
            if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self._edit_selected()
                return True
            if event.key() == Qt.Key.Key_Menu or (event.key() == Qt.Key.Key_F10 and event.modifiers() & Qt.KeyboardModifier.ShiftModifier):
                item = self.tree.currentItem()
                if item is not None:
                    self._context_menu(self.tree.visualItemRect(item).center())
                return True
        return super().eventFilter(watched, event)

    def _edit_selected(self) -> None:
        self.propertiesRequested.emit()
        current = self.tree.currentItem()
        if current is not None and current.data(0, _ROLE + 2) == "prepared":
            self.tabs.setCurrentIndex(1)
            return
        self.tabs.setCurrentIndex(0)
        for path, widget in self._inputs.items():
            if widget.isEnabled() and not (isinstance(widget, QLineEdit) and widget.isReadOnly()):
                page = {"common": (0, self.form_scroll), "advanced": (3, self.advanced_scroll),
                        "details": (4, self.details_scroll)}[self._field_tier(path)]
                self.tabs.setCurrentIndex(page[0])
                widget.setFocus(Qt.FocusReason.OtherFocusReason)
                page[1].ensureWidgetVisible(widget)
                break

    def _context_menu(self, position: Any) -> None:
        item = self.tree.itemAt(position)
        if item is None:
            return
        self.tree.setCurrentItem(item)
        self._update_actions()
        menu = QMenu(self.tree)
        edit = menu.addAction(self._t("查看属性" if self._locked else "编辑属性", "Inspect properties" if self._locked else "Edit properties"))
        edit.triggered.connect(self._edit_selected)
        for button in (self.add_button, self.remove_button, self.clone_button):
            if button.isEnabled():
                action = menu.addAction(button.text())
                action.triggered.connect(button.click)
        menu.exec(self.tree.viewport().mapToGlobal(position))

    @property
    def has_pending_changes(self) -> bool:
        """Whether the form draft differs from the last controller document."""
        return bool(self._invalid) or self._draft != self._document

    def draft_document(self) -> dict[str, Any]:
        """Return a copied draft for an explicit recovery/inspection request."""
        self._flush_inputs()
        return deepcopy(self._draft)

    def set_document(self, document: dict[str, Any]) -> None:
        """Replace the editable baseline with a value-owned document snapshot."""
        if not isinstance(document, dict):
            raise TypeError("document must be a dictionary")
        first_document = not self._document
        self._document = deepcopy(document)
        self._draft = deepcopy(document)
        self._invalid.clear()
        self._invalid_text.clear()
        self._raw_text.clear()
        if first_document and isinstance(document.get("model", {}).get("geometry"), dict):
            self._current_path = ("model", "geometry")
        self._rebuild_tree(self._current_path)
        self._update_status()

    def set_prepared(self, prepared: dict[str, Any] | None) -> None:
        """Inspect actual generated values without making them model inputs."""
        self._prepared = deepcopy(prepared) if prepared is not None else None
        self.inspection.clear()
        if prepared is None:
            QTreeWidgetItem(self.inspection, [self._t("尚无当前修订的准备结果", "No prepared model for this revision"),
                                             self._t("请先应用并检查模型", "Apply and check the model first"), ""])
            return
        identity = prepared.get("identity", {})
        root = QTreeWidgetItem(self.inspection, [self._t("准备快照", "Prepared snapshot"), str(identity.get("snapshot_id", "")), ""])
        for key in ("prepared_hash", "kernel_version", "numerics_contract", "analysis", "nodes", "elements", "sections", "constrained_dofs", "free_dofs", "gravity_load", "reference_load", "control_dof"):
            if key in prepared:
                self._inspection_item(root, key, prepared[key], ())
        mapping = prepared.get("document", {}).get("model", {}).get("discretization", {}).get("length_mapping", LEGACY_LENGTH_MAPPING)
        self._inspection_item(root, "length_mapping", mapping, ())
        root.setExpanded(True)

    def set_diagnostics(self, diagnostics: list[dict[str, Any]]) -> None:
        """Show located errors separately from runnable warnings/assumptions."""
        self._diagnostics = deepcopy(diagnostics)
        self.report.clear()
        for diagnostic in self._diagnostics:
            severity = str(diagnostic.get("severity", "information"))
            title = {"error": self._t("阻止运行", "Blocking error"), "warning": self._t("警告", "Warning"),
                     "information": self._t("说明", "Information"), "info": self._t("说明", "Information")}.get(severity, severity)
            item = QTreeWidgetItem(self.report, [title, str(diagnostic.get("object_id", "")),
                                               str(diagnostic.get("field", "")), str(diagnostic.get("message", ""))])
            item.setData(0, _ROLE, diagnostic)
            item.setToolTip(3, str(diagnostic.get("code", "")) + "\n" + str(diagnostic.get("message", "")))
            if severity == "error":
                item.setForeground(0, QColor(colors(self._theme)["error"]))
            elif severity == "warning":
                item.setForeground(0, QColor(colors(self._theme)["warning"]))
        self.report.resizeColumnToContents(0)
        self._update_status()

    def set_locked(self, locked: bool, *, reason: str = "running") -> None:
        """Lock edits, distinguishing an active worker from result inspection."""
        if reason not in ("", "running", "inspection"):
            raise ValueError("Unsupported editor lock reason: " + str(reason))
        if not self._locked:
            self._flush_inputs()
        self._locked = bool(locked)
        self._lock_reason = reason if self._locked else ""
        self._show_form(self._current_path)
        self._update_actions()
        self._update_status()

    def select_object(self, object_id: str) -> bool:
        """Select a stable object from a viewport pick without changing the model."""
        item = self._id_items.get(object_id)
        if item is None:
            return self._select_prepared_object(object_id)
        self.tree.setCurrentItem(item)
        self.tree.scrollToItem(item)
        return True

    def _select_prepared_object(self, object_id: str, *, notify: bool = True) -> bool:
        """Locate only an exact ID in the editor's own prepared snapshot."""
        if not object_id or not self._prepared:
            return False
        def find(value: Any, path: tuple[Any, ...]) -> tuple[Any, ...] | None:
            if isinstance(value, dict):
                if value.get("id") == object_id:
                    return path
                for key, child in value.items():
                    if key == "document":
                        continue
                    result = find(child, path + (key,))
                    if result is not None:
                        return result
            elif isinstance(value, list):
                for index, child in enumerate(value):
                    result = find(child, path + (index,))
                    if result is not None:
                        return result
            return None
        path = find(self._prepared, ())
        if path is None or self.inspection.topLevelItemCount() == 0:
            return False
        item = self.inspection.topLevelItem(0)
        for depth in range(1, len(path) + 1):
            self._expand_inspection(item)
            candidate = next((item.child(i) for i in range(item.childCount())
                              if isinstance(item.child(i).data(0, _ROLE), dict)
                              and tuple(item.child(i).data(0, _ROLE).get("path", ())) == path[:depth]), None)
            if candidate is None:
                return False
            item.setExpanded(True)
            item = candidate
        self.inspection.blockSignals(True)
        self.inspection.setCurrentItem(item)
        self.inspection.scrollToItem(item)
        self.inspection.blockSignals(False)
        self.tabs.setCurrentIndex(1)
        if notify:
            self.propertiesRequested.emit()
        return True

    def _get(self, path: tuple[Any, ...]) -> Any:
        value: Any = self._draft
        for index, key in enumerate(path):
            if (index == len(path) - 1 and path == ("model", "discretization", "length_mapping")
                    and isinstance(value, dict) and key not in value):
                return LEGACY_LENGTH_MAPPING
            if (index == len(path) - 1 and path[:2] == ("model", "analysis")
                    and isinstance(value, dict) and key not in value and key in REVISED_ANALYSIS_DEFAULTS):
                return deepcopy(REVISED_ANALYSIS_DEFAULTS[key])
            value = value[key]
        return value

    def _set(self, path: tuple[Any, ...], value: Any) -> None:
        target = self._draft
        for key in path[:-1]:
            target = target[key]
        exists = isinstance(target, list) or path[-1] in target
        changed = (target[path[-1]] if exists else self._get(path)) != value
        # Viewing or flushing a virtual legacy default must not change its document.
        if exists or changed:
            target[path[-1]] = value
        self._invalid.pop(path, None)
        self._invalid_text.pop(path, None)
        if path[-1] == "id":
            item = self._path_items.get(path[:-1])
            if item is not None:
                self._id_items = {key: old_item for key, old_item in self._id_items.items() if old_item is not item}
                self._id_items[str(value)] = item
                item.setText(1, str(value))
        self._update_status()
        if changed:
            self.draftChanged.emit(deepcopy(self._draft))

    def _rebuild_tree(self, selected: tuple[Any, ...]) -> None:
        self.tree.blockSignals(True)
        self.tree.clear()
        self._path_items.clear()
        self._id_items.clear()
        self._module_items.clear()
        root = QTreeWidgetItem(self.tree, [str(self._draft.get("name", self._t("工程", "Project")))])
        self._register(root, (), self._draft)
        paths = {"geometry": ("model", "geometry"), "materials": ("materials",),
                 "section": ("model", "reinforcement"), "step": ("model", "analysis"),
                 "loads": ("model", "loads"), "mesh": ("model", "discretization"),
                 "job": ("model", "analysis"), "results": ("observations",)}
        for module, chinese, english in _MODULES:
            group = QTreeWidgetItem(root, [self._t(chinese, english)])
            path = paths[module]
            try:
                value = self._get(path)
            except (KeyError, IndexError, TypeError):
                value = {}
            self._register(group, path, value, module)
            self._module_items[module] = group
            if module == "geometry":
                self._engineering_item(group, path, self._t("梁几何", "Beam geometry"), module)
            elif module == "materials":
                for index, material in enumerate(self._draft.get("materials", [])):
                    kind = str(material.get("kind", ""))
                    name = {"concrete02": self._t("混凝土", "Concrete"),
                            "steel_bilinear": self._t("钢筋", "Steel"),
                            "elastic": self._t("线弹性材料", "Elastic material")}.get(kind, kind)
                    self._engineering_item(group, ("materials", index),
                                           str(material.get("name") or f"{name} {index + 1}"), module)
            elif module == "section":
                self._engineering_item(group, ("model", "geometry"), self._t("截面尺寸", "Section dimensions"), module)
                self._engineering_item(group, ("model",), self._t("截面材料", "Section material"), module)
                self._engineering_item(group, path, self._t("纵筋与箍筋（箍筋仅外观）", "Reinforcement (stirrups: appearance only)"), module)
                stirrups = self._engineering_item(group, path, self._t("箍筋（仅外观）", "Stirrups (appearance only)"), module)
                if stirrups is not None:
                    stirrups.setData(0, _ROLE + 2, "stirrups")
                    self._id_items["reinforcement/stirrups"] = stirrups
                bars_path = path + ("bars",)
                bars = self._engineering_item(group, bars_path, self._t("直接坐标纵筋", "Direct-coordinate bars"), module)
                if bars is not None:
                    for index, bar in enumerate(self._get(bars_path)):
                        self._engineering_item(bars, bars_path + (index,), self._t("纵筋", "Bar") + f" {index + 1}", module)
            elif module == "step":
                self._engineering_item(group, path, self._t("先自重平衡，后位移控制加载", "Self-weight, then displacement-controlled loading"), module)
                self._engineering_item(group, ("model",), self._t("位移控制", "Displacement control"), module)
                self._engineering_item(group, ("model", "compression_criterion"), self._t("压缘终止判据", "Compression criterion"), module)
            elif module == "loads":
                for key, zh, en in (("supports", "支座约束", "Supports"), ("loads", "两点荷载", "Point loads")):
                    list_path = ("model", key)
                    parent = self._engineering_item(group, list_path, self._t(zh, en), module)
                    if parent is not None:
                        for index, obj in enumerate(self._get(list_path)):
                            self._engineering_item(parent, list_path + (index,),
                                                   f"{self._t(zh, en)} {index + 1}  x={obj.get('x', '')} mm", module)
                self._engineering_item(group, ("model", "gravity"), self._t("自重", "Self-weight"), module)
            elif module == "mesh":
                self._engineering_item(group, path, self._t("网格与积分", "Mesh and integration"), module)
                prepared_item = self._engineering_item(group, path, self._t("实际节点与截面（检查后）", "Actual nodes and sections (after checking)"), module)
                if prepared_item is not None:
                    prepared_item.setData(0, _ROLE + 2, "prepared")
            elif module == "results":
                self._engineering_item(group, ("observations",), self._t("观测与标距", "Observations and gauges"), module)
                self._engineering_item(group, ("observations", "gauges"), self._t("虚拟引伸计", "Virtual gauges"), module)
            group.setExpanded(True)
        root.setExpanded(True)
        item = self._path_items.get(selected, root)
        candidates = self.tree.findItems("*", Qt.MatchFlag.MatchWildcard | Qt.MatchFlag.MatchRecursive)
        item = next((candidate for candidate in candidates if tuple(candidate.data(0, _ROLE) or ()) == selected
                     and candidate.data(0, _ROLE + 1) == self._module
                     and str(candidate.data(0, _ROLE + 2) or "") == self._current_kind), item)
        self.tree.setCurrentItem(item)
        self.tree.blockSignals(False)
        self._current_path = tuple(item.data(0, _ROLE) or ())
        self._current_kind = str(item.data(0, _ROLE + 2) or "")
        self._show_form(self._current_path)
        self._update_actions()

    def _register(self, item: QTreeWidgetItem, path: tuple[Any, ...], value: Any, module: str = "") -> None:
        item.setData(0, _ROLE, path)
        item.setData(0, _ROLE + 1, module)
        self._path_items.setdefault(path, item)
        item.setToolTip(0, _path_text(path) + ("\n" + str(value.get("id", "")) if isinstance(value, dict) else ""))
        if isinstance(value, dict) and value.get("id"):
            self._id_items[str(value["id"])] = item
        if path in {("model", "geometry"), ("model", "reinforcement")}:
            self._id_items.setdefault(str(path[-1]), item)
        if path == ("model", "reinforcement", "bars"):
            self._id_items.setdefault("reinforcement/bars", item)

    def _engineering_item(self, parent: QTreeWidgetItem, path: tuple[Any, ...], title: str, module: str) -> QTreeWidgetItem | None:
        try:
            value = self._get(path)
        except (KeyError, IndexError, TypeError):
            return None
        item = QTreeWidgetItem(parent, [title])
        self._register(item, path, value, module)
        return item

    def _tree_selected(self) -> None:
        selected = self.tree.selectedItems()
        if not selected:
            return
        self._flush_inputs()
        self._current_path = tuple(selected[0].data(0, _ROLE) or ())
        self._current_kind = str(selected[0].data(0, _ROLE + 2) or "")
        module = selected[0].data(0, _ROLE + 1)
        if module and module != self._module:
            self._module = module
            self.moduleRequested.emit(module)
        self._show_form(self._current_path)
        if selected[0].data(0, _ROLE + 2) == "prepared":
            self.tabs.setCurrentIndex(1)
            self.propertiesRequested.emit()
        self._update_actions()
        self._emit_selected_object()

    def _emit_selected_object(self) -> None:
        """A click on the already-current item is still a selection command."""
        selected = self.tree.selectedItems()
        if not selected:
            return
        value = self._get(self._current_path)
        object_id = value.get("id") if isinstance(value, dict) else None
        semantic = {("model", "geometry"): "geometry", ("model", "reinforcement"): "reinforcement",
                    ("model", "reinforcement", "bars"): "reinforcement/bars"}.get(self._current_path)
        if selected[0].data(0, _ROLE + 2) == "stirrups":
            semantic = "reinforcement/stirrups"
        self.objectSelected.emit(str(object_id or semantic or _path_text(self._current_path)))

    def _show_form(self, path: tuple[Any, ...]) -> None:
        self._current_path = path
        for widget in self._inputs.values():
            widget.blockSignals(True)
        self._inputs.clear()
        try:
            value = self._get(path)
        except (KeyError, IndexError, TypeError):
            value = self._draft
            path = ()
            self._current_path = ()
        key = str(path[-1]) if path else ""
        hint_key = key if key in _HINTS else next((str(p) for p in reversed(path) if str(p) in _HINTS), "")
        for tier, scroll in (("common", self.form_scroll), ("advanced", self.advanced_scroll), ("details", self.details_scroll)):
            body = QWidget(self)
            body_layout = QVBoxLayout(body)
            selected_item = self.tree.currentItem()
            title = self._label(key) if path else self._t("工程属性", "Project properties")
            if selected_item is not None and tuple(selected_item.data(0, _ROLE) or ()) == path:
                title = selected_item.text(0)
            heading = QLabel(title, body)
            heading.setStyleSheet("font-weight:600;font-size:14px")
            body_layout.addWidget(heading)
            if hint_key:
                hint = QLabel(self._hint(hint_key), body)
                hint.setWordWrap(True)
                body_layout.addWidget(hint)
            if tier == "common" and len(path) == 2 and path[0] == "materials" and isinstance(value, dict):
                source = value.get("source", {})
                category = source.get("category", "") if isinstance(source, dict) else ""
                categories = _ENUMS["category"]
                category_index = next((i for i, (_, data) in enumerate(categories) if data == category), None)
                label = str(category)
                if category_index is not None:
                    label = _ENUMS_EN["category"][category_index] if self._language == "en" else categories[category_index][0]
                body_layout.addWidget(QLabel(self._t("来源类别：", "Source category: ") + label, body))
            form = QFormLayout()
            form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
            form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
            body_layout.addLayout(form)
            self._fill_form(form, value, path, tier)
            if form.rowCount() == 0:
                body_layout.addWidget(QLabel(self._t("此对象没有本页属性。", "No properties in this category."), body))
            if key == "bars" and self._prepared and tier == "common":
                note = QLabel(self._t("自动生成的实际钢筋坐标见“离散检查”。复制前须先应用并检查模型。", "Actual generated bar coordinates are in Prepared model. Apply and check before copying."), body)
                note.setWordWrap(True)
                body_layout.addWidget(note)
                copy_button = QPushButton(self._t("由当前准备结果填入纵筋坐标", "Copy actual prepared bar coordinates"), body)
                copy_button.setEnabled(not self._locked)
                copy_button.clicked.connect(self._copy_prepared_bars)
                body_layout.addWidget(copy_button)
            body_layout.addStretch(1)
            previous = scroll.takeWidget()
            scroll.setWidget(body)
            if previous is not None:
                previous.deleteLater()
        self._update_actions()

    def _field_tier(self, path: tuple[Any, ...]) -> str:
        key = str(path[-1]) if path else ""
        if any(part in {"source", "sources", "numerical_revision"} for part in path) or key in {"id", "version", "schema_version", "project_id", "revision_id", "reference_step_id"}:
            return "details"
        if path[:2] == ("model", "analysis") and key not in {"initial_step", "minimum_step", "maximum_step", "maximum_control_displacement", "gravity_steps"}:
            return "advanced"
        if key == "length_mapping":
            return "advanced"
        return "common"

    def _fill_form(self, form: QFormLayout, value: Any, path: tuple[Any, ...], tier: str = "common") -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                child_path = path + (key,)
                if not path and key in {"model", "materials", "observations"}:
                    continue
                if path == ("model",) and key not in {"control_x", "control_component", "control_direction", "concrete_material_id", "sources"}:
                    continue
                if path == ("model",) and self._module == "section" and key not in {"concrete_material_id", "sources"}:
                    continue
                if path == ("model",) and self._module == "step" and key == "concrete_material_id":
                    continue
                if path == ("model", "geometry") and self._module == "section" and key in {"length", "angle"}:
                    continue
                current = self.tree.currentItem()
                if (path == ("model", "reinforcement") and current is not None
                        and current.data(0, _ROLE + 2) == "stirrups"
                        and key not in {"stirrup_diameter", "stirrup_spacing", "cover", "cover_definition"}):
                    continue
                if self._field_tier(child_path) != tier:
                    continue
                if isinstance(child, (dict, list)):
                    if tier == "details" or (isinstance(child, list) and all(not isinstance(x, (dict, list)) for x in child)):
                        box = QGroupBox(self._label(key), self)
                        nested = QFormLayout(box)
                        self._fill_form(nested, child, child_path, tier)
                        form.addRow(box)
                    else:
                        button = QPushButton(self._t("打开 ", "Open ") + self._label(key), self)
                        button.clicked.connect(lambda checked=False, p=child_path: self._select_path(p))
                        form.addRow(self._label(key), button)
                else:
                    self._add_field(form, child_path, child, self._label(key))
            if path == ("model",) and self._module == "step" and "compression_criterion" not in value and tier == "common":
                form.addRow(self._t("压缘终止判据", "Compression criterion"), QLabel(self._t("旧点 Route-C（原输入未声明；保持原含义）", "Legacy point Route-C (absent in source; original meaning retained)"), self))
                button = QPushButton(self._t("显式选择压缘终止判据…", "Explicitly choose compression criterion..."), self)
                button.setEnabled(not self._locked)
                button.clicked.connect(self._create_criterion)
                form.addRow(button)
            if path == ("model", "analysis"):
                for key, default in REVISED_ANALYSIS_DEFAULTS.items():
                    if key not in value and self._field_tier(path + (key,)) == tier:
                        self._add_field(form, path + (key,), default, self._label(key) + self._t(" [旧默认；未写入]", " [legacy default; not written]"))
            if path == ("model", "discretization") and "length_mapping" not in value and tier == "advanced":
                self._add_field(form, path + ("length_mapping",), LEGACY_LENGTH_MAPPING,
                                self._label("length_mapping") + self._t(" [旧默认；未写入]", " [legacy default; not written]"))
        elif isinstance(value, list):
            for index, child in enumerate(value):
                child_path = path + (index,)
                if isinstance(child, (dict, list)):
                    if tier == "details":
                        box = QGroupBox(str(index + 1), self)
                        self._fill_form(QFormLayout(box), child, child_path, tier)
                        form.addRow(box)
                    elif tier == "common":
                        title = self._t("条目", "Item") + f" {index + 1}"
                        button = QPushButton(title, self)
                        button.clicked.connect(lambda checked=False, p=child_path: self._select_path(p))
                        form.addRow(str(index + 1), button)
                else:
                    names = _ARRAY_LABELS.get(str(path[-1]), ()) if path else ()
                    if self._field_tier(child_path) == tier:
                        title = names[index] if index < len(names) else str(index + 1)
                        if self._language == "en" and str(path[-1]) in {"constrained", "values"}:
                            title = ("Constrain " if path[-1] == "constrained" else "Prescribed ") + ("ux", "uy", "theta")[index]
                        self._add_field(form, child_path, child, title)
            if not value and tier == "common":
                form.addRow(QLabel(self._t("当前为空；可在模型树右键新增条目。", "Empty; use the model-tree context menu to add an item."), self))
        else:
            if self._field_tier(path) == tier:
                self._add_field(form, path, value, self._label(path[-1]) if path else self._t("值", "Value"))

    def _add_field(self, form: QFormLayout, path: tuple[Any, ...], value: Any, title: str) -> None:
        key = str(path[-1])
        readonly = key in _READONLY or (key == "E" and len(path) > 1 and isinstance(self._get(path[:-1]), dict) and self._get(path[:-1]).get("kind") == "concrete02")
        choices = _ENUMS.get(key)
        if choices and self._language == "en" and key in _ENUMS_EN:
            choices = [(label, data) for label, (_, data) in zip(_ENUMS_EN[key], choices)]
        if path == ("model", "compression_criterion", "mode"):
            choices = [(self._t("固定物理标距压缘平均应变（未标定假设）", "Physical edge-gauge mean strain (uncalibrated assumption)"), "physical_gauge_mean_v1"),
                       (self._t("旧点 Route-C（历史对照）", "Legacy point Route-C (historical)"), "route_c_point_v1")]
            title = self._t("压缘判据模式", "Compression criterion mode")
        if key.endswith("material_id"):
            choices = [(str(m.get("name") or ((self._t("混凝土", "Concrete") if m.get("kind") == "concrete02"
                                              else self._t("钢筋", "Steel") if m.get("kind") == "steel_bilinear"
                                              else self._t("线弹性材料", "Elastic material")) + f" {index + 1}")), m.get("id", ""))
                       for index, m in enumerate(self._draft.get("materials", [])) if isinstance(m, dict)]
        if choices:
            widget = QComboBox(self)
            for text, data in choices:
                widget.addItem(text, data)
            index = widget.findData(value)
            if index < 0:
                widget.addItem(str(value), value)
                index = widget.count() - 1
            widget.setCurrentIndex(index)
            widget.currentIndexChanged.connect(lambda idx, p=path, w=widget: self._choice_changed(p, w.itemData(idx)))
            widget.setEnabled(not self._locked and not readonly)
        elif isinstance(value, bool):
            widget = QCheckBox(self._t("是", "Yes"), self)
            widget.setChecked(value)
            widget.toggled.connect(lambda checked, p=path: self._set(p, checked))
            widget.setEnabled(not self._locked and not readonly)
        else:
            widget = QLineEdit(self)
            widget.setText(repr(value) if isinstance(value, float) else ("" if value is None else str(value)))
            if path in self._raw_text:
                widget.setText(self._raw_text[path])
            if path in self._invalid_text:
                widget.setText(self._invalid_text[path])
            widget.setReadOnly(self._locked or readonly)
            widget.textEdited.connect(lambda text, p=path, w=widget, old=value: self._read_text(p, w, old))
            widget.editingFinished.connect(lambda p=path, w=widget, old=value: self._read_text(p, w, old))
        widget.setObjectName("field_" + _path_text(path))
        widget.setAccessibleName(title)
        widget.setToolTip(_path_text(path) + ("\n" + self._hint(key) if key in _HINTS else ""))
        if path in self._invalid:
            widget.setStyleSheet("border:2px solid " + colors(self._theme)["error"])
            widget.setToolTip(self._invalid[path])
        self._inputs[path] = widget
        unit = _unit(path)
        form.addRow(title + (f" ({unit})" if unit else "") + (self._t(" [只读]", " [read-only]") if readonly else ""), widget)

    def _choice_changed(self, path: tuple[Any, ...], value: Any) -> None:
        if path == ("model", "compression_criterion", "mode") and value != self._get(path):
            criterion = self._draft["model"]["compression_criterion"]
            if value == "route_c_point_v1":
                material = self._criterion_material()
                if material is None:
                    QMessageBox.warning(self, self._t("无法选择旧点判据", "Cannot select legacy criterion"), self._t("请先选择具有参考长度和参考应变的混凝土材料。", "Select concrete with reference length and reference strain first."))
                    self._show_form(path[:-1])
                    return
                criterion["gauge_length_mm"] = material["reference_length"]
                criterion["threshold"] = material["crush_reference_strain"]
            criterion["source"] = deepcopy(CR_NUM_002_COMPRESSION_CRITERION["source"])
            if value == "route_c_point_v1":
                criterion["source"]["reference"] = "Explicit legacy point criterion; parameters retain selected material references"
            self._set(path, value)
            self._show_form(path[:-1])
            return
        self._set(path, value)
        if path[-1] != "kind" or not (len(path) >= 3 and path[0] == "materials"):
            return
        material = self._get(path[:-1])
        if value == "concrete02":
            material.pop("E", None)
            self._raw_text.pop(path[:-1] + ("E",), None)
        required = {
            "concrete02": {"fc": 0.0, "e0": 0.002, "fr": 0.0, "ft": 0.0, "Gft": 0.0,
                           "Gcp": 0.0, "unload_ratio": 0.1, "reference_length": 100.0,
                           "crush_reference_strain": 0.0035},
            "steel_bilinear": {"E": 0.0, "fy": 0.0, "b": 0.0},
            "elastic": {"E": 0.0},
        }
        for key, initial in required.get(value, {}).items():
            material.setdefault(key, initial)
        # The kind selection makes all new mandatory fields visible immediately.
        # Zero strength/modulus fields intentionally fail preparation until filled.
        self._show_form(path[:-1])
        self.draftChanged.emit(deepcopy(self._draft))

    def _criterion_material(self) -> dict[str, Any] | None:
        """Resolve legacy redundant values from the selected concrete package."""
        identity = self._draft.get("model", {}).get("concrete_material_id")
        return next((item for item in self._draft.get("materials", [])
                     if item.get("id") == identity and item.get("kind") == "concrete02"
                     and "reference_length" in item and "crush_reference_strain" in item), None)

    def _create_criterion(self) -> None:
        """Only an explicit selection adds a criterion to an old source document."""
        if self._locked:
            return
        self._flush_inputs()
        labels = [self._t("固定物理标距压缘平均应变（未标定助手假设）", "Physical edge-gauge mean strain (uncalibrated assistant assumption)"), self._t("旧点 Route-C（历史对照）", "Legacy point Route-C (historical)")]
        selected, accepted = QInputDialog.getItem(self, self._t("选择压缘终止判据", "Choose compression criterion"), self._t("此选择需应用为新修订，旧结果保持原定义。", "Apply as a new revision; old results retain their original definition."), labels, 0, False)
        if not accepted:
            return
        criterion = deepcopy(CR_NUM_002_COMPRESSION_CRITERION)
        if selected == labels[1]:
            material = self._criterion_material()
            if material is None:
                QMessageBox.warning(self, self._t("无法选择旧点判据", "Cannot select legacy criterion"), self._t("请先选择具有参考长度和参考应变的混凝土材料。", "Select concrete with reference length and reference strain first."))
                return
            criterion.update(mode="route_c_point_v1", gauge_length_mm=material["reference_length"],
                             threshold=material["crush_reference_strain"])
            criterion["source"]["reference"] = "Explicit legacy point criterion; parameters retain selected material references"
        self._draft["model"]["compression_criterion"] = criterion
        self._rebuild_tree(("model", "compression_criterion"))
        self._update_status()
        self.draftChanged.emit(deepcopy(self._draft))

    def _read_text(self, path: tuple[Any, ...], widget: QLineEdit, old: Any) -> None:
        if self._locked or widget.isReadOnly():
            return
        self._raw_text[path] = widget.text()
        text = widget.text().strip()
        try:
            if isinstance(old, int) and not isinstance(old, bool):
                if not re.fullmatch(r"[+-]?\d+", text):
                    raise ValueError(self._t("请输入整数", "Enter an integer"))
                value: Any = int(text)
            elif isinstance(old, float):
                value = float(text)
                if not isfinite(value):
                    raise ValueError(self._t("数值必须有限，不能为 NaN 或无穷大", "Enter a finite number, not NaN or infinity"))
            else:
                value = text
            self._set(path, value)
            widget.setStyleSheet("")
        except (ValueError, OverflowError) as exc:
            self._invalid[path] = str(exc)
            self._invalid_text[path] = widget.text()
            widget.setStyleSheet("border:2px solid " + colors(self._theme)["error"])
            widget.setToolTip(str(exc))
            self._update_status()

    def _flush_inputs(self) -> None:
        for path, widget in list(self._inputs.items()):
            if isinstance(widget, QLineEdit):
                try:
                    self._read_text(path, widget, self._get(path))
                except (KeyError, IndexError, TypeError):
                    continue

    def _select_path(self, path: tuple[Any, ...]) -> None:
        item = self._path_items.get(path)
        if item is not None:
            self.tree.setCurrentItem(item)
            self.tree.scrollToItem(item)
        else:
            self._flush_inputs()
            self._show_form(path)
        self.propertiesRequested.emit()

    def _update_status(self) -> None:
        errors = sum(d.get("severity") == "error" for d in self._diagnostics)
        if not self._draft:
            text = self._t("尚未打开工程。请新建或打开工程。", "No project. Create or open a project.")
        elif self._locked:
            if self._lock_reason == "inspection":
                text = self._t("结果浏览只读；切换“当前模型”可编辑模型输入。", "Read-only result inspection; switch to Current model to edit inputs.")
            else:
                text = self._t("分析运行中：模型输入已锁定，可继续检查模型与结果。", "Analysis running: model inputs locked; inspection remains available.")
        elif self._invalid:
            text = self._t("输入格式错误 {count} 项，修正后才能应用。", "{count} invalid inputs; correct them before applying.").format(count=len(self._invalid))
        elif self.has_pending_changes:
            text = self._t("表单有未应用修改。“应用”形成模型修订，“还原”撤销草稿。", "Pending edits: Apply creates a model revision; Revert discards the draft.")
        else:
            text = self._t("当前显示已应用模型。数值单位为 N / mm / MPa / rad。", "Applied model. Numerical units: N / mm / MPa / rad.")
        if errors:
            text += self._t(" 检查报告有 {count} 项阻止运行的错误，点击定位。", " Check report: {count} blocking errors; select to locate.").format(count=errors)
        self.status.setText(text)
        self._update_actions()

    def _list_target(self) -> tuple[Any, ...] | None:
        path = self._current_path
        try:
            if isinstance(self._get(path), list):
                return path
            if path and isinstance(path[-1], int) and isinstance(self._get(path[:-1]), list):
                return path[:-1]
        except (KeyError, IndexError, TypeError):
            pass
        return None

    def _update_actions(self) -> None:
        active = bool(self._draft) and not self._locked
        self.apply_button.setEnabled(active and not self._invalid)
        self.revert_button.setEnabled(active)
        list_path = self._list_target()
        supported = {"bars", "supports", "loads", "materials", "gauges", "sources"}
        self.add_button.setEnabled(active and list_path is not None and str(list_path[-1]) in supported)
        self.remove_button.setEnabled(active and bool(self._current_path) and isinstance(self._current_path[-1], int)
                                      and list_path is not None and str(list_path[-1]) in supported)
        self.clone_button.setEnabled(active and self._material_index() is not None)

    def _apply(self) -> None:
        if self._locked:
            return
        self._flush_inputs()
        if self._invalid:
            self._update_status()
            return
        try:
            validate_finite(self._draft)
            criterion = self._draft.get("model", {}).get("compression_criterion")
            if criterion is not None:
                length, threshold = criterion.get("gauge_length_mm"), criterion.get("threshold")
                if any(type(number) not in (int, float) or not isfinite(number) or number <= 0
                       for number in (length, threshold)):
                    raise ValueError(self._t("判据窗口长度和应变阈值必须为有限正数。", "Criterion window length and strain threshold must be finite and positive."))
                if criterion.get("mode") == "physical_gauge_mean_v1" and length > self._draft["model"]["geometry"]["length"]:
                    raise ValueError(self._t("固定物理窗口长度不能超过梁全长。", "The physical window cannot exceed the beam length."))
                if criterion.get("mode") == "route_c_point_v1":
                    material = self._criterion_material()
                    if material is None or (length, threshold) != (material["reference_length"], material["crush_reference_strain"]):
                        raise ValueError(self._t("旧点判据的长度和阈值须与所选混凝土材料参考值完全一致。", "Legacy point length and threshold must exactly match the selected concrete references."))
        except ValueError as exc:
            QMessageBox.warning(self, self._t("无法应用", "Cannot apply"), str(exc))
            return
        self.applyRequested.emit(deepcopy(self._draft))

    def _revert(self) -> None:
        if self._locked:
            return
        self._draft = deepcopy(self._document)
        self._invalid.clear()
        self._invalid_text.clear()
        self._raw_text.clear()
        self._rebuild_tree(self._current_path)
        self._update_status()
        self.revertRequested.emit()
        self.draftChanged.emit(deepcopy(self._draft))

    def _material_index(self) -> int | None:
        path = self._current_path
        return path[1] if len(path) > 1 and path[0] == "materials" and isinstance(path[1], int) else None

    def _clone_material(self) -> None:
        index = self._material_index()
        if self._locked or index is None:
            return
        self._flush_inputs()
        source = self._draft["materials"][index]
        proposed = str(source.get("id", "material")) + "-copy"
        identity, ok = QInputDialog.getText(self, self._t("复制材料包", "Clone material"), self._t("新材料身份（保留原包与旧结果）：", "New material ID (original material and results remain):"), text=proposed)
        if not ok:
            return
        identity = identity.strip()
        if not identity or any(m.get("id") == identity for m in self._draft["materials"]):
            QMessageBox.warning(self, self._t("身份重复", "Duplicate identity"), self._t("请输入非空且未使用的材料身份。", "Enter a nonempty unused material ID."))
            return
        clone = deepcopy(source)
        clone["id"] = identity
        clone["version"] = str(source.get("version", "1")) + "-copy"
        clone.setdefault("source", {})["quality"] = "复制自 " + str(source.get("id", "")) + "；新参数尚未标定或验证。"
        self._draft["materials"].append(clone)
        self._rebuild_tree(("materials", len(self._draft["materials"]) - 1))
        self._update_status()
        self.draftChanged.emit(deepcopy(self._draft))

    def _add_entry(self) -> None:
        path = self._list_target()
        if self._locked or path is None:
            return
        self._flush_inputs()
        key = str(path[-1])
        materials = self._draft.get("materials", [])
        steel_id = next((m.get("id", "") for m in materials if m.get("kind") == "steel_bilinear"), "")
        templates = {
            "bars": {"id": new_id("bar"), "material_id": steel_id, "diameter": 0.0, "area": 0.0,
                     "eta": 0.0, "zeta": 0.0, "group": "bottom"},
            "supports": {"id": new_id("support"), "x": 0.0, "constrained": [False, False, False], "values": [0.0, 0.0, 0.0]},
            "loads": {"id": new_id("load"), "x": 0.0, "reference": [0.0, 0.0, 0.0]},
            "gauges": {"id": new_id("gauge"), "x1": 0.0, "x2": 0.0, "eta": 0.0},
            "sources": {"category": "assumption", "reference": "", "quality": "待核实", "version": "1"},
            "materials": {"id": new_id("material"), "version": "1", "kind": "steel_bilinear",
                          "E": 0.0, "fy": 0.0, "b": 0.0,
                          "source": {"category": "assumption", "reference": "", "quality": "新增参数待填写和验证", "version": "1"}},
        }
        if key not in templates:
            return
        values = self._get(path)
        values.append(templates[key])
        self._rebuild_tree(path + (len(values) - 1,))
        self._update_status()
        self.draftChanged.emit(deepcopy(self._draft))

    def _remove_entry(self) -> None:
        path = self._current_path
        if self._locked or not path or not isinstance(path[-1], int):
            return
        if QMessageBox.question(self, self._t("删除条目", "Delete item"), self._t("删除当前草稿条目？材料引用等问题会在模型检查时报告。", "Delete this draft item? Model checking will report reference problems.")) != QMessageBox.StandardButton.Yes:
            return
        del self._get(path[:-1])[path[-1]]
        self._invalid = {p: reason for p, reason in self._invalid.items() if p[:len(path) - 1] != path[:-1]}
        self._invalid_text = {p: value for p, value in self._invalid_text.items() if p[:len(path) - 1] != path[:-1]}
        self._raw_text = {p: value for p, value in self._raw_text.items() if p[:len(path) - 1] != path[:-1]}
        self._rebuild_tree(path[:-1])
        self._update_status()
        self.draftChanged.emit(deepcopy(self._draft))

    def _copy_prepared_bars(self) -> None:
        if self._locked or not self._prepared:
            return
        if self.has_pending_changes or self._prepared.get("document") != self._document:
            QMessageBox.warning(self, self._t("准备快照不匹配", "Prepared snapshot mismatch"), self._t("请先应用当前草稿并重新检查模型，再复制实际钢筋坐标。", "Apply the current draft and check again before copying actual bar coordinates."))
            return
        sections = self._prepared.get("sections", [])
        if not sections:
            return
        bars = []
        for fiber in sections[0].get("fibers", []):
            if fiber.get("role") != "steel":
                continue
            area = float(fiber["area"])
            bars.append({"id": new_id("bar"), "material_id": fiber["material_id"], "area": area,
                         "diameter": (4.0 * area / 3.141592653589793) ** 0.5,
                         "eta": fiber["eta"], "zeta": fiber["zeta"],
                         "group": "top" if float(fiber["eta"]) >= 0.0 else "bottom"})
        self._draft["model"]["reinforcement"]["bars"] = bars
        self._draft["model"]["reinforcement"]["mode"] = "direct"
        self._rebuild_tree(("model", "reinforcement", "bars"))
        self._update_status()
        self.draftChanged.emit(deepcopy(self._draft))

    def _inspection_item(self, parent: QTreeWidgetItem, key: Any, value: Any, path: tuple[Any, ...]) -> None:
        item_path = path + (key,)
        title = self._label(key)
        if isinstance(key, int):
            title = str(value.get("id", key)) if isinstance(value, dict) else str(key)
        summary = self._t("{count} 项", "{count} items").format(count=len(value)) if isinstance(value, (dict, list)) else str(value)
        item = QTreeWidgetItem(parent, [title, summary, _unit(item_path)])
        item.setData(0, _ROLE, {"value": value, "path": item_path, "expanded": False})
        if isinstance(value, (dict, list)) and value:
            QTreeWidgetItem(item, [self._t("展开查看", "Expand to inspect"), "", ""])

    def _expand_inspection(self, item: QTreeWidgetItem) -> None:
        data = item.data(0, _ROLE)
        if not isinstance(data, dict) or data.get("expanded"):
            return
        value = data["value"]
        item.takeChildren()
        iterable = value.items() if isinstance(value, dict) else enumerate(value)
        for key, child in iterable:
            self._inspection_item(item, key, child, tuple(data["path"]))
        data["expanded"] = True
        item.setData(0, _ROLE, data)

    def _inspection_selected(self) -> None:
        items = self.inspection.selectedItems()
        if not items:
            return
        data = items[0].data(0, _ROLE)
        value = data.get("value") if isinstance(data, dict) else None
        if isinstance(value, dict) and value.get("id"):
            self.objectSelected.emit(str(value["id"]))

    def _locate_diagnostic(self, item: QTreeWidgetItem, column: int = 0) -> None:
        diagnostic = item.data(0, _ROLE) or {}
        object_id = str(diagnostic.get("object_id", ""))
        field = str(diagnostic.get("field", ""))
        parent = self._id_items.get(object_id)
        base = tuple(parent.data(0, _ROLE) or ()) if parent is not None else ()
        candidates = [base + (field,), ("model", object_id, field), ("model", field), (field,)]
        if object_id == "control":
            candidates.insert(0, ("model", "control_" + field))
        parts = tuple(int(p) if p.isdigit() else p for p in re.findall(r"[^.\[\]/]+", field))
        candidates.extend((parts, ("model",) + parts))
        selected = parent
        focus_path = None
        for path in candidates:
            try:
                self._get(path)
            except (KeyError, IndexError, TypeError):
                continue
            focus_path = path
            while path not in self._path_items and path:
                path = path[:-1]
            selected = self._path_items.get(path, selected)
            break
        if selected is not None:
            self.tree.setCurrentItem(selected)
            self.tree.scrollToItem(selected)
        self.tabs.setCurrentIndex({"common": 0, "advanced": 3, "details": 4}.get(self._field_tier(focus_path or ()), 0))
        widget = self._inputs.get(focus_path)
        if widget is not None:
            widget.setStyleSheet("border:2px solid " + colors(self._theme)["error"])
            widget.setFocus(Qt.FocusReason.OtherFocusReason)
            {0: self.form_scroll, 3: self.advanced_scroll, 4: self.details_scroll}[self.tabs.currentIndex()].ensureWidgetVisible(widget)
        self.propertiesRequested.emit()
