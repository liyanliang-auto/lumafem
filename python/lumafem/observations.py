"""Read-only measurement operators and reproducible CSV comparison workflows.

CSV mapping version 1: ``channels`` maps P/P1/P2/midpoint/left_support/
right_support/time or named strain/curvature channels to {column, unit, sign,
offset}. A column is an exact header or zero-based index. Units are mandatory.
Pressure channels additionally require calibration={coefficients:[c0,c1,...],
output_unit:'N'|'kN',reference:'...'}; coefficients act on the stated raw
pressure unit, before sign and output offset. No pressure is treated as force.
Channel ``quantity`` may explicitly be force, displacement, time, strain or
curvature. Optional time clock={scale,offset,reference} records explicit drift
and synchronization. No automatic synchronization or fitting is performed.

Mapping fields: load_source ('total' or 'sum'), zero_row (zero-based data row,
None preserves instrument zero), settlement_correction, control_x, support_x
(two coordinates), reference_status, alignment_confirmed, calibration_case,
valid_interval, events and metadata_path. Numeric output units are N/mm/s/1.
Named source channels and raw strings are always retained. Missing values
create gaps; malformed/nonfinite values are rejected. Explicit synthetic
metadata can never be overridden to claim measured data.

Comparison consumes {w,P,identity,sequence,phase,channels,events} curve rows.
Options may explicitly freeze experimental_interval, simulation_rows and
experiment_rows (inclusive index pairs), stiffness_interval/uncracked_evidence,
channel_scales/noise_scales, event_definitions and event_interval_limits.
Paths are never sorted, peak-normalized, extrapolated or bridged across gaps.
"""
from __future__ import annotations

import bisect
import csv
import hashlib
import io
import json
import math
import os
from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping, Sequence

from .contracts import ContractError, diagnostic, new_id, same_run, validate_finite, validate_identity

__all__ = ["preview_csv", "import_csv", "observe_step", "criterion_evidence", "compare_curves", "export_csv"]
_MAX_BYTES = 64 * 1024 * 1024
_MAX_ROWS = 1_000_000
_FACTORS = {
    "force": {"N": 1., "kN": 1000., "MN": 1e6},
    "displacement": {"mm": 1., "m": 1000., "cm": 10., "um": .001},
    "time": {"s": 1., "ms": .001, "min": 60.},
    "strain": {"1": 1., "strain": 1., "microstrain": 1e-6, "%": .01},
    "curvature": {"1/mm": 1., "1/m": .001},
}
_UNITS = {"force": "N", "displacement": "mm", "time": "s", "strain": "1", "curvature": "1/mm"}


def _fail(code: str, message: str, field: str = "") -> ContractError:
    return ContractError(code, message, stage="observation", field=field)


def _number(value: Any, field: str) -> float:
    if isinstance(value, bool):
        raise _fail("invalid_number", "Boolean is not a numerical measurement", field)
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise _fail("invalid_number", "Expected a finite number", field) from exc
    if not math.isfinite(result):
        raise _fail("nonfinite", "Measurement must be finite", field)
    return result


def _read_csv(path: str, encoding: str, delimiter: str | None) -> tuple[bytes, list[str], list[list[str]], str]:
    try:
        target = Path(path)
        if target.stat().st_size > _MAX_BYTES:
            raise _fail("csv_capacity", "CSV exceeds the 64 MiB import limit")
        raw = target.read_bytes()
        text = raw.decode(encoding)
    except (OSError, UnicodeError, LookupError) as exc:
        raise _fail("csv_read", f"Cannot read CSV: {exc}") from exc
    if delimiter is None:
        try:
            delimiter = csv.Sniffer().sniff(text[:16384], delimiters=",;\t|").delimiter
        except csv.Error as exc:
            raise _fail("csv_delimiter", "Choose an explicit CSV delimiter") from exc
    if not isinstance(delimiter, str) or len(delimiter) != 1 or delimiter in "\r\n\0":
        raise _fail("csv_delimiter", "Delimiter must be one non-newline character")
    try:
        reader = csv.reader(io.StringIO(text, newline=""), delimiter=delimiter, strict=True)
        header = next(reader)
        if not header or any(not item.strip() for item in header) or len(set(header)) != len(header):
            raise _fail("csv_header", "Column names must be nonempty and unique")
        rows = []
        for index, row in enumerate(reader):
            if index >= _MAX_ROWS:
                raise _fail("csv_capacity", "CSV exceeds the one-million-row import limit")
            if len(row) != len(header):
                raise _fail("csv_shape", f"Data row {index} has a different column count")
            rows.append(row)
        if not rows:
            raise _fail("csv_empty", "CSV has no data rows")
    except (csv.Error, StopIteration) as exc:
        raise _fail("csv_parse", f"Invalid CSV: {exc}") from exc
    return raw, header, rows, delimiter


def preview_csv(path: str, encoding: str = "utf-8-sig", delimiter: str | None = None) -> dict:
    """Preview exact headers/strings and missing cells without guessing physical units."""
    raw, header, rows, separator = _read_csv(path, encoding, delimiter)
    return {"schema_version": 1, "path": str(Path(path).resolve()), "encoding": encoding,
            "delimiter": separator, "columns": header, "headers": header, "row_count": len(rows),
            "preview_rows": deepcopy(rows[:50]), "rows": deepcopy(rows[:50]),
            "sha256": hashlib.sha256(raw).hexdigest(),
            "missing_cells": sum(not value.strip() for row in rows for value in row),
            "units_inferred": False}


