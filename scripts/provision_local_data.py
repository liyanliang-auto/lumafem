"""Provision local case inputs and explicitly synthetic comparison channels.

Private case numbers live in an ignored seed JSON, never in this source file.
The default CR-NUM-002 profile derives a fresh revision from the existing local
runtime/NC0.json and writes only runtime/CR-NUM-002. Use --profile legacy for the
original copy-only workflow. Existing inputs are protected unless an output
overwrite is explicitly selected; the CR profile never replaces its seed.
Synthetic channels are neither independent FE results nor physical validation.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import csv
import hashlib
import json
import math
import os
from pathlib import Path
import sys
import tempfile

_ROOT = Path(__file__).resolve().parents[1]
# Share transport settings with the desktop without importing Qt or a solver.
sys.path.insert(0, str(_ROOT / "python"))
from lumafem.contracts import (CR_NUM_001_ANALYSIS_SETTINGS, CR_NUM_001_NUMERICS_CONTRACT,
                               CR_NUM_002_COMPRESSION_CRITERION, DEFAULT_LENGTH_MAPPING, NUMERICS_CONTRACT)


def revised_document(document: dict, seed_sha256: str) -> dict:
    """Derive an explicit numerical revision without changing physical inputs."""
    result = deepcopy(document)
    result.pop("identity", None)
    result.pop("view_state", None)
    result["project_id"] = str(document["project_id"]) + "-" + CR_NUM_001_NUMERICS_CONTRACT
    result["revision_id"] = str(document["revision_id"]) + "-" + CR_NUM_001_NUMERICS_CONTRACT
    result["name"] = str(document.get("name", "NC0")) + " / " + CR_NUM_001_NUMERICS_CONTRACT
    model = result["model"]
    model.setdefault("discretization", {})["length_mapping"] = DEFAULT_LENGTH_MAPPING
    model.setdefault("analysis", {}).update(deepcopy(CR_NUM_001_ANALYSIS_SETTINGS))
    result.setdefault("sources", []).append({
        "category": "assumption", "version": CR_NUM_001_NUMERICS_CONTRACT,
        "reference": "SPEC 1.1 section 15 / CR-NUM-001 controlled numerical revision",
        "quality": "Corrected element-length candidate and onset-aware Newton settings; numerical acceptance must be established separately."
    })
    result["numerical_revision"] = {"id": CR_NUM_001_NUMERICS_CONTRACT,
                                    "source_project_id": document["project_id"],
                                    "source_revision_id": document["revision_id"],
                                    "source_document_sha256": seed_sha256,
                                    "physical_parameters_changed": False}
    return result

def physical_document(document: dict, seed_sha256: str) -> dict:
    """Explicitly select a new mechanical criterion; no geometry/material fitting."""
    result = revised_document(document, seed_sha256)
    result["project_id"] = str(document["project_id"]) + "-" + NUMERICS_CONTRACT
    result["revision_id"] = str(document["revision_id"]) + "-" + NUMERICS_CONTRACT
    result["name"] = str(document.get("name", "NC0")) + " / " + NUMERICS_CONTRACT
    result["model"]["compression_criterion"] = deepcopy(CR_NUM_002_COMPRESSION_CRITERION)
    result["sources"][-1] = {
        "category": "assumption", "version": NUMERICS_CONTRACT,
        "reference": "SPEC 1.2 section 16 / CR-NUM-002 controlled criterion revision",
        "quality": "Assistant modeling assumption: whole-beam both-edge total signed compatible strain mean; includes gravity; uncalibrated, not measured or numerically validated."
    }
    result["numerical_revision"].update({
        "id": NUMERICS_CONTRACT, "physical_parameters_changed": True,
        "physical_criterion_definition_changed": True,
        "geometry_material_load_parameters_changed": False,
        "criterion_status": "uncalibrated_assistant_modeling_assumption"})
    return result


def _finite_positive(value: object, label: str) -> float:
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise ValueError(f"{label} must be finite and positive")
    return number


def _read_document(path: Path) -> dict:
    if path.stat().st_size > 16 * 1024 * 1024:
        raise ValueError("Case seed exceeds the 16 MiB input limit")
    with path.open("r", encoding="utf-8-sig") as stream:
        document = json.load(stream, parse_constant=lambda text: (_ for _ in ()).throw(ValueError(f"Invalid number {text}")))
    if not isinstance(document, dict) or document.get("schema_version") != 1:
        raise ValueError("Case seed must be a wire schema 1 document")
    for key in ("project_id", "revision_id", "model", "materials", "observations", "sources"):
        if key not in document:
            raise ValueError(f"Case seed is missing {key}")
    if not isinstance(document["model"], dict) or not isinstance(document["materials"], list):
        raise ValueError("Case model and material containers are invalid")
    geometry = document["model"]["geometry"]
    for field in ("length", "width", "height"):
        _finite_positive(geometry[field], field)
    return document


def _atomic_text(path: Path, text: str, overwrite: bool) -> None:
    if path.exists() and not overwrite:
        raise FileExistsError(f"Refusing to replace existing file: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: str | None = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="", dir=path.parent, delete=False) as stream:
            temporary = stream.name
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        if path.exists() and not overwrite:
            raise FileExistsError(f"Destination appeared during provisioning: {path}")
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            Path(temporary).unlink(missing_ok=True)


def provision(seed: Path, destination: Path, overwrite: bool = False, *, profile: str = "legacy") -> dict:
    """Copy or explicitly revise a private seed, preserving all prior inputs."""
    if profile not in ("legacy", "cr-num-001", "cr-num-002"):
        raise ValueError("Unknown provisioning profile")
    output_seed = destination / "NC0.json"
    runtime = (_ROOT / "development_doc" / "runtime").resolve()
    if profile != "legacy" and (output_seed.resolve() == seed.resolve() or destination.resolve() == runtime or
                               (profile == "cr-num-002" and destination.resolve() == runtime / "CR-NUM-001")):
        raise ValueError("A revised profile cannot replace its seed or a protected historical runtime directory")
    if profile == "cr-num-002" and overwrite:
        raise ValueError("CR-NUM-002 provisioning refuses overwrites; choose a new output directory")
    document = _read_document(seed)
    source_sha256 = hashlib.sha256(seed.read_bytes()).hexdigest()
    if profile == "cr-num-001":
        document = revised_document(document, source_sha256)
    elif profile == "cr-num-002":
        document = physical_document(document, source_sha256)
    model = document["model"]
    geometry = model["geometry"]
    length = _finite_positive(geometry["length"], "length")
    height = _finite_positive(geometry["height"], "height")
    supports = sorted(float(item["x"]) for item in model["supports"] if item["constrained"][1])
    if len(supports) < 2 or supports[-1] <= supports[0]:
        raise ValueError("Synthetic two-point demo requires two separated vertical supports")
    span = supports[-1] - supports[0]
    jack_positions = sorted(float(item["x"]) for item in model["loads"] if item["reference"][1] != 0)
    if len(jack_positions) != 2:
        raise ValueError("Synthetic demo requires exactly two vertical jack channels")
    lever = min(jack_positions[0] - supports[0], supports[-1] - jack_positions[1])
    lever = _finite_positive(lever, "jack lever arm")
    materials = {item["id"]: item for item in document["materials"]}
    reinforcement = model["reinforcement"]
    if reinforcement.get("mode") == "cover":
        count = int(reinforcement["bottom_count"])
        diameter = _finite_positive(reinforcement["bottom_diameter"], "bottom diameter")
        area = count * math.pi * diameter * diameter / 4
        bottom_eta = -height / 2 + float(reinforcement["cover"]) + float(reinforcement["stirrup_diameter"]) + diameter / 2
        fy = _finite_positive(materials[reinforcement["bottom_material_id"]]["fy"], "bottom yield")
    else:
        bars = [item for item in reinforcement.get("bars", []) if float(item["eta"]) < 0]
        area = sum(_finite_positive(item.get("area") or math.pi * float(item["diameter"]) ** 2 / 4, "bar area") for item in bars)
        if not bars:
            raise ValueError("Synthetic demo requires lower longitudinal steel")
        bottom_eta = sum(float(item["eta"]) for item in bars) / len(bars)
        fy = sum(_finite_positive(materials[item["material_id"]]["fy"], "bar yield") for item in bars) / len(bars)
    nominal_force = _finite_positive(2 * area * fy * (height / 2 - bottom_eta) / lever, "synthetic force scale")
    # A transparent synthetic response generator, deliberately separate from FE.
    maximum_w = min(float(model.get("analysis", {}).get("maximum_control_displacement", span / 40)), span / 50)
    maximum_w = _finite_positive(maximum_w, "synthetic displacement range")
    characteristic_w = maximum_w / 5
    rows: list[list[float]] = []
    for index in range(301):
        fraction = index / 300
        w = maximum_w * fraction
        p = nominal_force * (1 - math.exp(-w / characteristic_w)) * (1 - 0.08 * fraction)
        split = 0.5 + 0.005 * math.sin(2 * math.pi * fraction)
        left_support = 0.02 * w
        right_support = 0.022 * w
        middle_raw = w + 0.5 * (left_support + right_support)
        top_strain = -6 * w * height / (span * span)
        rows.append([float(index), p * split / 1000, p * (1 - split) / 1000, p / 1000,
                     middle_raw, left_support, right_support, top_strain * 1e6])
    destination.mkdir(parents=True, exist_ok=True)
    document_text = json.dumps(document, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    import io
    stream = io.StringIO(newline="")
    writer = csv.writer(stream, lineterminator="\n")
    writer.writerow(["time_s", "jack1_kN", "jack2_kN", "total_kN", "midpoint_mm", "left_support_mm", "right_support_mm", "top_gauge_microstrain"])
    writer.writerows([[format(value, ".17g") for value in row] for row in rows])
    csv_text = stream.getvalue()
    metadata = {
        "schema_version": 1, "synthetic": True, "category": "synthetic",
        "name": "SYNTHETIC channel-mapping demonstration",
        "physical_validation": "UNVERIFIED_SYNTHETIC_DATA",
        "warning": "Not measured NC0 data, not an independent numerical reference, and never evidence of physical validation.",
        "generator": "lumafem-synthetic-channels-v1",
        "generator_formula": "P=nominal_force*(1-exp(-w/characteristic_w))*(1-0.08*w/maximum_w); jack split=0.5+0.005*sin(2*pi*w/maximum_w)",
        "parameters": {"nominal_force_N": nominal_force, "maximum_w_mm": maximum_w, "characteristic_w_mm": characteristic_w,
                       "points": len(rows), "force_scale_source": "rough rebar moment/lever estimate; software demo assumption"},
        "seed_sha256": (hashlib.sha256(document_text.encode("utf-8")).hexdigest()
                        if seed.resolve() != output_seed.resolve() else source_sha256),
        "csv_sha256": hashlib.sha256(csv_text.encode("utf-8")).hexdigest(),
        "columns": {"time_s": "s", "jack1_kN": "kN", "jack2_kN": "kN", "total_kN": "kN", "midpoint_mm": "mm",
                    "left_support_mm": "mm", "right_support_mm": "mm", "top_gauge_microstrain": "microstrain"},
        "signs": {"load": "compression/downward positive", "deflection": "downward positive", "strain": "tension positive"},
        "processing": {"zero_row": 0, "settlement_correction": "midpoint_mm - (left_support_mm + right_support_mm)/2",
                       "total_force": "jack1_kN + jack2_kN; total_kN is a separately retained consistency channel",
                       "synchronization": "all rows share time_s; no sorting, fitting, or peak normalization"},
        "observation_note": "Synthetic top strain is a heuristic channel and is not route-C reference compression or actual DIC."
    }
    if profile != "legacy":
        metadata["numerical_revision"] = (CR_NUM_001_NUMERICS_CONTRACT if profile == "cr-num-001" else NUMERICS_CONTRACT)
        metadata["source_seed_sha256"] = source_sha256
    # Preflight the complete output set before writing, preserving existing data.
    outputs = [destination / "synthetic_channels.csv", destination / "synthetic_channels.metadata.json"]
    if seed.resolve() != output_seed.resolve():
        outputs.append(output_seed)
    for output in outputs:
        if output.exists() and not overwrite:
            raise FileExistsError(f"Refusing to replace existing file: {output}")
    if seed.resolve() != output_seed.resolve():
        _atomic_text(output_seed, document_text, overwrite)
    _atomic_text(destination / "synthetic_channels.csv", csv_text, overwrite)
    _atomic_text(destination / "synthetic_channels.metadata.json", json.dumps(metadata, indent=2, allow_nan=False) + "\n", overwrite)
    return {"document": str(output_seed), "csv": str(destination / "synthetic_channels.csv"),
            "synthetic": True, "profile": profile}


def main() -> int:
    """Command entry point; all generated data remains in the local directory."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=Path, default=_ROOT / "development_doc" / "runtime" / "NC0.json")
    parser.add_argument("--profile", choices=("legacy", "cr-num-001", "cr-num-002"), default="cr-num-002")
    parser.add_argument("--output-dir", type=Path, help="Default is the selected profile's local runtime directory")
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()
    try:
        destination = args.output_dir or (_ROOT / "development_doc" / "runtime" /
                                         ({"cr-num-001": "CR-NUM-001", "cr-num-002": "CR-NUM-002"}.get(args.profile, "")))
        result = provision(args.seed, destination, args.overwrite, profile=args.profile)
    except (OSError, ValueError, TypeError, KeyError) as error:
        parser.exit(1, f"Local data provisioning failed: {error}\n")
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
