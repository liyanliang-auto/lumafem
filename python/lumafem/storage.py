"""Transactional, inspectable LumaFEM schema-1 project files.

The connection and every operation belong to the creating thread.  A process
lock enforces one application writer; SQLite read transactions give readers a
consistent view.  This format stores accepted output, not solver checkpoints.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import sqlite3
import struct
import threading
from typing import Any, Callable, Iterator
from uuid import uuid4

from .contracts import (
    ARRAY_FIELDS, DOCUMENT_KEYS, MATERIAL_VERSION, PHASES, PREPARED_KEYS,
    SCHEMA_VERSION, STEP_KEYS, SUMMARY_KEYS, TERMINATIONS, ContractError,
    CONVERGENCE_SEARCH_KEYS, NUMERICS_CONTRACT, REVISED_STEP_KEYS, TRANSITION_KEYS, TRANSITION_KINDS,
    KERNEL_VERSION, PREDICTOR_POLICY_ID, PREDICTOR_EVIDENCE_KEYS,
    LEGACY_PREDICTOR_POLICY_ID, LEGACY_PREDICTOR_EVIDENCE_KEYS,
    CR_NUM_001_A04_PREDICTOR_POLICY_ID,
    READER_CAPABILITY, CR_NUM_001_NUMERICS_CONTRACT, COMPRESSION_CRITERION_KEYS,
    COMPRESSION_CRITERION_MODES, GAUGE_WINDOW_KEYS, GAUGE_STATE_KEYS, GAUGE_STATUSES,
    PHYSICAL_GAUGE_OPERATOR_VERSION, PHYSICAL_GAUGE_REFERENCE, PHYSICAL_LOCALIZATION_KEYS,
    PHYSICAL_LOCALIZATION_KINDS,
    diagnostic, require_keys, same_run, validate_finite, validate_identity,
)

__all__ = ["ProjectStore"]

_FORMAT = "LumaFEM"
_APPLICATION_ID = 0x4C554D41
# Reader capability is independent of SQL schema. BL1 readers require exactly1
# and therefore reject files containing newly written CR numerical semantics.
_READER_CAPABILITY = READER_CAPABILITY
_APPLICATION_VERSION = KERNEL_VERSION
_MAX_JSON = 64 * 1024 * 1024
_MAX_BLOCK = 256 * 1024 * 1024
_MAX_VALUES = _MAX_BLOCK // 8
_SQL = """
CREATE TABLE metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE revisions(
 project_id TEXT NOT NULL, revision_id TEXT PRIMARY KEY,
 payload TEXT NOT NULL, sha256 TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE project(
 singleton INTEGER PRIMARY KEY CHECK(singleton=1), project_id TEXT NOT NULL,
 current_revision TEXT REFERENCES revisions(revision_id));
CREATE TABLE runs(
 run_id TEXT PRIMARY KEY, project_id TEXT NOT NULL,
 revision_id TEXT NOT NULL REFERENCES revisions(revision_id), snapshot_id TEXT NOT NULL,
 prepared TEXT NOT NULL, prepared_sha256 TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('running','finished')),
 last_sequence INTEGER NOT NULL DEFAULT -1, last_step_id TEXT NOT NULL DEFAULT '',
 summary TEXT, summary_sha256 TEXT, created_at TEXT NOT NULL, finished_at TEXT);
CREATE TABLE steps(
 run_id TEXT NOT NULL REFERENCES runs(run_id), sequence INTEGER NOT NULL,
 step_id TEXT NOT NULL UNIQUE, phase TEXT NOT NULL, P REAL NOT NULL,
 control_displacement REAL, payload TEXT NOT NULL, sha256 TEXT NOT NULL,
 complete INTEGER NOT NULL CHECK(complete=1), created_at TEXT NOT NULL,
 PRIMARY KEY(run_id,sequence));
CREATE TABLE result_blocks(
 run_id TEXT NOT NULL, sequence INTEGER NOT NULL, path TEXT NOT NULL,
 descriptor TEXT NOT NULL, descriptor_sha256 TEXT NOT NULL,
 payload BLOB NOT NULL, sha256 TEXT NOT NULL,
 PRIMARY KEY(run_id,sequence,path),
 FOREIGN KEY(run_id,sequence) REFERENCES steps(run_id,sequence));
CREATE TABLE events(
 run_id TEXT NOT NULL, sequence INTEGER NOT NULL, event_index INTEGER NOT NULL,
 payload TEXT NOT NULL, sha256 TEXT NOT NULL,
 PRIMARY KEY(run_id,sequence,event_index),
 FOREIGN KEY(run_id,sequence) REFERENCES steps(run_id,sequence));
CREATE TABLE imports(
 dataset_id TEXT PRIMARY KEY, payload TEXT NOT NULL, sha256 TEXT NOT NULL,
 created_at TEXT NOT NULL);
CREATE INDEX steps_run_phase ON steps(run_id,phase,sequence);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _error(code: str, message: str, *, field: str = "", object_id: str = "") -> ContractError:
    return ContractError(code, message, stage="storage", field=field, object_id=object_id)


def _digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _json(value: Any) -> str:
    validate_finite(value)
    try:
        encoded = json.dumps(value, ensure_ascii=False, allow_nan=False,
                             separators=(",", ":"), sort_keys=True)
    except (TypeError, ValueError, OverflowError, RecursionError) as exc:
        raise _error("invalid_json", f"Cannot encode project value: {exc}") from exc
    if len(encoded.encode("utf-8")) > _MAX_JSON:
        raise _error("resource_limit", "JSON record exceeds the 64 MiB format limit")
    return encoded


def _checked_json(payload: str, expected_hash: str, field: str) -> Any:
    if not isinstance(payload, str) or len(payload.encode("utf-8")) > _MAX_JSON:
        raise _error("resource_limit", "Invalid or excessive JSON record", field=field)
    if _digest(payload.encode("utf-8")) != expected_hash:
        raise _error("json_hash_mismatch", "Stored JSON checksum failed", field=field)
    try:
        value = json.loads(payload, parse_constant=lambda token: (_ for _ in ()).throw(
            ValueError(f"Nonfinite JSON token: {token}")))
        validate_finite(value)
        return value
    except (ValueError, TypeError, RecursionError) as exc:
        raise _error("invalid_json", f"Stored JSON is invalid: {exc}", field=field) from exc


def _hash_json(value: Any) -> tuple[str, str]:
    payload = _json(value)
    return payload, _digest(payload.encode("utf-8"))


def _numeric_array(value: Any) -> tuple[list[int], list[Any]] | None:
    if not isinstance(value, list) or not value:
        return None
    if all(item is None or (isinstance(item, (int, float)) and not isinstance(item, bool))
           for item in value):
        return [len(value)], value
    children = [_numeric_array(item) for item in value]
    if all(item is not None for item in children):
        shape = children[0][0]
        if all(item[0] == shape for item in children):
            return [len(value), *shape], [item for child in children for item in child[1]]
    return None


def _location(path: list[str], prepared: dict[str, Any]) -> dict[str, Any]:
    """Describe actual source identity and physical positions, never array row IDs."""
    top = path[0]
    if top in ARRAY_FIELDS:
        info = dict(ARRAY_FIELDS[top])
        info["objects"] = [{"id": node["id"], "x": node["x"], "dofs": node["dofs"]}
                           for node in prepared["nodes"]]
        return info
    identity = prepared["identity"]
    info: dict[str, Any] = {"location": top, "snapshot_id": identity["snapshot_id"],
                            "source_path": path, "unit": "per-component"}
    if top == "element_forces" and len(path) > 1 and path[1].isdigit():
        index = int(path[1])
        if index < len(prepared["elements"]):
            info["object_id"] = prepared["elements"][index]["id"]
        if path[-1] == "end_forces":
            info.update(components=["N1", "V1", "M1", "N2", "V2", "M2"],
                        units=["N", "N", "N.mm", "N", "N", "N.mm"])
    elif top == "sections" and len(path) > 1 and path[1].isdigit():
        index = int(path[1])
        if index < len(prepared["sections"]):
            item = prepared["sections"][index]
            info.update(object_id=item["id"], x=item["x"])
        if path[-1] == "tangent":
            info.update(components=["K00", "K01", "K10", "K11"],
                        units=["N", "N.mm", "N.mm", "N.mm2"])
    return info