def _quantity(name: str, spec: Mapping) -> str:
    default = ("force" if name in ("P", "P1", "P2") else
               "displacement" if name in ("midpoint", "w", "left_support", "right_support") else
               "time" if name == "time" else "strain" if "strain" in name else
               "curvature" if "curvature" in name else None)
    result = spec.get("quantity", default)
    if result not in _FACTORS:
        raise _fail("channel_quantity", f"Declare a supported quantity for {name}")
    return result


def _convert(raw: str, spec: Mapping, quantity: str, field: str) -> float | None:
    if not raw.strip():
        return None
    value = _number(raw, field)
    unit = spec.get("unit")
    if unit in ("Pa", "kPa", "MPa", "bar", "psi"):
        if quantity != "force":
            raise _fail("pressure_quantity", "Pressure calibration is only supported for force channels", field)
        calibration = spec.get("calibration")
        if not isinstance(calibration, Mapping) or not calibration.get("reference"):
            raise _fail("pressure_calibration", "Pressure requires a traceable force calibration", field)
        coefficients = calibration.get("coefficients")
        if not isinstance(coefficients, list) or not 1 <= len(coefficients) <= 8:
            raise _fail("pressure_calibration", "Provide 1 to 8 polynomial coefficients in ascending order", field)
        result = 0.
        for coefficient in reversed(coefficients):
            result = result * value + _number(coefficient, field + ".calibration")
        value = result
        unit = calibration.get("output_unit")
    try:
        factor = _FACTORS[quantity][unit]
    except (KeyError, TypeError) as exc:
        raise _fail("channel_unit", f"Unsupported or missing {quantity} unit: {unit}", field) from exc
    sign = _number(spec.get("sign", 1), field + ".sign")
    if sign not in (-1., 1.):
        raise _fail("channel_sign", "Channel sign must be +1 or -1", field)
    value = sign * value * factor + _number(spec.get("offset", 0.), field + ".offset")
    if quantity == "time":
        clock = spec.get("clock", {})
        if clock:
            if not isinstance(clock, Mapping) or not clock.get("reference"):
                raise _fail("clock_reference", "Clock correction requires its physical reference", field)
            scale = _number(clock.get("scale", 1.), field + ".clock.scale")
            if scale <= 0:
                raise _fail("clock_scale", "Clock scale must be positive", field)
            value = value * scale + _number(clock.get("offset", 0.), field + ".clock.offset")
    return _number(value, field)


def _metadata(path: str, mapping: Mapping, digest: str) -> dict:
    explicit = mapping.get("metadata_path")
    target = Path(explicit) if explicit else Path(path).with_suffix(".metadata.json")
    if not target.exists() and not explicit:
        return {}
    try:
        if target.stat().st_size > _MAX_BYTES:
            raise _fail("metadata_capacity", "Metadata file exceeds import limit")
        result = json.loads(target.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError, UnicodeError) as exc:
        raise _fail("metadata_read", f"Cannot read CSV metadata: {exc}") from exc
    if not isinstance(result, dict):
        raise _fail("metadata_shape", "CSV metadata must be an object")
    validate_finite(result)
    if result.get("csv_sha256") and result["csv_sha256"] != digest:
        raise _fail("metadata_hash", "CSV metadata belongs to different source bytes")
    return result


