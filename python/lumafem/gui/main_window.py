"""Qt desktop composition, explicit channel import and real-result selection.

This module only connects the application service and read-only renderers.
It contains no constitutive equations, solver substitutes or invented results.
"""
from __future__ import annotations

from copy import deepcopy
import json
import math
from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import QEvent, Qt, QTimer
from PySide6.QtGui import QAction, QActionGroup, QCloseEvent, QFont, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView, QAbstractSpinBox, QApplication, QCheckBox, QComboBox, QDialog,
    QDialogButtonBox, QDockWidget, QFileDialog, QFormLayout, QHBoxLayout,
    QHeaderView, QInputDialog, QLabel, QLineEdit, QMainWindow, QMessageBox,
    QPlainTextEdit, QPushButton, QScrollArea, QSizePolicy, QSpinBox, QTableWidget, QTableWidgetItem, QTabWidget,
    QTextEdit, QToolBar, QVBoxLayout, QWidget,
)

from ..application import ProjectController
from ..contracts import ContractError, diagnostic, validate_finite
from ..observations import import_csv, preview_csv
from ..storage import ProjectStore
from .branding import configure_application_branding
from .editor import ModelEditor
from .results import ResultsPanel
from .viewport import BeamViewport
from .i18n import LanguageManager, language, source_key, tr
from .workflow import MODULES, PHASES, TERMINATIONS, JobMonitor
from .themes import NativeWindowTheme, colors, palette, stylesheet

__all__ = ["MainWindow", "CsvImportDialog"]


def _number(text: str, label: str) -> float:
    value = float(text)
    if not math.isfinite(value):
        raise ValueError(tr("{label}必须是有限数值", label=label))
    return value


def _table(headers: list[str]) -> QTableWidget:
    widget = QTableWidget(0, len(headers))
    widget.setHorizontalHeaderLabels(headers)
    widget.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    widget.setAlternatingRowColors(True)
    widget.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
    return widget


def _fill(table: QTableWidget, rows: list[list[Any]]) -> None:
    table.setRowCount(len(rows))
    for row, values in enumerate(rows):
        for column, value in enumerate(values):
            text = tr("缺失") if value is None else format(value, ".17g") if isinstance(value, float) else str(value)
            table.setItem(row, column, QTableWidgetItem(text))