def _pack_arrays(step: dict[str, Any], prepared: dict[str, Any]) -> tuple[dict[str, Any], list[tuple]]:
    blocks: list[tuple] = []

    def visit(value: Any, path: list[str]) -> Any:
        array = _numeric_array(value)
        if array:
            shape, values = array
            if len(values) > _MAX_VALUES:
                raise _error("resource_limit", "Result array exceeds the 256 MiB block limit")
            valid = [item is not None for item in values]
            integers = [isinstance(item, int) and not isinstance(item, bool) for item in values]
            for item in values:
                if isinstance(item, int) and abs(item) > 2 ** 53:
                    raise _error("integer_precision", "Integer identity cannot be stored as float64")
            payload = struct.pack(f"<{len(values)}d", *(0.0 if item is None else item for item in values))
            name = "/" + "/".join(part.replace("~", "~0").replace("/", "~1") for part in path)
            descriptor = {"encoding_version": 1, "dtype": "float64", "byte_order": "little",
                          "order": "C", "shape": shape, "length": len(payload),
                          "valid": valid if not all(valid) else None,
                          "integers": integers if any(integers) else None,
                          "location": _location(path, prepared)}
            text, checksum = _hash_json(descriptor)
            blocks.append((name, text, checksum, payload, _digest(payload)))
            return {"$lumafem_block": name}
        if isinstance(value, dict):
            if "$lumafem_block" in value:
                raise _error("reserved_key", "Result contains a reserved array marker")
            return {key: visit(item, [*path, key]) for key, item in value.items()}
        if isinstance(value, list):
            return [visit(item, [*path, str(index)]) for index, item in enumerate(value)]
        return value

    return visit(step, []), blocks


def _unpack_arrays(payload: dict[str, Any], blocks: dict[str, sqlite3.Row]) -> dict[str, Any]:
    used: set[str] = set()

    def visit(value: Any) -> Any:
        if isinstance(value, dict) and set(value) == {"$lumafem_block"}:
            name = value["$lumafem_block"]
            if name not in blocks or name in used:
                raise _error("missing_block", "Missing or repeated result block", field=str(name))
            used.add(name)
            row = blocks[name]
            desc = _checked_json(row["descriptor"], row["descriptor_sha256"], name)
            require_keys(desc, ("encoding_version", "dtype", "byte_order", "order", "shape",
                                "length", "valid", "integers", "location"), stage="storage")
            if (desc["encoding_version"], desc["dtype"], desc["byte_order"], desc["order"]) != (
                    1, "float64", "little", "C"):
                raise _error("unsupported_encoding", "Unknown critical array encoding", field=name)
            shape = desc["shape"]
            if not isinstance(shape, list) or not shape or len(shape) > 8:
                raise _error("invalid_shape", "Invalid result block rank", field=name)
            count = 1
            for dimension in shape:
                if type(dimension) is not int or dimension <= 0:
                    raise _error("invalid_shape", "Invalid result block dimension", field=name)
                count *= dimension
                if count > _MAX_VALUES:
                    raise _error("resource_limit", "Array shape exceeds resource limit", field=name)
            data = row["payload"]
            if len(data) != count * 8 or desc["length"] != len(data):
                raise _error("invalid_block_length", "Array length disagrees with shape", field=name)
            if _digest(data) != row["sha256"]:
                raise _error("block_hash_mismatch", "Result block checksum failed", field=name)
            valid, integers = desc["valid"], desc["integers"]
            for mask in (valid, integers):
                if mask is not None and (not isinstance(mask, list) or len(mask) != count or
                                         not all(type(item) is bool for item in mask)):
                    raise _error("invalid_mask", "Invalid array validity/type mask", field=name)
            values: list[Any] = list(struct.unpack(f"<{count}d", data))
            for index, number in enumerate(values):
                if not math.isfinite(number):
                    raise _error("nonfinite_block", "Result block contains nonfinite values", field=name)
                if valid is not None and not valid[index]:
                    values[index] = None
                elif integers is not None and integers[index]:
                    if not number.is_integer() or abs(number) > 2 ** 53:
                        raise _error("invalid_integer", "Invalid integer array value", field=name)
                    values[index] = int(number)
            def reshape(items: list[Any], dimensions: list[int]) -> list[Any]:
                if len(dimensions) == 1:
                    return items
                width = math.prod(dimensions[1:])
                return [reshape(items[i * width:(i + 1) * width], dimensions[1:])
                        for i in range(dimensions[0])]
            return reshape(values, shape)
        if isinstance(value, dict):
            return {key: visit(item) for key, item in value.items()}
        if isinstance(value, list):
            return [visit(item) for item in value]
        return value

    result = visit(payload)
    if used != set(blocks):
        raise _error("unreferenced_block", "Step contains unreferenced result blocks")
    return result


def _verify_snapshot(prepared: dict[str, Any], *, for_analysis: bool = False) -> bool:
    """Validate exact producer-scoped native snapshots without rewriting their bytes."""
    from .native import verify_prepared
    result = verify_prepared(prepared, for_analysis=for_analysis)
    if not result.get("ok"):
        details = result.get("diagnostics") or []
        first = details[0] if details else {}
        raise _error(first.get("code", "invalid_prepared"),
                     first.get("message", "Prepared snapshot failed native verification"),
                     field=first.get("field", "prepared"))
    if result.get("prepared") != prepared or type(result.get("analysis_read_only")) is not bool:
        raise _error("invalid_verification", "Native view transport changed its frozen snapshot")
    return result["analysis_read_only"]


def _source_criterion(document: dict[str, Any]) -> None:
    """Validate explicit source semantics, retaining absence in historical sources."""
    model = document["model"]
    if "compression_criterion" not in model:
        return
    criterion = model["compression_criterion"]
    if not isinstance(criterion, dict):
        raise _error("invalid_criterion", "Compression criterion must be an object", field="compression_criterion")
    for key in COMPRESSION_CRITERION_KEYS:
        if key not in criterion:
            raise _error("invalid_criterion", "Missing required criterion field", field="compression_criterion." + key)
    if set(criterion) != set(COMPRESSION_CRITERION_KEYS) or criterion["mode"] not in COMPRESSION_CRITERION_MODES:
        raise _error("invalid_criterion", "Unknown or mixed compression criterion")
    for key in ("gauge_length_mm", "threshold"):
        number = criterion[key]
        if type(number) not in (int, float) or not math.isfinite(number) or number <= 0:
            raise _error("invalid_criterion", "Criterion parameters must be finite positive numbers", field=key)
    source = criterion["source"]
    field = "compression_criterion.source"
    if not isinstance(source, dict):
        raise _error("invalid_criterion", "Criterion provenance must be an object", field=field)
    keys = ("category", "reference", "quality", "version")
    for key in keys:
        if key not in source:
            raise _error("invalid_criterion", "Missing required provenance field", field=field + "." + key)
        if not isinstance(source[key], str):
            raise _error("invalid_criterion", "Provenance field must be text", field=field + "." + key)
    for key in source:
        if key not in keys:
            raise _error("invalid_criterion", "Unknown provenance field", field=field + "." + key)
    if source["category"] not in ("paper", "assumption", "calibrated", "measured", "synthetic", "derived"):
        raise _error("invalid_criterion", "Unknown provenance category", field=field + ".category")
    if criterion["mode"] == "physical_gauge_mean_v1":
        length = model.get("geometry", {}).get("length")
        if type(length) not in (int, float) or not math.isfinite(length) or criterion["gauge_length_mm"] > length:
            raise _error("invalid_criterion", "Physical criterion window exceeds beam length")
    else:
        material = next((item for item in document["materials"]
                         if item.get("id") == model.get("concrete_material_id")), None)
        if (material is None or material.get("kind") != "concrete02" or
                criterion["gauge_length_mm"] != material.get("reference_length") or
                criterion["threshold"] != material.get("crush_reference_strain")):
            raise _error("invalid_criterion", "Legacy criterion must agree exactly with material references")