def import_csv(path: str, mapping: dict) -> dict:
    """Freeze raw channels and explicitly converted, referenced observations in a dataset."""
    if not isinstance(mapping, dict) or not isinstance(mapping.get("channels"), dict):
        raise _fail("csv_mapping", "Provide explicit channel mappings")
    validate_finite(mapping)
    raw, header, raw_rows, separator = _read_csv(path, mapping.get("encoding", "utf-8-sig"), mapping.get("delimiter"))
    digest = hashlib.sha256(raw).hexdigest()
    metadata = _metadata(path, mapping, digest)
    channels = mapping["channels"]
    if not channels or not any(name in channels for name in ("P", "P1", "P2")):
        raise _fail("load_mapping", "Map a total force or both jack force channels")
    if not any(name in channels for name in ("midpoint", "w")):
        raise _fail("displacement_mapping", "Map the midpoint displacement channel")
    specifications, units = {}, {}
    for name, spec in channels.items():
        if not isinstance(name, str) or not isinstance(spec, Mapping):
            raise _fail("channel_mapping", "Each named channel requires an object")
        column = spec.get("column")
        if isinstance(column, str):
            if column not in header:
                raise _fail("csv_column", f"Unknown column: {column}", name)
            column = header.index(column)
        if isinstance(column, bool) or not isinstance(column, int) or not 0 <= column < len(header):
            raise _fail("csv_column", "Column must be an exact header or zero-based index", name)
        quantity = _quantity(name, spec)
        _convert("0", spec, quantity, name)
        specifications[name] = (column, spec, quantity)
        units[name] = _UNITS[quantity]
    source = mapping.get("load_source", "sum" if "P1" in channels and "P2" in channels else "total")
    if source not in ("sum", "total") or (source == "sum" and not {"P1", "P2"} <= channels.keys()) or (source == "total" and "P" not in channels):
        raise _fail("load_source", "Selected force source does not have the required channels")
    converted = [{name: _convert(row[index], spec, quantity, f"row[{i}].{name}")
                  for name, (index, spec, quantity) in specifications.items()} for i, row in enumerate(raw_rows)]
    zero_row = mapping.get("zero_row")
    zero = None
    if zero_row is not None:
        if isinstance(zero_row, bool) or not isinstance(zero_row, int) or not 0 <= zero_row < len(converted):
            raise _fail("zero_row", "Zero row must identify an existing data row")
        zero = converted[zero_row]
        if any(value is None for value in zero.values()):
            raise _fail("zero_missing", "Reference row has missing mapped measurements")
    correct = bool(mapping.get("settlement_correction", False))
    diagnostics = []
    support_available = {"left_support", "right_support"} <= channels.keys()
    if correct and not support_available:
        diagnostics.append(diagnostic("settlement_unavailable", "Support channels are missing; displacement remains uncorrected",
                                      severity="warning", stage="observation"))
    fraction = .5
    if "support_x" in mapping or "control_x" in mapping:
        positions = mapping.get("support_x")
        if not isinstance(positions, list) or len(positions) != 2 or "control_x" not in mapping:
            raise _fail("support_positions", "Provide both support_x coordinates and control_x")
        left, right = [_number(v, "support_x") for v in positions]
        control = _number(mapping["control_x"], "control_x")
        if not left < right or not left <= control <= right:
            raise _fail("support_positions", "Control must lie between ordered support positions")
        fraction = (control - left) / (right - left)
    rows = []
    for index, absolute in enumerate(converted):
        values = {name: None if value is None else value - (zero[name] if zero is not None else 0.)
                  for name, value in absolute.items()}
        midpoint = values.get("midpoint", values.get("w"))
        support = (values.get("left_support"), values.get("right_support"))
        corrected = correct and support_available
        w = (None if midpoint is None or (corrected and None in support) else
             midpoint - ((1. - fraction) * support[0] + fraction * support[1] if corrected else 0.))
        total = values.get("P")
        summed = None if values.get("P1") is None or values.get("P2") is None else values["P1"] + values["P2"]
        force = summed if source == "sum" else total
        absolute_force = (None if absolute.get("P1") is None or absolute.get("P2") is None else absolute["P1"] + absolute["P2"]) if source == "sum" else absolute.get("P")
        rows.append({"source_row": index, "P": force, "w": w, "P1": values.get("P1"), "P2": values.get("P2"),
                     "time": values.get("time"), "channels": values, "absolute_channels": deepcopy(absolute),
                     "P_total": absolute_force, "instrument_P": force,
                     "total_channel": total, "jack_sum": summed,
                     "total_difference": None if total is None or summed is None else total - summed,
                     "settlement_corrected": corrected, "valid": force is not None and w is not None})
    synthetic = bool(metadata.get("synthetic") or metadata.get("category") == "synthetic" or mapping.get("synthetic"))
    if not any(row["valid"] for row in rows):
        raise _fail("csv_no_valid_rows", "No complete force-displacement pair exists")
    times = [row["time"] for row in rows if row["time"] is not None]
    if any(b <= a for a, b in zip(times, times[1:])):
        diagnostics.append(diagnostic("time_nonmonotonic", "Time is not strictly increasing; original order is retained",
                                      severity="warning", stage="observation"))
    return {"schema_version": 1, "id": new_id("dataset"), "name": mapping.get("name", metadata.get("name", Path(path).name)),
            "source": {"path": str(Path(path).resolve()), "sha256": digest, "encoding": mapping.get("encoding", "utf-8-sig"),
                       "delimiter": separator, "category": "synthetic" if synthetic else mapping.get("source_category", "unverified")},
            "synthetic": synthetic, "metadata": metadata, "columns": header, "raw_rows": raw_rows,
            "mapping": deepcopy(mapping), "units": units, "rows": rows,
            "events": deepcopy(mapping.get("events", metadata.get("events", {}))),
            "valid_interval": deepcopy(mapping.get("valid_interval")),
            "alignment_confirmed": bool(mapping.get("alignment_confirmed", False)),
            "reference_status": mapping.get("reference_status", "UNVERIFIED"),
            "calibration_case": bool(mapping.get("calibration_case", False)),
            "processing": {"version": "csv-observation-v1", "load_source": source, "zero_row": zero_row,
                           "settlement_requested": correct, "settlement_available": support_available,
                           "support_fraction": fraction, "raw_order_retained": True, "normalization": "none"},
            "physical_validation": "UNVERIFIED_SYNTHETIC_DATA" if synthetic else "UNVERIFIED",
            "diagnostics": diagnostics}


