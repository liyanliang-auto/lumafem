"""Actual VTK view of value-owned planar beam data in three-dimensional space.

The geometry is a display reconstruction, not a solid finite-element model.
Section colours are piecewise nearest *actual* Gauss results and never enter
event detection. Camera, visibility and magnification never modify a snapshot.
"""
from __future__ import annotations

from bisect import bisect_right
from copy import deepcopy
from math import cos, isfinite, pi, sin, sqrt
from pathlib import Path
from typing import Any, Sequence

from PySide6.QtCore import QByteArray, QBuffer, QIODevice, QRect, QThread, QTimer, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QImage, QPainter
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDoubleSpinBox, QGridLayout, QHBoxLayout,
    QLabel, QPlainTextEdit, QPushButton, QSizePolicy, QToolButton, QVBoxLayout, QWidget,
)

import vtkmodules.qt
vtkmodules.qt.PyQtImpl = "PySide6"
from vtkmodules.qt.QVTKRenderWindowInteractor import QVTKRenderWindowInteractor
from vtkmodules.vtkCommonCore import vtkDoubleArray, vtkLookupTable, vtkPoints
from vtkmodules.vtkCommonDataModel import vtkCellArray, vtkPolyData
from vtkmodules.vtkFiltersCore import vtkTubeFilter
from vtkmodules.vtkFiltersSources import vtkConeSource, vtkSphereSource
from vtkmodules.vtkInteractionStyle import vtkInteractorStyleTrackballCamera
from vtkmodules.vtkInteractionWidgets import vtkOrientationMarkerWidget
from vtkmodules.vtkIOImage import vtkPNGWriter
from vtkmodules.vtkRenderingAnnotation import vtkAxesActor, vtkScalarBarActor
from vtkmodules.vtkRenderingCore import (
    vtkActor, vtkCellPicker, vtkPolyDataMapper, vtkRenderer,
    vtkTextActor, vtkWindowToImageFilter,
)
# Register platform rendering, interaction and font factories.
import vtkmodules.vtkRenderingOpenGL2  # noqa: F401
import vtkmodules.vtkRenderingFreeType  # noqa: F401

from ..contracts import ContractError, same_run, validate_finite
from .formatting import full_precision, scientific
from .themes import colors, palette, rgb


class _PickingInteractor(QVTKRenderWindowInteractor):
    """Observe real Qt clicks after VTK's camera style releases mouse capture.

    A trackball style grabs VTK's release event during a gesture. Watching that
    low-priority event on the interactor therefore misses clicks. Qt still owns
    the paired events; forwarding them first preserves all stock camera input.
    """

    clicked = Signal(tuple)

    def mousePressEvent(self, event: Any) -> None:
        self._click_start = event.position() if event.button() == Qt.MouseButton.LeftButton else None
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event: Any) -> None:
        start = getattr(self, "_click_start", None)
        self._click_start = None
        super().mouseReleaseEvent(event)
        if event.button() == Qt.MouseButton.LeftButton and start is not None:
            delta = event.position() - start
            if delta.x() ** 2 + delta.y() ** 2 <= 16:
                self.clicked.emit(tuple(self.GetRenderWindow().GetInteractor().GetEventPosition()))

_SCALARS = {
    "geometry": ("模型外观", "Geometry only; no solid stress field"),
    "displacement": ("位移幅值 / mm", "|u| [mm], EB kinematics, unscaled numeric values"),
    "N": ("轴力 N / N", "N [N], nearest actual Gauss section, piecewise display"),
    "V": ("剪力 V / N", "V=dM/dx [N], element-end equilibrium recovery at midpoint"),
    "M": ("弯矩 M / N.mm", "M [N.mm], nearest actual Gauss section, piecewise display"),
    "curvature": ("曲率 / 1/mm", "Curvature [1/mm], nearest actual Gauss section"),
    "threshold_ratio": ("点式路线C / 阈值（诊断）", "Point Route-C ratio [1], actual Gauss edge history; diagnostic only in physical mode"),
    "concrete_stress": ("混凝土纤维轴向应力 / MPa", "Saved concrete-fiber axial stress [MPa]; nearest actual Gauss/layer blocks; tension positive"),
    "concrete_strain": ("混凝土纤维轴向总机械应变 / 1", "Saved concrete-fiber total mechanical axial strain [1], including gravity; representative blocks"),
}

_SCALAR_EN = {
    "geometry": "Model appearance", "displacement": "Displacement magnitude / mm",
    "N": "Axial force / N", "V": "Shear force / N", "M": "Bending moment / N.mm",
    "curvature": "Curvature / 1/mm", "threshold_ratio": "Point Route-C / limit (diagnostic)",
    "concrete_stress": "Concrete-fiber axial stress / MPa",
    "concrete_strain": "Concrete-fiber total axial mechanical strain / 1",
}
_SCALAR_NOTES_ZH = {
    "geometry": "仅模型外观，不代表实体应力场",
    "displacement": "位移幅值 [mm]；欧拉—伯努利运动学，数值不乘显示倍率",
    "N": "轴力 [N]；最近实际高斯积分截面的分段显示",
    "V": "剪力 [N]；由单元端力及分布荷载平衡恢复",
    "M": "弯矩 [N.mm]；最近实际高斯积分截面的分段显示",
    "curvature": "曲率 [1/mm]；最近实际高斯积分截面的分段显示",
    "threshold_ratio": "点式路线C阈值比 [1]；物理标距模式中仅作诊断",
    "concrete_stress": "已保存混凝土纤维轴向应力 [MPa]；最近实际高斯/纤维层代表分块，拉正压负；非实体应力场",
    "concrete_strain": "已保存混凝土纤维轴向总机械应变 [1]，包含自重；最近实际采样代表分块，拉正压负；非实体场",
}
_FIBER_FIELDS = {"concrete_stress": "stress", "concrete_strain": "strain"}
_PHASES = {"S0": ("初始态", "Initial"), "G": ("自重加载", "Gravity loading"),
           "SG": ("自重平衡态", "Gravity equilibrium"), "J": ("千斤顶加载", "Jack loading")}
_VISIBILITY = {
    "beam": ("梁外观", "Beam"), "rebar": ("纵向钢筋", "Longitudinal bars"),
    "stirrups": ("箍筋（仅外观）", "Stirrups (appearance only)"),
    "mesh": ("节点与网格", "Nodes and mesh"), "gauss": ("高斯积分截面", "Gauss sections"),
    "events": ("边缘状态与物理标距窗口", "Edges and gauge windows"),
    "original": ("未变形轮廓", "Undeformed outline"),
}


def _number(value: Any) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool) and isfinite(value):
        return float(value)
    return None