class CsvImportDialog(QDialog):
    """Preview raw and converted channels before adopting an immutable dataset.

    Column suggestions are editable and never imply confirmed physical units.
    The returned mapping follows observations.import_csv's version-one schema.
    """

    def __init__(self, path: str, document: dict[str, Any], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._status_spec: tuple[str, dict[str, Any]] | None = None
        self.setWindowTitle(tr("导入 CSV · 通道、单位与零点"))
        self.resize(1160, 760)
        self.path = str(Path(path).resolve())
        self.mapping: dict[str, Any] = {}
        self.dataset_preview: dict[str, Any] = {}
        self._document = deepcopy(document)
        self._preview: dict[str, Any] = {}
        self._channel_widgets: list[dict[str, Any]] = []
        layout = QVBoxLayout(self)
        source = QLabel(self.path)
        source.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(source)
        layout.addWidget(QLabel(tr("先核对原始列，再明确单位、符号和标定。导入保留原始顺序；不自动平移或峰值归一。")))
        row = QHBoxLayout()
        self.encoding = QComboBox()
        self.encoding.addItems(["utf-8-sig", "utf-8", "gb18030", "utf-16"])
        self.separator = QComboBox()
        for title, value in ((tr("自动识别分隔符"), None), (tr("逗号"), ","), (tr("制表符"), "\t"), (tr("分号"), ";"), (tr("竖线"), "|")):
            self.separator.addItem(title, value)
        refresh = QPushButton(tr("重新预览原始文件"))
        refresh.clicked.connect(self._read_source)
        for widget in (QLabel(tr("编码")), self.encoding, self.separator, refresh):
            row.addWidget(widget)
        row.addStretch()
        layout.addLayout(row)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs, 1)
        self.raw = _table([])
        self.tabs.addTab(self.raw, tr("原始预览"))
        mapping_page = QWidget()
        mapping_layout = QVBoxLayout(mapping_page)
        mapping_layout.addWidget(QLabel(tr("压力单位必须填写力标定多项式：c0,c1,…；输出单位为 N 或 kN，并记录标定依据。")))
        self.channels = QTableWidget(0, 9)
        self.channels.setHorizontalHeaderLabels([tr("物理通道"), tr("原始列（留空不导入）"), tr("原始单位"), tr("符号"), tr("输出偏置"), tr("压力标定系数"), tr("标定输出单位"), tr("标定依据"), tr("物理量")])
        self.channels.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        mapping_layout.addWidget(self.channels, 1)
        add = QPushButton(tr("增加应变 / 曲率通道"))
        add.clicked.connect(lambda: self._add_channel("strain_" + str(len(self._channel_widgets)), "", "microstrain", "strain"))
        mapping_layout.addWidget(add)
        self.tabs.addTab(mapping_page, tr("通道映射"))
        processing = QWidget()
        form = QFormLayout(processing)
        self.load_source = QComboBox()
        self.load_source.addItem(tr("两千斤顶合计 P1＋P2"), "sum")
        self.load_source.addItem(tr("独立总荷载 P"), "total")
        self.zero = QSpinBox()
        self.zero.setRange(-1, 999999)
        self.zero.setSpecialValueText(tr("保留仪器零点"))
        self.zero.setValue(-1)
        self.zero.setToolTip(tr("0 为第一行数据；-1 不减去任何参考行。"))
        self.settlement = QCheckBox(tr("按两支点连线修正跨中位移（需两支点通道）"))
        self.control_x = QLineEdit(str(document.get("model", {}).get("control_x", "")))
        supports = document.get("model", {}).get("supports", [])
        positions = sorted(float(item["x"]) for item in supports if item.get("constrained", [False, False])[1])
        self.support_x = QLineEdit(",".join(format(x, ".17g") for x in positions[:2]))
        self.time_scale, self.time_offset = QLineEdit("1"), QLineEdit("0")
        self.time_reference = QLineEdit()
        self.time_reference.setPlaceholderText(tr("仅在明确同步依据后填写；不会自动同步"))
        self.valid_interval = QLineEdit()
        self.valid_interval.setPlaceholderText(tr("预先冻结的 w 下限,上限 [mm]；留空使用实际有效范围"))
        self.reference_status = QComboBox()
        self.reference_status.addItem(tr("未核实"), "UNVERIFIED")
        self.reference_status.addItem(tr("已确认"), "CONFIRMED")
        self.aligned = QCheckBox(tr("已核对加载段、单位、零点与时间对应关系"))
        self.calibration_case = QCheckBox(tr("此数据参与材料标定（不能充当独立预测验证）"))
        self.synthetic = QCheckBox(tr("此文件为合成 / 默认演示数据"))
        self.metadata_path = QLineEdit()
        self.metadata_path.setPlaceholderText(tr("默认读取同名 .metadata.json；可填写明确元数据路径"))
        self.confirmed = QCheckBox(tr("我已核对映射与单位，并同意保存这套处理记录"))
        for label, widget in ((tr("总荷载来源"), self.load_source), (tr("参考数据行（从0开始）"), self.zero),
                              (tr("沉降处理"), self.settlement), (tr("控制点 x [mm]"), self.control_x),
                              (tr("两支点 x [mm]"), self.support_x), (tr("时间倍率"), self.time_scale),
                              (tr("时间偏移 [s]"), self.time_offset), (tr("同步依据"), self.time_reference),
                              (tr("比较有效位移区间"), self.valid_interval), (tr("观测口径状态"), self.reference_status),
                              (tr("对齐说明"), self.aligned), (tr("校准身份"), self.calibration_case),
                              (tr("来源分类"), self.synthetic), (tr("元数据"), self.metadata_path), (tr("确认处理"), self.confirmed)):
            form.addRow(label, widget)
        self.tabs.addTab(processing, tr("零点、沉降与同步"))
        self.converted = _table([tr("原始行"), "P [N]", "P1 [N]", "P2 [N]", "w [mm]", "t [s]", tr("P与双通道差 [N]"), tr("有效")])
        self.tabs.addTab(self.converted, tr("处理后预览"))
        self.status = QLabel(tr("尚未验证映射"))
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText(tr("保存映射并导入"))
        buttons.accepted.connect(self._adopt)
        buttons.rejected.connect(self.reject)
        preview = buttons.addButton(tr("预览转换结果"), QDialogButtonBox.ButtonRole.ActionRole)
        preview.clicked.connect(self._convert)
        layout.addWidget(buttons)
        self._caption_bindings: list[tuple[Any, str, str]] = []
        self._bind_static_captions()
        if parent is not None and hasattr(parent, "language_manager"):
            parent.language_manager.languageChanged.connect(self.retranslate_ui)
        self._read_source()

    def _bind_static_captions(self) -> None:
        """Retain declared source keys once; never bind numeric/source contents."""
        for widget in [self, *self.findChildren(QWidget)]:
            for name in ("text", "windowTitle", "toolTip", "placeholderText", "specialValueText"):
                getter = getattr(widget, name, None)
                setter = "set" + name[0].upper() + name[1:]
                # QAbstractSpinBox.text() is a value reader, not a caption and
                # has no setText. Only actual writable presentation properties
                # are bound; user/numeric text never becomes translation data.
                if (getter is not None and callable(getter) and callable(getattr(widget, setter, None))
                        and not isinstance(widget, (QLineEdit, QPlainTextEdit, QTextEdit))):
                    value = getter()
                    key = source_key(value) if isinstance(value, str) else None
                    if key:
                        self._caption_bindings.append((widget, "set" + name[0].upper() + name[1:], key))
                elif name in ("placeholderText", "toolTip") and getter is not None:
                    key = source_key(getter())
                    if key:
                        self._caption_bindings.append((widget, "set" + name[0].upper() + name[1:], key))
            if isinstance(widget, QComboBox):
                widget.setProperty("caption_sources", [source_key(widget.itemText(index)) for index in range(widget.count())])
            elif isinstance(widget, QTabWidget):
                widget.setProperty("caption_sources", [source_key(widget.tabText(index)) for index in range(widget.count())])
            elif isinstance(widget, QTableWidget):
                widget.setProperty("caption_sources", [source_key(widget.horizontalHeaderItem(index).text())
                    if widget.horizontalHeaderItem(index) else None for index in range(widget.columnCount())])

    def retranslate_ui(self, value: str) -> None:
        """Relabel an open import wizard while preserving all mapping inputs."""
        for widget, setter, source in self._caption_bindings:
            getattr(widget, setter)(tr(source))
        for widget in self.findChildren(QWidget):
            sources = widget.property("caption_sources")
            if sources is None:
                continue
            blocked = widget.blockSignals(True)
            try:
                for index, source in enumerate(sources):
                    if not source:
                        continue
                    if isinstance(widget, QComboBox):
                        widget.setItemText(index, tr(source))
                    elif isinstance(widget, QTabWidget):
                        widget.setTabText(index, tr(source))
                    elif isinstance(widget, QTableWidget) and widget.horizontalHeaderItem(index):
                        widget.horizontalHeaderItem(index).setText(tr(source))
            finally:
                widget.blockSignals(blocked)
        if self._status_spec:
            source, values = self._status_spec
            self.status.setText(tr(source, **{key: tr(item) if key == "source" else item for key, item in values.items()}))

    def _set_status(self, source: str, **values: Any) -> None:
        self._status_spec = (source, values)
        self.status.setText(tr(source, **{key: tr(item) if key == "source" else item for key, item in values.items()}))

    def _read_source(self) -> None:
        try:
            self._preview = preview_csv(self.path, self.encoding.currentText(), self.separator.currentData())
            columns = self._preview["columns"]
            self.raw.setColumnCount(len(columns))
            self.raw.setHorizontalHeaderLabels(columns)
            _fill(self.raw, self._preview["preview_rows"])
            self.channels.setRowCount(0)
            self._channel_widgets.clear()
            suggestions = (("P", "total_kN", "kN", "force"), ("P1", "jack1_kN", "kN", "force"),
                           ("P2", "jack2_kN", "kN", "force"), ("midpoint", "midpoint_mm", "mm", "displacement"),
                           ("left_support", "left_support_mm", "mm", "displacement"),
                           ("right_support", "right_support_mm", "mm", "displacement"),
                           ("time", "time_s", "s", "time"), ("top_strain", "top_gauge_microstrain", "microstrain", "strain"))
            for name, column, unit, quantity in suggestions:
                self._add_channel(name, column if column in columns else "", unit, quantity)
            self._set_status("原始 {rows} 行；缺失单元格 {missing}。列名建议尚未确认单位。", rows=self._preview['row_count'], missing=self._preview['missing_cells'])
            self.confirmed.setChecked(False)
        except Exception as error:
            self._show_error(error)

    def _add_channel(self, name: str, column: str, unit: str, quantity: str) -> None:
        row = self.channels.rowCount()
        self.channels.insertRow(row)
        name_edit = QLineEdit(name)
        if name in ("P", "P1", "P2", "midpoint", "left_support", "right_support", "time"):
            name_edit.setReadOnly(True)
        column_box = QComboBox()
        column_box.addItem(tr("不导入"), "")
        for item in self._preview.get("columns", []):
            column_box.addItem(item, item)
        index = column_box.findData(column)
        column_box.setCurrentIndex(max(index, 0))
        units = QComboBox()
        units.setEditable(True)
        units.addItems([unit, *[x for x in ("N", "kN", "mm", "m", "s", "ms", "microstrain", "1", "1/mm", "MPa", "kPa", "bar") if x != unit]])
        sign = QComboBox()
        sign.addItems(["+1", "-1"])
        offset, coefficients, reference = QLineEdit("0"), QLineEdit(), QLineEdit()
        output = QComboBox()
        output.addItems(["N", "kN"])
        kinds = QComboBox()
        for title, code in (("力", "force"), ("位移", "displacement"), ("时间", "time"), ("应变", "strain"), ("曲率", "curvature")):
            kinds.addItem(tr(title), code)
        kinds.setCurrentIndex(max(0, kinds.findData(quantity)))
        widgets = [name_edit, column_box, units, sign, offset, coefficients, output, reference, kinds]
        for index, widget in enumerate(widgets):
            self.channels.setCellWidget(row, index, widget)
        self._channel_widgets.append(dict(zip(("name", "column", "unit", "sign", "offset", "coefficients", "output", "reference", "quantity"), widgets)))
        column_box.setProperty("caption_sources", ["不导入", *([None] * (column_box.count() - 1))])
        kinds.setProperty("caption_sources", ["力", "位移", "时间", "应变", "曲率"])

    def _mapping(self) -> dict[str, Any]:
        channels = {}
        for widgets in self._channel_widgets:
            column = widgets["column"].currentData()
            if not column:
                continue
            name = widgets["name"].text().strip()
            if not name or name in channels:
                raise ValueError(tr("通道名称必须非空且不能重复"))
            spec = {"column": column, "unit": widgets["unit"].currentText().strip(),
                    "sign": int(widgets["sign"].currentText()), "offset": _number(widgets["offset"].text(), tr("偏置")),
                    "quantity": widgets["quantity"].currentData()}
            coefficients = widgets["coefficients"].text().strip()
            if coefficients:
                spec["calibration"] = {"coefficients": [_number(x, tr("标定系数")) for x in coefficients.split(",")],
                                       "output_unit": widgets["output"].currentText(), "reference": widgets["reference"].text().strip()}
            if name == "time" and self.time_reference.text().strip():
                spec["clock"] = {"scale": _number(self.time_scale.text(), tr("时间倍率")),
                                 "offset": _number(self.time_offset.text(), tr("时间偏移")), "reference": self.time_reference.text().strip()}
            elif name == "time" and (self.time_scale.text().strip() != "1" or self.time_offset.text().strip() != "0"):
                raise ValueError(tr("时间修正需填写明确的同步依据"))
            channels[name] = spec
        result = {"encoding": self.encoding.currentText(), "delimiter": self._preview.get("delimiter"), "channels": channels,
                  "load_source": self.load_source.currentData(), "zero_row": None if self.zero.value() < 0 else self.zero.value(),
                  "settlement_correction": self.settlement.isChecked(), "reference_status": self.reference_status.currentData(),
                  "alignment_confirmed": self.aligned.isChecked(), "calibration_case": self.calibration_case.isChecked(),
                  "synthetic": self.synthetic.isChecked()}
        if self.control_x.text().strip() or self.support_x.text().strip():
            result["control_x"] = _number(self.control_x.text(), tr("控制点坐标"))
            result["support_x"] = [_number(x, tr("支点坐标")) for x in self.support_x.text().split(",")]
        if self.valid_interval.text().strip():
            interval = [_number(x, tr("比较区间")) for x in self.valid_interval.text().split(",")]
            if len(interval) != 2 or interval[0] >= interval[1]:
                raise ValueError(tr("比较区间需填写递增的两个位移值"))
            result["valid_interval"] = interval
        if self.metadata_path.text().strip():
            result["metadata_path"] = self.metadata_path.text().strip()
        return result

    def _convert(self) -> bool:
        try:
            self.mapping = self._mapping()
            self.dataset_preview = import_csv(self.path, self.mapping)
            keys = ("source_row", "P", "P1", "P2", "w", "time", "total_difference", "valid")
            _fill(self.converted, [[row.get(key) for key in keys] for row in self.dataset_preview["rows"][:100]])
            label = "合成演示，非真实试验" if self.dataset_preview["synthetic"] else "导入来源待核实"
            notes = "; ".join(item["message"] for item in self.dataset_preview.get("diagnostics", []))
            self._set_status("{source}；{rows} 行。{notes} 真实物理验证不会因导入自动通过。", source=label, rows=len(self.dataset_preview['rows']), notes=notes)
            self.tabs.setCurrentWidget(self.converted)
            return True
        except Exception as error:
            self._show_error(error)
            return False

    def _show_error(self, error: Exception) -> None:
        self._set_status("无法导入：{error}", error=str(error))
        QMessageBox.warning(self, tr("请修正导入设置"), str(error))

    def _adopt(self) -> None:
        if not self.confirmed.isChecked():
            QMessageBox.information(self, tr("核对处理记录"), tr("请在“零点、沉降与同步”页确认映射和单位。"))
            self.tabs.setCurrentIndex(2)
            return
        if self._convert():
            self.accept()