def _kinematics(prepared: Mapping, displacement: Sequence, x: float) -> tuple[float, float, float]:
    nodes, elements = prepared["nodes"], prepared["elements"]
    for element in elements:
        left, right = (nodes[i] for i in element["nodes"])
        if left["x"] - 1e-9 <= x <= right["x"] + 1e-9:
            length = _number(element["length"], "element.length")
            if length <= 0:
                raise _fail("element_length", "Invalid element length")
            t = (x - left["x"]) / length
            c, s = element["cosine"], element["sine"]
            values = []
            for node in (left, right):
                ux, uy, theta = [displacement[i] for i in node["dofs"]]
                values.extend((c * ux + s * uy, -s * ux + c * uy, theta))
            u1, v1, r1, u2, v2, r2 = values
            u = (1 - t) * u1 + t * u2
            v = ((1 - 3*t*t + 2*t*t*t)*v1 + length*(t - 2*t*t + t*t*t)*r1 +
                 (3*t*t - 2*t*t*t)*v2 + length*(-t*t + t*t*t)*r2)
            theta = ((-6*t + 6*t*t)*v1 / length + (1 - 4*t + 3*t*t)*r1 +
                     (6*t - 6*t*t)*v2 / length + (-2*t + 3*t*t)*r2)
            return u, v, theta
    raise _fail("observation_outside", "Observation lies outside the prepared beam", "x")


def criterion_evidence(prepared: Mapping, step: Mapping) -> dict:
    """Copy actual mechanical evidence independently of display sensors and zeros.

    Missing archived evidence stays absent. This projection neither evaluates
    the window operator nor infers a structural terminal from local flags.
    """
    result = {}
    if "compression_criterion" in prepared:
        result["compression_criterion"] = deepcopy(prepared["compression_criterion"])
        for key, value in (prepared["compression_criterion"] or {}).items():
            result["criterion_" + key] = deepcopy(value)
    for key in ("gauge", "localization", "control_displacement", "gravity_factor", "load_factor"):
        if key in step:
            result[key] = deepcopy(step[key])
    gauge = step.get("gauge") or {}
    current = gauge.get("current") or {}
    if current:
        for key in ("mode", "operator_version", "reference", "gauge_length_mm", "threshold",
                    "search_domain_mm", "side", "eta", "x1", "x2", "signed_mean",
                    "compression_maximum", "status"):
            if key in current:
                result["criterion_" + key] = deepcopy(current[key])
    # Only real formal edge histories can define the old point diagnostic.
    # Compact indexes legitimately omit these arrays; never turn absence into 0.
    edges = step.get("edges")
    if isinstance(edges, list):
        candidates = []
        for edge in edges:
            amplitude = (edge.get("state") or {}).get("max_reference_compression_strain")
            threshold = edge.get("reference_threshold")
            if (isinstance(amplitude, (int, float)) and not isinstance(amplitude, bool)
                    and math.isfinite(amplitude) and isinstance(threshold, (int, float))
                    and not isinstance(threshold, bool) and math.isfinite(threshold) and threshold > 0):
                candidates.append((amplitude / threshold, edge, amplitude, threshold))
        if candidates:
            ratio, edge, amplitude, threshold = max(candidates, key=lambda item: item[0])
            result["point_route_c_diagnostic"] = {
                "mode": "route_c_point_v1", "point_id": edge.get("id"),
                "section_id": edge.get("section_id"), "x": edge.get("x"), "eta": edge.get("eta"),
                "historical_reference_compression": amplitude, "threshold": threshold, "ratio": ratio,
                "source": "actual_formal_edge_history", "structural_terminal_in_physical_mode": False}
    return result