def _gauge_window(window: dict[str, Any], prepared: dict[str, Any], *, status: str | None = None) -> None:
    """Check actual window metadata and ownership; do not synthesize absent evidence."""
    require_keys(window, GAUGE_WINDOW_KEYS, stage="storage")
    criterion = prepared["compression_criterion"]
    for key in ("mode", "operator_version", "reference", "gauge_length_mm", "threshold", "search_domain_mm"):
        if window[key] != criterion[key]:
            raise _error("gauge_criterion_mismatch", "Window disagrees with selected prepared criterion", field=key)
    if (window["mode"] != "physical_gauge_mean_v1" or
            window["operator_version"] != PHYSICAL_GAUGE_OPERATOR_VERSION or
            window["reference"] != PHYSICAL_GAUGE_REFERENCE or window["side"] not in ("top", "bottom") or
            window["status"] not in GAUGE_STATUSES or (status and window["status"] != status)):
        raise _error("invalid_gauge", "Invalid physical window definition/status")
    validate_identity(window["identity"])
    if not same_run(window["identity"], prepared["identity"]) or window["phase"] not in PHASES:
        raise _error("gauge_identity_mismatch", "Window belongs to another run or phase")
    numbers = ("eta", "x1", "x2", "signed_mean", "compression_maximum", "control_displacement", "load_factor", "gravity_factor")
    if any(type(window[key]) not in (int, float) or not math.isfinite(window[key]) for key in numbers):
        raise _error("invalid_gauge", "Window has missing or nonfinite physical metadata")
    eta = prepared["document"]["model"]["geometry"]["height"] / 2
    if window["side"] == "bottom":
        eta = -eta
    domain = criterion["search_domain_mm"]
    if (window["eta"] != eta or not domain[0] - 1e-9 <= window["x1"] <= domain[1] + 1e-9 or
            not math.isclose(window["x2"] - window["x1"], criterion["gauge_length_mm"], rel_tol=1e-12, abs_tol=1e-9) or
            window["compression_maximum"] != max(0.0, -window["signed_mean"])):
        raise _error("invalid_gauge", "Window geometry or signed-mean compression is inconsistent")


def _gauge_state(value: dict[str, Any], prepared: dict[str, Any], step: dict[str, Any] | None = None) -> None:
    """Validate separate actual current and accepted historical maxima."""
    require_keys(value, GAUGE_STATE_KEYS, stage="storage")
    _gauge_window(value["current"], prepared, status="current")
    _gauge_window(value["historical"], prepared, status="accepted_historical")
    if value["historical"]["compression_maximum"] < value["current"]["compression_maximum"]:
        raise _error("invalid_gauge", "Historical maximum is below the actual current maximum")
    if step is not None:
        current = value["current"]
        if current["identity"] != step["identity"] or any(current[key] != step[key] for key in (
                "phase", "control_displacement", "load_factor", "gravity_factor")):
            raise _error("gauge_identity_mismatch", "Current gauge does not describe its accepted step")


def _gauge_localization(value: dict[str, Any], prepared: dict[str, Any]) -> None:
    """Retain independently converged physical bracket ends and exact-crossing absence."""
    if not isinstance(value, dict):
        raise _error("invalid_localization", "Localization must be an object")
    if not value.get("active"):
        return
    require_keys(value, PHYSICAL_LOCALIZATION_KEYS, stage="storage")
    if value["kind"] not in PHYSICAL_LOCALIZATION_KINDS or value["control_unit"] != "mm":
        raise _error("invalid_localization", "Unknown physical localization kind or control unit")
    _gauge_window(value["upper_gauge"], prepared)
    if value["upper_converged"] is not True or value["upper_ratio"] < 1:
        raise _error("invalid_localization", "Physical upper bracket must be converged at threshold")
    if value["kind"] == "bracket":
        require_keys(value, ("lower_gauge",), stage="storage")
        _gauge_window(value["lower_gauge"], prepared)
        if value["lower_converged"] is not True or not 0 <= value["lower_ratio"] < 1:
            raise _error("invalid_localization", "Physical lower bracket must be genuinely converged below threshold")
    elif "lower_gauge" in value or value["bracket_width"] != 0 or value["final_excess"] != 0:
        raise _error("invalid_localization", "Exact crossing cannot fabricate a lower bracket")
    threshold = prepared["compression_criterion"]["threshold"]
    for prefix in ("upper", "lower") if value["kind"] == "bracket" else ("upper",):
        gauge = value[prefix + "_gauge"]
        if not math.isclose(value[prefix + "_ratio"], gauge["compression_maximum"] / threshold,
                            rel_tol=1e-12, abs_tol=1e-12):
            raise _error("invalid_localization", "Bracket ratio disagrees with its actual physical window")


def _criterion_summary(summary: dict[str, Any], prepared: dict[str, Any]) -> None:
    """Reject mixed terminal identities while preserving historical unknown fields."""
    physical = prepared.get("compression_criterion", {}).get("mode") == "physical_gauge_mean_v1"
    if not isinstance(summary["physical_evidence"], list):
        raise _error("invalid_summary", "Physical evidence must be an array")
    for event in summary["physical_evidence"]:
        if not isinstance(event, dict):
            raise _error("invalid_summary", "Physical evidence event must be an object")
        if event.get("kind") == "physical_gauge_compression":
            if not physical or any(event.get(key) for key in ("element_id", "section_id", "point_id")):
                raise _error("mixed_criterion", "Physical terminal event has incompatible mode or point identities")
            require_keys(event, ("gauge", "localization"), stage="storage")
            _gauge_state(event["gauge"], prepared)
            _gauge_localization(event["localization"], prepared)
            current = event["gauge"]["current"]
            if (event.get("criterion") != current["mode"] or event.get("side") != current["side"] or
                    event.get("step_id") != current["identity"]["step_id"] or
                    event.get("threshold") != current["threshold"] or
                    current["compression_maximum"] < current["threshold"] or not event["localization"].get("active")):
                raise _error("invalid_gauge_event", "Summary event disagrees with actual threshold window")
        elif physical and event.get("kind") == "reference_compression":
            raise _error("mixed_criterion", "Physical mode cannot claim the legacy reference terminal event")
    termination = summary["termination"]
    if termination == "physical_gauge_compression":
        if not physical or not any(event.get("kind") == termination for event in summary["physical_evidence"]):
            raise _error("invalid_summary", "Physical termination requires actual window event evidence")
        require_keys(summary, ("localization",), stage="storage")
        _gauge_localization(summary["localization"], prepared)
    elif physical and termination == "reference_compression":
        raise _error("mixed_criterion", "Legacy reference termination cannot be assigned to physical mode")


class _WriterLock:
    def __init__(self, path: Path) -> None:
        self.path = path.with_name(path.name + ".lock")
        self.handle: Any = None

    def acquire(self) -> None:
        try:
            self.handle = self.path.open("a+b")
            self.handle.seek(0, os.SEEK_END)
            if self.handle.tell() == 0:
                self.handle.write(b"\0")
                self.handle.flush()
            self.handle.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            if self.handle is not None:
                self.handle.close()
                self.handle = None
            raise _error("writer_locked", "Project already has a writer or its lock is inaccessible") from exc

    def close(self) -> None:
        if self.handle is not None:
            try:
                self.handle.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
            finally:
                self.handle.close()
                self.handle = None
        # Keep the harmless lock inode to avoid an unlink/acquire race.

    def mark_run(self, run_id: str | None) -> None:
        if self.handle is not None:
            self.handle.seek(0, os.SEEK_END)
            self.handle.write(("\n" + json.dumps({"pid": os.getpid(), "run_id": run_id}) + "\n").encode("utf-8"))
            self.handle.flush()


