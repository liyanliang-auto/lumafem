"""Document revisions and the single serial native-analysis task for the Qt app.

The worker owns its SQLite writer for the entire calculation.  UI reads use
separate, short-lived readonly connections.  Only compact accepted-step indexes
cross the normal Qt notification channel; complete material arrays stay on disk.
No solver restart or background-thread GUI access is provided.
"""
from __future__ import annotations

from collections import OrderedDict
from copy import deepcopy
from functools import wraps
import hashlib
import json
from pathlib import Path
import queue
import threading
import time
from typing import Any, Callable

from PySide6.QtCore import QObject, QThread, QTimer, Signal, Slot

from . import native
from .contracts import (ContractError, DOCUMENT_KEYS, KERNEL_VERSION, SCHEMA_VERSION, diagnostic,
                        new_id, require_keys, same_run, validate_finite,
                        validate_identity, validation_status)
from .observations import compare_curves, export_csv, import_csv, observe_step
from .storage import ProjectStore


def _compact(step: dict[str, Any]) -> dict[str, Any]:
    """Keep actual curve/nodal/force data while evicting bulk material histories."""
    result = {key: deepcopy(value) for key, value in step.items()
              if key not in ("sections", "fibers", "edges")}
    result["_bulk_unloaded"] = True
    return result


def _hash(value: Any) -> str:
    validate_finite(value)
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    allow_nan=False, separators=(",", ":")).encode("utf-8")).hexdigest()


def _detail(error: Exception, operation: str) -> dict[str, Any]:
    if isinstance(error, ContractError):
        return deepcopy(error.diagnostic)
    return diagnostic("application_error", f"{operation}: {error}", stage="application")


def _command(method: Callable[..., Any]) -> Callable[..., Any]:
    """Convert UI command failures to one structured notification, retaining state."""
    @wraps(method)
    def invoke(self: ProjectController, *args: Any, **kwargs: Any) -> Any:
        try:
            return method(self, *args, **kwargs)
        except Exception as error:
            self._report(_detail(error, method.__name__))
            return None
    return invoke


class _AnalysisWorker(QThread):
    """One task with thread-owned native work, SQLite writer and command mailbox."""

    accepted = Signal(dict)
    progress = Signal(dict)
    concluded = Signal(dict)
    operationDone = Signal(dict)
    error = Signal(dict)

    def __init__(self, path: Path, prepared: dict[str, Any], parent: QObject) -> None:
        super().__init__(parent)
        self._path = path
        self._prepared = deepcopy(prepared)
        self._cancel = threading.Event()
        self._commands: queue.SimpleQueue[dict[str, Any]] = queue.SimpleQueue()
        self._last_memory = -1
        self._last_memory_id = ""
        self._last_saved = -1
        self._last_saved_id = ""
        self._last_progress = 0.0
        self._stage = ""

    def request_cancel(self) -> None:
        """Set a thread-safe flag; never terminate a native calculation thread."""
        self._cancel.set()

    def submit(self, command: dict[str, Any]) -> None:
        """Queue copied IO work for execution at a safe worker callback boundary."""
        self._commands.put(deepcopy(command))

    def _mailbox(self, store: ProjectStore) -> None:
        while True:
            try:
                command = self._commands.get_nowait()
            except queue.Empty:
                return
            try:
                operation = command["operation"]
                if operation == "save":
                    store.save_document(command["document"])
                elif operation == "copy":
                    store.save_as(command["path"])
                else:
                    raise ContractError("invalid_worker_command", "Unknown worker IO command", stage="application")
                self.operationDone.emit(deepcopy(command))
            except Exception as error:
                self.error.emit(_detail(error, "worker IO"))

    def run(self) -> None:
        identity = deepcopy(self._prepared["identity"])
        summary: dict[str, Any] | None = None
        store: ProjectStore | None = None
        started = False
        try:
            store = ProjectStore(self._path)
            store.begin_run(self._prepared)
            started = True

            def accept(step: dict[str, Any]) -> dict[str, Any]:
                # Acceptance/save is deliberately noncancellable.  Failed writes
                # still publish the actual accepted in-memory physical state.
                self._last_memory = step["sequence"]
                self._last_memory_id = step["identity"]["step_id"]
                receipt = store.append_step(step)
                self._last_saved = receipt["persisted_sequence"]
                if self._last_saved == step["sequence"]:
                    self._last_saved_id = step["identity"]["step_id"]
                self.accepted.emit({"identity": identity, "step": _compact(step),
                                    "receipt": deepcopy(receipt),
                                    "unsaved_step": deepcopy(step) if not receipt["ok"] else None})
                self._mailbox(store)
                return receipt

            def progress(value: dict[str, Any]) -> None:
                self._mailbox(store)
                now = time.monotonic()
                stage = str(value.get("stage", ""))
                if stage != self._stage or now - self._last_progress >= 0.1:
                    self.progress.emit(deepcopy(value))
                    self._stage, self._last_progress = stage, now

            def cancelled() -> bool:
                return self._cancel.is_set()

            summary = native.analyze(self._prepared, accept, progress, cancelled)
            self._mailbox(store)
            summary["conclusion_persisted"] = True
            try:
                store.finish_run(summary)
            except Exception as error:
                # The file may contain the final step but no final conclusion.
                summary = deepcopy(summary)
                summary["secondary_termination"] = summary.get("termination")
                summary["termination"] = "storage_error"
                summary["message"] = "Final run conclusion could not be saved; complete saved steps remain available."
                summary.setdefault("diagnostics", []).append(_detail(error, "finish_run"))
                summary["conclusion_persisted"] = False
        except Exception as error:
            detail = _detail(error, "analysis task")
            summary = {"identity": identity, "termination": "storage_error" if not started else "internal_error",
                       "message": detail["message"], "memory_last_step": self._last_memory,
                       "persisted_last_step": self._last_saved, "memory_last_step_id": self._last_memory_id,
                       "persisted_last_step_id": self._last_saved_id,
                       "physical_criterion_reached": False, "cancellation_requested": self._cancel.is_set(),
                       "physical_evidence": [], "diagnostics": [detail], "localization": None,
                       "validation_status": validation_status(), "conclusion_persisted": False}
            if started and store is not None:
                try:
                    store.finish_run(summary)
                    summary["conclusion_persisted"] = True
                except Exception as save_error:
                    summary["termination"] = "storage_error"
                    summary["diagnostics"].append(_detail(save_error, "save failure conclusion"))
        finally:
            if store is not None:
                try:
                    store.close()
                except Exception as error:
                    if summary is not None:
                        summary.setdefault("diagnostics", []).append(_detail(error, "close project writer"))
                    self.error.emit(_detail(error, "close project writer"))
            if summary is not None:
                self.concluded.emit(deepcopy(summary))