def observe_step(prepared: dict, step: dict, reference: dict | None = None, definition: dict | None = None) -> dict:
    """Reconstruct EB measurements and exact-reference increments without changing physics."""
    validate_identity(prepared.get("identity", {}))
    validate_identity(step.get("identity", {}), require_step=True)
    if not same_run(prepared["identity"], step["identity"]):
        raise _fail("observation_identity", "Step does not belong to the prepared snapshot")
    document = prepared["document"]
    model = document["model"]
    setup = deepcopy(definition if definition is not None else document.get("observations", {}))
    if definition is None and not model.get("gravity", {}).get("enabled", False) and not setup.get("reference_step_id") and setup.get("reference_phase", "SG") == "SG":
        setup["reference_phase"] = "S0"
    validate_finite(setup)
    if reference is None and (setup.get("reference_step_id") == step["identity"]["step_id"] or
                              (not setup.get("reference_step_id") and step.get("phase") == setup.get("reference_phase", "SG"))):
        reference = step
    if reference is not None:
        validate_identity(reference.get("identity", {}), require_step=True)
        if not same_run(step["identity"], reference["identity"]):
            raise _fail("reference_identity", "Reference belongs to another run")
        if setup.get("reference_step_id") and setup["reference_step_id"] != reference["identity"]["step_id"]:
            raise _fail("reference_identity", "Reference does not match selected step")
        if not setup.get("reference_step_id") and reference.get("phase") != setup.get("reference_phase", "SG"):
            raise _fail("reference_phase", "Reference does not match selected phase")
    expected = 3 * len(prepared["nodes"])
    for payload in (step, reference):
        if payload is None:
            continue
        displacement = payload.get("displacement")
        if not isinstance(displacement, list) or len(displacement) != expected:
            raise _fail("displacement_shape", "Expected all prepared nodal displacement components")
        for value in displacement:
            _number(value, "displacement")
    ref_id = reference["identity"]["step_id"] if reference else None
    sign = -1. if setup.get("downward_positive", True) else 1.
    angle = model["geometry"].get("angle", 0.)
    sine, cosine = math.sin(angle), math.cos(angle)
    control_x = model["control_x"]
    supports = sorted(model.get("supports", []), key=lambda item: item["x"])
    support_positions = [item["x"] for item in supports if item.get("constrained", [False, False])[1]]

    def measured(payload: Mapping) -> dict[str, tuple[float, str, str]]:
        vector = payload["displacement"]
        def vertical(x: float) -> float:
            u, v, _ = _kinematics(prepared, vector, x)
            return sine * u + cosine * v
        vertical_control = vertical(control_x)
        if setup.get("settlement_correction", True):
            if len(support_positions) < 2 or support_positions[-1] <= support_positions[0]:
                raise _fail("support_chord", "Support-chord observation needs two vertical support positions")
            a, b = support_positions[0], support_positions[-1]
            fraction = (control_x - a) / (b - a)
            vertical_control -= (1 - fraction)*vertical(a) + fraction*vertical(b)
        result = {"w": (sign*vertical_control, "mm", "global-Y EB interpolation relative to support chord" if setup.get("settlement_correction", True) else "global-Y EB interpolation without settlement correction"),
                  "P": (_number(payload["P"], "P"), "N", "external two-jack force, excluding gravity")}
        for gauge in setup.get("gauges", []):
            x1, x2, eta = (_number(gauge[key], f"gauge.{key}") for key in ("x1", "x2", "eta"))
            if x2 <= x1 or abs(eta) > model["geometry"]["height"] / 2 + 1e-9:
                raise _fail("gauge_domain", "Gauge needs ordered endpoints and physical section height")
            u1, _, t1 = _kinematics(prepared, vector, x1)
            u2, _, t2 = _kinematics(prepared, vector, x2)
            result[gauge["id"]] = (((u2 - eta*t2) - (u1 - eta*t1)) / (x2 - x1), "1", "display-only finite-gauge EB displacement difference; not the whole-beam compression criterion or point Route-C")
        for node in prepared["nodes"]:
            reactions = payload.get("reactions", [])
            if len(reactions) == expected:
                for component, dof in enumerate(node["dofs"]):
                    result[f"reaction:{node['id']}:{component}"] = (_number(reactions[dof], "reaction"), "N.mm" if component == 2 else "N", "cumulative global equilibrium reaction/residual")
        for section in payload.get("sections", []):
            for key, unit in (("N", "N"), ("M", "N.mm"), ("curvature", "1/mm")):
                result[f"section:{section['id']}:{key}"] = (_number(section[key], key), unit, "actual Gauss section")
        return result

    current = measured(step)
    prior = measured(reference) if reference else {}
    values = [{"id": key, "quantity": "deflection" if key == "w" else "force" if key == "P" else "strain" if unit == "1" else key.rsplit(":", 1)[-1],
               "unit": unit, "source": source, "total": value,
               "increment": value - prior[key][0] if key in prior else None, "reference_step_id": ref_id}
              for key, (value, unit, source) in current.items()]
    by_id = {value["id"]: value for value in values}
    curve = {"identity": deepcopy(step["identity"]), "sequence": step["sequence"], "phase": step["phase"],
             "w": by_id["w"]["increment"], "P": by_id["P"]["total"], "w_total": by_id["w"]["total"],
             "P_increment": by_id["P"]["increment"], "events": deepcopy(step.get("events", [])),
             "channels": {value["id"]: value["increment"] for value in values if value["id"] not in ("P", "w")},
             **criterion_evidence(prepared, step)}
    return {"identity": deepcopy(step["identity"]), "definition": setup, "reference_step_id": ref_id,
            "reference_status": "NUMERICAL_REFERENCE_ONLY" if reference else "REFERENCE_UNAVAILABLE",
            "values": values, "observations": deepcopy(values), "curve": curve, "w": curve["w"], "P": curve["P"],
            "diagnostics": [] if reference else [diagnostic("reference_unavailable", "Incremental measurements need the selected accepted reference", severity="warning", stage="observation")]}


def _curve_rows(rows: list, selection: Any, label: str) -> list[dict]:
    if not isinstance(rows, list):
        raise _fail("curve_shape", f"{label} curve must be a list")
    if selection is not None:
        if not isinstance(selection, (list, tuple)) or len(selection) != 2 or any(isinstance(i, bool) or not isinstance(i, int) for i in selection):
            raise _fail("curve_branch", "Branch selection is an inclusive index pair")
        a, b = selection
        if not 0 <= a < b < len(rows):
            raise _fail("curve_branch", "Branch selection is outside data")
        rows = rows[a:b+1]
    result = []
    for index, row in enumerate(rows):
        if not isinstance(row, Mapping):
            raise _fail("curve_row", "Curve rows must be objects")
        if "curve" in row:
            row = row["curve"]
        w = row.get("w")
        force = row.get("P")
        if w is None or force is None or row.get("valid") is False:
            result.append({"w": None, "P": None, "channels": {}})
            continue
        result.append({**deepcopy(row), "w": _number(w, f"{label}[{index}].w"), "P": _number(force, f"{label}[{index}].P")})
    valid = [row for row in result if row["w"] is not None]
    if len(valid) < 2:
        raise _fail("curve_points", f"{label} needs at least two valid points")
    for previous, current in zip(valid, valid[1:]):
        if current["w"] < previous["w"] or (current["w"] == previous["w"] and current["P"] != previous["P"]):
            raise _fail("nonmonotonic_curve", f"{label} is not a single increasing displacement branch; select a branch explicitly")
    return result


