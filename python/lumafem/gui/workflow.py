"""Presentation-only module guidance and actual single-job monitoring."""
from __future__ import annotations

from copy import deepcopy
import json
from typing import Any

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QFormLayout, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QVBoxLayout, QWidget

from .i18n import tr

__all__ = ["MODULES", "TERMINATIONS", "PHASES", "JobMonitor"]

MODULES = (
    ("geometry", "几何", "选择梁对象并编辑尺寸；应用后检查模型。"),
    ("materials", "材料", "编辑材料物理参数并保留来源与假设标记。"),
    ("section", "截面与配筋", "编辑截面与纵向钢筋；箍筋仅作外观与档案。"),
    ("step", "分析步", "检查自重后两点加载、控制与终止判据；高级求解参数在高级页。"),
    ("loads", "荷载与约束", "选择真实支座、两点荷载或自重对象。"),
    ("mesh", "网格", "编辑离散设置并检查实际节点、单元和截面。"),
    ("job", "作业", "提交一个后台作业；取消在安全接受与保存边界生效。"),
    ("results", "结果", "选择真实运行和已接受帧；曲线与视口联动。"),
)
PHASES = {"gravity": "自重平衡", "S0": "自重平衡", "SG": "自重平衡", "jack": "两点加载", "J": "两点加载", "loading": "两点加载",
          "localization": "参考压缘事件定位", "transition": "首次软化转折定位（非终止）"}
TERMINATIONS = {"reference_compression": "达到旧点参考压缘判据", "physical_gauge_compression": "达到固定物理压缘窗口平均应变判据",
                "cancelled": "已取消", "storage_error": "存储失败", "non_convergence": "数值不收敛", "localization_failure": "事件定位失败",
                "safety_limit": "达到安全限值", "internal_error": "内部错误"}


class JobMonitor(QWidget):
    """Inspect original controller state; buttons request existing services."""

    submitRequested = Signal()
    cancelRequested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("jobMonitor")
        self._state: dict[str, Any] = {}
        self._progress: dict[str, Any] = {}
        self._summary: dict[str, Any] = {}
        layout = QVBoxLayout(self)
        self.heading = QLabel()
        self.heading.setWordWrap(True)
        layout.addWidget(self.heading)
        self.form = QFormLayout()
        self.values: dict[str, QLabel] = {}
        self.labels: dict[str, QLabel] = {}
        for key, source in (("status", "作业状态"), ("phase", "阶段"), ("accepted", "已接受步"),
                            ("persisted", "已保存步"), ("termination", "终止原因")):
            label, value = QLabel(source), QLabel("—")
            value.setWordWrap(True)
            self.labels[key], self.values[key] = label, value
            self.form.addRow(label, value)
        layout.addLayout(self.form)
        controls = QHBoxLayout()
        self.submit_button, self.cancel_button = QPushButton(), QPushButton()
        self.submit_button.setObjectName("monitorSubmit")
        self.cancel_button.setObjectName("monitorCancel")
        self.submit_button.clicked.connect(self.submitRequested)
        self.cancel_button.clicked.connect(self.cancelRequested)
        controls.addWidget(self.submit_button)
        controls.addWidget(self.cancel_button)
        controls.addStretch()
        layout.addLayout(controls)
        self.details_heading = QLabel()
        self.details_heading.setObjectName("jobDiagnosticDetailsHeading")
        self.details_heading.setWordWrap(True)
        layout.addWidget(self.details_heading)
        self.details = QPlainTextEdit()
        self.details.setReadOnly(True)
        self.details.setMaximumBlockCount(300)
        layout.addWidget(self.details, 1)
        self.retranslate_ui()

    def set_state(self, state: dict[str, Any]) -> None:
        old_run = self._state.get("run_id")
        self._state = deepcopy(state)
        if state.get("running") and state.get("run_id") != old_run:
            self._summary, self._progress = {}, {}
        self._render()

    def set_progress(self, value: dict[str, Any]) -> None:
        self._progress = deepcopy(value)
        self._render()

    def set_summary(self, value: dict[str, Any]) -> None:
        self._summary = deepcopy(value)
        self._render()

    def retranslate_ui(self) -> None:
        self.heading.setText(tr("提交一个后台作业；取消在安全接受与保存边界生效。"))
        for key, source in (("status", "作业状态"), ("phase", "阶段"), ("accepted", "已接受步"),
                            ("persisted", "已保存步"), ("termination", "终止原因")):
            self.labels[key].setText(tr(source))
        self.submit_button.setText(tr("提交作业"))
        self.cancel_button.setText(tr("取消作业"))
        self.details_heading.setText(tr("原始诊断与身份详情"))
        self.details.setAccessibleName(tr("原始诊断与身份详情"))
        self._render()

    def _render(self) -> None:
        running = bool(self._state.get("running"))
        self.submit_button.setEnabled(not running and bool(self._state.get("working_path")))
        self.cancel_button.setEnabled(running and not self._state.get("cancel_requested"))
        status = "取消待处理" if running and self._state.get("cancel_requested") else "运行中" if running else "就绪"
        self.values["status"].setText(tr(status))
        phase = self._progress.get("stage")
        self.values["phase"].setText(tr(PHASES.get(phase, "尚无作业进度")))
        self.values["accepted"].setText(str(self._state.get("memory_last_step", "—")))
        self.values["persisted"].setText(str(self._state.get("persisted_last_step", "—")))
        reason = self._summary.get("termination")
        self.values["termination"].setText(tr(TERMINATIONS.get(reason, "未结束 / 中断")) if reason else "—")
        self.details.setPlainText(json.dumps({"progress": self._progress, "summary": self._summary,
            "identity": {key: self._state.get(key) for key in ("run_id", "revision_id", "path", "memory_last_step", "persisted_last_step")}},
            ensure_ascii=False, indent=2))