class ProjectController(QObject):
    """Own one document and task; expose value snapshots to the Qt workbench.

    ``current_run.steps`` contains compact real-step records.  ``get_step`` reads
    exact complete arrays on demand with a four-step LRU.  Only an unsaved final
    accepted step is retained separately in memory after storage failure.
    """

    documentChanged = Signal(dict)
    preparedChanged = Signal(dict)
    stepAccepted = Signal(dict)
    progressChanged = Signal(dict)
    runFinished = Signal(dict)
    errorRaised = Signal(dict)
    selectionChanged = Signal(dict)
    importsChanged = Signal(dict)
    stateChanged = Signal(dict)
    runChanged = Signal(dict)
    observationChanged = Signal(dict)

    def __init__(self, parent: QObject | None = None, *, work_dir: str | Path | None = None) -> None:
        super().__init__(parent)
        self._work_dir = Path(work_dir) if work_dir is not None else Path(__file__).resolve().parents[2] / ".cache" / "projects"
        self._document: dict[str, Any] = {}
        self._prepared: dict[str, Any] = {}
        self._current_run: dict[str, Any] = {}
        self._imports: list[dict[str, Any]] = []
        self._selection: dict[str, Any] = {}
        self._observation: dict[str, Any] = {}
        self._view_state: dict[str, Any] = {}
        self._draft: dict[str, Any] | None = None
        self._undo: list[dict[str, Any]] = []
        self._redo: list[dict[str, Any]] = []
        self._path: Path | None = None
        self._reader: ProjectStore | None = None
        self._public_path: Path | None = None
        self._saved_hash: str | None = None
        self._last_recovery_hash: str | None = None
        self._cache: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self._unsaved_step: dict[str, Any] | None = None
        self._worker: _AnalysisWorker | None = None
        self._running = False
        self._closing = False
        self._cancel_requested = False
        self._progress_sequence = -1
        self._pending_retarget: Path | None = None
        self._diagnostics: list[dict[str, Any]] = []
        self._timer = QTimer(self)
        self._timer.setInterval(30000)
        self._timer.timeout.connect(self._recover)
        self._timer.start()

    @property
    def document(self) -> dict[str, Any]:
        """Current editable revision, independently owned by the caller."""
        return deepcopy(self._document)

    @property
    def prepared(self) -> dict[str, Any]:
        """Current checked preparation, or an empty object when invalidated."""
        return deepcopy(self._prepared)

    @property
    def current_run(self) -> dict[str, Any]:
        """Displayed run snapshot and compact accepted records; full data use get_step."""
        return deepcopy(self._current_run)

    @property
    def imports(self) -> list[dict[str, Any]]:
        """Self-contained imported numeric channels and their processing provenance."""
        return deepcopy(self._imports)

    @property
    def selection(self) -> dict[str, Any]:
        """The authoritative selected real-step/object identity."""
        return deepcopy(self._selection)

    @property
    def observation(self) -> dict[str, Any]:
        """Display/measurement definition, separate from a frozen analysis snapshot."""
        return deepcopy(self._observation)

    @property
    def state(self) -> dict[str, Any]:
        """Independent document, task, saving and display state for action enablement."""
        dirty = bool(self._document) and (self._saved_hash != _hash(self._document) or
                                          self._view_state != self._document.get("view_state", {}))
        run_identity = self._current_run.get("prepared", {}).get("identity", {})
        summary = self._current_run.get("summary") or {}
        return {"running": self._running, "dirty": dirty, "has_pending_draft": self._draft is not None,
                "can_undo": bool(self._undo) and not self._running,
                "can_redo": bool(self._redo) and not self._running,
                "path": str(self._public_path) if self._public_path else None,
                "working_path": str(self._path) if self._path else None,
                "recovery_path": str(self._recovery_path()) if self._path else None,
                "closing": self._closing, "cancel_requested": self._cancel_requested,
                "pending_save_as": str(self._pending_retarget) if self._pending_retarget else None,
                "run_id": run_identity.get("run_id"),
                "results_stale": bool(run_identity) and any(
                    self._current_run.get("prepared", {}).get("document", {}).get(key) != self._document.get(key)
                    for key in ("model", "materials")),
                "memory_last_step": summary.get("memory_last_step", self._current_run.get("memory_last_step", -1)),
                "persisted_last_step": summary.get("persisted_last_step", self._current_run.get("persisted_last_step", -1)),
                "diagnostics": deepcopy(self._diagnostics)}

    def _emit_state(self) -> None:
        self.stateChanged.emit(self.state)

    def _report(self, detail: dict[str, Any]) -> None:
        self._diagnostics.append(deepcopy(detail))
        self.errorRaised.emit(deepcopy(detail))
        self._emit_state()

    def _idle(self) -> None:
        if self._running:
            raise ContractError("task_running", "Finish or cancel the active analysis before editing or replacing the project.", stage="application")

    def _require_document(self) -> None:
        if not self._document or self._path is None:
            raise ContractError("no_project", "Create or open a project first.", stage="application")

    def _close_reader(self) -> None:
        if self._reader is not None:
            self._reader.close()
            self._reader = None

    def _read_store(self) -> ProjectStore:
        if self._reader is None:
            self._reader = ProjectStore(self._path, readonly=True)
        return self._reader

    @staticmethod
    def _validate_document(document: dict[str, Any]) -> None:
        require_keys(document, DOCUMENT_KEYS, stage="application")
        validate_finite(document)
        if document["schema_version"] != SCHEMA_VERSION:
            raise ContractError("unsupported_document", "Unsupported document version", stage="application")
        if not all(isinstance(document.get(key), str) and document[key] for key in ("project_id", "revision_id")):
            raise ContractError("invalid_identity", "Project and revision identities are required", stage="application")

    def _replace_document(self, document: dict[str, Any]) -> None:
        self._document = deepcopy(document)
        self._document.pop("identity", None)
        self._prepared = {}
        self._draft = None
        self._view_state = deepcopy(document.get("view_state", {}))
        self._observation = deepcopy(self._view_state.get("observation", document.get("observations", {})))
        self.documentChanged.emit(self.document)
        self.preparedChanged.emit({})
        self._emit_state()

    @_command
    def new_project(self, template: dict[str, Any]) -> bool:
        """Create a separate working container from a value-owned template."""
        self._idle()
        document = deepcopy(template)
        document["project_id"], document["revision_id"] = new_id("project"), new_id("revision")
        document.pop("identity", None)
        document.pop("view_state", None)
        self._validate_document(document)
        self._work_dir.mkdir(parents=True, exist_ok=True)
        path = self._work_dir / (document["project_id"] + ".lumafem")
        with ProjectStore(path) as store:
            store.save_document(document)
        self._close_reader()
        self._path, self._public_path, self._saved_hash = path, None, None
        self._undo, self._redo, self._imports, self._current_run = [], [], [], {}
        self._selection, self._cache, self._unsaved_step = {}, OrderedDict(), None
        self._diagnostics = []
        self._replace_document(document)
        self.runChanged.emit({})
        self.importsChanged.emit([])
        return True

    def _read_run(self, store: ProjectStore, run_id: str | None = None) -> dict[str, Any]:
        run = store.load_run(run_id, include_steps=False)
        # Reopen without loading all material histories at once.  Each exact
        # step is checked then immediately compacted; bad blocks stay absent.
        steps, unavailable = [], []
        for index in run.get("step_index", []):
            try:
                steps.append(_compact(store.load_step(run["prepared"]["identity"]["run_id"], index["sequence"])))
            except Exception as error:
                detail = _detail(error, "read accepted step")
                run.setdefault("diagnostics", []).append(detail)
                unavailable.append({**index, "diagnostics": [detail]})
        run["steps"], run["unavailable_steps"] = steps, unavailable
        run["memory_last_step"] = (run.get("summary") or {}).get("memory_last_step")
        return run

    def available_runs(self) -> list[dict[str, Any]]:
        """List saved run identities without material arrays for a history chooser."""
        self._require_document()
        return self._read_store().list_runs()

    @_command
    def load_run(self, run_id: str) -> bool:
        """Browse another saved snapshot without replacing the editable model."""
        self._idle()
        run = self._read_run(self._read_store(), run_id)
        self._current_run = run
        self._selection, self._cache, self._unsaved_step = {}, OrderedDict(), None
        self.runChanged.emit(self.current_run)
        if run.get("steps"):
            self.select(self._step_selection(run["steps"][-1]))
        self._emit_state()
        return True

    @_command
    def open_project(self, path: str) -> bool:
        """Inspect a candidate completely before replacing the open document."""
        self._idle()
        target = Path(path).expanduser().resolve()
        with ProjectStore(target, readonly=True) as store:
            document = store.load_document()
            self._validate_document(document)
            imports = store.load_imports()
            runs = store.list_runs()
            view = document.get("view_state", {})
            preferred = view.get("run_id")
            if preferred and not any(item["run_id"] == preferred for item in runs):
                preferred = None
            run = self._read_run(store, preferred) if runs else {}
            diagnostics = [*store.source_diagnostics(), *run.get("diagnostics", [])]
        self._close_reader()
        self._path = self._public_path = target
        self._saved_hash = _hash(document)
        self._undo, self._redo, self._imports, self._current_run = [], [], imports, run
        self._cache, self._unsaved_step, self._selection = OrderedDict(), None, {}
        self._diagnostics = diagnostics
        self._replace_document(document)
        self.runChanged.emit(self.current_run)
        self.importsChanged.emit(self.imports)
        if run.get("steps"):
            saved_selection = view.get("selection", {})
            try:
                self.select(saved_selection if saved_selection else self._step_selection(run["steps"][-1]))
            except Exception:
                self.select(self._step_selection(run["steps"][-1]))
        self._emit_state()
        return True

    @_command
    def apply_document(self, document: dict[str, Any]) -> bool:
        """Apply one model revision without discarding old result snapshots."""
        self._idle()
        self._require_document()
        candidate = deepcopy(document)
        if candidate.get("project_id") != self._document["project_id"]:
            raise ContractError("project_mismatch", "Edits must belong to the current project", stage="application")
        self._validate_document(candidate)
        candidate["revision_id"] = self._document["revision_id"]
        candidate.pop("identity", None)
        if candidate == self._document:
            self._draft = None
            self._emit_state()
            return True
        candidate["revision_id"] = new_id("revision")
        self._undo.append(deepcopy(self._document))
        self._redo.clear()
        self._replace_document(candidate)
        return True

    @_command
    def revert_document(self) -> bool:
        """Discard only unapplied form edits; applied history uses undo/redo."""
        self._draft = None
        self.documentChanged.emit(self.document)
        self._emit_state()
        return True

    @_command
    def set_draft(self, document: dict[str, Any]) -> None:
        """Retain an independent unapplied form snapshot for timed recovery."""
        self._idle()
        self._draft = deepcopy(document) if document != self._document else None
        self._emit_state()

    @_command
    def undo(self) -> bool:
        """Restore a previous immutable applied revision, preserving its identity."""
        self._idle()
        if not self._undo:
            return False
        self._redo.append(deepcopy(self._document))
        self._replace_document(self._undo.pop())
        return True

    @_command
    def redo(self) -> bool:
        """Restore the next applied revision after undo."""
        self._idle()
        if not self._redo:
            return False
        self._undo.append(deepcopy(self._document))
        self._replace_document(self._redo.pop())
        return True

    @_command
    def check_model(self) -> dict[str, Any]:
        """Prepare the editable model and publish located native diagnostics."""
        self._idle()
        self._require_document()
        result = native.prepare(self._document)
        self._prepared = deepcopy(result["prepared"]) if result["ok"] else {}
        self._diagnostics = deepcopy(result.get("diagnostics", []))
        self.preparedChanged.emit(self.prepared)
        self._emit_state()
        return deepcopy(result)

    @_command
    def start_run(self) -> bool:
        """Freeze/save inputs, then start one native task on a real QThread."""
        self._idle()
        self._require_document()
        if self._draft is not None:
            raise ContractError("unapplied_edits", "Apply or revert pending form edits before analysis.", stage="application")
        result = native.prepare(self._document)  # A fresh run owns fresh histories and IDs.
        self._diagnostics = deepcopy(result.get("diagnostics", []))
        if not result["ok"]:
            self._prepared = {}
            self.preparedChanged.emit({})
            self._emit_state()
            return False
        prepared = result["prepared"]
        with ProjectStore(self._path) as store:
            store.save_document(self._document)
        self._prepared = deepcopy(prepared)
        self._current_run = {"prepared": deepcopy(prepared), "summary": None, "steps": [],
                             "run_status": "running", "memory_last_step": -1, "persisted_last_step": -1,
                             "diagnostics": [], "step_index": [], "unavailable_steps": []}
        self._cache, self._unsaved_step, self._selection = OrderedDict(), None, {}
        self._progress_sequence = -1
        self._cancel_requested, self._closing, self._running = False, False, True
        worker = _AnalysisWorker(self._path, prepared, self)
        self._worker = worker
        worker.accepted.connect(self._accepted)
        worker.progress.connect(self._progress)
        worker.concluded.connect(self._concluded)
        worker.operationDone.connect(self._operation_done)
        worker.error.connect(self._report)
        worker.finished.connect(self._worker_finished)
        self.preparedChanged.emit(self.prepared)
        self.runChanged.emit(self.current_run)
        self._emit_state()
        worker.start()
        return True

    @_command
    def cancel_run(self) -> bool:
        """Request cooperative cancellation at the next native safe boundary."""
        if self._worker is None or not self._running:
            return False
        self._cancel_requested = True
        self._worker.request_cancel()
        self._emit_state()
        return True

    @Slot(dict)
    def _accepted(self, message: dict[str, Any]) -> None:
        owner = self._current_run.get("prepared", {}).get("identity", {})
        step = message.get("step", {})
        if not self._running or not same_run(owner, message.get("identity", {})) or not same_run(owner, step.get("identity", {})):
            return
        sequence = step.get("sequence")
        previous = self._current_run.get("memory_last_step", -1)
        if type(sequence) is not int or sequence <= previous:
            return
        if sequence != previous + 1:
            self._report(diagnostic("notification_gap", "Accepted-step notification sequence is incomplete", stage="application"))
            self.cancel_run()
            return
        value = _compact(step)
        self._current_run["steps"].append(value)
        self._current_run["memory_last_step"] = sequence
        receipt = message["receipt"]
        self._current_run["persisted_last_step"] = receipt["persisted_sequence"]
        self._current_run["step_index"].append({key: deepcopy(value.get(key)) for key in
                                               ("sequence", "phase", "P", "control_displacement")}
                                              | {"step_id": value["identity"]["step_id"]})
        if message.get("unsaved_step") is not None:
            self._unsaved_step = deepcopy(message["unsaved_step"])
        self.stepAccepted.emit(deepcopy(value))
        self._emit_state()

    @Slot(dict)
    def _progress(self, value: dict[str, Any]) -> None:
        owner = self._current_run.get("prepared", {}).get("identity", {})
        sequence = value.get("sequence")
        if not self._running or not same_run(owner, value.get("identity", {})) or type(sequence) is not int or sequence <= self._progress_sequence:
            return
        self._progress_sequence = sequence
        self.progressChanged.emit(deepcopy(value))

    @Slot(dict)
    def _concluded(self, summary: dict[str, Any]) -> None:
        owner = self._current_run.get("prepared", {}).get("identity", {})
        if not same_run(owner, summary.get("identity", {})):
            return
        self._current_run["summary"] = deepcopy(summary)
        self._current_run["run_status"] = "finished" if summary.get("conclusion_persisted") else "conclusion_unsaved"
        self._current_run["memory_last_step"] = summary["memory_last_step"]
        self._current_run["persisted_last_step"] = summary["persisted_last_step"]
        self.runFinished.emit(deepcopy(summary))

    @Slot(dict)
    def _operation_done(self, command: dict[str, Any]) -> None:
        if command.get("operation") == "save" and _hash(command["document"]) == _hash(self._document):
            self._saved_hash = _hash(self._document) if self._public_path else None
        self._emit_state()

    @Slot()
    def _worker_finished(self) -> None:
        worker = self._worker
        self._worker = None
        self._running = False
        if worker is not None:
            worker.deleteLater()
        if self._pending_retarget is not None:
            destination = self._pending_retarget
            self._pending_retarget = None
            self.save_as(str(destination))
        self._emit_state()
        if self._closing:
            self._timer.stop()

    @staticmethod
    def _step_selection(step: dict[str, Any]) -> dict[str, Any]:
        return {"run_id": step["identity"]["run_id"], "step_id": step["identity"]["step_id"],
                "sequence": step["sequence"], "phase": step["phase"]}

    def get_step(self, selection: dict[str, Any] | int | str | None = None) -> dict[str, Any]:
        """Read one exact full step with a four-entry LRU; missing data raises."""
        self._require_document()
        owner = self._current_run.get("prepared", {}).get("identity", {})
        if not owner:
            raise ContractError("run_missing", "No analysis result is selected", stage="application")
        query = self._selection if selection is None else selection
        if isinstance(query, dict):
            if query.get("run_id", owner["run_id"]) != owner["run_id"]:
                raise ContractError("stale_selection", "Selection belongs to another run", stage="application")
            step_id, sequence = query.get("step_id"), query.get("sequence")
        elif type(query) is int:
            step_id, sequence = None, query
        elif isinstance(query, str):
            step_id, sequence = query, None
        else:
            raise ContractError("invalid_selection", "A real step identity or sequence is required", stage="application")
        item = next((item for item in self._current_run.get("steps", [])
                     if (step_id is not None and item["identity"]["step_id"] == step_id) or
                     (step_id is None and sequence == item["sequence"])), None)
        if item is None or (sequence is not None and sequence != item["sequence"]):
            raise ContractError("step_missing", "Selected accepted step is missing or unavailable", stage="application")
        key = item["identity"]["step_id"]
        if self._unsaved_step is not None and self._unsaved_step["identity"]["step_id"] == key:
            return deepcopy(self._unsaved_step)
        if key not in self._cache:
            value = self._read_store().load_step(owner["run_id"], item["sequence"])
            if not same_run(owner, value["identity"]) or value["identity"]["step_id"] != key:
                raise ContractError("step_identity_mismatch", "Saved step identity disagrees with selection", stage="application")
            self._cache[key] = value
            while len(self._cache) > 4:
                self._cache.popitem(last=False)
        self._cache.move_to_end(key)
        return deepcopy(self._cache[key])

    @_command
    def select(self, selection: dict[str, Any]) -> bool:
        """Select a real available step and optional object without inventing states."""
        if not isinstance(selection, dict):
            raise ContractError("invalid_selection", "Expected a selection object", stage="application")
        if not self._current_run:
            self._selection = {key: deepcopy(value) for key, value in selection.items() if key == "object_id"}
        else:
            query = {**self._selection, **deepcopy(selection)}
            if not query.get("step_id") and "sequence" not in query:
                steps = self._current_run.get("steps", [])
                if not steps:
                    self._selection = query
                    self.selectionChanged.emit(self.selection)
                    return True
                query = {**self._step_selection(steps[-1]), **query}
            step = self.get_step(query)
            self._selection = {**query, **self._step_selection(step)}
        self._view_state["selection"] = self.selection
        self.selectionChanged.emit(self.selection)
        return True

    @_command
    def set_observation(self, definition: dict[str, Any]) -> bool:
        """Change measurement/display processing without mutating frozen inputs."""
        self._require_document()
        if not isinstance(definition, dict):
            raise ContractError("invalid_observation", "Expected an observation definition", stage="observation")
        validate_finite(definition)
        if definition.get("reference_step_id"):
            self.get_step(str(definition["reference_step_id"]))
        self._observation = deepcopy(definition)
        self._view_state["observation"] = deepcopy(definition)
        self.observationChanged.emit(deepcopy(definition))
        self._emit_state()
        return True

    def _saved_document(self) -> dict[str, Any]:
        candidate = deepcopy(self._document)
        view = deepcopy(self._view_state)
        if self._current_run:
            view["run_id"] = self._current_run["prepared"]["identity"]["run_id"]
        if view != candidate.get("view_state", {}):
            candidate["revision_id"] = new_id("revision")
            candidate["view_state"] = view
        return candidate

    @_command
    def save(self, path: str | None = None) -> bool:
        """Save applied edits/settings; worker owns any save performed during a run."""
        self._require_document()
        if self._draft is not None:
            raise ContractError("unapplied_edits", "Apply or revert form edits before saving.", stage="application")
        if path is not None and Path(path).expanduser().resolve() != self._path:
            return bool(self.save_as(path))
        candidate = self._saved_document()
        if self._running and self._worker is not None:
            self._document = candidate
            self._view_state = deepcopy(candidate.get("view_state", {}))
            self._worker.submit({"operation": "save", "document": deepcopy(candidate)})
            self._emit_state()
            return True
        with ProjectStore(self._path) as store:
            store.save_document(candidate)
        self._document = candidate
        self._view_state = deepcopy(candidate.get("view_state", {}))
        self._saved_hash = _hash(candidate) if self._public_path else None
        self.documentChanged.emit(self.document)
        self._emit_state()
        return True

    @_command
    def save_as(self, path: str) -> bool:
        """Publish a consistent copy; an active run retargets only after it stops."""
        self._require_document()
        if self._draft is not None:
            raise ContractError("unapplied_edits", "Apply or revert form edits before saving.", stage="application")
        target = Path(path).expanduser().resolve()
        if target.suffix.lower() != ".lumafem":
            raise ContractError("project_extension", "Choose a .lumafem project destination", stage="application")
        for dataset in self._imports:
            source = dataset.get("source", {}).get("path")
            if source and Path(source).expanduser().resolve() == target:
                raise ContractError("source_overwrite", "Project saving cannot overwrite an imported original", stage="application")
        if target == self._path:
            historical = self._current_run.get("prepared", {}).get("kernel_version")
            if historical and historical != KERNEL_VERSION:
                raise ContractError("same_copy_target", "Choose a different Save As destination for a historical result.", stage="application")
            self._public_path = target
            return bool(self.save())
        if self._running and self._worker is not None:
            self.save()
            self._worker.submit({"operation": "copy", "path": str(target)})
            self._pending_retarget = target
            self._emit_state()
            return True
        with ProjectStore(self._path, readonly=True) as store:
            stored = store.load_document()
            editable = deepcopy(stored)
            editable.pop("identity", None)
            historical = self._current_run.get("prepared", {}).get("kernel_version")
            preserve = bool(historical and historical != KERNEL_VERSION and self._document == editable)
            # Browsing a historical result changes transient selection/view state,
            # not its source revision or capability. Explicit model edits are
            # saved only inside the unpublished destination copy, never here.
            candidate = stored if preserve else self._saved_document()
            store.save_as(target, document=None if preserve else candidate)
        # Validate the completed published copy before changing active location.
        with ProjectStore(target, readonly=True) as store:
            if store.load_document() != candidate:
                raise ContractError("copy_identity", "Published project differs from saved input", stage="application")
        self._document = deepcopy(candidate)
        self._document.pop("identity", None)
        self._view_state = deepcopy(candidate.get("view_state", {}))
        self._close_reader()
        self._path = self._public_path = target
        self._saved_hash = _hash(self._document)
        self.documentChanged.emit(self.document)
        self._emit_state()
        return True

    def _recovery_path(self) -> Path:
        assert self._path is not None
        return self._path.with_name(self._path.name + ".recovery.lumafem")

    @_command
    def _recover(self) -> None:
        if not self._document or self._running or self._closing or not (self.state["dirty"] or self._draft is not None):
            return
        candidate = deepcopy(self._draft if self._draft is not None else self._document)
        validate_finite(candidate)
        candidate["revision_id"] = new_id("revision")
        candidate["view_state"] = deepcopy(self._view_state)
        candidate["recovery_has_unapplied_edits"] = self._draft is not None
        fingerprint = _hash({key: value for key, value in candidate.items() if key != "revision_id"})
        if fingerprint == self._last_recovery_hash:
            return
        with ProjectStore(self._path, readonly=True) as store:
            store.write_recovery(candidate, self._recovery_path())
        self._last_recovery_hash = fingerprint

    @_command
    def restore_recovery(self, path: str) -> bool:
        """Open an explicitly chosen recovery file as an unsaved recovered document."""
        self._idle()
        if not self.open_project(path):
            return False
        self._saved_hash = None
        self._diagnostics.append(diagnostic("recovered_document", "Recovery copy opened by explicit choice; it is not a completed analysis.",
                                            severity="warning", stage="application"))
        self._emit_state()
        return True

    @_command
    def import_data(self, path: str, mapping: dict[str, Any]) -> dict[str, Any]:
        """Adopt mapped channels without changing the external source file."""
        self._idle()
        self._require_document()
        dataset = import_csv(path, mapping)
        with ProjectStore(self._path) as store:
            store.save_import(dataset)
        self._imports.append(deepcopy(dataset))
        self.importsChanged.emit(self.imports)
        self._emit_state()
        return deepcopy(dataset)

    def _reference(self) -> dict[str, Any] | None:
        if self._observation.get("reference_step_id"):
            return self.get_step(str(self._observation["reference_step_id"]))
        phase = self._observation.get("reference_phase", "SG")
        if phase == "SG" and not self._current_run.get("prepared", {}).get("document", {}).get("model", {}).get("gravity", {}).get("enabled"):
            phase = "S0"
        return next((step for step in self._current_run.get("steps", []) if step.get("phase") == phase), None)

    def _effective_observation(self) -> dict[str, Any]:
        definition = deepcopy(self._observation)
        gravity = self._current_run.get("prepared", {}).get("document", {}).get("model", {}).get("gravity", {})
        if not gravity.get("enabled") and definition.get("reference_phase", "SG") == "SG" and not definition.get("reference_step_id"):
            definition["reference_phase"] = "S0"
        return definition

    def _curves(self) -> list[dict[str, Any]]:
        reference = self._reference()
        prepared = self._current_run["prepared"]
        result = []
        for step in self._current_run.get("steps", []):
            record = observe_step(prepared, step, reference, self._effective_observation())["curve"]
            mode = self._observation.get("display_mode", "increment")
            if mode == "total":
                record["w"] = record["w_total"]
            elif mode == "reference":
                record["P"] = record["P_increment"]
            result.append(record)
        return result

    @_command
    def export_data(self, path: str, kind: str) -> bool:
        """Export real selected numeric results with precise units and provenance."""
        self._require_document()
        if not self._current_run:
            raise ContractError("run_missing", "No results to export", stage="application")
        prepared = self._current_run["prepared"]
        source_paths = [item.get("source", {}).get("path") for item in self._imports]
        metadata = {"identity": deepcopy(prepared["identity"]), "prepared_hash": prepared["prepared_hash"],
                    "kind": kind, "units": {"length": "mm", "force": "N", "moment": "N.mm", "stress": "MPa", "strain": "1"},
                    "observation": deepcopy(self._observation), "source_paths": [p for p in source_paths if p],
                    "validation_status": deepcopy((self._current_run.get("summary") or {}).get("validation_status", validation_status()))}
        if "summary" in self._current_run:
            metadata["summary"] = deepcopy(self._current_run["summary"])
            summary = self._current_run["summary"]
            if isinstance(summary, dict) and "termination" in summary:
                metadata["termination"] = deepcopy(summary["termination"])
        if "compression_criterion" in prepared:
            metadata["compression_criterion"] = deepcopy(prepared["compression_criterion"])
        if kind in ("curve", "curves"):
            rows = self._curves()
        elif kind == "events":
            rows = [{**deepcopy(event), "run_id": step["identity"]["run_id"], "sequence": step["sequence"],
                     "step_id": step["identity"]["step_id"], "phase": step["phase"]}
                    for step in self._current_run.get("steps", []) for event in step.get("events", [])]
        elif kind in ("diagnostics", "convergence"):
            rows = [{"identity": deepcopy(step["identity"]), "sequence": step["sequence"], "phase": step["phase"],
                     **deepcopy(step.get("convergence", {})), "localization": deepcopy(step.get("localization"))}
                    for step in self._current_run.get("steps", [])]
        elif kind in ("fibers", "edges", "sections", "element_forces", "observations"):
            step = self.get_step()
            metadata["selected_identity"] = deepcopy(step["identity"])
            if kind == "observations":
                rows = observe_step(prepared, step, self._reference(), self._effective_observation())["values"]
            else:
                rows = deepcopy(step[kind])
        elif kind == "comparison":
            rows = [{"dataset_id": item["id"], "comparison": compare_curves(self._curves(), item)} for item in self._imports]
        else:
            raise ContractError("export_kind", "Unknown numeric export kind", stage="application")
        export_csv(path, rows, metadata)
        return True

    @_command
    def close(self) -> bool:
        """Close immediately when idle, or request cancellation and await finished."""
        if self._running:
            self._closing = True
            self.cancel_run()
            self._emit_state()
            return False
        self._timer.stop()
        self._close_reader()
        self._closing = True
        self._emit_state()
        return True