def _segments(rows: list[dict], key: str = "P") -> list[tuple[float, float, float, float]]:
    segments = []
    for a, b in zip(rows, rows[1:]):
        va = a.get(key) if key == "P" else a.get("channels", {}).get(key)
        vb = b.get(key) if key == "P" else b.get("channels", {}).get(key)
        if a["w"] is not None and b["w"] is not None and va is not None and vb is not None and b["w"] > a["w"]:
            segments.append((a["w"], b["w"], _number(va, key), _number(vb, key)))
    return segments


def _interpolate(segments: list, x: float) -> float | None:
    if not segments:
        return None
    index = bisect.bisect_right([segment[0] for segment in segments], x) - 1
    if index >= 0:
        a, b, va, vb = segments[index]
        if a - 1e-12 <= x <= b + 1e-12:
            return va + (vb - va) * (x - a) / (b - a)
    return None


def _common_intervals(left: list, right: list, interval: tuple[float, float]) -> list[tuple[float, float]]:
    output, i, j = [], 0, 0
    while i < len(left) and j < len(right):
        a, b = max(left[i][0], right[j][0], interval[0]), min(left[i][1], right[j][1], interval[1])
        if b > a:
            if output and abs(output[-1][1] - a) <= 1e-12:
                output[-1] = (output[-1][0], b)
            else:
                output.append((a, b))
        if left[i][1] <= right[j][1]:
            i += 1
        else:
            j += 1
    return output


def _sample_intervals(intervals: list, count: int) -> list[float]:
    length = sum(b - a for a, b in intervals)
    samples, offset, index = [], 0., 0
    for i in range(count):
        distance = length * i / (count - 1)
        while index + 1 < len(intervals) and distance > offset + intervals[index][1] - intervals[index][0]:
            offset += intervals[index][1] - intervals[index][0]
            index += 1
        samples.append(min(intervals[index][1], intervals[index][0] + distance - offset))
    return samples


def _fit_stiffness(rows: list, interval: Sequence) -> float | None:
    points = [(row["w"], row["P"]) for row in rows if row["w"] is not None and interval[0] <= row["w"] <= interval[1]]
    if len(points) < 2:
        return None
    xm, ym = (sum(item[i] for item in points) / len(points) for i in (0, 1))
    denominator = sum((x - xm)**2 for x, _ in points)
    return sum((x - xm)*(y - ym) for x, y in points) / denominator if denominator else None


def _event_metrics(simulation: list, experiment: Mapping, options: Mapping, intervals: list, wscale: float) -> dict:
    sim_events = [event for row in simulation for event in row.get("events", [])]
    actual = experiment.get("events", {})
    if isinstance(actual, list):
        actual = {event.get("kind"): event for event in actual}
    result = {}
    for kind, p_limit, w_limit in (("first_crack", .25, None), ("first_yield", .15, .20), ("reference_compression", .15, .20)):
        observed = actual.get(kind) if isinstance(actual, Mapping) else None
        predicted = next((event for event in sim_events if event.get("kind") == kind), None)
        item = {"status": "PENDING_EVIDENCE", "P_error": None, "w_error": None, "P_limit": p_limit, "w_limit": w_limit}
        if not isinstance(observed, Mapping) or predicted is None:
            result[kind] = item
            continue
        definition = options.get("event_definitions", {}).get(kind)
        if not observed.get("evidence") or not definition or observed.get("definition") != definition:
            item["reason"] = "Comparable event definition and evidence are required"
            result[kind] = item
            continue
        covered, errors = True, {}
        for key, predicted_key in (("P", "P"), ("w", "control_displacement")):
            value = observed.get(key)
            prediction = predicted.get(predicted_key, predicted.get(key))
            if value is None or prediction is None:
                covered = False
                continue
            bounds = value if isinstance(value, list) else [value, value]
            if len(bounds) != 2:
                raise _fail("event_interval", "Event interval must contain two endpoints")
            lo, hi = [_number(v, f"event.{key}") for v in bounds]
            if hi < lo:
                raise _fail("event_interval", "Event interval endpoints must be ordered")
            if hi > lo:
                bound = options.get("event_interval_limits", {}).get(kind, {}).get(key)
                if bound is None or hi - lo > _number(bound, "event_interval_limit"):
                    covered = False
                    item["reason"] = "Event uncertainty needs a frozen, sufficiently narrow interval limit"
            if key == "w":
                covered &= any(a <= lo and hi <= b for a, b in intervals)
                scale = wscale
            else:
                scale = max(abs(lo), abs(hi))
            prediction = _number(prediction, f"prediction.{key}")
            errors[key] = max(lo - prediction, 0., prediction - hi) / scale if scale > 0 else None
            item[f"{key}_interval_width"] = hi - lo
        item.update(P_error=errors.get("P"), w_error=errors.get("w"))
        if covered and errors.get("P") is not None and (w_limit is None or errors.get("w") is not None):
            item["status"] = "PASS" if errors["P"] <= p_limit and (w_limit is None or errors["w"] <= w_limit) else "FAIL"
        result[kind] = item
    return result