class ProjectStore:
    """One thread-owned schema-1 project connection, writable unless readonly.

    ``fault_injector`` may be assigned a callable receiving named transaction
    boundaries.  Exceptions before commit roll back the entire operation; the
    ``after_commit`` boundary models a committed transaction with a lost receipt.
    Lazy readers may use list_runs/list_steps/load_step instead of load_run.
    """

    def __init__(self, path: str | Path, readonly: bool = False) -> None:
        self.path = Path(path).expanduser().resolve()
        self.readonly = bool(readonly)
        self.fault_injector: Callable[[str], None] | None = None
        self._owner = threading.get_ident()
        self._connection: sqlite3.Connection | None = None
        self._lock: _WriterLock | None = None
        self._suppress_backup = False
        self._active_operation = ""
        self._owned_runs: set[str] = set()
        self._verified_snapshots: dict[tuple[str, bool], bool] = {}
        existed = self.path.exists()
        try:
            if readonly and not existed:
                raise _error("file_missing", "Project file does not exist")
            if existed:
                with self.path.open("rb") as stream:
                    if stream.read(16) != b"SQLite format 3\0":
                        raise _error("invalid_container", "File is not a SQLite project container")
            if not readonly:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                self._lock = _WriterLock(self.path)
                self._lock.acquire()
                self._lock.mark_run(None)
            uri = self.path.as_uri() + ("?mode=ro" if readonly else "?mode=rwc")
            self._connection = sqlite3.connect(uri, uri=True, timeout=5.0, isolation_level=None)
            self._connection.row_factory = sqlite3.Row
            self._connection.execute("PRAGMA foreign_keys=ON")
            self._connection.execute("PRAGMA busy_timeout=5000")
            if existed:
                # Identify a supported project before changing its journal configuration.
                self._check_format()
            if not readonly:
                mode = self._connection.execute("PRAGMA journal_mode=DELETE").fetchone()[0]
                self._connection.execute("PRAGMA synchronous=FULL")
                if str(mode).lower() != "delete" or self._connection.execute(
                        "PRAGMA synchronous").fetchone()[0] != 2:
                    raise _error("unsafe_sqlite_settings", "Required DELETE/FULL settings did not take effect")
            if not existed:
                self._create()
                self._check_format()
        except (OSError, sqlite3.Error, ContractError) as exc:
            if self._connection is not None:
                self._connection.close()
                self._connection = None
            if self._lock is not None:
                self._lock.close()
            if isinstance(exc, ContractError):
                raise
            raise _error("open_failed", f"Cannot open project: {exc}") from exc

    def __enter__(self) -> ProjectStore:
        self._check()
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        self.close()

    def _check(self, *, write: bool = False) -> sqlite3.Connection:
        if threading.get_ident() != self._owner:
            raise _error("wrong_thread", "Project connection must be used in its creating thread")
        if self._connection is None:
            raise _error("store_closed", "Project connection is closed")
        if write and self.readonly:
            raise _error("readonly_project", "Project is open read-only")
        return self._connection

    def _fault(self, point: str) -> None:
        if self.fault_injector is not None:
            self.fault_injector(point)

    @contextmanager
    def _transaction(self, operation: str, *, write: bool = True) -> Iterator[sqlite3.Connection]:
        connection = self._check(write=write)
        if connection.in_transaction:
            raise _error("nested_transaction", "Nested project transaction is not allowed")
        committed = False
        self._active_operation = operation
        try:
            connection.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            if write:
                self._fault(operation + ":begin")
                # Compatibility protection and payload become visible in one commit.
                connection.executemany("INSERT OR REPLACE INTO metadata VALUES(?,?)",
                                       [("minimum_reader", _json(_READER_CAPABILITY)),
                                        ("application_version", _json(_APPLICATION_VERSION))])
                self._fault(operation + ":capability")
            yield connection
            if write:
                self._fault(operation + ":before_commit")
            connection.commit()
            committed = True
            if write:
                self._fault(operation + ":after_commit")
        except Exception as exc:
            if not committed and connection.in_transaction:
                connection.rollback()
            if isinstance(exc, ContractError):
                raise
            raise _error("transaction_failed", f"{operation} failed: {exc}") from exc
        finally:
            self._active_operation = ""

    def _create(self) -> None:
        connection = self._check(write=True)
        try:
            connection.executescript("BEGIN IMMEDIATE;\n" + _SQL)
            entries = {"format": _FORMAT, "schema_version": SCHEMA_VERSION,
                       "schema_minor": 0, "minimum_reader": _READER_CAPABILITY,
                       "material_version": MATERIAL_VERSION, "array_encoding_version": 1,
                       "application_version": _APPLICATION_VERSION, "created_at": _now(),
                       "timezone": str(datetime.now().astimezone().tzinfo),
                       "units": {"length": "mm", "force": "N", "stress": "MPa", "rotation": "rad"},
                       "canonicalization": "json-sort-keys-utf8-float64-roundtrip-v1"}
            connection.executemany("INSERT INTO metadata VALUES(?,?)",
                                   [(key, _json(value)) for key, value in entries.items()])
            connection.execute(f"PRAGMA application_id={_APPLICATION_ID}")
            connection.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
            connection.commit()
        except sqlite3.Error as exc:
            if connection.in_transaction:
                connection.rollback()
            raise _error("create_failed", f"Cannot initialize project schema: {exc}") from exc

    def _check_format(self) -> None:
        connection = self._check()
        try:
            if connection.execute("PRAGMA application_id").fetchone()[0] != _APPLICATION_ID:
                raise _error("invalid_format", "SQLite file is not a LumaFEM project")
            entries = {row["key"]: json.loads(row["value"])
                       for row in connection.execute("SELECT key,value FROM metadata")}
            expected = {"format": _FORMAT, "schema_version": SCHEMA_VERSION,
                        "schema_minor": 0,
                        "material_version": MATERIAL_VERSION, "array_encoding_version": 1,
                        "canonicalization": "json-sort-keys-utf8-float64-roundtrip-v1"}
            for key, value in expected.items():
                if entries.get(key) != value:
                    raise _error("unsupported_format", f"Unsupported or missing format field: {key}", field=key)
            minimum_reader = entries.get("minimum_reader")
            if type(minimum_reader) is not int or not 1 <= minimum_reader <= _READER_CAPABILITY:
                raise _error("unsupported_format", "Project requires an unsupported reader capability", field="minimum_reader")
            if connection.execute("PRAGMA user_version").fetchone()[0] != SCHEMA_VERSION:
                raise _error("unsupported_format", "SQLite schema version disagrees with project metadata")
            checks = connection.execute("PRAGMA quick_check").fetchall()
            if any(row[0] != "ok" for row in checks):
                raise _error("container_corrupt", "SQLite integrity check failed")
            if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
                raise _error("broken_reference", "Project contains broken object references")
            required = {"metadata", "project", "revisions", "runs", "steps", "result_blocks", "events", "imports"}
            found = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if not required <= found:
                raise _error("invalid_schema", "Project is missing required tables")
        except (sqlite3.Error, ValueError, TypeError) as exc:
            raise _error("invalid_schema", f"Cannot read project schema: {exc}") from exc

    def close(self) -> None:
        """Close safely in the owning thread and release the process writer lock."""
        if threading.get_ident() != self._owner:
            raise _error("wrong_thread", "Project connection must be closed in its creating thread")
        if self._connection is not None:
            try:
                if self._connection.in_transaction:
                    self._connection.rollback()
                self._connection.close()
            except sqlite3.Error as exc:
                raise _error("close_failed", f"Cannot close project: {exc}") from exc
            finally:
                self._connection = None
                if self._lock is not None:
                    self._lock.close()

    def _read_prepared(self, row: sqlite3.Row, *, for_analysis: bool = False) -> tuple[dict[str, Any], bool]:
        """Recheck stored hash on every read; cache canonical verification by exact payload hash."""
        prepared = _checked_json(row["prepared"], row["prepared_sha256"], "prepared")
        key = (row["prepared_sha256"], for_analysis)
        if key not in self._verified_snapshots:
            self._verified_snapshots[key] = _verify_snapshot(prepared, for_analysis=for_analysis)
        return prepared, self._verified_snapshots[key]

    @staticmethod
    def _document_envelope(document: dict[str, Any]) -> None:
        """Check shared stored/source structure without reinterpreting frozen provenance."""
        require_keys(document, DOCUMENT_KEYS, stage="storage")
        validate_finite(document)
        if type(document["schema_version"]) is not int or document["schema_version"] != SCHEMA_VERSION:
            raise _error("unsupported_document", "Unsupported model document schema")
        for field in ("project_id", "revision_id"):
            if not isinstance(document[field], str) or not document[field]:
                raise _error("invalid_identity", "Missing model document identity", field=field)
        if not isinstance(document["model"], dict) or str(document["model"].get("version", "")) != "1":
            raise _error("unsupported_model", "Unsupported or missing model version")
        materials = document["materials"]
        if not isinstance(materials, list) or not materials:
            raise _error("invalid_materials", "Material definitions are missing")
        ids: set[str] = set()
        for material in materials:
            require_keys(material, ("id", "version", "kind", "source"), stage="storage")
            if material["kind"] not in ("concrete02", "steel_bilinear", "elastic"):
                raise _error("unsupported_material", "Unknown critical material model")
            # Definition/package revisions are opaque identities, not encoding versions.
            # Critical material encoding compatibility is checked separately in metadata.
            if not isinstance(material["version"], str) or not material["version"].strip():
                raise _error("invalid_materials", "Material definition revision must be nonempty text")
            if not isinstance(material["id"], str) or not material["id"] or material["id"] in ids:
                raise _error("invalid_materials", "Material identities are invalid or repeated")
            ids.add(material["id"])

    @staticmethod
    def _document(document: dict[str, Any]) -> None:
        """Validate new source writes; historical read contexts cannot select a bypass."""
        ProjectStore._document_envelope(document)
        _source_criterion(document)

    def _revision(self, connection: sqlite3.Connection, document: dict[str, Any]) -> None:
        self._document(document)
        # The caller's revision/run transaction makes this compatibility upgrade
        # atomic. Readonly opens never edit legacy metadata or saved snapshots.
        connection.executemany("INSERT OR REPLACE INTO metadata VALUES(?,?)",
                               [("minimum_reader", _json(_READER_CAPABILITY)),
                                ("application_version", _json(_APPLICATION_VERSION))])
        payload, checksum = _hash_json(document)
        project = connection.execute("SELECT project_id FROM project WHERE singleton=1").fetchone()
        if project and project[0] != document["project_id"]:
            raise _error("project_mismatch", "Cannot write another project into this container")
        prior = connection.execute("SELECT project_id,sha256 FROM revisions WHERE revision_id=?",
                                   (document["revision_id"],)).fetchone()
        if prior:
            if prior["project_id"] != document["project_id"] or prior["sha256"] != checksum:
                raise _error("immutable_revision", "Changed model requires a new revision identity")
        else:
            connection.execute("INSERT INTO revisions VALUES(?,?,?,?,?)",
                               (document["project_id"], document["revision_id"], payload, checksum, _now()))
        if project is None:
            connection.execute("INSERT INTO project VALUES(1,?,?)",
                               (document["project_id"], document["revision_id"]))

    def save_document(self, document: dict[str, Any]) -> None:
        """Save a new immutable model revision, preserving a successful prior backup."""
        connection = self._check(write=True)
        self._document(document)
        if not self._suppress_backup and connection.execute("SELECT 1 FROM project").fetchone():
            self.backup(self.path.with_name(self.path.name + ".bak"))
        with self._transaction("save_document") as connection:
            self._revision(connection, document)
            self._fault("save_document:revision")
            connection.execute("UPDATE project SET current_revision=? WHERE singleton=1",
                               (document["revision_id"],))

    def load_document(self) -> dict[str, Any]:
        """Load the authoritative current revision without altering historical runs."""
        try:
            with self._transaction("load_document", write=False) as connection:
                row = connection.execute("SELECT r.payload,r.sha256 FROM project p JOIN revisions r "
                                         "ON r.revision_id=p.current_revision WHERE singleton=1").fetchone()
                if row is None:
                    raise _error("document_missing", "Project has no saved model document")
                document = _checked_json(row["payload"], row["sha256"], "document")
                self._document_envelope(document)
                frozen = connection.execute(
                    "SELECT prepared,prepared_sha256 FROM runs WHERE revision_id=? ORDER BY rowid DESC LIMIT 1",
                    (document["revision_id"],)).fetchone()
                if frozen is not None:
                    prepared, _ = self._read_prepared(frozen)
                    if prepared["document"] != document:
                        raise _error("snapshot_mismatch", "Stored revision disagrees with its verified frozen source")
                else:
                    # Historical standalone storage already required exact source
                    # provenance. Only a verified snapshot establishes frozen context.
                    _source_criterion(document)
                return document
        except sqlite3.Error as exc:
            raise _error("read_failed", f"Cannot load model document: {exc}") from exc

    def begin_run(self, prepared: dict[str, Any]) -> None:
        """Atomically persist the frozen snapshot before any calculation output."""
        require_keys(prepared, PREPARED_KEYS, stage="storage")
        validate_identity(prepared["identity"])
        validate_finite(prepared)
        if prepared["schema_version"] != SCHEMA_VERSION:
            raise _error("unsupported_prepared", "Unsupported prepared-model schema")
        identity, document = prepared["identity"], prepared["document"]
        self._document(document)
        if identity["project_id"] != document["project_id"] or identity["revision_id"] != document["revision_id"]:
            raise _error("snapshot_mismatch", "Frozen snapshot disagrees with its document revision")
        if not isinstance(prepared["prepared_hash"], str) or not prepared["prepared_hash"]:
            raise _error("snapshot_hash_missing", "Prepared-model content identity is missing")
        _verify_snapshot(prepared, for_analysis=True)
        payload, checksum = _hash_json(prepared)
        with self._transaction("begin_run") as connection:
            self._revision(connection, document)
            connection.execute("INSERT INTO runs(run_id,project_id,revision_id,snapshot_id,prepared,"
                               "prepared_sha256,status,created_at) VALUES(?,?,?,?,?,?,'running',?)",
                               (identity["run_id"], identity["project_id"], identity["revision_id"],
                                identity["snapshot_id"], payload, checksum, _now()))
        self._owned_runs.add(identity["run_id"])
        if self._lock is not None:
            self._lock.mark_run(identity["run_id"])

    @staticmethod
    def _step(step: dict[str, Any], prepared: dict[str, Any]) -> None:
        require_keys(step, (*STEP_KEYS, "control_displacement", "external_force", "localization"), stage="storage")
        if prepared.get("numerics_contract") in (NUMERICS_CONTRACT, CR_NUM_001_NUMERICS_CONTRACT):
            require_keys(step, REVISED_STEP_KEYS, stage="storage")
            convergence = step.get("convergence")
            require_keys(convergence, CONVERGENCE_SEARCH_KEYS, stage="storage")
            evaluations, backtracks = convergence["line_search_evaluations"], convergence["line_search_backtracks"]
            last, minimum = convergence["last_correction_factor"], convergence["minimum_correction_factor"]
            if (type(evaluations) is not int or type(backtracks) is not int or
                    not 0 <= backtracks <= evaluations or
                    any(isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)
                        for value in (last, minimum)) or not 0 < minimum <= last <= 1):
                raise _error("invalid_search_evidence", "Invalid actual Newton search counts/factors")
        if "transition" in step:
            transition = step["transition"]
            require_keys(transition, TRANSITION_KEYS, stage="storage")
            if (type(transition["active"]) is not bool or type(transition["candidate_solves"]) is not int or
                    not 0 <= transition["candidate_solves"] <= 40 or
                    not isinstance(transition["point_id"], str) or
                    transition["kind"] not in ("", *TRANSITION_KINDS)):
                raise _error("invalid_transition_evidence", "Invalid nonterminal onset evidence")
            if transition["active"]:
                numbers = [transition[key] for key in ("threshold", "lower_control", "upper_control", "lower_ratio", "upper_ratio")]
                if (transition["kind"] not in TRANSITION_KINDS or not transition["point_id"] or
                        transition["candidate_solves"] == 0 or
                        any(isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)
                            for value in numbers) or numbers[0] <= 0 or
                        transition["lower_converged"] is not True or transition["upper_converged"] is not True):
                    raise _error("invalid_transition_evidence", "Active onset requires real converged bracket endpoints")
            # Absence in an old stored step is retained; never manufacture zero evidence.
        policy = prepared.get("predictor_policy")
        if isinstance(policy, dict) and policy.get("id") in (PREDICTOR_POLICY_ID, CR_NUM_001_A04_PREDICTOR_POLICY_ID, LEGACY_PREDICTOR_POLICY_ID):
            current_policy = policy["id"] in (PREDICTOR_POLICY_ID, CR_NUM_001_A04_PREDICTOR_POLICY_ID)
            value = step.get("convergence")
            require_keys(value, PREDICTOR_EVIDENCE_KEYS if current_policy else LEGACY_PREDICTOR_EVIDENCE_KEYS,
                         stage="storage")
            total, attempts, work = (value[key] for key in (
                "total_newton_evaluations", "predictor_attempts", "predictor_newton_evaluations"))
            decompositions, linears = value["predictor_decompositions"], value["predictor_linear_solves"]
            kind, factor = value["predictor_kind"], value["predictor_seed_factor"]
            advance, amplitude = value["predictor_control_advance"], value["predictor_amplitude"]
            finite = lambda number: type(number) in (int, float) and math.isfinite(number)
            if (any(type(number) is not int for number in (total, attempts, work, decompositions, linears)) or
                    not 0 <= work <= total or not 0 <= attempts <= (15 if current_policy else 11) or
                    decompositions not in (0, 1) or linears not in (0, 1) or
                    kind not in ("", "accepted_secant", "force_unloading", "critical_mode") or
                    (kind == "" and any(number is not None for number in (factor, advance, amplitude))) or
                    (kind != "" and (attempts == 0 or not finite(factor) or not 0 < factor <= 8 or
                                     not finite(advance) or advance <= 0)) or
                    (kind == "critical_mode" and (not finite(amplitude) or amplitude == 0 or
                                                  decompositions != 1 or linears != 1)) or
                    (kind != "critical_mode" and amplitude is not None)):
                raise _error("invalid_predictor_evidence", "Invalid actual bounded predictor work or displacement evidence")
            if current_policy:
                mode_index = value["predictor_mode_index"]
                if (type(mode_index) is not int or
                        (kind == "critical_mode" and mode_index not in (1, 2)) or
                        (kind != "critical_mode" and mode_index != 0)):
                    raise _error("invalid_predictor_evidence", "Successful modal direction must be 1-based; otherwise zero")
        validate_identity(step["identity"], require_step=True)
        validate_finite(step)
        if not same_run(step["identity"], prepared["identity"]):
            raise _error("step_identity_mismatch", "Accepted step does not belong to the frozen run")
        if type(step["sequence"]) is not int or step["sequence"] < 0 or step["phase"] not in PHASES:
            raise _error("invalid_step", "Invalid accepted step sequence or phase")
        for field in ("P", "P1", "P2", "load_factor", "gravity_factor", "control_displacement"):
            value = step[field]
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise _error("invalid_scalar", "Accepted step scalar is not finite", field=field)
        ndof = 3 * len(prepared["nodes"])
        for field in ("displacement", "reactions", "external_force"):
            values = step[field]
            if not isinstance(values, list) or len(values) != ndof:
                raise _error("invalid_shape", "Nodal result shape disagrees with snapshot", field=field)
            if any(isinstance(item, bool) or not isinstance(item, (int, float)) or not math.isfinite(item)
                   for item in values):
                raise _error("invalid_nodal_value", "Nodal result contains missing or nonfinite values", field=field)
        for field, source in (("sections", "sections"), ("element_forces", "elements")):
            if not isinstance(step[field], list) or len(step[field]) != len(prepared[source]):
                raise _error("invalid_shape", "Result object count disagrees with snapshot", field=field)
            if [item.get("id") for item in step[field]] != [item["id"] for item in prepared[source]]:
                raise _error("location_mismatch", "Result object identities disagree with snapshot", field=field)
        expected_fibers = [point["id"] for section in prepared["sections"] for point in section["fibers"]]
        expected_edges = [point["id"] for section in prepared["sections"] for point in section["edges"]]
        for field, expected in (("fibers", expected_fibers), ("edges", expected_edges)):
            if not isinstance(step[field], list) or [point.get("id") for point in step[field]] != expected:
                raise _error("location_mismatch", "Material point identities disagree with snapshot", field=field)
        if not isinstance(step["events"], list) or not isinstance(step["observations"], list):
            raise _error("invalid_step", "Accepted events and observations must be arrays")
        physical = prepared.get("compression_criterion", {}).get("mode") == "physical_gauge_mean_v1"
        if physical:
            require_keys(step, ("gauge",), stage="storage")
            _gauge_state(step["gauge"], prepared, step)
            _gauge_localization(step["localization"], prepared)
            for event in step["events"]:
                if event.get("kind") == "reference_compression":
                    raise _error("mixed_criterion", "Legacy reference event cannot terminate physical mode")
                if event.get("kind") == "physical_gauge_compression":
                    if any(event.get(key) for key in ("element_id", "section_id", "point_id")):
                        raise _error("invalid_gauge_event", "Physical window cannot impersonate a material point")
                    require_keys(event, ("gauge", "localization"), stage="storage")
                    _gauge_state(event["gauge"], prepared, step)
                    _gauge_localization(event["localization"], prepared)
                    current = event["gauge"]["current"]
                    if (event.get("criterion") != current["mode"] or event.get("side") != current["side"] or
                            event.get("step_id") != current["identity"]["step_id"] or
                            event.get("threshold") != current["threshold"] or
                            current["compression_maximum"] < current["threshold"] or not event["localization"].get("active")):
                        raise _error("invalid_gauge_event", "Physical event disagrees with its actual threshold window")
        elif "gauge" in step or any(event.get("kind") == "physical_gauge_compression" for event in step["events"]):
            raise _error("mixed_criterion", "Legacy result cannot contain physical-mode gauge evidence")

    def append_step(self, step: dict[str, Any]) -> dict[str, Any]:
        """Write one whole accepted step or return a failed, accurately located receipt."""
        candidate_identity = step.get("identity", {}) if isinstance(step, dict) else {}
        identity = candidate_identity if isinstance(candidate_identity, dict) else {}
        run_id = identity.get("run_id", "")
        transaction_id = uuid4().hex
        persisted = -1
        try:
            self._check(write=True)
            with self._transaction("append_step") as connection:
                row = connection.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
                if row is None:
                    raise _error("run_missing", "Frozen run must be saved before accepting steps")
                persisted = row["last_sequence"]
                if row["status"] != "running":
                    raise _error("run_finished", "Cannot append output to a finished run")
                prepared, _ = self._read_prepared(row, for_analysis=True)
                self._step(step, prepared)
                if step["sequence"] != persisted + 1:
                    raise _error("step_order", "Accepted steps must be contiguous and written exactly once")
                packed, blocks = _pack_arrays(step, prepared)
                payload, checksum = _hash_json(packed)
                connection.execute("INSERT INTO steps VALUES(?,?,?,?,?,?,?,?,1,?)",
                                   (run_id, step["sequence"], identity["step_id"], step["phase"],
                                    step["P"], step["control_displacement"], payload, checksum, _now()))
                self._fault("append_step:scalars")
                connection.executemany("INSERT INTO result_blocks VALUES(?,?,?,?,?,?,?)",
                                       [(run_id, step["sequence"], *block) for block in blocks])
                self._fault("append_step:blocks")
                for index, event in enumerate(step["events"]):
                    event_payload, event_hash = _hash_json(event)
                    connection.execute("INSERT INTO events VALUES(?,?,?,?,?)",
                                       (run_id, step["sequence"], index, event_payload, event_hash))
                self._fault("append_step:events")
                connection.execute("UPDATE runs SET last_sequence=?,last_step_id=? WHERE run_id=?",
                                   (step["sequence"], identity["step_id"], run_id))
                self._fault("append_step:last_saved")
            return {"ok": True, "identity": dict(identity), "persisted_sequence": step["sequence"],
                    "transaction_id": transaction_id, "diagnostics": []}
        except Exception as exc:
            # A post-commit fault may lose the receipt, but never lie about the file position.
            try:
                row = self._check().execute("SELECT last_sequence FROM runs WHERE run_id=?", (run_id,)).fetchone()
                if row is not None:
                    persisted = row[0]
            except Exception:
                pass
            detail = exc.diagnostic if isinstance(exc, ContractError) else diagnostic(
                "storage_write_failed", str(exc), stage="storage", object_id=str(run_id))
            return {"ok": False, "identity": dict(identity), "persisted_sequence": persisted,
                    "transaction_id": "", "diagnostics": [detail]}

    def finish_run(self, summary: dict[str, Any]) -> None:
        """Atomically save conclusion and its true final accepted/persisted references."""
        require_keys(summary, SUMMARY_KEYS, stage="storage")
        validate_identity(summary["identity"])
        validate_finite(summary)
        if summary["termination"] not in TERMINATIONS:
            raise _error("unknown_termination", "Unknown run termination code")
        with self._transaction("finish_run") as connection:
            row = connection.execute("SELECT * FROM runs WHERE run_id=?",
                                     (summary["identity"]["run_id"],)).fetchone()
            if row is None:
                raise _error("run_missing", "Cannot conclude a missing run")
            prepared, _ = self._read_prepared(row)
            _criterion_summary(summary, prepared)
            if not same_run(summary["identity"], prepared["identity"]):
                raise _error("summary_identity_mismatch", "Conclusion belongs to another run")
            if (type(summary["persisted_last_step"]) is not int or
                    summary["persisted_last_step"] != row["last_sequence"] or
                    type(summary["memory_last_step"]) is not int or
                    summary["memory_last_step"] < row["last_sequence"]):
                raise _error("summary_position_mismatch", "Conclusion disagrees with complete file position")
            if summary.get("persisted_last_step_id", row["last_step_id"]) != row["last_step_id"]:
                raise _error("summary_position_mismatch", "Conclusion names a different saved step")
            payload, checksum = _hash_json(summary)
            if row["status"] == "finished":
                if payload == row["summary"]:
                    return
                raise _error("immutable_summary", "Saved run conclusion cannot be overwritten")
            connection.execute("UPDATE runs SET status='finished',summary=?,summary_sha256=?,finished_at=? "
                               "WHERE run_id=?", (payload, checksum, _now(), row["run_id"]))
        self._owned_runs.discard(summary["identity"]["run_id"])
        if self._lock is not None:
            self._lock.mark_run(None)

    def list_runs(self) -> list[dict[str, Any]]:
        """Read lightweight run identities and saved positions without material arrays."""
        try:
            return [dict(row) for row in self._check().execute(
                "SELECT run_id,project_id,revision_id,snapshot_id,status,last_sequence,last_step_id,"
                "created_at,finished_at FROM runs ORDER BY rowid")]
        except sqlite3.Error as exc:
            raise _error("read_failed", f"Cannot list runs: {exc}") from exc

    def list_steps(self, run_id: str) -> list[dict[str, Any]]:
        """Read curve/index scalars; full arrays remain unloaded until load_step."""
        try:
            return [dict(row) for row in self._check().execute(
                "SELECT sequence,step_id,phase,P,control_displacement FROM steps "
                "WHERE run_id=? AND complete=1 ORDER BY sequence", (run_id,))]
        except sqlite3.Error as exc:
            raise _error("read_failed", f"Cannot list accepted steps: {exc}") from exc

    def _read_step(self, connection: sqlite3.Connection, run_id: str, sequence: int,
                   prepared: dict[str, Any]) -> dict[str, Any]:
        row = connection.execute("SELECT * FROM steps WHERE run_id=? AND sequence=? AND complete=1",
                                 (run_id, sequence)).fetchone()
        if row is None:
            raise _error("step_missing", "Accepted step is missing", object_id=f"{run_id}:{sequence}")
        payload = _checked_json(row["payload"], row["sha256"], "step")
        blocks = {item["path"]: item for item in connection.execute(
            "SELECT * FROM result_blocks WHERE run_id=? AND sequence=?", (run_id, sequence))}
        step = _unpack_arrays(payload, blocks)
        self._step(step, prepared)
        if step["sequence"] != sequence or step["identity"]["step_id"] != row["step_id"] or step["P"] != row["P"]:
            raise _error("step_index_mismatch", "Accepted step index disagrees with its payload")
        events = [_checked_json(item["payload"], item["sha256"], "event") for item in connection.execute(
            "SELECT * FROM events WHERE run_id=? AND sequence=? ORDER BY event_index", (run_id, sequence))]
        if events != step["events"]:
            raise _error("event_index_mismatch", "Accepted event index disagrees with step payload")
        return step

    def load_step(self, run_id: str, sequence: int) -> dict[str, Any]:
        """Validate and load one actual accepted step without recalculating physics."""
        with self._transaction("load_step", write=False) as connection:
            row = connection.execute("SELECT prepared,prepared_sha256 FROM runs WHERE run_id=?", (run_id,)).fetchone()
            if row is None:
                raise _error("run_missing", "Requested run is missing")
            prepared, _ = self._read_prepared(row)
            return self._read_step(connection, run_id, sequence, prepared)

    def _writer_active(self, run_id: str) -> bool:
        if self._lock is not None and self._lock.handle is not None:
            return run_id in self._owned_runs
        lock = _WriterLock(self.path)
        if not lock.path.exists():
            return False
        try:
            lock.acquire()
            return False
        except ContractError:
            try:
                with lock.path.open("rb") as stream:
                    stream.seek(1)
                    records = stream.read().splitlines()
                records = [record for record in records if record.strip()]
                return bool(records) and json.loads(records[-1]).get("run_id") == run_id
            except (OSError, ValueError, AttributeError):
                # A writer whose activity marker cannot be inspected is not assumed dead.
                return True
        finally:
            lock.close()

    def load_run(self, run_id: str | None = None, *, include_steps: bool = True) -> dict[str, Any]:
        """Load a run, retaining other valid steps if individual blocks are damaged.

        A missing final transaction is reported as run_status='interrupted' when
        no writer is active.  The original record is never rewritten on read.
        ``include_steps=False`` supports lazy desktop opening.
        """
        with self._transaction("load_run", write=False) as connection:
            row = connection.execute("SELECT * FROM runs ORDER BY rowid DESC LIMIT 1" if run_id is None else
                                     "SELECT * FROM runs WHERE run_id=?", () if run_id is None else (run_id,)).fetchone()
            if row is None:
                raise _error("run_missing", "Project has no matching saved analysis")
            prepared, analysis_read_only = self._read_prepared(row)
            require_keys(prepared, PREPARED_KEYS, stage="storage")
            validate_identity(prepared["identity"])
            # _read_prepared already verified original hash and complete native
            # producer reconstruction. Do not apply new-source validation here.
            self._document_envelope(prepared["document"])
            steps: list[dict[str, Any]] = []
            unavailable: list[dict[str, Any]] = []
            diagnostics: list[dict[str, Any]] = []
            indexes = [dict(item) for item in connection.execute(
                "SELECT sequence,step_id,phase,P,control_displacement FROM steps WHERE run_id=? ORDER BY sequence",
                (row["run_id"],))]
            sequences = [item["sequence"] for item in indexes]
            if sequences != list(range(row["last_sequence"] + 1)) or (
                    indexes and indexes[-1]["step_id"] != row["last_step_id"]):
                diagnostics.append(diagnostic("saved_index_corrupt", "Saved step directory is inconsistent",
                                              stage="storage", object_id=row["run_id"]))
            if include_steps:
                for index in indexes:
                    try:
                        steps.append(self._read_step(connection, row["run_id"], index["sequence"], prepared))
                    except (ContractError, sqlite3.Error, KeyError, TypeError, ValueError) as exc:
                        detail = exc.diagnostic if isinstance(exc, ContractError) else diagnostic(
                            "step_unavailable", str(exc), stage="storage", object_id=index["step_id"])
                        diagnostics.append(detail)
                        unavailable.append({**index, "diagnostics": [detail]})
            summary = None
            if row["summary"] is not None:
                summary = _checked_json(row["summary"], row["summary_sha256"], "summary")
                require_keys(summary, SUMMARY_KEYS, stage="storage")
                validate_identity(summary["identity"])
                _criterion_summary(summary, prepared)
                if not same_run(summary["identity"], prepared["identity"]):
                    raise _error("summary_identity_mismatch", "Stored conclusion belongs to another run")
                if summary["persisted_last_step"] != row["last_sequence"]:
                    raise _error("summary_position_mismatch", "Stored conclusion disagrees with step directory")
            status = row["status"]
            if status == "running" and not self._writer_active(row["run_id"]):
                status = "interrupted"
                diagnostics.append(diagnostic("run_interrupted", "Previous run has no saved final conclusion; "
                                              "only complete persisted steps are available", severity="warning",
                                              stage="storage", object_id=row["run_id"]))
            return {"prepared": prepared, "analysis_read_only": analysis_read_only, "summary": summary, "steps": steps,
                    "step_index": indexes, "unavailable_steps": unavailable, "diagnostics": diagnostics,
                    "run_status": status, "persisted_last_step": row["last_sequence"],
                    "persisted_last_step_id": row["last_step_id"]}

    def save_import(self, dataset: dict[str, Any]) -> None:
        """Keep adopted numeric channels and processing provenance for offline use."""
        if not isinstance(dataset, dict):
            raise _error("invalid_import", "Imported dataset must be an object")
        dataset_id = dataset.get("id", dataset.get("dataset_id"))
        if not isinstance(dataset_id, str) or not dataset_id:
            raise _error("invalid_import", "Imported dataset requires a stable id")
        payload, checksum = _hash_json(dataset)
        with self._transaction("save_import") as connection:
            row = connection.execute("SELECT sha256 FROM imports WHERE dataset_id=?", (dataset_id,)).fetchone()
            if row:
                if row[0] != checksum:
                    raise _error("immutable_import", "Changed data or processing requires a new dataset identity")
                return
            connection.execute("INSERT INTO imports VALUES(?,?,?,?)", (dataset_id, payload, checksum, _now()))

    def load_imports(self) -> list[dict[str, Any]]:
        """Load self-contained imported channels; missing external originals are warnings."""
        with self._transaction("load_imports", write=False) as connection:
            return [_checked_json(row["payload"], row["sha256"], "import")
                    for row in connection.execute("SELECT * FROM imports ORDER BY rowid")]

    def source_diagnostics(self) -> list[dict[str, Any]]:
        """Inspect external source hints without replacing stored channels or their IDs."""
        result: list[dict[str, Any]] = []
        for dataset in self.load_imports():
            source = dataset.get("source", {})
            if not isinstance(source, dict):
                continue
            path = source.get("path", source.get("original_path"))
            expected = source.get("sha256", source.get("hash"))
            if not isinstance(path, str) or not path:
                continue
            source_path = Path(path)
            if not source_path.is_absolute():
                source_path = self.path.parent / source_path
            try:
                actual = hashlib.sha256()
                with source_path.open("rb") as stream:
                    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                        actual.update(chunk)
                if isinstance(expected, str) and expected and actual.hexdigest() != expected:
                    result.append(diagnostic("external_source_changed", "External source content changed; "
                                             "saved numeric channels retain their original identity",
                                             severity="warning", stage="storage", field=path))
            except OSError:
                result.append(diagnostic("external_source_missing", "External source is unavailable; "
                                         "saved numeric channels remain available offline",
                                         severity="warning", stage="storage", field=path))
        return result

    def _copy_to(self, destination: Path) -> None:
        connection = self._check()
        if connection.in_transaction:
            raise _error("backup_during_transaction", "Finish the current transaction before creating a copy")
        target: sqlite3.Connection | None = None
        try:
            target = sqlite3.connect(str(destination), isolation_level=None)
            connection.backup(target, pages=256, sleep=0.01)
            if target.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise _error("backup_corrupt", "Copied project failed SQLite integrity check")
            target.close()
            target = None
            # Reopen with the actual format reader before publishing the copy.
            with ProjectStore(destination, readonly=True):
                pass
        except (sqlite3.Error, OSError) as exc:
            raise _error("backup_failed", f"Cannot create consistent project copy: {exc}") from exc
        finally:
            if target is not None:
                target.close()

    def _publish_copy(self, path: str | Path, edit: Callable[[Path], None] | None = None) -> None:
        self._check()
        destination = Path(path).expanduser().resolve()
        if destination == self.path:
            raise _error("same_copy_target", "Copy target must differ from the open project")
        temporary = destination.with_name(destination.name + "." + uuid4().hex + ".tmp")
        target_lock: _WriterLock | None = None
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            target_lock = _WriterLock(destination)
            target_lock.acquire()
            self._copy_to(temporary)
            if edit is not None:
                edit(temporary)
            with ProjectStore(temporary, readonly=True) as copy:
                copy._check_format()
            # SQLite has closed and flushed the standalone copy before atomic publication.
            with temporary.open("r+b") as stream:
                os.fsync(stream.fileno())
            os.replace(temporary, destination)
        except OSError as exc:
            raise _error("copy_publish_failed", f"Cannot publish project copy: {exc}") from exc
        finally:
            if target_lock is not None:
                target_lock.close()
            # Only task-owned exact temporary paths are removed, never SQLite user journals.
            for residue in (temporary, temporary.with_name(temporary.name + ".lock")):
                try:
                    residue.unlink(missing_ok=True)
                except OSError:
                    pass

    def backup(self, path: str | Path) -> None:
        """Publish a consistent SQLite backup, preserving any old target on failure."""
        self._publish_copy(path)

    def save_as(self, path: str | Path, *, document: dict[str, Any] | None = None) -> None:
        """Copy unchanged, or atomically save an explicit revision in the copy only."""
        if document is None:
            self._publish_copy(path)
            return
        self._document(document)
        def edit(temporary: Path) -> None:
            with ProjectStore(temporary) as copy:
                copy._suppress_backup = True
                copy.save_document(document)
        self._publish_copy(path, edit)

    def write_recovery(self, document: dict[str, Any], path: str | Path) -> None:
        """Copy saved output and include unsaved edits in an independent recovery file."""
        self._document(document)
        def edit(temporary: Path) -> None:
            with ProjectStore(temporary) as recovery:
                recovery._suppress_backup = True
                recovery.save_document(document)
                with recovery._transaction("recovery_metadata") as connection:
                    for key, value in {"recovery_origin": str(self.path), "recovery_created_at": _now(),
                                       "recovery_revision": document["revision_id"]}.items():
                        connection.execute("INSERT OR REPLACE INTO metadata VALUES(?,?)", (key, _json(value)))
        self._publish_copy(path, edit)