class MainWindow(QMainWindow):
    """One-document Qt/VTK workbench composed around ProjectController.

    The model-preview/result selector makes the displayed snapshot explicit.
    Full steps are requested lazily and never reconstructed from plot points.
    """

    def __init__(self, template: dict[str, Any] | None = None, *, case_dir: str | Path | None = None,
                 controller: ProjectController | None = None) -> None:
        application = QApplication.instance()
        if not isinstance(application, QApplication):
            raise RuntimeError("Create a QApplication before constructing the LumaFEM main window.")
        icon = configure_application_branding(application)
        super().__init__()
        self.setWindowIcon(icon)
        self.setObjectName("LumaFEMMainWindow")
        self.setWindowTitle("LumaFEM")
        self.setFont(QFont("Microsoft YaHei", 9))
        self.resize(1500, 980)
        self.setDockOptions(QMainWindow.DockOption.AllowNestedDocks | QMainWindow.DockOption.AllowTabbedDocks)
        self.controller = controller or ProjectController(self)
        self._template = deepcopy(template)
        self._case_dir = Path(case_dir) if case_dir is not None else Path(__file__).resolve().parents[3] / "development_doc" / "runtime" / "CR-NUM-002"
        self._close_after_run = False
        self._selection_sync = False
        self._locked = False
        self._editor_lock_reason = ""
        self._model_prepared: dict[str, Any] = {}
        self._run_prepared: dict[str, Any] = {}
        self._viewport_model_key: tuple[Any, ...] | None = None
        self._viewport_step_id: str | None = None
        self._last_diagnostics: list[dict[str, Any]] = []
        self.actions: dict[str, QAction] = {}
        self._module = "geometry"
        self._module_sync = False
        self._frame_available = True
        self._last_progress: dict[str, Any] = {}
        self._last_summary: dict[str, Any] = {}
        self._phase_notice: tuple[str, dict[str, Any]] | None = None
        self._run_list_key: tuple[Any, ...] | None = None
        self._last_job_running = False
        self._theme = "dark"
        self.language_manager = LanguageManager(self)
        central = QWidget()
        central_layout = QVBoxLayout(central)
        central_layout.setContentsMargins(4, 4, 4, 4)
        heading = QHBoxLayout()
        self.display_mode = QComboBox()
        self.display_mode.setObjectName("displayMode")
        self.display_mode.addItem(tr("当前模型 · 检查预览"), "model")
        self.display_mode.addItem(tr("已选运行 · 真实结果"), "result")
        self.display_mode.setAccessibleName(tr("切换模型预览与冻结结果"))
        self.display_mode.currentIndexChanged.connect(self._display_mode_changed)
        self.snapshot_label = QLabel(tr("尚未打开工程"))
        self.snapshot_label.setWordWrap(True)
        self.snapshot_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        heading.addWidget(self.display_mode)
        heading.addWidget(self.snapshot_label, 1)
        central_layout.addLayout(heading)
        self.viewport = BeamViewport(self)
        central_layout.addWidget(self.viewport, 1)
        self.setCentralWidget(central)
        self.editor = ModelEditor(self)
        self.results = ResultsPanel(self)
        self.results.set_step_provider(self.controller.get_step)
        self.navigation_tabs = QTabWidget()
        self.navigation_tabs.setObjectName("navigationTabs")
        self.navigation_tabs.addTab(self.editor.navigation_widget(), tr("模型"))
        self.navigation_tabs.addTab(self.results.navigation_widget(), tr("结果"))
        self.navigation_tabs.currentChanged.connect(self._navigation_tab_changed)
        self.navigation_dock = self._dock(tr("模型树 / 结果树"), "navigationDock", self.navigation_tabs, Qt.DockWidgetArea.LeftDockWidgetArea)
        self.editor_dock = self._dock(tr("属性与检查"), "modelDock", self.editor, Qt.DockWidgetArea.RightDockWidgetArea)
        self.properties_dock = self.editor_dock
        self.bottom_tabs = QTabWidget()
        self.bottom_tabs.setObjectName("bottomTabs")
        # Large result pages and the monitor scroll inside their dock instead
        # of imposing the tallest page's minimum on the entire main window.
        self.result_scroll = QScrollArea()
        self.result_scroll.setWidgetResizable(True)
        self.result_scroll.setWidget(self.results)
        self.result_scroll.setMinimumSize(0, 0)
        self.bottom_tabs.addTab(self.result_scroll, tr("结果"))
        self.job_monitor = JobMonitor(self)
        self.monitor_scroll = QScrollArea()
        self.monitor_scroll.setWidgetResizable(True)
        self.monitor_scroll.setWidget(self.job_monitor)
        self.monitor_scroll.setMinimumSize(0, 0)
        self.bottom_tabs.addTab(self.monitor_scroll, tr("作业监视"))
        self.messages = QPlainTextEdit()
        self.messages.setObjectName("workbenchMessages")
        self.messages.setReadOnly(True)
        self.messages.setMaximumBlockCount(1000)
        self.bottom_tabs.addTab(self.messages, tr("消息"))
        self.results_dock = self._dock(tr("结果与作业"), "resultsDock", self.bottom_tabs, Qt.DockWidgetArea.BottomDockWidgetArea)
        self.monitor_dock = self.results_dock
        self.phase_label, self.save_label = QLabel(), QLabel()
        self.phase_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.statusBar().addWidget(self.phase_label, 1)
        self.statusBar().addPermanentWidget(self.save_label)
        self._make_actions()
        self._make_context_bar()
        self._connect()
        self.native_theme = NativeWindowTheme(self)
        self.language_manager.languageChanged.connect(self._retranslate_ui)
        self.set_theme("dark")
        self._retranslate_ui("zh_CN")
        self.restore_layout()
        self._state_changed(self.controller.state)

    def _dock(self, title: str, name: str, widget: QWidget, area: Qt.DockWidgetArea) -> QDockWidget:
        dock = QDockWidget(title, self)
        dock.setObjectName(name)
        dock.setMinimumSize(0, 0)
        dock.setWidget(widget)
        self.addDockWidget(area, dock)
        return dock

    def _action(self, key: str, text: str, callback: Callable[[], Any], shortcut: str | QKeySequence.StandardKey | None = None) -> QAction:
        action = QAction(text, self)
        action.setObjectName(key + "Action")
        action.setProperty("source_text", text)
        if shortcut is not None:
            action.setShortcut(shortcut)
        action.triggered.connect(lambda checked=False: self._guard(callback))
        self.actions[key] = action
        return action

    def _make_actions(self) -> None:
        self._menus: list[tuple[Any, str]] = []
        definitions = (
            ("new", tr("新建 NC0 默认工程"), self.new_project, QKeySequence.StandardKey.New),
            ("open", tr("打开工程…"), self.open_project, QKeySequence.StandardKey.Open),
            ("save", tr("保存"), self.save_project, QKeySequence.StandardKey.Save),
            ("save_as", tr("另存为…"), lambda: self.save_project(True), QKeySequence.StandardKey.SaveAs),
            ("backup", tr("创建一致备份…"), self.backup_project, None),
            ("recover", tr("打开恢复副本…"), self.restore_recovery, None),
            ("close", tr("退出"), self.close, QKeySequence.StandardKey.Quit),
            ("apply", tr("应用表单"), self.editor.apply_button.click, "Ctrl+Return"),
            ("revert", tr("还原表单"), self.editor.revert_button.click, None),
            ("undo", tr("撤销已应用修改"), self._undo, QKeySequence.StandardKey.Undo),
            ("redo", tr("重做"), self._redo, QKeySequence.StandardKey.Redo),
            ("check", tr("检查模型"), self.check_model, "F6"),
            ("run", tr("运行分析"), self.start_run, "F5"),
            ("cancel", tr("取消分析"), self.controller.cancel_run, "Shift+F5"),
            ("history", tr("选择历史运行…"), self.choose_run, None),
            ("import", tr("导入试验格式 CSV…"), self.import_data, "Ctrl+I"),
            ("synthetic", tr("导入合成通道示例…"), self.import_synthetic, None),
            ("export", tr("导出数值 CSV…"), self.export_data, "Ctrl+E"),
            ("curve_png", tr("导出曲线 PNG…"), lambda: self.export_png("curve"), None),
            ("view_png", tr("导出视口 PNG…"), lambda: self.export_png("viewport"), None),
            ("about", tr("关于与能力边界"), self.about, None),
        )
        for definition in definitions:
            self._action(*definition)
        for title, keys in ((tr("工程"), ("new", "open", "save", "save_as", "backup", "recover", "close")),
                            (tr("编辑"), ("apply", "revert", "undo", "redo")),
                            (tr("分析"), ("check", "run", "cancel", "history")),
                            (tr("数据与导出"), ("import", "synthetic", "export", "curve_png", "view_png")),
                            (tr("帮助"), ("about",))):
            menu = self.menuBar().addMenu(title)
            self._menus.append((menu, title))
            for key in keys:
                menu.addAction(self.actions[key])
        view = self.menuBar().addMenu(tr("视图"))
        self._menus.append((view, tr("视图")))
        view.addAction(self.navigation_dock.toggleViewAction())
        view.addAction(self.editor_dock.toggleViewAction())
        view.addAction(self.results_dock.toggleViewAction())
        for key, title, callback in (("restore_layout", tr("恢复默认布局"), self.restore_layout),
                                     ("fit", tr("适合窗口"), self.viewport.reset_camera),
                                     ("front", tr("平面视图"), self.viewport.front_view),
                                     ("clear_selection", tr("清除选择"), self.clear_selection),
                                     ("job_monitor", tr("作业监视"), self.show_job_monitor)):
            view.addAction(self._action(key, title, callback))
        languages = self.menuBar().addMenu(tr("界面语言"))
        self._menus.append((languages, tr("界面语言")))
        self._language_actions: dict[str, QAction] = {}
        group = QActionGroup(self)
        group.setExclusive(True)
        for code, title in (("zh_CN", tr("中文")), ("en", "English")):
            action = languages.addAction(title)
            action.setCheckable(True)
            action.triggered.connect(lambda checked=False, value=code: self.set_language(value))
            group.addAction(action)
            self._language_actions[code] = action
        toolbar = QToolBar(tr("常用操作"), self)
        toolbar.setObjectName("mainToolbar")
        self.main_toolbar = toolbar
        toolbar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        toolbar.setMovable(False)
        self.addToolBar(toolbar)
        for key in ("new", "open", "save", "undo", "redo", "check", "run", "cancel", "import", "export"):
            if key in {"undo", "check", "import"}:
                toolbar.addSeparator()
            toolbar.addAction(self.actions[key])

    @property
    def language(self) -> str:
        """Current session GUI language, independent of engineering documents."""
        return self.language_manager.language

    @property
    def current_module(self) -> str:
        return self._module

    @property
    def theme(self) -> str:
        """Current display theme, never a project or solver preference."""
        return self._theme

    def set_theme(self, value: str) -> None:
        """Apply presentation colors without rebuilding data or view state."""
        colors(value)  # Validate before changing any part of the display.
        self._theme = value
        self.setPalette(palette(value))
        self.setStyleSheet(stylesheet(value))
        for component in (self.editor, self.results, self.viewport):
            component.set_theme(value)
        self.native_theme.set_theme(value)
        self.theme_combo.blockSignals(True)
        self.theme_combo.setCurrentIndex(self.theme_combo.findData(value))
        self.theme_combo.blockSignals(False)

    def _make_context_bar(self) -> None:
        self.context_toolbar = QToolBar(self)
        self.context_toolbar.setObjectName("contextToolbar")
        self.context_toolbar.setMovable(False)
        self.addToolBarBreak()
        self.addToolBar(self.context_toolbar)
        page = QWidget()
        self.context_page = page
        row = QHBoxLayout(page)
        row.setContentsMargins(4, 2, 4, 2)
        self.module_label = QLabel()
        self.module_combo = QComboBox()
        self.module_combo.setObjectName("moduleCombo")
        self.module_combo.setMinimumContentsLength(10)
        self.module_combo.setMaximumWidth(245)
        for key, title, _ in MODULES:
            self.module_combo.addItem(title, key)
        self.module_combo.currentIndexChanged.connect(lambda *_: self.set_module(self.module_combo.currentData()))
        self.context_prompt = QLabel()
        self.context_prompt.setObjectName("contextPrompt")
        self.context_prompt.setWordWrap(False)
        self.context_prompt.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.language_combo = QComboBox()
        self.language_combo.setObjectName("languageCombo")
        self.language_combo.addItem(tr("中文"), "zh_CN")
        self.language_combo.addItem("English", "en")
        self.language_combo.currentIndexChanged.connect(lambda *_: self.set_language(self.language_combo.currentData()))
        self.theme_combo = QComboBox()
        self.theme_combo.setObjectName("themeCombo")
        self.theme_combo.addItem("石墨深色", "dark")
        self.theme_combo.addItem("分层浅色", "light")
        self.theme_combo.currentIndexChanged.connect(lambda *_: self.set_theme(self.theme_combo.currentData()))
        for widget in (self.module_label, self.module_combo, self.context_prompt, self.theme_combo, self.language_combo):
            row.addWidget(widget, 1 if widget is self.context_prompt else 0)
        # A Preferred vertical policy here allowed QToolBar to absorb hundreds
        # of blank pixels. One compact fixed-height row is deliberate.
        page.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        page.setFixedHeight(38)
        self.context_toolbar.addWidget(page)
        self.context_toolbar.setMaximumHeight(46)
        self._view_shortcuts = []
        for sequence, operation in (("F", self.viewport.reset_camera), ("Shift+F", self.viewport.front_view), ("Escape", self.clear_selection)):
            shortcut = QShortcut(QKeySequence(sequence), self.viewport)
            shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            shortcut.activated.connect(lambda callback=operation: self._view_command(callback))
            self._view_shortcuts.append(shortcut)

    def _view_command(self, callback: Callable[[], Any]) -> None:
        focus = QApplication.focusWidget()
        if isinstance(focus, (QLineEdit, QTextEdit, QPlainTextEdit, QComboBox, QAbstractSpinBox)):
            return
        self._guard(callback)

    def set_language(self, value: str) -> None:
        """Switch labels without applying drafts, loading frames or moving cameras."""
        self.language_manager.set_language(value)

    def _retranslate_ui(self, value: str) -> None:
        self.editor.retranslate_ui(value)
        self.results.retranslate_ui(value)
        self.viewport.retranslate_ui(value)
        for action in self.actions.values():
            action.setText(tr(action.property("source_text")))
        for menu, source in self._menus:
            menu.setTitle(tr(source))
        for code, action in self._language_actions.items():
            action.setChecked(code == value)
        self.language_combo.blockSignals(True)
        self.language_combo.setCurrentIndex(self.language_combo.findData(value))
        self.language_combo.blockSignals(False)
        self.language_combo.setAccessibleName(tr("界面语言"))
        self.theme_combo.setAccessibleName("界面主题" if value == "zh_CN" else "Interface theme")
        self.theme_combo.setItemText(0, "石墨深色" if value == "zh_CN" else "Graphite dark")
        self.theme_combo.setItemText(1, "分层浅色" if value == "zh_CN" else "Layered light")
        self.module_label.setText(tr("模块"))
        self.module_combo.blockSignals(True)
        for index, (_, title, _) in enumerate(MODULES):
            self.module_combo.setItemText(index, tr(title))
        self.module_combo.blockSignals(False)
        self.module_combo.setAccessibleName(tr("模块"))
        for index, source in enumerate((tr("当前模型 · 检查预览"), tr("已选运行 · 真实结果"))):
            self.display_mode.setItemText(index, tr(source))
        self.display_mode.setAccessibleName(tr("切换模型预览与冻结结果"))
        for index, source in enumerate((tr("模型"), tr("结果"))):
            self.navigation_tabs.setTabText(index, tr(source))
        for index, source in enumerate((tr("结果"), tr("作业监视"), tr("消息"))):
            self.bottom_tabs.setTabText(index, tr(source))
        for dock, source in ((self.navigation_dock, tr("模型树 / 结果树")), (self.editor_dock, tr("属性与检查")), (self.results_dock, tr("结果与作业"))):
            dock.setWindowTitle(tr(source))
        self.main_toolbar.setWindowTitle(tr("常用操作"))
        self.context_toolbar.setWindowTitle(tr("模块"))
        self.messages.setAccessibleName(tr("原始诊断与身份详情"))
        self.job_monitor.retranslate_ui()
        self._update_context_prompt()
        self._update_snapshot_label()
        self._state_changed(self.controller.state)
        if self._phase_notice:
            self._render_phase_notice()
        elif self._last_summary:
            self._render_finished()
        elif self._last_progress:
            self._render_progress()
        else:
            self.phase_label.setText(tr("就绪"))

    def _update_context_prompt(self) -> None:
        source = next((prompt for key, _, prompt in MODULES if key == self._module), MODULES[0][2])
        self.context_prompt.setText(tr(source))
        self.context_prompt.setToolTip(tr(source))

    def set_module(self, key: str) -> bool:
        """Navigate supported workflow without applying or discarding a draft."""
        if self._module_sync or key not in {item[0] for item in MODULES}:
            return False
        self._module_sync = True
        try:
            self._module = key
            self.module_combo.blockSignals(True)
            self.module_combo.setCurrentIndex(self.module_combo.findData(key))
            self.module_combo.blockSignals(False)
            if key == "results":
                self.navigation_tabs.setCurrentIndex(1)
                self.display_mode.setCurrentIndex(1)
                self.show_results()
            elif key == "job":
                self.navigation_tabs.setCurrentIndex(0)
                self.display_mode.setCurrentIndex(0)
                self.show_job_monitor()
            else:
                self.navigation_tabs.setCurrentIndex(0)
                self.display_mode.setCurrentIndex(0)
                self.editor.set_module(key)
                self.show_properties()
            self._update_context_prompt()
        finally:
            self._module_sync = False
        return True

    def _navigation_tab_changed(self, index: int) -> None:
        if self._module_sync:
            return
        self.set_module("results" if index == 1 else self._module if self._module != "results" else "geometry")

    def _display_mode_changed(self, *_: Any) -> None:
        self._guard(self._refresh_viewport)
        self._state_changed(self.controller.state)

    def show_properties(self) -> None:
        self.editor_dock.show()
        self.editor_dock.raise_()

    def show_results(self) -> None:
        self.results_dock.show()
        self.bottom_tabs.setCurrentIndex(0)
        self.results_dock.raise_()

    def show_job_monitor(self) -> None:
        self.results_dock.show()
        self.bottom_tabs.setCurrentIndex(1)
        self.results_dock.raise_()

    def restore_layout(self) -> None:
        """Restore essential dock reachability at supported desktop sizes."""
        for dock, area in ((self.navigation_dock, Qt.DockWidgetArea.LeftDockWidgetArea),
                           (self.editor_dock, Qt.DockWidgetArea.RightDockWidgetArea),
                           (self.results_dock, Qt.DockWidgetArea.BottomDockWidgetArea)):
            dock.setFloating(False)
            self.addDockWidget(area, dock)
            dock.show()
        self.resizeDocks([self.navigation_dock, self.editor_dock], [210, 285], Qt.Orientation.Horizontal)
        self.resizeDocks([self.results_dock], [165], Qt.Orientation.Vertical)

    def clear_selection(self) -> None:
        """Clear object highlight while retaining the exact chosen result frame."""
        self._guard(lambda: self.controller.select({"object_id": "", "section_id": "", "point_id": "", "element_id": "", "node_id": ""}))

    def _load_result_run(self, run_id: str) -> None:
        if self.controller.state.get("running"):
            return
        if self.controller.load_run(run_id):
            self.set_module("results")

    def _frame_availability_changed(self, available: bool) -> None:
        self._frame_available = available
        if not available:
            self.viewport.set_step(None)
            self._viewport_step_id = None
            self.snapshot_label.setText(tr("所选帧不可用；已清除视口结果。"))

    def _refresh_run_list(self, state: dict[str, Any]) -> None:
        key = (state.get("working_path"), state.get("run_id"), bool(state.get("running")))
        if key == self._run_list_key:
            return
        self._run_list_key = key
        if not state.get("running"):
            self._guard(lambda: self.results.set_available_runs(self.controller.available_runs() if state.get("working_path") else []))

    def _update_snapshot_label(self) -> None:
        if self.display_mode.currentData() == "result":
            if not self._run_prepared:
                self.snapshot_label.setText(tr("尚无已保存运行"))
                self.snapshot_label.setToolTip("")
                return
            if not self._frame_available:
                self.snapshot_label.setText(tr("所选帧不可用；已清除视口结果。"))
                return
            flags = [tr("冻结结果浏览")]
            if getattr(self, "_run_analysis_read_only", False):
                flags.append(tr("历史快照只读"))
            if self.controller.state.get("results_stale"):
                flags.append(tr("结果属于旧模型修订"))
            self.snapshot_label.setText(tr("运行结果 · {status}", status=" / ".join(flags)))
            self.snapshot_label.setToolTip(json.dumps((self._run_prepared or {}).get("identity", {}), ensure_ascii=False, indent=2))
        else:
            self.snapshot_label.setText(tr("当前模型预览 · {status}", status=tr(tr("已检查，显示实际离散") if self._model_prepared else tr("请先应用并检查模型"))))
            self.snapshot_label.setToolTip("")

    def _set_notice(self, source: str, **values: Any) -> None:
        self._phase_notice = (source, values)
        self._render_phase_notice()

    def _render_phase_notice(self) -> None:
        if self._phase_notice:
            source, values = self._phase_notice
            self.phase_label.setText(tr(source, **values))

    def _connect(self) -> None:
        self.editor.applyRequested.connect(lambda value: self._guard(lambda: self.controller.apply_document(value)))
        self.editor.revertRequested.connect(self.controller.revert_document)
        self.editor.draftChanged.connect(self.controller.set_draft)
        self.editor.objectSelected.connect(self._editor_object_selected)
        self.editor.moduleRequested.connect(self.set_module)
        self.editor.propertiesRequested.connect(self.show_properties)
        self.viewport.objectSelected.connect(self._object_selected)
        self.viewport.selectionStatus.connect(self.context_prompt.setText)
        self.results.selectionRequested.connect(lambda value: self._guard(lambda: self.controller.select(value)))
        self.results.runRequested.connect(lambda value: self._guard(lambda: self._load_result_run(value)))
        self.results.viewRequested.connect(self.show_results)
        self.results.frameAvailabilityChanged.connect(self._frame_availability_changed)
        self.job_monitor.submitRequested.connect(lambda: self._guard(self.start_run))
        self.job_monitor.cancelRequested.connect(self.controller.cancel_run)
        self.results.zeroChanged.connect(self.controller.set_observation)
        self.controller.documentChanged.connect(self._document_changed)
        self.controller.preparedChanged.connect(self._prepared_changed)
        self.controller.runChanged.connect(self._run_changed)
        self.controller.stepAccepted.connect(lambda value: self._guard(lambda: self.results.append_step(value)))
        self.controller.progressChanged.connect(self._progress)
        self.controller.runFinished.connect(self._finished)
        self.controller.errorRaised.connect(self._error)
        self.controller.selectionChanged.connect(self._selection_changed)
        self.controller.importsChanged.connect(self.results.set_imports)
        self.controller.observationChanged.connect(self._observation_changed)
        self.controller.stateChanged.connect(self._state_changed)

    def _guard(self, operation: Callable[[], Any]) -> Any:
        try:
            return operation()
        except Exception as error:
            detail = error.diagnostic if isinstance(error, ContractError) else diagnostic("desktop_error", str(error), stage="desktop")
            self._error(detail)
            QMessageBox.warning(self, tr("操作未完成"), tr("操作未完成；原始诊断见消息详情。") + "\n\n" + str(detail.get("message", error)))
            return None

    def _document_changed(self, document: dict[str, Any]) -> None:
        self.editor.set_document(document)
        self._run_list_key = None

    def _prepared_changed(self, prepared: dict[str, Any]) -> None:
        self._model_prepared = deepcopy(prepared)
        self.editor.set_prepared(prepared or None)
        self._guard(self._refresh_viewport)

    def _run_changed(self, run: dict[str, Any]) -> None:
        self._run_prepared = deepcopy(run.get("prepared") or {})
        self._last_progress = {}
        self._frame_available = True
        self._last_summary = deepcopy(run.get("summary") or {})
        self.job_monitor.set_summary(self._last_summary)
        self._run_analysis_read_only = bool(run.get("analysis_read_only"))
        self.results.set_step_provider(self.controller.get_step)
        self._guard(lambda: self.results.set_run(run))
        self.results.set_imports(self.controller.imports)
        self.display_mode.blockSignals(True)
        self.display_mode.setCurrentIndex(1 if run.get("prepared") else 0)
        self.display_mode.blockSignals(False)
        self._guard(self._refresh_viewport)
        self._observation_changed(self.controller.observation)

    def _refresh_viewport(self, *_: Any) -> None:
        if self.display_mode.currentData() == "result":
            prepared = self._run_prepared
            self._set_view_model(prepared)
            selection = self.controller.selection
            if prepared and selection.get("step_id") and self._frame_available:
                if selection["step_id"] != self._viewport_step_id:
                    try:
                        step = self.controller.get_step(selection)
                    except Exception:
                        self._frame_availability_changed(False)
                        raise
                    self.viewport.set_step(step)
                    self._viewport_step_id = selection["step_id"]
                self.viewport.set_selection(selection)
            else:
                if self._viewport_step_id is not None:
                    self.viewport.set_step(None)
                    self._viewport_step_id = None
        else:
            prepared = self._model_prepared
            self._set_view_model(prepared)
            if self._viewport_step_id is not None:
                self.viewport.set_step(None)
                self._viewport_step_id = None
        self._update_snapshot_label()

    def _set_view_model(self, prepared: dict[str, Any]) -> None:
        identity = prepared.get("identity", {})
        key = (prepared.get("prepared_hash"), identity.get("snapshot_id"), identity.get("run_id")) if prepared else None
        if key != self._viewport_model_key:
            self.viewport.set_prepared(prepared or None)
            self._viewport_model_key = key
            self._viewport_step_id = None

    def _selection_changed(self, selection: dict[str, Any]) -> None:
        if self._selection_sync:
            return
        self._selection_sync = True
        try:
            model_origin = getattr(self, "_model_selection", False)
            if not model_origin:
                self._guard(lambda: self.results.set_selection(selection))
            if selection.get("object_id"):
                current = self.controller.document
                identity = self._run_prepared.get("identity", {})
                if model_origin or self.display_mode.currentData() == "model" or (
                    identity.get("revision_id") == current.get("revision_id") and identity.get("project_id") == current.get("project_id")):
                    self.editor.select_object(selection["object_id"])
            if selection.get("step_id") and not model_origin:
                if self.display_mode.currentData() != "result":
                    self.display_mode.setCurrentIndex(1)
                else:
                    self._guard(self._refresh_viewport)
            else:
                self.viewport.set_selection({"object_id": selection.get("object_id", "")} if model_origin else selection)
        finally:
            self._selection_sync = False

    def _object_selected(self, object_id: str) -> None:
        if not self._selection_sync:
            if self.display_mode.currentData() == "model":
                self._editor_object_selected(object_id)
            else:
                self._guard(lambda: self.controller.select({"object_id": object_id}))

    def _editor_object_selected(self, object_id: str) -> None:
        if self._selection_sync:
            return
        self._model_selection = True
        try:
            self.display_mode.setCurrentIndex(0)
            self._guard(lambda: self.controller.select({"object_id": object_id}))
        finally:
            self._model_selection = False

    def _observation_changed(self, definition: dict[str, Any]) -> None:
        mode = definition.get("display_mode", "increment")
        index = {"increment": 0, "total": 1, "reference": 2}.get(mode, 0)
        self.results.zero_combo.blockSignals(True)
        self.results.zero_combo.setCurrentIndex(index)
        self.results.zero_combo.blockSignals(False)
        # These are display-only values; analysis preparation stays immutable.
        self.results._reference_id = str(definition.get("reference_step_id", ""))
        self.results._refresh_curve()
        self.results._refresh_comparison()
        if self.controller.selection:
            self._guard(lambda: self.results.set_selection(self.controller.selection))

    def _state_changed(self, state: dict[str, Any]) -> None:
        running = bool(state.get("running"))
        inspecting = self.display_mode.currentData() == "result"
        locked = running or inspecting
        reason = "running" if running else "inspection" if inspecting else ""
        if locked != self._locked or reason != self._editor_lock_reason:
            self.editor.set_locked(locked, reason=reason)
            self._locked = locked
            self._editor_lock_reason = reason
        has_document = bool(self.controller.document)
        has_results = bool(state.get("run_id"))
        for key in ("new", "open", "recover", "check", "run", "import", "synthetic", "history", "apply", "revert"):
            self.actions[key].setEnabled(not running and (has_document or key in ("new", "open", "recover")))
        for key in ("check", "run", "apply", "revert"):
            self.actions[key].setEnabled(has_document and not locked)
        self.actions["undo"].setEnabled(bool(state.get("can_undo")) and not inspecting)
        self.actions["redo"].setEnabled(bool(state.get("can_redo")) and not inspecting)
        self.actions["cancel"].setEnabled(running and not state.get("cancel_requested"))
        for key in ("save", "save_as", "backup"):
            self.actions[key].setEnabled(has_document)
        for key in ("export", "curve_png"):
            self.actions[key].setEnabled(has_results)
        self.actions["view_png"].setEnabled(bool(self._model_prepared) or has_results)
        path = state.get("path") or tr("未命名工程")
        dirty = " *" if state.get("dirty") or state.get("has_pending_draft") else ""
        self.setWindowTitle(f"{Path(path).name}{dirty} · LumaFEM")
        memory, saved = state.get("memory_last_step"), state.get("persisted_last_step")
        status = tr("取消待处理") if state.get("cancel_requested") and running else tr("运行中") if running else tr("就绪")
        self.save_label.setText(tr("{status} · 内存步 {memory} / 文件步 {saved}", status=tr(status), memory=memory, saved=saved))
        self.save_label.setToolTip(tr("结果属于旧模型修订") if state.get("results_stale") else "")
        self.results.set_context_state(state)
        self.job_monitor.set_state(state)
        self.job_monitor.submit_button.setEnabled(has_document and not locked)
        self._refresh_run_list(state)
        self._update_snapshot_label()
        if running and not self._last_job_running:
            self.show_job_monitor()
        self._last_job_running = running
        diagnostics = state.get("diagnostics", [])
        if diagnostics != self._last_diagnostics:
            self._last_diagnostics = deepcopy(diagnostics)
            self.editor.set_diagnostics(diagnostics)
        if self._close_after_run and not running:
            QTimer.singleShot(0, self.close)

    def _progress(self, value: dict[str, Any]) -> None:
        self._last_progress = deepcopy(value)
        self.results.append_progress(value)
        self.job_monitor.set_progress(value)
        self._phase_notice = None
        self._render_progress()

    def _render_progress(self) -> None:
        value = self._last_progress
        self.phase_label.setText(tr("{stage} · 已接受 {accepted} 步 · 迭代 {iteration}",
            stage=tr(PHASES.get(value.get("stage"), tr("计算"))), accepted=value.get("accepted_steps", "?"), iteration=value.get("iteration", "?")))
        self.phase_label.setToolTip(str(value.get("message", "")))

    def _finished(self, summary: dict[str, Any]) -> None:
        self._last_summary = deepcopy(summary)
        self.results.set_summary(summary)
        self.job_monitor.set_summary(summary)
        self._phase_notice = None
        self._render_finished()
        self._guard(self._refresh_viewport)

    def _render_finished(self) -> None:
        self.phase_label.setText(tr("分析结束：{reason}", reason=tr(TERMINATIONS.get(self._last_summary.get("termination"), tr("未结束 / 中断")))))
        self.phase_label.setToolTip(json.dumps(self._last_summary, ensure_ascii=False, indent=2))

    def _error(self, detail: dict[str, Any]) -> None:
        self._set_notice("操作未完成；原始诊断见消息详情。")
        self.phase_label.setToolTip(json.dumps(detail, ensure_ascii=False, indent=2))
        self.results.log.appendPlainText(self.results._diagnostic_line(detail))
        self.messages.appendPlainText(json.dumps(detail, ensure_ascii=False, indent=2))
        diagnostics = self.controller.state.get("diagnostics", [])
        if detail not in diagnostics:
            diagnostics.append(deepcopy(detail))
        self.editor.set_diagnostics(diagnostics)

    def _settle_draft(self) -> bool:
        QApplication.focusWidget().clearFocus() if QApplication.focusWidget() else None
        if not self.editor.has_pending_changes:
            return True
        answer = QMessageBox.question(self, tr("表单尚未应用"), tr("先应用当前表单，还是丢弃这部分草稿？"),
                                      QMessageBox.StandardButton.Apply | QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
                                      QMessageBox.StandardButton.Apply)
        if answer == QMessageBox.StandardButton.Apply:
            self.editor.apply_button.click()
            return not self.editor.has_pending_changes
        if answer == QMessageBox.StandardButton.Discard:
            self.editor.revert_button.click()
            return True
        return False

    def _allow_replace(self) -> bool:
        if self.controller.state.get("running") or not self._settle_draft():
            return False
        if not self.controller.state.get("dirty"):
            return True
        answer = QMessageBox.question(self, tr("保存当前工程"), tr("当前工程有未保存内容。是否先保存？"),
                                      QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
                                      QMessageBox.StandardButton.Save)
        if answer == QMessageBox.StandardButton.Save:
            return bool(self.save_project())
        return answer == QMessageBox.StandardButton.Discard

    def new_project(self) -> bool:
        """Create a separate default-input project without overwriting another file."""
        if not self._allow_replace():
            return False
        template = self._template
        if template is None:
            candidate = self._case_dir / "NC0.json"
            if not candidate.is_file():
                raise ContractError("template_missing", tr("本地默认输入缺失。请先运行 scripts/provision_local_data.py 或指定 --case-dir。"), stage="desktop")
            template = json.loads(candidate.read_text(encoding="utf-8-sig"))
            if not isinstance(template, dict):
                raise ValueError(tr("默认工程必须为对象"))
            validate_finite(template)
        if not self.controller.new_project(template):
            return False
        self.check_model()
        return True

    def open_project(self, path: str | None = None) -> bool:
        """Open a checked .lumafem file after resolving pending document edits."""
        if not self._allow_replace():
            return False
        if path is None:
            path, _ = QFileDialog.getOpenFileName(self, tr("打开工程"), "", tr("LumaFEM 工程 (*.lumafem)"), options=QFileDialog.Option.DontUseNativeDialog)
        return bool(path and self.controller.open_project(path))

    def save_project(self, save_as: bool = False) -> bool:
        """Save applied state or choose a consistent project-copy destination."""
        if not self._settle_draft():
            return False
        state = self.controller.state
        if save_as or not state.get("path"):
            path, _ = QFileDialog.getSaveFileName(self, tr("另存为工程"), "LumaFEM.lumafem", tr("LumaFEM 工程 (*.lumafem)"), options=QFileDialog.Option.DontUseNativeDialog)
            if not path:
                return False
            if not path.lower().endswith(".lumafem"):
                path += ".lumafem"
            return bool(self.controller.save_as(path))
        return bool(self.controller.save())

    def backup_project(self) -> bool:
        """Create a consistent saved-content backup using SQLite's backup API."""
        state = self.controller.state
        path, _ = QFileDialog.getSaveFileName(self, tr("保存已持久化内容的备份"), "LumaFEM-backup.lumafem", tr("LumaFEM 工程 (*.lumafem)"), options=QFileDialog.Option.DontUseNativeDialog)
        if not path:
            return False
        if not path.lower().endswith(".lumafem"):
            path += ".lumafem"
        if any(Path(item.get("source", {}).get("path", "")).resolve() == Path(path).resolve()
               for item in self.controller.imports if item.get("source", {}).get("path")):
            raise ContractError("source_overwrite", tr("备份不能覆盖导入的原始数据。"), stage="desktop")
        with ProjectStore(state["working_path"], readonly=True) as store:
            store.backup(path)
        self._set_notice("已创建一致备份；未应用草稿不在此备份中。")
        return True

    def restore_recovery(self) -> bool:
        """Explicitly open an independent recovery copy, never silently replacing input."""
        if not self._allow_replace():
            return False
        path, _ = QFileDialog.getOpenFileName(self, tr("选择恢复副本"), self.controller.state.get("recovery_path") or "", tr("恢复工程 (*.lumafem)"), options=QFileDialog.Option.DontUseNativeDialog)
        return bool(path and self.controller.restore_recovery(path))

    def _undo(self) -> None:
        if self._settle_draft():
            self.controller.undo()

    def _redo(self) -> None:
        if self._settle_draft():
            self.controller.redo()

    def check_model(self) -> bool:
        """Check actual editable inputs and show their located native diagnostics."""
        if not self._settle_draft():
            return False
        result = self.controller.check_model()
        valid = bool(result and result.get("ok"))
        self._set_notice("模型检查通过；假设和来源说明见检查报告。" if valid else "模型检查未通过，请定位检查报告。")
        return valid

    def start_run(self) -> bool:
        """Start one real background native analysis after resolving form edits."""
        if not self._settle_draft():
            return False
        return bool(self.controller.start_run())

    def choose_run(self) -> None:
        runs = self.controller.available_runs()
        if not runs:
            QMessageBox.information(self, tr("历史运行"), tr("当前工程尚无保存的运行。"))
            return
        labels = [tr("运行 {number} · {status} · 最后步 {last}", number=index + 1,
                     status=tr(tr("运行中") if item.get("status", item.get("run_status")) == "running" else tr("未结束 / 中断")),
                     last=item.get("last_sequence", "?")) for index, item in enumerate(runs)]
        chosen, accepted = QInputDialog.getItem(self, tr("选择历史运行"), tr("每个运行绑定其原始输入快照"), labels, 0, False)
        if accepted:
            self.controller.load_run(runs[labels.index(chosen)]["run_id"])

    def import_data(self, path: str | None = None) -> bool:
        """Adopt raw CSV channels only after explicit mapping and conversion preview."""
        if path is None:
            path, _ = QFileDialog.getOpenFileName(self, tr("选择原始 CSV"), "", tr("CSV 数据 (*.csv *.txt *.tsv);;所有文件 (*)"), options=QFileDialog.Option.DontUseNativeDialog)
        if not path:
            return False
        dialog = CsvImportDialog(path, self.controller.document, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return False
        dataset = self.controller.import_data(path, dialog.mapping)
        if dataset:
            self.results_dock.show()
            self._set_notice("已导入通道和处理记录；合成或未核实数据不表示物理验证通过。")
        return bool(dataset)

    def import_synthetic(self) -> bool:
        """Open the clearly marked synthetic workflow example through the same wizard."""
        path = self._case_dir / "synthetic_channels.csv"
        if not path.is_file():
            raise ContractError("synthetic_missing", tr("合成示例文件缺失，请先运行本地数据准备脚本。"), stage="desktop")
        return self.import_data(str(path))

    def export_data(self) -> bool:
        labels = {tr("荷载—挠度曲线"): "curve", tr("事件"): "events", tr("收敛与定位诊断"): "diagnostics",
                  tr("所选步纤维"): "fibers", tr("所选步边缘"): "edges", tr("所选步截面"): "sections",
                  tr("所选步单元内力"): "element_forces", tr("所选步观测"): "observations", tr("导入曲线对比"): "comparison"}
        label, accepted = QInputDialog.getItem(self, tr("导出数值"), tr("导出内容"), list(labels), 0, False)
        if not accepted:
            return False
        path, _ = QFileDialog.getSaveFileName(self, tr("选择新的 CSV 路径"), labels[label] + ".csv", "CSV (*.csv)", options=QFileDialog.Option.DontUseNativeDialog)
        if not path:
            return False
        if not path.lower().endswith(".csv"):
            path += ".csv"
        return bool(self.controller.export_data(path, labels[label]))

    def export_png(self, kind: str) -> bool:
        path, _ = QFileDialog.getSaveFileName(self, tr("选择新的 PNG 路径"), "LumaFEM-" + kind + ".png", "PNG (*.png)", options=QFileDialog.Option.DontUseNativeDialog)
        if not path:
            return False
        if not path.lower().endswith(".png"):
            path += ".png"
        if Path(path).exists():
            raise ContractError("export_exists", tr("请选择新的 PNG 路径，以保留已有文件。"), stage="desktop")
        (self.results if kind == "curve" else self.viewport).export_png(path)
        self._set_notice("已导出带有身份和单位的 PNG。")
        return True

    def about(self) -> None:
        QMessageBox.information(self, "LumaFEM", tr("二维平面纤维梁计算 · Qt＋VTK 三维外观\n"
                                "小变形、完全粘结、无剪切变形；箍筋仅作档案与外观。\n"
                                "参考压缘判据与真实试验压碎不是同一证据。\n"
                                "默认材料及合成数据未标定；A/B/C/D 验收须依据独立证据分别报告。\n"
                                "保存重开支持结果浏览，不承诺任意步续算。"))

    def closeEvent(self, event: QCloseEvent) -> None:
        """Wait for cooperative cancellation; never destroy an active native thread."""
        if self.controller.state.get("running"):
            if not self._close_after_run:
                answer = QMessageBox.question(self, tr("分析仍在运行"), tr("请求取消并在完整接受/保存边界后关闭？"),
                                              QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                              QMessageBox.StandardButton.No)
                if answer != QMessageBox.StandardButton.Yes:
                    event.ignore()
                    return
            self._close_after_run = True
            self.controller.cancel_run()
            event.ignore()
            return
        self._close_after_run = False
        if not self._allow_replace():
            event.ignore()
            return
        if self.controller.close():
            self.viewport.close()
            event.accept()
        else:
            event.ignore()