def compare_curves(simulation: list[dict], experiment: dict, options: dict | None = None) -> dict:
    """Compute frozen-domain metrics with explicit coverage and honest evidence status."""
    options = deepcopy(options or {})
    validate_finite(options)
    if not isinstance(experiment, dict):
        raise _fail("experiment_shape", "Experiment must be an imported dataset")
    synthetic = bool(experiment.get("synthetic") or experiment.get("source", {}).get("category") == "synthetic")
    base = {"version": "curve-comparison-v1", "synthetic": synthetic,
            "case_role": "calibration" if experiment.get("calibration_case") else "independent_candidate",
            "options": options, "dataset_id": experiment.get("id"), "source": deepcopy(experiment.get("source")),
            "physical_validation": "UNVERIFIED_SYNTHETIC_DATA" if synthetic else "UNVERIFIED",
            "diagnostics": [], "thresholds": {"NRMSE": .15, "Emax": .25, "coverage": .95,
                                                "maximum_P": .10, "initial_stiffness": .15, "gauge_NRMSE": .20}}
    try:
        sim = _curve_rows(simulation, options.get("simulation_rows"), "Simulation")
        exp = _curve_rows(experiment.get("rows", []), options.get("experiment_rows"), "Experiment")
    except ContractError as exc:
        return {**base, "status": "UNALIGNED", "coverage": 0., "NRMSE": None, "Emax": None,
                "diagnostics": [exc.diagnostic]}
    exp_valid = [row for row in exp if row["w"] is not None]
    interval = options.get("experimental_interval", experiment.get("valid_interval"))
    if interval is None:
        interval = [exp_valid[0]["w"], exp_valid[-1]["w"]]
    if not isinstance(interval, (list, tuple)) or len(interval) != 2:
        raise _fail("comparison_interval", "Freeze a two-endpoint experimental interval")
    lower, upper = [_number(v, "experimental_interval") for v in interval]
    if not exp_valid[0]["w"] <= lower < upper <= exp_valid[-1]["w"]:
        raise _fail("comparison_interval", "Experimental interval must lie in recorded data")
    scale_points = [abs(row["P"]) for row in exp_valid if lower <= row["w"] <= upper]
    exp_segments, sim_segments = _segments(exp), _segments(sim)
    scale_points += [abs(value) for x in (lower, upper) if (value := _interpolate(exp_segments, x)) is not None]
    pscale = max(scale_points, default=0.)
    if pscale <= 0:
        raise _fail("force_scale", "Experimental interval needs a nonzero force scale")
    intervals = _common_intervals(sim_segments, exp_segments, (lower, upper))
    coverage = sum(b - a for a, b in intervals) / (upper - lower)
    base.update(experimental_interval=[lower, upper], common_intervals=[list(item) for item in intervals],
                coverage=coverage, Pscale=pscale, wscale=upper-lower)
    if not intervals:
        return {**base, "status": "INCOMPLETE_PATH", "NRMSE": None, "Emax": None, "samples": []}
    samples = _sample_intervals(intervals, 200)
    triples = [(x, _interpolate(sim_segments, x), _interpolate(exp_segments, x)) for x in samples]
    errors = [sim_value-exp_value for _, sim_value, exp_value in triples]
    nrmse = math.sqrt(sum(error*error for error in errors) / len(errors)) / pscale
    emax = max(abs(error) for error in errors) / pscale
    event_metrics = _event_metrics(sim, experiment, options, intervals, upper-lower)
    known_events = experiment.get("events", {})
    if isinstance(known_events, list):
        known_events = {event.get("kind"): event for event in known_events}
    known_events_covered = all(item["status"] != "PENDING_EVIDENCE" for kind, item in event_metrics.items()
                               if kind in known_events)
    complete = coverage >= .95 and known_events_covered
    numerical_pass = nrmse <= .15 and emax <= .25 and complete
    base.update(NRMSE=nrmse, Emax=emax, sample_count=200,
                samples=[{"w": x, "P_sim": a, "P_exp": b, "error": a-b} for x, a, b in triples],
                curve_status="PASS" if numerical_pass else "FAIL", events=event_metrics)
    max_sim = max((row["P"] for row in sim if row["w"] is not None), default=None)
    max_exp = max((row["P"] for row in exp_valid), default=None)
    max_error = abs(max_sim-max_exp)/abs(max_exp) if max_exp else None
    base["maximum_P"] = {"simulation": max_sim, "experiment": max_exp, "relative_error": max_error,
                          "limit": .10, "status": "PENDING_COMPLETE_PATH" if not complete else "PASS" if max_error is not None and max_error <= .10 else "FAIL"}
    stiffness = {"status": "PENDING_UNCRACKED_EVIDENCE", "simulation": None, "experiment": None, "relative_error": None, "limit": .15}
    if options.get("uncracked_evidence") and options.get("stiffness_interval"):
        selected = options["stiffness_interval"]
        if not isinstance(selected, (list, tuple)) or len(selected) != 2 or not lower <= selected[0] < selected[1] <= upper:
            raise _fail("stiffness_interval", "Stiffness interval must be within the frozen comparison domain")
        ks, ke = _fit_stiffness(sim, selected), _fit_stiffness(exp, selected)
        error = abs(ks-ke)/abs(ke) if ks is not None and ke else None
        stiffness.update(simulation=ks, experiment=ke, relative_error=error,
                         status="PENDING_POINTS" if error is None else "PASS" if error <= .15 else "FAIL", interval=list(selected))
    base["initial_stiffness"] = stiffness
    channel_metrics = {}
    for channel, scale in options.get("channel_scales", {}).items():
        scale = _number(scale, "channel_scale")
        noise = _number(options.get("noise_scales", {}).get(channel, 0.), "noise_scale")
        if scale <= 0 or scale < noise or noise < 0:
            raise _fail("channel_scale", "Channel scale must be positive and no smaller than its frozen noise scale")
        sa, sb = _segments(sim, channel), _segments(exp, channel)
        ranges = _common_intervals(sa, sb, (lower, upper))
        fraction = sum(b-a for a, b in ranges)/(upper-lower)
        if not ranges:
            channel_metrics[channel] = {"status": "PENDING_DATA", "coverage": 0.}
            continue
        differences = [_interpolate(sa, x)-_interpolate(sb, x) for x in _sample_intervals(ranges, 200)]
        normalized = math.sqrt(sum(value*value for value in differences)/len(differences))/scale
        channel_metrics[channel] = {"NRMSE": normalized, "maximum_absolute_error": max(map(abs, differences)),
                                    "coverage": fraction, "scale": scale, "noise_scale": noise, "limit": .20,
                                    "status": "PASS" if fraction >= .95 and normalized <= .20 else "FAIL"}
    base["channels"] = channel_metrics
    base["gauge_evidence_status"] = "AVAILABLE" if channel_metrics else "PENDING_GAUGE_DATA"
    base["status"] = "COMPARED" if complete else "INCOMPLETE_PATH"
    evidence_statuses = [base["curve_status"], base["maximum_P"]["status"], stiffness["status"]]
    evidence_statuses += [item["status"] for item in event_metrics.values()]
    evidence_statuses += [item["status"] for item in channel_metrics.values()]
    if not channel_metrics:
        evidence_statuses.append("PENDING_GAUGE_DATA")
    if not synthetic:
        if not experiment.get("alignment_confirmed"):
            base["physical_validation"] = "UNALIGNED"
        elif "FAIL" in evidence_statuses:
            base["physical_validation"] = "FAIL"
        elif complete and all(status == "PASS" for status in evidence_statuses):
            base["physical_validation"] = "PASS_CALIBRATION_CASE" if experiment.get("calibration_case") else "PASS_COMPARISON_CASE"
        else:
            base["physical_validation"] = "PENDING_EVIDENCE"
    return base