class BeamViewport(QWidget):
    """Embedded VTK beam viewport consuming immutable prepared and step values.

    ``objectSelected`` carries a stable source ID, never an actor/array index.
    All methods must be invoked on the Qt GUI thread.
    """

    objectSelected = Signal(str)
    selectionStatus = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("beamViewport")
        self.setAccessibleName("三维梁模型及真实结果视口")
        self._language = "zh_CN"
        self._theme = "dark"
        self._prepared: dict[str, Any] | None = None
        self._step: dict[str, Any] | None = None
        self._selection: dict[str, Any] = {}
        self._magnification = 1.0
        self._actors: list[Any] = []
        self._actor_ids: dict[str, str] = {}
        self._sections: dict[str, dict[str, Any]] = {}
        self._section_results: dict[str, dict[str, Any]] = {}
        self._xs: list[float] = []
        self._initialised = False
        self._render_pending = False
        self._press_position: tuple[int, int] | None = None
        self._selected_actor: Any = None
        self._selection_actors: list[Any] = []
        self._geometry_actors: list[Any] = []
        self._bar_actors: dict[str, list[Any]] = {}
        self._highlighted_ids: tuple[str, ...] = ()
        self._selection_message = ""
        self._actor_base_colors: dict[str, tuple[float, float, float]] = {}
        self._fiber_samples: list[dict[str, Any]] = []
        self._fiber_actors: dict[str, Any] = {}
        self._fiber_cell_sources: dict[str, tuple[str, ...]] = {}
        self._fiber_range: tuple[float, float] | None = None
        self._fiber_error = ""
        self._closed = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        toolbar = QHBoxLayout()
        self.scalar_mode = QComboBox(self)
        self.scalar_mode.setObjectName("viewportVariable")
        self.scalar_mode.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.scalar_mode.setMinimumContentsLength(10)
        self.scalar_mode.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.scalar_mode.setAccessibleName("显示的实际结果量")
        for key, (title, _) in _SCALARS.items():
            self.scalar_mode.addItem(title, key)
        self.scalar_mode.currentIndexChanged.connect(self._rebuild)
        toolbar.addWidget(self.scalar_mode)
        self.scale_label = QLabel("变形倍率", self)
        toolbar.addWidget(self.scale_label)
        self.magnification = QDoubleSpinBox(self)
        self.magnification.setRange(0.0, 10000.0)
        self.magnification.setDecimals(3)
        self.magnification.setValue(1.0)
        self.magnification.setAccessibleName("仅显示用的变形倍率")
        self.magnification.valueChanged.connect(self.set_magnification)
        toolbar.addWidget(self.magnification)
        self.fit_button = QPushButton("适合窗口", self)
        self.fit_button.clicked.connect(self.reset_camera)
        toolbar.addWidget(self.fit_button)
        self.front_button = QPushButton("平面视图", self)
        self.front_button.clicked.connect(self.front_view)
        toolbar.addWidget(self.front_button)
        layout.addLayout(toolbar)
        self.context_label = QLabel(self)
        self.context_label.setObjectName("viewportContext")
        self.context_label.setWordWrap(True)
        self.context_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.context_label)
        self.visibility_button = QToolButton(self)
        self.visibility_button.setCheckable(True)
        self.visibility_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.visibility_button.setArrowType(Qt.ArrowType.RightArrow)
        layout.addWidget(self.visibility_button)
        self.visibility_widget = QWidget(self)
        options = QGridLayout(self.visibility_widget)
        options.setContentsMargins(0, 0, 0, 0)
        self._visible: dict[str, QCheckBox] = {}
        for index, (category, title, checked) in enumerate((
            ("beam", "梁外观", True), ("rebar", "纵筋", True),
            ("stirrups", "箍筋（仅外观）", True), ("mesh", "节点 / 网格", False),
            ("gauss", "Gauss截面", False), ("events", "真实边缘 / 结构窗口", True),
            ("original", "未变形轮廓", True),
        )):
            box = QCheckBox(title, self)
            box.setChecked(checked)
            box.toggled.connect(self._rebuild)
            self._visible[category] = box
            options.addWidget(box, index // 3, index % 3)
        self.visibility_widget.hide()
        self.visibility_button.toggled.connect(self._toggle_visibility_options)
        layout.addWidget(self.visibility_widget)
        self.vtk_widget = _PickingInteractor(self)
        self.vtk_widget.clicked.connect(self._pick_position)
        self.vtk_widget.setMinimumSize(320, 220)
        self.vtk_widget.setAccessibleName("VTK三维视图，鼠标旋转缩放，单击选择对象")
        layout.addWidget(self.vtk_widget, 1)
        self.status = QLabel("检查模型后显示实际网格；三维外观不增加计算自由度。", self)
        self.status.setWordWrap(True)
        self.status.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.status)
        self.selection_label = QLabel(self)
        self.selection_label.setObjectName("viewportSelectionStatus")
        self.selection_label.setWordWrap(True)
        layout.addWidget(self.selection_label)
        self.details_button = QToolButton(self)
        self.details_button.setCheckable(True)
        self.details_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.details_button.setArrowType(Qt.ArrowType.RightArrow)
        layout.addWidget(self.details_button)
        self.details = QPlainTextEdit(self)
        self.details.setObjectName("viewportIdentityDetails")
        self.details.setReadOnly(True)
        self.details.setMaximumHeight(130)
        self.details.hide()
        self.details_button.toggled.connect(self._toggle_details)
        layout.addWidget(self.details)

        self.renderer = vtkRenderer()
        self.renderer.SetBackground(0.945, 0.960, 0.980)
        self.renderer.SetBackground2(0.82, 0.87, 0.93)
        self.renderer.GradientBackgroundOn()
        self.render_window = self.vtk_widget.GetRenderWindow()
        self.render_window.AddRenderer(self.renderer)
        self.render_window.SetMultiSamples(0)
        self.interactor = self.render_window.GetInteractor()
        self.interactor.SetInteractorStyle(vtkInteractorStyleTrackballCamera())
        self.picker = vtkCellPicker()
        self.picker.SetTolerance(0.005)
        self.lookup = vtkLookupTable()
        self.lookup.SetHueRange(0.62, 0.0)
        self.lookup.SetSaturationRange(0.85, 0.85)
        self.lookup.SetValueRange(0.85, 0.95)
        self.lookup.Build()
        self.legend = vtkScalarBarActor()
        self.legend.SetLookupTable(self.lookup)
        self.legend.SetNumberOfLabels(5)
        self.legend.SetLabelFormat("%.3E")
        self.legend.SetWidth(0.20)
        self.legend.SetHeight(0.45)
        self.legend.SetPosition(0.79, 0.14)
        for prop in (self.legend.GetTitleTextProperty(), self.legend.GetLabelTextProperty()):
            prop.SetColor(0.12, 0.18, 0.24)
            prop.SetFontSize(11)
        self.renderer.AddActor2D(self.legend)
        self.annotation = vtkTextActor()
        self.annotation.SetPosition(12, 12)
        self.annotation.GetTextProperty().SetColor(0.12, 0.18, 0.24)
        self.annotation.GetTextProperty().SetFontSize(12)
        self.renderer.AddActor2D(self.annotation)
        # VTK owns camera synchronization inside this noninteractive widget;
        # it uses the existing render window/interactor, not another input loop.
        self.orientation_axes = vtkAxesActor()
        self.orientation_axes.SetShaftTypeToCylinder()
        self.orientation_axes.SetTipTypeToCone()
        self.orientation_axes.SetTotalLength(1.0, 1.0, 1.0)
        self.orientation_axes.AxisLabelsOn()
        for shaft, tip, colour in (
            (self.orientation_axes.GetXAxisShaftProperty(), self.orientation_axes.GetXAxisTipProperty(), (.95, .23, .23)),
            (self.orientation_axes.GetYAxisShaftProperty(), self.orientation_axes.GetYAxisTipProperty(), (.20, .80, .35)),
            (self.orientation_axes.GetZAxisShaftProperty(), self.orientation_axes.GetZAxisTipProperty(), (.22, .48, 1.0)),
        ):
            shaft.SetColor(*colour)
            tip.SetColor(*colour)
        self.orientation_axes.PickableOff()
        self.orientation_marker = vtkOrientationMarkerWidget()
        self.orientation_marker.SetOrientationMarker(self.orientation_axes)
        self.orientation_marker.SetInteractor(self.interactor)
        self.orientation_marker.SetCurrentRenderer(self.renderer)
        self.orientation_marker.SetViewport(0.015, 0.02, 0.18, 0.25)
        self.set_theme("dark")
        self.retranslate_ui("zh_CN")

    def set_theme(self, name: str) -> None:
        """Recolor existing actors and overlays, preserving camera and arrays."""
        colors(name)
        self._theme = name
        self.setPalette(palette(name))
        self.renderer.SetBackground(*rgb(name, "viewport"))
        self.renderer.GradientBackgroundOff()
        for prop in (self.legend.GetTitleTextProperty(), self.legend.GetLabelTextProperty(),
                     self.annotation.GetTextProperty()):
            prop.SetColor(*rgb(name, "text"))
        for caption in (self.orientation_axes.GetXAxisCaptionActor2D(),
                        self.orientation_axes.GetYAxisCaptionActor2D(),
                        self.orientation_axes.GetZAxisCaptionActor2D()):
            caption.GetCaptionTextProperty().SetColor(*rgb(name, "text"))
            caption.GetCaptionTextProperty().ShadowOff()
        for actor in self._actors + self._selection_actors:
            base = self._actor_base_colors.get(actor.GetAddressAsString(""))
            if base is not None:
                actor.GetProperty().SetColor(*self._theme_color(base))
        self._queue_render()

    def _theme_color(self, base: tuple[float, float, float]) -> tuple[float, float, float]:
        # Only qualitative display marks are remapped. The physical scalar LUT
        # and the actual scalar-colored beam actors are deliberately untouched.
        dark_marks = {
            (0.10, 0.25, 0.37): (0.51, 0.72, 0.82),
            (0.52, 0.57, 0.62): (0.66, 0.72, 0.78),
            (0.72, 0.31, 0.18): (0.94, 0.64, 0.39),
            (0.33, 0.41, 0.48): (0.55, 0.66, 0.75),
            (0.12, 0.23, 0.36): (0.57, 0.75, 0.90),
            (0.17, 0.36, 0.43): (0.45, 0.77, 0.82),
            (0.78, 0.25, 0.15): (0.98, 0.59, 0.40),
            (0.52, 0.27, 0.68): (0.78, 0.62, 0.91),
            (0.27, 0.55, 0.65): (0.47, 0.80, 0.86),
            (0.17, 0.49, 0.57): (0.47, 0.80, 0.86),
            (0.48, 0.20, 0.76): (0.77, 0.56, 0.98),
            (0.96, 0.18, 0.48): (1.0, 0.75, 0.40),
        }
        return dark_marks.get(tuple(base), base) if self._theme == "dark" else base

    def _color_actor(self, actor: Any, base: tuple[float, float, float]) -> None:
        self._actor_base_colors[actor.GetAddressAsString("")] = tuple(base)
        actor.GetProperty().SetColor(*self._theme_color(base))

    @property
    def language(self) -> str:
        """Current presentation language; numerical values never depend on it."""
        return self._language

    def _t(self, chinese: str, english: str) -> str:
        return english if self._language == "en" else chinese

    def retranslate_ui(self, language: str) -> None:
        """Relabel the existing view without replacing actors, selection or camera."""
        self._assert_gui()
        if language not in ("zh_CN", "en"):
            raise ValueError("Unsupported viewport language: " + str(language))
        self._language = language
        self.setAccessibleName(self._t("三维梁模型及真实结果视口", "Beam model and actual result viewport"))
        self.scalar_mode.blockSignals(True)
        for index in range(self.scalar_mode.count()):
            key = self.scalar_mode.itemData(index)
            self.scalar_mode.setItemText(index, _SCALAR_EN[key] if language == "en" else _SCALARS[key][0])
            self.scalar_mode.setItemData(index, _SCALARS[key][0], Qt.ItemDataRole.ToolTipRole)
        self.scalar_mode.blockSignals(False)
        self.scalar_mode.setAccessibleName(self._t("显示的实际结果量", "Displayed actual result variable"))
        self.scale_label.setText(self._t("变形倍率", "Scale"))
        self.magnification.setAccessibleName(self._t("仅显示用的变形倍率", "Display-only deformation scale"))
        self.fit_button.setText(self._t("适合窗口", "Fit"))
        self.front_button.setText(self._t("平面视图", "Front"))
        self.fit_button.setToolTip(self._t("适合窗口；视口获得焦点时按 F", "Fit; F with viewport focus"))
        self.front_button.setToolTip(self._t("平面视图；视口获得焦点时按 Shift+F", "Front; Shift+F with viewport focus"))
        for key, box in self._visible.items():
            box.setText(_VISIBILITY[key][1 if language == "en" else 0])
            box.setToolTip(_VISIBILITY[key][0])
        self.vtk_widget.setAccessibleName(self._t("三维视口：拖动旋转，滚轮缩放，单击选择", "3D viewport: drag to rotate, wheel to zoom, click to select"))
        self.details_button.setText(self._t("身份与数值证据详情", "Identity and numerical evidence details"))
        self.visibility_button.setText(self._t("显示选项", "Display options"))
        self._update_status()
        self._draw_selection()
        self._set_annotation()
        self._queue_render()

    def _toggle_details(self, shown: bool) -> None:
        self.details.setVisible(shown)
        self.details_button.setArrowType(Qt.ArrowType.DownArrow if shown else Qt.ArrowType.RightArrow)

    def _toggle_visibility_options(self, shown: bool) -> None:
        self.visibility_widget.setVisible(shown)
        self.visibility_button.setArrowType(Qt.ArrowType.DownArrow if shown else Qt.ArrowType.RightArrow)

    def _assert_gui(self) -> None:
        app = QApplication.instance()
        if app is None or QThread.currentThread() != app.thread():
            raise ContractError("gui_thread", self._t("视口必须在界面线程运行。", "VTK view must run on the Qt GUI thread"), stage="display")

    def showEvent(self, event: Any) -> None:
        super().showEvent(event)
        if self._closed:
            return
        if not self._initialised:
            self.interactor.Initialize()
            self._initialised = True
        self.orientation_marker.SetEnabled(True)
        self.orientation_marker.InteractiveOff()
        self._layout_overlays()
        self._queue_render()

    def hideEvent(self, event: Any) -> None:
        if not self._closed:
            self.orientation_marker.SetEnabled(False)
        super().hideEvent(event)

    def resizeEvent(self, event: Any) -> None:
        super().resizeEvent(event)
        self._layout_overlays()

    def _layout_overlays(self) -> None:
        """Reserve a square in physical render pixels without changing bounds."""
        if not hasattr(self, "orientation_marker") or self._closed:
            return
        width, height = self.render_window.GetSize()
        if width <= 0 or height <= 0:
            return
        ratio = self.vtk_widget.devicePixelRatioF()
        margin = 8.0 * ratio
        size = min(88.0 * ratio, width * .22, height * .34)
        self.orientation_marker.SetViewport(margin/width, margin/height,
                                            (margin+size)/width, (margin+size)/height)
        self.annotation.SetPosition(margin+size+12*ratio, 12*ratio)

    def closeEvent(self, event: Any) -> None:
        if not self._closed:
            self._closed = True
            self.orientation_marker.SetEnabled(False)
            self.orientation_marker.SetInteractor(None)
            self.orientation_marker.SetOrientationMarker(None)
            self.vtk_widget.Finalize()
        super().closeEvent(event)

    def set_prepared(self, prepared: dict[str, Any] | None) -> None:
        """Replace display model, clearing a result from a different snapshot."""
        self._assert_gui()
        if prepared is None:
            self._prepared = None
            self._step = None
            self._selection = {}
        else:
            validate_finite(prepared)
            if not prepared.get("nodes") or not prepared.get("elements"):
                raise ContractError("display_model", self._t("已准备网格为空。", "Prepared mesh is empty"), stage="display")
            changed = not self._prepared or self._prepared.get("prepared_hash") != prepared.get("prepared_hash")
            if changed:
                self._selection = {}
            self._prepared = deepcopy(prepared)
            if self._step and not same_run(prepared.get("identity", {}), self._step.get("identity", {})):
                self._step = None
            self._sections = {item["id"]: item for item in prepared.get("sections", [])}
            self._xs = [float(node["x"]) for node in prepared["nodes"]]
            self._rebuild()
            if changed:
                self.reset_camera()
                QTimer.singleShot(0, self.reset_camera)
            return
        self._sections = {}
        self._xs = []
        self._rebuild()

    def set_step(self, step: dict[str, Any] | None) -> None:
        """Display only a real accepted step owned by the prepared run."""
        self._assert_gui()
        try:
            if step is not None:
                if not self._prepared or not same_run(self._prepared.get("identity", {}), step.get("identity", {})):
                    raise ContractError("display_identity", self._t("结果步不属于当前显示模型。", "Step does not belong to displayed model"), stage="display")
                validate_finite(step)
                expected = 3 * len(self._prepared["nodes"])
                displacement = step.get("displacement")
                if not isinstance(displacement, (list, tuple)) or len(displacement) != expected:
                    raise ContractError("display_shape", self._t("位移数组尺寸与网格不符。", "Displacement shape does not match mesh"), stage="display")
        except ContractError:
            # Reject as before, but never leave a previous accepted field visible
            # while the caller is reporting an unavailable/corrupt requested frame.
            self._step = None
            self._section_results = {}
            self._rebuild()
            raise
        self._step = deepcopy(step)
        self._rebuild()

    def set_selection(self, selection: dict[str, Any]) -> None:
        """Highlight a stable source identity while keeping camera and numbers."""
        self._assert_gui()
        if selection.get("run_id") and self._prepared and selection["run_id"] != self._prepared["identity"]["run_id"]:
            self._selection = {}
            self._draw_selection()
            self._report_selection(self._t("所选对象不属于当前显示的运行，未高亮。", "Selection belongs to a different displayed run; no highlight."))
            self._set_annotation()
            self._queue_render()
            return
        self._selection = deepcopy(selection)
        self._draw_selection()
        self._set_annotation()
        self._queue_render()

    def set_magnification(self, value: float) -> None:
        """Set a finite nonnegative display multiplier, leaving data immutable."""
        self._assert_gui()
        if not isfinite(value) or not 0.0 <= value <= 10000.0:
            raise ContractError("display_scale", self._t("变形倍率必须位于 [0,10000]。", "Magnification must lie in [0,10000]"), stage="display")
        self._magnification = float(value)
        if self.magnification.value() != value:
            self.magnification.blockSignals(True)
            self.magnification.setValue(value)
            self.magnification.blockSignals(False)
        self._rebuild()

    def _queue_render(self) -> None:
        if not self._closed and not self._render_pending:
            self._render_pending = True
            QTimer.singleShot(0, self._render)

    def _render(self) -> None:
        self._render_pending = False
        if not self._closed and self._initialised and self.isVisible():
            self._layout_overlays()
            self.renderer.ResetCameraClippingRange()
            self.render_window.Render()

    def _kinematics(self, x: float) -> tuple[float, float, float]:
        """Actual local axial/transverse displacement and EB section rotation."""
        if not self._step or not self._prepared:
            return 0.0, 0.0, 0.0
        nodes = self._prepared["nodes"]
        index = min(max(bisect_right(self._xs, x) - 1, 0), len(nodes) - 2)
        left, right = nodes[index], nodes[index + 1]
        length = float(right["x"]) - float(left["x"])
        r = (x - float(left["x"])) / length
        angle = float(self._prepared["document"]["model"]["geometry"].get("angle", 0.0))
        c, s = cos(angle), sin(angle)
        values = self._step["displacement"]
        endpoints = []
        for node in (left, right):
            ux, uy, theta = (float(values[int(dof)]) for dof in node["dofs"])
            endpoints.append((c * ux + s * uy, -s * ux + c * uy, theta))
        (u1, v1, t1), (u2, v2, t2) = endpoints
        u = (1 - r) * u1 + r * u2
        v = (1 - 3*r*r + 2*r*r*r)*v1 + length*(r - 2*r*r + r*r*r)*t1
        v += (3*r*r - 2*r*r*r)*v2 + length*(-r*r + r*r*r)*t2
        theta = (-6*r + 6*r*r)*v1/length + (1 - 4*r + 3*r*r)*t1
        theta += (6*r - 6*r*r)*v2/length + (-2*r + 3*r*r)*t2
        return u, v, theta

    def _position(self, x: float, eta: float = 0.0, zeta: float = 0.0,
                  *, original: bool = False) -> tuple[float, float, float]:
        angle = float(self._prepared["document"]["model"]["geometry"].get("angle", 0.0))
        c, s = cos(angle), sin(angle)
        u, v, theta = (0.0, 0.0, 0.0) if original else self._kinematics(x)
        axial = x + self._magnification * (u - eta * theta)
        transverse = eta + self._magnification * v
        return c*axial - s*transverse, s*axial + c*transverse, zeta

    def _polyline(self, coordinates: Sequence[Sequence[float]], colour: tuple[float, float, float],
                  identity: str, radius: float = 0.0, width: float = 1.5,
                  opacity: float = 1.0, register: bool = True) -> Any:
        points = vtkPoints()
        cells = vtkCellArray()
        cells.InsertNextCell(len(coordinates))
        for value in coordinates:
            cells.InsertCellPoint(points.InsertNextPoint(*value))
        data = vtkPolyData()
        data.SetPoints(points)
        data.SetLines(cells)
        mapper = vtkPolyDataMapper()
        if radius > 0.0:
            tube = vtkTubeFilter()
            tube.SetInputData(data)
            tube.SetRadius(radius)
            tube.SetNumberOfSides(8)
            tube.CappingOn()
            mapper.SetInputConnection(tube.GetOutputPort())
        else:
            mapper.SetInputData(data)
        actor = vtkActor()
        actor.SetMapper(mapper)
        self._color_actor(actor, tuple(colour))
        actor.GetProperty().SetLineWidth(width)
        actor.GetProperty().SetOpacity(opacity)
        self.renderer.AddActor(actor)
        if register:
            self._actors.append(actor)
            self._actor_ids[actor.GetAddressAsString("")] = identity
        else:
            actor.PickableOff()
        return actor

    def _sphere(self, point: Sequence[float], radius: float, colour: tuple[float, float, float],
                identity: str) -> Any:
        source = vtkSphereSource()
        source.SetCenter(*point)
        source.SetRadius(radius)
        source.SetThetaResolution(10)
        source.SetPhiResolution(8)
        mapper = vtkPolyDataMapper()
        mapper.SetInputConnection(source.GetOutputPort())
        actor = vtkActor()
        actor.SetMapper(mapper)
        self._color_actor(actor, tuple(colour))
        self.renderer.AddActor(actor)
        self._actors.append(actor)
        self._actor_ids[actor.GetAddressAsString("")] = identity

        return actor

    def _beam_piece(self, x1: float, x2: float, height: float, width: float,
                    colour: tuple[float, float, float], identity: str) -> None:
        points = vtkPoints()
        faces = vtkCellArray()
        # Four sections per element make the actual cubic deformation visible.
        for sample in range(5):
            x = x1 + (x2-x1)*sample/4
            for eta, zeta in ((-height/2,-width/2),(-height/2,width/2),(height/2,width/2),(height/2,-width/2)):
                points.InsertNextPoint(*self._position(x, eta, zeta))
        quads = [(0,3,2,1), (16,17,18,19)]
        for index in range(4):
            for corner in range(4):
                a, b = index*4+corner, index*4+(corner+1)%4
                quads.append((a,b,b+4,a+4))
        for quad in quads:
            faces.InsertNextCell(4)
            for point_id in quad:
                faces.InsertCellPoint(point_id)
        data = vtkPolyData()
        data.SetPoints(points)
        data.SetPolys(faces)
        mapper = vtkPolyDataMapper()
        mapper.SetInputData(data)
        actor = vtkActor()
        actor.SetMapper(mapper)
        actor.GetProperty().SetColor(*colour)
        actor.GetProperty().SetOpacity(0.34 if self._visible["rebar"].isChecked() else 0.88)
        actor.GetProperty().SetAmbient(0.35)
        actor.GetProperty().SetDiffuse(0.65)
        self.renderer.AddActor(actor)
        self._actors.append(actor)
        self._actor_ids[actor.GetAddressAsString("")] = identity
        self._geometry_actors.append(actor)

    def _scalar(self, element: dict[str, Any], x: float) -> float | None:
        mode = self.scalar_mode.currentData()
        if not self._step or mode == "geometry" or mode in _FIBER_FIELDS:
            return None
        if mode == "displacement":
            u, v, _ = self._kinematics(x)
            return sqrt(u*u + v*v)
        if mode == "V":
            force = next((item for item in self._step.get("element_forces", [])
                          if item.get("id") == element["id"]), None)
            if force is None or len(force.get("end_forces", [])) != 6:
                return None
            shear = _number(force["end_forces"][1])
            load = _number(force.get("distributed_transverse"))
            left = float(self._prepared["nodes"][int(element["nodes"][0])]["x"])
            return shear + load*(x-left) if shear is not None and load is not None else None
        records = [self._section_results.get(self._prepared["sections"][i]["id"])
                   for i in element.get("section_indices", [])]
        records = [record for record in records if record is not None]
        if not records:
            return None
        record = min(records, key=lambda item: abs(float(item["x"]) - x))
        if mode == "threshold_ratio":
            ratios = []
            for edge in record.get("edges", []):
                state = edge.get("state", {})
                amplitude = _number(state.get("max_reference_compression_strain"))
                threshold = _number(edge.get("reference_threshold"))
                if amplitude is not None and threshold is not None and threshold > 0:
                    ratios.append(amplitude/threshold)
            return max(ratios) if ratios else None
        return _number(record.get(mode))

    def _saved_fiber_blocks(self) -> list[dict[str, Any]]:
        """Validate the entire saved layer field before publishing any coloring.

        Bounds are nearest-sample display partitions, not integration lengths.
        No material calculation or edge extrapolation occurs here.
        """
        def require(condition: bool, reason: str) -> None:
            if not condition:
                raise ContractError("display_field", reason, stage="display")

        require(self._prepared is not None and self._step is not None,
                "No accepted saved frame")
        prepared, step = self._prepared, self._step
        require(same_run(prepared.get("identity", {}), step.get("identity", {})),
                "Frame identity does not match frozen geometry")
        height = _number(prepared["document"]["model"]["geometry"].get("height"))
        require(height is not None and height > 0, "Invalid frozen section height")
        source_sections = prepared.get("sections")
        saved_sections = step.get("sections")
        saved_fibers = step.get("fibers")
        require(isinstance(source_sections, list) and bool(source_sections), "No prepared sections")
        require(isinstance(saved_sections, list) and isinstance(saved_fibers, list),
                "Saved fiber/section arrays unavailable")

        def records_by_id(records: list[Any]) -> dict[str, dict[str, Any]]:
            result: dict[str, dict[str, Any]] = {}
            for record in records:
                require(isinstance(record, dict), "Malformed saved record")
                key = record.get("id")
                require(isinstance(key, str) and bool(key) and key not in result,
                        "Missing or duplicate source identity")
                result[key] = record
            return result

        section_map = records_by_id(source_sections)
        saved_section_map = records_by_id(saved_sections)
        fiber_map = records_by_id(saved_fibers)
        require(set(saved_section_map) == set(section_map), "Incomplete or foreign section set")
        field = _FIBER_FIELDS[self.scalar_mode.currentData()]
        expected_ids: set[str] = set()
        used_sections: set[str] = set()
        blocks: list[dict[str, Any]] = []
        for element in prepared["elements"]:
            indices = element.get("section_indices")
            require(isinstance(indices, list) and bool(indices), "No element Gauss sections")
            require(all(isinstance(index, int) and not isinstance(index, bool)
                        and 0 <= index < len(source_sections) for index in indices),
                    "Invalid Gauss section indices")
            sections = [source_sections[index] for index in indices]
            require(all(_number(section.get("x")) is not None for section in sections),
                    "Invalid Gauss coordinates")
            sections.sort(key=lambda section: float(section["x"]))
            ends = element.get("nodes", [])
            require(isinstance(ends, list) and len(ends) == 2
                    and all(isinstance(index, int) and not isinstance(index, bool)
                            and 0 <= index < len(prepared["nodes"]) for index in ends),
                    "Invalid element endpoints")
            left, right = (float(prepared["nodes"][index]["x"]) for index in ends)
            xs = [_number(section.get("x")) for section in sections]
            require(all(x is not None for x in xs) and left < xs[0]
                    and xs[-1] < right and all(a < b for a, b in zip(xs, xs[1:])),
                    "Unrepresentable Gauss coordinates")
            bounds = [left] + [(a+b)/2 for a, b in zip(xs, xs[1:])] + [right]
            for index, section in enumerate(sections):
                sid = section["id"]
                require(sid not in used_sections and section.get("element_id") == element["id"],
                        "Duplicate or foreign Gauss owner")
                used_sections.add(sid)
                saved_section = saved_section_map[sid]
                require(saved_section.get("element_id") == element["id"]
                        and _number(saved_section.get("x")) == xs[index],
                        "Saved section coordinates/owner disagree")
                source_fibers = section.get("fibers")
                require(isinstance(source_fibers, list), "No prepared fiber array")
                all_source = records_by_id(source_fibers)
                concrete = [fiber for fiber in all_source.values() if fiber.get("role") == "concrete"]
                require(bool(concrete), "No prepared concrete layers")
                require(all(_number(fiber.get("eta")) is not None for fiber in concrete),
                        "Invalid layer coordinate")
                require(all(_number(fiber.get("zeta")) == 0.0 for fiber in concrete),
                        "Only planar concrete layers can be represented")
                concrete.sort(key=lambda fiber: float(fiber["eta"]))
                etas = [float(fiber["eta"]) for fiber in concrete]
                require(-height/2 < etas[0] and etas[-1] < height/2
                        and all(a < b for a, b in zip(etas, etas[1:])),
                        "Concrete layers must have unique interior heights")
                eta_bounds = [-height/2] + [(a+b)/2 for a, b in zip(etas, etas[1:])] + [height/2]
                nested = saved_section.get("fibers")
                require(isinstance(nested, list), "Saved section fiber array unavailable")
                nested_map = records_by_id(nested)
                require({key for key, record in nested_map.items() if record.get("role") == "concrete"}
                        == {fiber["id"] for fiber in concrete}, "Incomplete or foreign nested concrete set")
                for layer, fiber in enumerate(concrete):
                    fid = fiber["id"]
                    require(fid not in expected_ids and fid in fiber_map,
                            "Missing or duplicate concrete sample")
                    expected_ids.add(fid)
                    saved = fiber_map[fid]
                    require(saved == nested_map[fid], "Flat/nested saved samples disagree")
                    require(saved.get("section_id") == sid and saved.get("role") == "concrete"
                            and saved.get("material_id") == fiber.get("material_id"),
                            "Saved concrete sample ownership disagrees")
                    for coordinate, expected in (("x", xs[index]), ("eta", etas[layer]),
                                                  ("zeta", _number(fiber.get("zeta")))):
                        require(expected is not None and _number(saved.get(coordinate)) == expected,
                                "Saved sample coordinates disagree")
                    state = saved.get("state")
                    require(isinstance(state, dict), "Saved material state unavailable")
                    value = _number(state.get(field))
                    require(value is not None, "Saved physical field unavailable")
                    for duplicate in ("stress", "strain"):
                        if duplicate in saved:
                            require(_number(saved.get(duplicate)) is not None
                                    and saved[duplicate] == state.get(duplicate),
                                    "Contradictory saved physical fields")
                    blocks.append({"fiber_id": fid, "section_id": sid, "element_id": element["id"],
                                   "x": xs[index], "eta": etas[layer], "zeta": 0.0, "value": value,
                                   "x_bounds": (bounds[index], bounds[index+1]),
                                   "eta_bounds": (eta_bounds[layer], eta_bounds[layer+1])})
        require(used_sections == set(section_map), "Unmapped prepared section")
        require(expected_ids == {key for key, record in fiber_map.items() if record.get("role") == "concrete"},
                "Incomplete or foreign concrete sample set")
        return blocks

    def _fiber_section_actor(self, section_id: str, samples: list[dict[str, Any]], width: float) -> None:
        """Display saved doubles on subdivided layer faces; picks keep section ID."""
        points, faces, scalars = vtkPoints(), vtkCellArray(), vtkDoubleArray()
        points.SetDataTypeToDouble()
        scalars.SetName(str(self.scalar_mode.currentData()))
        sources: list[str] = []
        for sample in samples:
            offset = points.GetNumberOfPoints()
            x1, x2 = sample["x_bounds"]
            eta1, eta2 = sample["eta_bounds"]
            for station in range(5):
                x = x1 + (x2-x1)*station/4
                for eta, zeta in ((eta1,-width/2), (eta1,width/2), (eta2,width/2), (eta2,-width/2)):
                    points.InsertNextPoint(*self._position(x, eta, zeta))
            quads = [(0,3,2,1), (16,17,18,19)]
            for station in range(4):
                for corner in range(4):
                    a, b = station*4+corner, station*4+(corner+1)%4
                    quads.append((a,b,b+4,a+4))
            for quad in quads:
                faces.InsertNextCell(4)
                for point in quad:
                    faces.InsertCellPoint(offset+point)
                scalars.InsertNextValue(sample["value"])
                sources.append(sample["fiber_id"])
        data = vtkPolyData()
        data.SetPoints(points)
        data.SetPolys(faces)
        data.GetCellData().SetScalars(scalars)
        mapper = vtkPolyDataMapper()
        mapper.SetInputData(data)
        mapper.SetScalarModeToUseCellData()
        mapper.SetColorModeToMapScalars()
        mapper.SetLookupTable(self.lookup)
        mapper.UseLookupTableScalarRangeOn()
        mapper.InterpolateScalarsBeforeMappingOff()
        mapper.ScalarVisibilityOn()
        actor = vtkActor()
        actor.SetMapper(mapper)
        actor.GetProperty().SetLighting(False)
        actor.GetProperty().SetOpacity(1.0)
        self.renderer.AddActor(actor)
        self._actors.append(actor)
        self._geometry_actors.append(actor)
        self._actor_ids[actor.GetAddressAsString("")] = section_id
        self._fiber_actors[section_id] = actor
        self._fiber_cell_sources[section_id] = tuple(sources)

    def _rebuild(self, *_: Any) -> None:
        self._assert_gui()
        for actor in self._actors:
            self.renderer.RemoveActor(actor)
        self._actors.clear()
        self._actor_ids.clear()
        self._geometry_actors.clear()
        self._bar_actors.clear()
        self._clear_highlight()
        self._actor_base_colors.clear()
        self._fiber_samples = []
        self._fiber_actors.clear()
        self._fiber_cell_sources.clear()
        self._fiber_range = None
        self._fiber_error = ""
        self.legend.UseCustomLabelsOff()
        self.lookup.SetNumberOfTableValues(256)
        self.lookup.ForceBuild()
        if not self._prepared:
            self.legend.SetVisibility(False)
            self._draw_selection()
            self._update_status()
            self._set_annotation()
            self._queue_render()
            return
        model = self._prepared["document"]["model"]
        geometry = model["geometry"]
        height, width, length = (float(geometry[key]) for key in ("height", "width", "length"))
        records = (self._step or {}).get("sections", [])
        self._section_results = {record["id"]: record for record in records
                                 if isinstance(record, dict) and isinstance(record.get("id"), str)} if isinstance(records, list) else {}
        elements, nodes = self._prepared["elements"], self._prepared["nodes"]
        fiber_mode = self.scalar_mode.currentData() in _FIBER_FIELDS
        if fiber_mode:
            try:
                self._fiber_samples = self._saved_fiber_blocks()
            except ContractError as exc:
                # Only field-contract rejections are translated to unavailable;
                # unexpected renderer/programming errors still propagate.
                self._fiber_error = str(exc)
        element_values = []
        for element in elements:
            left, right = (nodes[int(i)] for i in element["nodes"])
            x1, x2 = float(left["x"]), float(right["x"])
            element_values.append(self._scalar(element, (x1+x2)/2))
        finite_values = ([sample["value"] for sample in self._fiber_samples] if fiber_mode
                         else [value for value in element_values if value is not None])
        active = self.scalar_mode.currentData() != "geometry" and bool(finite_values)
        if active:
            lower, upper = min(finite_values), max(finite_values)
            if fiber_mode:
                self._fiber_range = (lower, upper)
            if lower == upper:
                padding = max(abs(lower)*1e-6, 1e-12)
                if fiber_mode:
                    # The single custom label exposes the true constant rather
                    # than the renderer's necessary nonzero lookup interval.
                    labels = vtkDoubleArray()
                    labels.InsertNextValue(lower)
                    self.legend.SetCustomLabels(labels)
                    self.legend.UseCustomLabelsOn()
                lower, upper = lower-padding, upper+padding
            self.lookup.SetTableRange(lower, upper)
            self.lookup.Build()
            if fiber_mode:
                constant = self._fiber_range[0] == self._fiber_range[1]
                self.lookup.SetNumberOfTableValues(256)
                for index in range(256):
                    # Fixed diverging display palette. Raw values and range are
                    # untouched; a constant field has one genuinely uniform color.
                    t = .5 if constant else index/255
                    if t <= .5:
                        a = t*2
                        colour = (.18+.72*a, .38+.52*a, .72+.18*a)
                    else:
                        a = (t-.5)*2
                        colour = (.90-.12*a, .90-.70*a, .90-.70*a)
                    self.lookup.SetTableValue(index, *colour, 1.)
        self.legend.SetVisibility(active and (not fiber_mode or self._visible["beam"].isChecked()))
        self.legend.SetTitle(self._legend_title())
        if fiber_mode and active and self._visible["beam"].isChecked():
            grouped: dict[str, list[dict[str, Any]]] = {}
            for sample in self._fiber_samples:
                grouped.setdefault(sample["section_id"], []).append(sample)
            for section_id, samples in grouped.items():
                self._fiber_section_actor(section_id, samples, width)
        for element, value in zip(elements, element_values):
            left, right = (nodes[int(i)] for i in element["nodes"])
            x1, x2 = float(left["x"]), float(right["x"])
            colour = [0.45, 0.63, 0.78]
            if active and value is not None:
                self.lookup.GetColor(value, colour)
            elif self.scalar_mode.currentData() != "geometry":
                colour = [0.64, 0.64, 0.64]
            if self._visible["beam"].isChecked() and not (fiber_mode and active):
                self._beam_piece(x1, x2, height, width, tuple(colour), element["id"])
            if self._visible["mesh"].isChecked():
                self._polyline([self._position(x1+(x2-x1)*i/4) for i in range(5)],
                               (0.10,0.25,0.37), element["id"], width=2.0)
        if self._visible["original"].isChecked():
            for eta in (-height/2,height/2):
                for zeta in (-width/2,width/2):
                    self._polyline([self._position(0,eta,zeta,original=True),
                                    self._position(length,eta,zeta,original=True)],
                                   (0.52,0.57,0.62), "geometry", opacity=0.50)
        if self._visible["rebar"].isChecked() and self._prepared.get("sections"):
            section = self._prepared["sections"][0]
            sample_x = sorted(set(self._xs + [(a+b)/2 for a,b in zip(self._xs,self._xs[1:])]))
            for fiber in section.get("fibers", []):
                if fiber.get("role") != "steel":
                    continue
                radius = sqrt(float(fiber["area"])/pi)
                # The core constructs a steel ID as section.id + '/' + bar.id.
                # Remove exactly that owned prefix; never guess using coordinates.
                prefix = str(section["id"]) + "/"
                source_bar = str(fiber["id"])[len(prefix):] if str(fiber["id"]).startswith(prefix) else ""
                direct_ids = {str(bar["id"]) for bar in model.get("reinforcement", {}).get("bars", [])}
                pick_id = source_bar if source_bar in direct_ids else fiber["id"]
                actor = self._polyline([self._position(x,float(fiber["eta"]),float(fiber["zeta"])) for x in sample_x],
                                       (0.72,0.31,0.18), pick_id, radius=radius)
                if source_bar:
                    self._bar_actors.setdefault(source_bar, []).append(actor)
        reinforcement = model.get("reinforcement", {})
        if self._visible["stirrups"].isChecked():
            diameter = float(reinforcement.get("stirrup_diameter", 0.0))
            spacing = float(reinforcement.get("stirrup_spacing", 0.0))
            cover = float(reinforcement.get("cover", 0.0))
            # The selected cover meaning is explicitly to stirrup outer surface.
            if diameter > 0 and spacing > 0:
                eta = height/2-cover-diameter/2
                zeta = width/2-cover-diameter/2
                count = int(length/spacing) + 1
                if count <= 2000 and eta > 0 and zeta > 0:
                    for index in range(count):
                        x = index*spacing
                        ring = [self._position(x,a,b) for a,b in
                                ((-eta,-zeta),(-eta,zeta),(eta,zeta),(eta,-zeta),(-eta,-zeta))]
                        self._polyline(ring,(0.33,0.41,0.48),"reinforcement",radius=diameter/2,opacity=0.75)
        if self._visible["mesh"].isChecked():
            for node in nodes:
                self._sphere(self._position(float(node["x"])),height*0.035,(0.12,0.23,0.36),node["id"])
        for support in model.get("supports", []):
            x = float(support["x"])
            ring = [self._position(x+dx,-height/2+dy,z) for dx,dy,z in
                    ((0,0,0),(-height*.22,-height*.32,0),(height*.22,-height*.32,0),(0,0,0))]
            self._polyline(ring,(0.17,0.36,0.43),support["id"],radius=height*.018)
            if support.get("constrained", [False])[0] is False:
                self._sphere(self._position(x,-height*.88),height*.055,(0.17,0.36,0.43),support["id"])
        for load in model.get("loads", []):
            force = load.get("reference", [0,0,0])
            fx, fy = float(force[0]), float(force[1])
            norm = sqrt(fx*fx+fy*fy)
            if norm <= 0:
                continue
            tip = self._position(float(load["x"]),height/2)
            direction = (fx/norm,fy/norm,0.0)
            tail = tuple(tip[i]-direction[i]*height*.6 for i in range(3))
            self._polyline([tail,tip],(0.78,0.25,0.15),load["id"],radius=height*.018)
            cone = vtkConeSource()
            cone.SetDirection(*direction)
            cone.SetCenter(*(tip[i]-direction[i]*height*.06 for i in range(3)))
            cone.SetHeight(height*.12)
            cone.SetRadius(height*.05)
            cone.SetResolution(12)
            mapper = vtkPolyDataMapper()
            mapper.SetInputConnection(cone.GetOutputPort())
            actor = vtkActor()
            actor.SetMapper(mapper)
            self._color_actor(actor, (0.78,0.25,0.15))
            self.renderer.AddActor(actor)
            self._actors.append(actor)
            self._actor_ids[actor.GetAddressAsString("")] = load["id"]
        self._sphere(self._position(float(model["control_x"]),-height*.65),height*.045,
                     (0.52,0.27,0.68),"control")
        if self._visible["gauss"].isChecked():
            for section in self._prepared.get("sections", []):
                x = float(section["x"])
                self._polyline([self._position(x,-height/2),self._position(x,height/2)],
                               (0.27,0.55,0.65),section["id"],width=1.0)
                self._sphere(self._position(x),height*.018,(0.17,0.49,0.57),section["id"])
        if self._step and self._visible["events"].isChecked() and not (fiber_mode and not active):
            physical = self._prepared.get("compression_criterion", {}).get("mode") == "physical_gauge_mean_v1"
            for edge in self._step.get("edges", []):
                state = edge.get("state", {})
                if state.get("crushed") or state.get("cracked"):
                    colour = ((0.68,0.43,0.12) if physical else (0.78,0.15,0.15)) if state.get("crushed") else (0.86,0.57,0.12)
                    self._sphere(self._position(float(edge["x"]),float(edge["eta"])),height*.026,colour,edge["id"])
            for fiber in self._step.get("fibers", []):
                if fiber.get("role") == "steel" and fiber.get("state", {}).get("yielded"):
                    self._sphere(self._position(float(fiber["x"]),float(fiber["eta"]),float(fiber["zeta"])),
                                 height*.03,(0.75,0.26,0.71),fiber["id"])
            self._draw_gauge_windows(height)
        self._draw_selection()
        self._missing_scalar = not fiber_mode and active and any(value is None for value in element_values)
        self._update_status()
        self._set_annotation()
        self._queue_render()

    def _draw_gauge_windows(self, height: float) -> None:
        """Draw saved edge intervals without inventing selectable point identities."""
        gauge = (self._step or {}).get("gauge") or {}
        windows = [(gauge.get("current"), (0.48, 0.20, 0.76), 4.0)]
        for event in (self._step or {}).get("events", []):
            if event.get("kind") == "physical_gauge_compression":
                windows.append(((event.get("gauge") or {}).get("current"), (0.93, 0.24, 0.09), 7.0))
        for window, colour, line_width in windows:
            if not isinstance(window, dict):
                continue
            x1, x2, eta = (_number(window.get(key)) for key in ("x1", "x2", "eta"))
            if x1 is None or x2 is None or eta is None or x2 <= x1:
                continue
            # Sample only the drawn cubic geometry; all interval/criterion values
            # come directly from native evidence, never from these render points.
            samples = sorted({x1, x2, *(x for x in self._xs if x1 < x < x2),
                              *(x1 + (x2-x1)*i/12 for i in range(1,12))})
            actor = self._polyline([self._position(x, eta) for x in samples], colour, "",
                                   width=line_width, register=False)
            self._actors.append(actor)
            for x in (x1, x2):
                actor = self._polyline([self._position(x, eta-height*.045),
                                        self._position(x, eta+height*.045)], colour, "",
                                       width=line_width, register=False)
                self._actors.append(actor)

    def _clear_highlight(self) -> None:
        for actor in self._selection_actors:
            self.renderer.RemoveActor(actor)
            self._actor_base_colors.pop(actor.GetAddressAsString(""), None)
        self._selection_actors.clear()
        self._selected_actor = None
        self._highlighted_ids = ()

    def _report_selection(self, text: str) -> None:
        self._selection_message = text
        self.selection_label.setText(text)
        self.selectionStatus.emit(text)

    def _highlight_actors(self, actors: Sequence[Any], identity: str) -> None:
        for original in actors:
            overlay = vtkActor()
            mapper = vtkPolyDataMapper()
            mapper.ShallowCopy(original.GetMapper())
            mapper.ScalarVisibilityOff()
            overlay.SetMapper(mapper)
            overlay.SetUserMatrix(original.GetMatrix())
            self._color_actor(overlay, (0.96, 0.18, 0.48))
            overlay.GetProperty().SetRepresentationToWireframe()
            overlay.GetProperty().SetLineWidth(3.0)
            overlay.GetProperty().SetLighting(False)
            overlay.PickableOff()
            self.renderer.AddActor(overlay)
            self._selection_actors.append(overlay)
        if self._selection_actors:
            self._selected_actor = self._selection_actors[0]
            self._highlighted_ids = (identity,)

    def _draw_selection(self) -> None:
        """Resolve only identities belonging to this prepared display snapshot."""
        self._clear_highlight()
        chosen = next((self._selection.get(key) for key in ("point_id", "section_id", "object_id")
                       if self._selection.get(key)), "")
        if not self._prepared or not chosen:
            self._report_selection(self._t("未选择显示对象。", "No displayed object selected."))
            return
        model = self._prepared["document"]["model"]
        geometry = model["geometry"]
        h, w = float(geometry["height"]), float(geometry["width"])
        # Engineering aggregate identities are explicit GUI adapters, not ID guesses.
        actors: list[Any] = []
        if chosen == "geometry":
            actors = self._geometry_actors
        elif chosen in ("reinforcement", "reinforcement/bars"):
            actors = [actor for items in self._bar_actors.values() for actor in items]
            if chosen == "reinforcement":
                actors += [actor for actor in self._actors
                           if self._actor_ids.get(actor.GetAddressAsString("")) == "reinforcement"]
        elif chosen == "reinforcement/stirrups":
            actors = [actor for actor in self._actors
                      if self._actor_ids.get(actor.GetAddressAsString("")) == "reinforcement"]
        elif chosen in self._bar_actors:
            actors = self._bar_actors[chosen]
        else:
            actors = [actor for actor in self._actors
                      if self._actor_ids.get(actor.GetAddressAsString("")) == chosen]
        # A full material-point identity denotes a local section point, even when
        # its first-section fiber also owns a displayed longitudinal bar actor.
        point_match = next(((section, point) for section in self._prepared.get("sections", [])
                            for point in section.get("fibers", []) + section.get("edges", [])
                            if point["id"] == chosen), None)
        if point_match is not None:
            section, point = point_match
            x, eta, zeta = float(section["x"]), float(point["eta"]), float(point.get("zeta", 0.0))
            marker = self._polyline([self._position(x, eta-h*.07, zeta), self._position(x, eta+h*.07, zeta)],
                                    (0.96, 0.18, 0.48), chosen, width=6.0, register=False)
            self._selection_actors.append(marker)
            self._selected_actor = marker
            self._highlighted_ids = (chosen,)
        elif actors:
            self._highlight_actors(actors, chosen)
        else:
            # Hidden generated nodes/sections can still be located exactly; a
            # selection marker is display-only and does not enable their layer.
            section = self._sections.get(chosen)
            node = next((item for item in self._prepared["nodes"] if item["id"] == chosen), None)
            if section is not None or node is not None:
                x = float((section if section is not None else node)["x"])
                if section is not None:
                    coordinates = [self._position(x, eta, zeta) for eta, zeta in
                                   ((-h*.55,-w*.55),(-h*.55,w*.55),(h*.55,w*.55),(h*.55,-w*.55),(-h*.55,-w*.55))]
                else:
                    coordinates = [self._position(x, -h*.07), self._position(x, h*.07)]
                marker = self._polyline(coordinates, (0.96, 0.18, 0.48), chosen, width=4.0, register=False)
                self._selection_actors.append(marker)
                self._selected_actor = marker
                self._highlighted_ids = (chosen,)
        self.selection_label.setToolTip(str(chosen))
        if self._highlighted_ids:
            self._report_selection(self._t("已选择并定位当前快照中的对象。", "Selected and located in the displayed snapshot."))
        else:
            self._report_selection(self._t("已选择；当前显示无对应几何，不高亮其他对象。", "Selected; no matching geometry in this display. No other object highlighted."))

    def _legend_title(self) -> str:
        # VTK's built-in font need not provide CJK glyphs. The principal localized
        # variable/unit is a Qt label and is composited into every PNG below.
        title = {"geometry": "", "displacement": "|u| [mm]", "N": "N [N]", "V": "V [N]",
                 "M": "M [N.mm]", "curvature": "k [1/mm]", "threshold_ratio": "Route-C [1]",
                 "concrete_stress": "sigma_x [MPa]", "concrete_strain": "epsilon_x [1]"}[self.scalar_mode.currentData()]
        if self.scalar_mode.currentData() in _FIBER_FIELDS and self._fiber_range is not None:
            if self._fiber_range[0] == self._fiber_range[1]:
                title += "\nconstant"
        return title

    def _update_status(self) -> None:
        if not self._prepared:
            self.status.setText(self._t("检查模型后显示实际网格；三维外观不增加计算自由度。",
                                        "Check the model to display its actual mesh; 3D appearance adds no solver degrees of freedom."))
            return
        count = (len(self._prepared["nodes"]), len(self._prepared["elements"]), len(self._sections))
        text = self._t("二维纤维梁 / 三维外观；{0}节点、{1}单元、{2}个实际高斯积分截面。",
                       "Planar fiber beam / 3D appearance; {0} nodes, {1} elements, {2} actual Gauss sections.").format(*count)
        text += self._t(" 箍筋仅外观；状态点不代表离散裂缝路径。", " Stirrups are appearance only; state markers are not discrete cracks.")
        if self.scalar_mode.currentData() in _FIBER_FIELDS:
            if self._fiber_range is None:
                text += self._t(" 混凝土纤维场不可用：无已接受帧或完整一致的保存数据；未补零或借帧。",
                                " Concrete-fiber field unavailable: no accepted frame or complete consistent saved data; no zero fill or substitute frame.")
            else:
                text += self._t(" 已保存二维纤维层/高斯代表分块，拉正压负；宽度仅外观，钢筋不属混凝土色标。",
                                " Saved planar fiber/Gauss representative blocks, tension positive; width is appearance, reinforcement is outside concrete legend.")
                if self.scalar_mode.currentData() == "concrete_strain":
                    text += self._t(" 总机械应变包含自重。", " Total mechanical strain includes gravity.")
                if self._fiber_range[0] == self._fiber_range[1]:
                    text += self._t(" 常值场，无物理变化范围。", " Constant field; no physical range variation.")
                if not self._visible["beam"].isChecked():
                    text += self._t(" 梁外观已隐藏，混凝土场和色标隐藏。", " Beam hidden; concrete field and legend hidden.")
            # Full criterion/provenance remains in Details and export. Keep this
            # presentation note compact at the existing supported workbench sizes.
            self.status.setText(text)
            return
        if getattr(self, "_missing_scalar", False) and self._step:
            text += self._t(" 部分结果缺失，以灰色显示。", " Missing result values are gray.")
        if (self._step or {}).get("gauge") is not None:
            text += self._t(" 紫色：当前物理标距窗口；橙红：实际判据事件窗口。含自重总应变，未标定假设；点式标记仅诊断。",
                            " Purple: current gauge; orange-red: actual criterion event. Total strain includes gravity; uncalibrated assumption; point flags are diagnostic.")
        self.status.setText(text)

    def _set_annotation(self) -> None:
        """Keep concise localized context outside VTK and exact identity in details."""
        mode = str(self.scalar_mode.currentData())
        variable = _SCALAR_EN[mode] if self.language == "en" else _SCALARS[mode][0]
        note = _SCALARS[mode][1] if self.language == "en" else _SCALAR_NOTES_ZH[mode]
        self.legend.SetTitle(self._legend_title())
        if not self._prepared:
            self.context_label.setText(self._t("尚无已检查模型 / 无结果", "No checked model / no results"))
            self.context_label.setToolTip(note)
            self.annotation.SetInput("LumaFEM | no prepared model")
            self.details.setPlainText(self._t("尚无准备快照。", "No prepared snapshot."))
            return
        identity = self._prepared["identity"]
        details = [self._t("工程身份", "Project identity") + ": " + str(identity.get("project_id", "")),
                   self._t("修订", "Revision") + ": " + str(identity.get("revision_id", "")),
                   self._t("运行", "Run") + ": " + str(identity.get("run_id", "")),
                   self._t("快照", "Snapshot") + ": " + str(identity.get("snapshot_id", "")),
                   "prepared_hash: " + str(self._prepared.get("prepared_hash", ""))]
        if self._step:
            phase_code = str(self._step.get("phase", ""))
            phase = _PHASES.get(phase_code, (phase_code, phase_code))[1 if self.language == "en" else 0]
            context = self._t("冻结结果 · {phase} · 已接受步 {sequence} · {variable} · 变形倍率 {scale}",
                              "Frozen result | {phase} | accepted frame {sequence} | {variable} | scale {scale}")
            self.context_label.setText(context.format(phase=phase, sequence=self._step["sequence"],
                                                      variable=variable, scale=scientific(self._magnification)))
            self.annotation.SetInput(f"LumaFEM | {phase_code}/{self._step['sequence']} | P={scientific(self._step['P'])} N\n{self._legend_title()} | scale={scientific(self._magnification)}")
            details += [self._t("结果步身份", "Step identity") + ": " + str(self._step["identity"].get("step_id", "")),
                        f"sequence: {self._step['sequence']} | phase: {phase_code}", f"P: {full_precision(self._step['P'])} N",
                        "scale: " + full_precision(self._magnification)]
            gauge = self._step.get("gauge")
            if gauge is not None:
                for key in ("current", "historical"):
                    window = gauge.get(key) or {}
                    heading = self._t("当前物理标距窗口" if key == "current" else "历史最大窗口",
                                      "Current physical gauge" if key == "current" else "Historical maximum gauge")
                    details += [heading + f": {window.get('mode', 'unknown')} / {window.get('side', 'unknown')}",
                                f"x=[{full_precision(window.get('x1'))}, {full_precision(window.get('x2'))}] mm; eta={full_precision(window.get('eta'))} mm",
                                f"signed_mean={full_precision(window.get('signed_mean'))}; compression_maximum={full_precision(window.get('compression_maximum'))}; threshold={full_precision(window.get('threshold'))} [1]",
                                f"reference={window.get('reference', 'unknown')}; owner={window.get('identity', {}).get('step_id', 'unknown')}"]
                details.append(self._t("总应变含自重；未标定建模假设。点式压碎仅作诊断。",
                                       "Total strain includes gravity; uncalibrated modeling assumption. Point crushing is diagnostic only."))
            elif self._prepared.get("compression_criterion", {}).get("mode") == "physical_gauge_mean_v1":
                details.append(self._t("物理标距证据缺失；未重建窗口。", "Physical gauge evidence unavailable; no window reconstructed."))
            else:
                details.append(self._t("旧点式判据；不推断物理窗口证据。", "Legacy point criterion; no physical window evidence inferred."))
        else:
            self.context_label.setText(self._t("已检查模型预览 · 无已接受结果 · {variable} · 变形倍率 {scale}",
                                               "Checked model preview | no accepted result | {variable} | scale {scale}").format(variable=variable, scale=scientific(self._magnification)))
            self.annotation.SetInput(f"LumaFEM | prepared preview\n{self._legend_title()} | scale={scientific(self._magnification)}")
        chosen = next((self._selection.get(key) for key in ("point_id", "section_id", "object_id") if self._selection.get(key)), "")
        if mode in _FIBER_FIELDS:
            if self._fiber_range is None:
                self.context_label.setText(self.context_label.text() + self._t(" · 纤维场不可用", " | fiber field unavailable"))
                details += [self._t("混凝土纤维场不可用", "Concrete-fiber field unavailable"), self._fiber_error]
            else:
                unit = "MPa" if mode == "concrete_stress" else "1"
                lower, upper = self._fiber_range
                constant = lower == upper
                label = self._t("常值", "constant") if constant else self._t("实际保存范围", "saved range")
                self.context_label.setText(self.context_label.text() + f" · {label}: {scientific(lower)}" +
                                          ("" if constant else f" .. {scientific(upper)}") + " " + unit)
                details += [f"{mode}: {len(self._fiber_samples)} concrete samples; unit={unit}",
                            f"raw_range: [{full_precision(lower)}, {full_precision(upper)}] {unit}",
                            "constant: " + str(constant)]
                for sample in self._fiber_samples:
                    if chosen in (sample["fiber_id"], sample["section_id"]):
                        details += [f"fiber_id: {sample['fiber_id']}",
                                    f"section_id: {sample['section_id']}",
                                    f"saved x={full_precision(sample['x'])} mm; eta={full_precision(sample['eta'])} mm; value={full_precision(sample['value'])} {unit}"]
        details += [note, self._t("所选身份", "Selected identity") + ": " + str(chosen),
                    self._t("几何单位 mm；应变无量纲；三维外观不增加计算自由度。",
                            "Geometry: mm; strain: dimensionless; 3D appearance adds no solver degrees of freedom.")]
        # Native evidence, identifiers and unit symbols are never translated.
        self.details.setPlainText("\n".join(details))
        self.context_label.setToolTip(note + "\n" + "\n".join(details[:5]))

    def reset_camera(self) -> None:
        """Fit the current geometry using a useful 3D orientation."""
        self._assert_gui()
        if not self._prepared:
            return
        camera = self.renderer.GetActiveCamera()
        camera.SetPosition(0,-1,1.1)
        camera.SetFocalPoint(0,0,0)
        camera.SetViewUp(0,1,0)
        camera.ParallelProjectionOff()
        self.renderer.ResetCamera()
        self._fit_camera_projection()
        self._queue_render()

    def front_view(self) -> None:
        """Show the beam plane without changing data or display variables."""
        self._assert_gui()
        if not self._prepared:
            return
        camera = self.renderer.GetActiveCamera()
        camera.SetPosition(0,0,1)
        camera.SetFocalPoint(0,0,0)
        camera.SetViewUp(0,1,0)
        camera.ParallelProjectionOn()
        self.renderer.ResetCamera()
        self._fit_camera_projection()
        self._queue_render()

    def _fit_camera_projection(self) -> None:
        """Fit actual visible bounds to the current aspect, not a bounding sphere."""
        bounds = self.renderer.ComputeVisiblePropBounds()
        width, height = self.render_window.GetSize()
        if width <= 0 or height <= 0 or not bounds or bounds[0] > bounds[1]:
            return
        camera = self.renderer.GetActiveCamera()
        for _ in range(2):
            projected = []
            for x in (bounds[0], bounds[1]):
                for y in (bounds[2], bounds[3]):
                    for z in (bounds[4], bounds[5]):
                        self.renderer.SetWorldPoint(x, y, z, 1)
                        self.renderer.WorldToDisplay()
                        projected.append(self.renderer.GetDisplayPoint())
            span_x = max(p[0] for p in projected) - min(p[0] for p in projected)
            span_y = max(p[1] for p in projected) - min(p[1] for p in projected)
            if span_x <= 0 or span_y <= 0:
                break
            target_width = .70 if self.legend.GetVisibility() else .76
            factor = min(target_width * width / span_x, .76 * height / span_y)
            factor = max(.25, min(4., factor))
            if camera.GetParallelProjection():
                camera.SetParallelScale(camera.GetParallelScale() / factor)
            else:
                camera.Dolly(factor)
            self.renderer.ResetCameraClippingRange()

    def _front_view(self) -> None:
        """Compatibility alias for the public planar camera command."""
        self.front_view()

    def _mouse_press(self, *_: Any) -> None:
        self._press_position = tuple(self.interactor.GetEventPosition())

    def _mouse_release(self, *_: Any) -> None:
        position = tuple(self.interactor.GetEventPosition())
        start, self._press_position = self._press_position, None
        if start is None or sum((a-b)**2 for a,b in zip(start,position)) > 16:
            return
        self._pick_position(position)

    def _pick_position(self, position: tuple[int, int]) -> None:
        """Resolve the exact real widget event position through VTK picking."""
        if self.picker.Pick(position[0],position[1],0,self.renderer):
            actor = self.picker.GetActor()
            identity = self._actor_ids.get(actor.GetAddressAsString(""), "") if actor else ""
            if identity:
                self._selection = {"object_id":identity}
                self._draw_selection()
                self._set_annotation()
                self._queue_render()
                self.objectSelected.emit(identity)

    def export_png(self, path: str) -> None:
        """Export VTK pixels with Qt-rendered localized context and exact identity.

        Qt uses the platform's Unicode font fallback. This avoids silently blank
        CJK glyphs in VTK's built-in ASCII font while retaining its scalar bar.
        """
        self._assert_gui()
        target = Path(path).expanduser().resolve()
        if target.exists():
            raise ContractError("export_exists", self._t("请选择新的 PNG 路径；已有文件保留。", "Choose a new PNG path; existing files are preserved"), stage="display")
        if not target.parent.is_dir() or target.suffix.lower() != ".png":
            raise ContractError("export_path", self._t("PNG 需要已存在的父目录及 .png 后缀。", "PNG needs an existing parent directory and .png suffix"), stage="display")
        if not self._prepared or not self._initialised or not self.isVisible():
            raise ContractError("export_view", self._t("请先打开可见且已检查的视口，再导出 PNG。", "Open a prepared visible viewport before PNG export"), stage="display")
        self._set_annotation()
        self._layout_overlays()
        self.render_window.Render()
        capture = vtkWindowToImageFilter()
        capture.SetInput(self.render_window)
        capture.SetInputBufferTypeToRGB()
        capture.ReadFrontBufferOff()
        capture.Update()
        writer = vtkPNGWriter()
        writer.SetWriteToMemory(True)
        writer.SetInputConnection(capture.GetOutputPort())
        errors: list[str] = []
        writer.AddObserver("ErrorEvent", lambda *_: errors.append("VTK PNG writer failed"))
        try:
            writer.Write()
            if errors or writer.GetErrorCode():
                raise OSError("VTK did not produce a complete PNG")
            vtk_image = QImage.fromData(bytes(memoryview(writer.GetResult())), "PNG")
            if vtk_image.isNull():
                raise OSError("VTK image could not be decoded")
            font = QFont("Microsoft YaHei", 10)
            metrics = QFontMetrics(font)
            width = max(vtk_image.width(), 640)
            flags = int(Qt.AlignmentFlag.AlignLeft) | int(Qt.AlignmentFlag.AlignTop) | int(Qt.TextFlag.TextWordWrap)
            caption = self.context_label.text()
            caption += "\n" + self.status.text() + "\n" + self._selection_message
            # Full immutable identity is part of the exported evidence caption,
            # even though the normal interactive view keeps it in Details.
            identity = self._prepared["identity"]
            caption += "\n" + " | ".join(f"{key}={identity.get(key, '')}" for key in
                                            ("project_id", "revision_id", "run_id", "snapshot_id"))
            if self._step:
                caption += "\nstep_id=" + str(self._step["identity"].get("step_id", ""))
            caption += "\nprepared_hash=" + str(self._prepared.get("prepared_hash", ""))
            if self.scalar_mode.currentData() in _FIBER_FIELDS and self._fiber_range is not None:
                unit = "MPa" if self.scalar_mode.currentData() == "concrete_stress" else "1"
                caption += "\nsaved_concrete_range=[" + ", ".join(full_precision(value) for value in self._fiber_range) + "] " + unit
                caption += "; concrete_samples=" + str(len(self._fiber_samples))
            caption_height = metrics.boundingRect(QRect(0, 0, width-24, 10000), flags, caption).height()+24
            output = QImage(width, vtk_image.height()+caption_height, QImage.Format.Format_RGB32)
            output.fill(QColor(colors(self._theme)["plot"]))
            painter = QPainter(output)
            try:
                painter.drawImage((width-vtk_image.width())//2, 0, vtk_image)
                painter.setFont(font)
                painter.setPen(QColor(colors(self._theme)["text"]))
                painter.drawText(QRect(12, vtk_image.height()+12, width-24, caption_height-24), flags, caption)
            finally:
                painter.end()
            encoded = QByteArray()
            buffer = QBuffer(encoded)
            if not buffer.open(QIODevice.OpenModeFlag.WriteOnly):
                raise OSError("Cannot allocate PNG output buffer")
            try:
                if not output.save(buffer, "PNG"):
                    raise OSError("Qt did not encode the annotated PNG")
            finally:
                buffer.close()
            # Exclusive create also protects an existing path appearing after
            # the initial check; no source/project file is ever overwritten.
            with target.open("xb") as destination:
                destination.write(bytes(encoded))
        except (OSError, RuntimeError) as exc:
            raise ContractError("png_write", str(exc), stage="display", field=str(target)) from exc