def _csv_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return format(_number(value, "export"), ".17g")
    if isinstance(value, (dict, list, tuple)):
        validate_finite(value)
        return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    if isinstance(value, (int, str)):
        return str(value)
    raise _fail("export_type", "CSV supports JSON-compatible values only")


def export_csv(path: str, rows: list[dict], metadata: dict) -> None:
    """Export roundtrip-precision rows plus a lossless provenance sidecar, without overwrites."""
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows) or not isinstance(metadata, dict):
        raise _fail("export_shape", "Export requires object rows and metadata")
    validate_finite(rows)
    validate_finite(metadata)
    target = Path(path)
    sidecar = target.with_suffix(".metadata.json")
    sources = metadata.get("source_paths", [])
    if metadata.get("source_path"):
        sources = [*sources, metadata["source_path"]]
    if any(target.resolve() == Path(source).resolve() or sidecar.resolve() == Path(source).resolve() for source in sources):
        raise _fail("export_source", "Export cannot overwrite an original source")
    if target.exists() or sidecar.exists():
        raise _fail("export_exists", "Choose a new export destination to preserve existing files")
    fields = list(dict.fromkeys(key for row in rows for key in row))
    if not fields:
        raise _fail("export_empty", "No columns to export")
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow({key: _csv_value(row.get(key)) for key in fields})
    raw = buffer.getvalue().encode("utf-8-sig")
    descriptor = {"schema_version": 1, "encoding": "utf-8-sig", "delimiter": ",", "row_count": len(rows),
                  "columns": fields, "float_encoding": "17-significant-digit-roundtrip", "csv_sha256": hashlib.sha256(raw).hexdigest(),
                  "metadata": deepcopy(metadata), "synthetic": bool(metadata.get("synthetic")),
                  "category": "synthetic" if metadata.get("synthetic") else "numerical_export"}
    created = []
    try:
        with target.open("xb") as stream:
            created.append(target)
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        with sidecar.open("x", encoding="utf-8", newline="\n") as stream:
            created.append(sidecar)
            json.dump(descriptor, stream, ensure_ascii=False, allow_nan=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
    except (OSError, ValueError) as exc:
        for created_path in created:
            try:
                created_path.unlink()
            except OSError:
                pass
        raise _fail("export_write", f"CSV export failed: {exc}") from exc
