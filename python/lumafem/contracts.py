"""Versioned wire vocabulary shared by native, persistence and Qt adapters.

These dictionaries are transport values, never native constitutive history owners.
Numeric units are N, mm, MPa and rad. Optional unavailable quantities use None.
"""
from __future__ import annotations

from copy import deepcopy
from math import isfinite
from typing import Any, Mapping
from uuid import uuid4

SCHEMA_VERSION = 1
MATERIAL_VERSION = 1
ARRAY_ENCODING = "float64-le"
NUMERICS_CONTRACT = "CR-NUM-002"
KERNEL_VERSION = "0.3.1-cr-num-002-a05"
READER_CAPABILITY = 3
# Frozen identities remain separate so an explicit old profile is never renamed.
CR_NUM_001_NUMERICS_CONTRACT = "CR-NUM-001"
CR_NUM_001_KERNEL_VERSION = "0.2.0-cr-num-001"
CR_NUM_001_A03_KERNEL_VERSION = "0.2.1-cr-num-001-a03"
CR_NUM_001_A04_KERNEL_VERSION = "0.2.2-cr-num-001-a04"
CR_NUM_002_KERNEL_VERSION = "0.3.0-cr-num-002"
ANALYSIS_READ_ONLY_PRODUCERS = (CR_NUM_001_KERNEL_VERSION,
                               CR_NUM_001_A03_KERNEL_VERSION, CR_NUM_001_A04_KERNEL_VERSION,
                               CR_NUM_002_KERNEL_VERSION)
COMPRESSION_CRITERION_MODES = ("route_c_point_v1", "physical_gauge_mean_v1")
PHYSICAL_GAUGE_OPERATOR_VERSION = "eb_compatible_edge_window_v1"
PHYSICAL_GAUGE_REFERENCE = "total_from_S0"
PHYSICAL_GAUGE_ROUNDOFF_POLICY = "bounded_affine_root_v1"
COMPRESSION_CRITERION_KEYS = ("mode", "gauge_length_mm", "threshold", "source")
PREPARED_COMPRESSION_CRITERION_KEYS = COMPRESSION_CRITERION_KEYS + (
    "operator_version", "reference", "search_domain_mm", "edges", "roundoff_policy")
# Explicit fresh-template opt-in only. Never merge this into a loaded document.
CR_NUM_002_COMPRESSION_CRITERION = {
    "mode": "physical_gauge_mean_v1", "gauge_length_mm": 100.0, "threshold": 0.0035,
    "source": {"category": "assumption", "reference":
               "Assistant modeling assumption: whole-beam both-edge total compatible strain window; not experimental calibration",
               "quality": "uncalibrated_assistant_modeling_assumption", "version": "CR-NUM-002"},
}
LEGACY_PREDICTOR_POLICY_ID = "accepted_secant_critical_mode_v1"
PREDICTOR_POLICY_ID = "accepted_secant_onset_seeded_probe_subspace_v3"
# Producer-scoped aliases; A03 and A04 policy shapes must never be interchanged.
CR_NUM_001_A03_PREDICTOR_POLICY_ID = LEGACY_PREDICTOR_POLICY_ID
CR_NUM_001_A04_PREDICTOR_POLICY_ID = "accepted_secant_critical_subspace_v2"
LEGACY_PREDICTOR_EVIDENCE_KEYS = ("total_newton_evaluations", "predictor_attempts",
                           "predictor_newton_evaluations", "predictor_seed_factor",
                           "predictor_kind", "predictor_control_advance", "predictor_amplitude",
                           "predictor_decompositions", "predictor_linear_solves")
PREDICTOR_EVIDENCE_KEYS = LEGACY_PREDICTOR_EVIDENCE_KEYS + ("predictor_mode_index",)
LEGACY_LENGTH_MAPPING = "gauss_weight_v1"
DEFAULT_LENGTH_MAPPING = "element_length_v2"
SUPPORTED_LENGTH_MAPPINGS = (LEGACY_LENGTH_MAPPING, DEFAULT_LENGTH_MAPPING)
# Absent additive settings in an old schema-1 document keep the old algorithm.
REVISED_ANALYSIS_DEFAULTS = {
    "line_search_enabled": False,
    "maximum_line_search_trials": 12,
    "line_search_reduction": 0.5,
    "line_search_sufficient_decrease": 1e-4,
    "minimum_line_search_factor": 1e-6,
    "transition_resolution_enabled": False,
    "maximum_transition_solves": 40,
    "transition_displacement_tolerance": 1e-6,
    "transition_ratio_tolerance": 1e-4,
    "transition_minimum_step": 1e-7,
}
# New engineering templates opt in explicitly; these values never migrate old results.
CR_NUM_001_ANALYSIS_SETTINGS = {
    **REVISED_ANALYSIS_DEFAULTS,
    "line_search_enabled": True,
    "transition_resolution_enabled": True,
    "maximum_iterations": 60,
    "maximum_halvings": 18,
}
CONVERGENCE_SEARCH_KEYS = ("line_search_evaluations", "line_search_backtracks",
                           "last_correction_factor", "minimum_correction_factor")
TRANSITION_KINDS = ("tensile_onset", "compression_peak")
TRANSITION_KEYS = ("active", "kind", "point_id", "threshold", "candidate_solves",
                   "lower_control", "upper_control", "lower_ratio", "upper_ratio",
                   "lower_converged", "upper_converged", "lower_evidence", "upper_evidence")
UNITS = {"length": "mm", "force": "N", "stress": "MPa", "rotation": "rad",
         "curvature": "1/mm", "strain": "1", "moment": "N.mm"}
IDENTITY_KEYS = ("project_id", "revision_id", "snapshot_id", "run_id", "step_id")
DOCUMENT_KEYS = ("schema_version", "project_id", "revision_id", "name", "model",
                 "materials", "observations", "sources")
PREPARED_KEYS = ("schema_version", "identity", "document", "nodes", "elements",
                 "sections", "diagnostics", "prepared_hash")
STEP_KEYS = ("identity", "sequence", "phase", "load_factor", "P", "P1", "P2",
             "gravity_factor", "displacement", "reactions", "element_forces",
             "sections", "fibers", "edges", "events", "convergence", "observations")
# STEP_KEYS stays the required legacy shape; old saved outputs have no onset evidence.
REVISED_STEP_KEYS = STEP_KEYS + ("transition",)
# Required new physical-mode shape; legacy STEP_KEYS/REVISED_STEP_KEYS stay frozen.
PHYSICAL_GAUGE_STEP_KEYS = REVISED_STEP_KEYS + ("control_displacement", "external_force", "localization", "gauge")
GAUGE_WINDOW_KEYS = ("mode", "operator_version", "reference", "gauge_length_mm",
                     "threshold", "search_domain_mm", "side", "eta", "x1", "x2",
                     "signed_mean", "compression_maximum", "status", "identity", "phase",
                     "control_displacement", "load_factor", "gravity_factor")
GAUGE_STATE_KEYS = ("current", "historical")
GAUGE_STATUSES = ("current", "accepted_historical")
PHYSICAL_LOCALIZATION_KEYS = ("kind", "control_unit", "lower_gravity_factor",
                              "upper_gravity_factor", "bracket_width", "upper_gauge")
PHYSICAL_LOCALIZATION_KINDS = ("bracket", "exact_crossing")
EVENT_KINDS = ("first_crack", "first_yield", "reference_compression", "path_maximum",
               "physical_gauge_compression")
SUMMARY_KEYS = ("identity", "termination", "memory_last_step", "persisted_last_step",
                "physical_evidence", "diagnostics", "validation_status")
PHASES = ("S0", "G", "SG", "J")
TERMINATIONS = ("reference_compression", "safety_limit", "non_convergence", "linear_failure",
                "localization_failure", "cancelled", "input_error", "storage_error", "internal_error",
                "physical_gauge_compression")
SOURCE_CATEGORIES = ("paper", "assumption", "calibrated", "measured", "synthetic", "derived")
ARRAY_FIELDS = {
    "displacement": {"unit": "mixed:mm,mm,rad", "location": "node", "components": ["ux", "uy", "theta"]},
    "reactions": {"unit": "mixed:N,N,N.mm", "location": "node", "components": ["Fx", "Fy", "Mz"]},
    "external_force": {"unit": "mixed:N,N,N.mm", "location": "node", "components": ["Fx", "Fy", "Mz"]},
}


class ContractError(ValueError):
    """A structured boundary error, convertible to UI diagnostics without parsing text."""

    def __init__(self, code: str, message: str, *, stage: str = "format",
                 object_id: str = "", field: str = "") -> None:
        super().__init__(message)
        self.diagnostic = diagnostic(code, message, stage=stage, object_id=object_id, field=field)


def diagnostic(code: str, message: str, *, severity: str = "error", stage: str = "format",
               object_id: str = "", field: str = "") -> dict[str, Any]:
    """Construct a portable located diagnostic."""
    return {"code": code, "severity": severity, "stage": stage,
            "object_id": object_id, "field": field, "message": message}


def new_id(prefix: str = "") -> str:
    """Create a non-positional identity; caller-selected prefix has no numeric semantics."""
    return f"{prefix}-{uuid4().hex}" if prefix else uuid4().hex


def copy_value(value: Any) -> Any:
    """Return a value-owned transport snapshot for cross-thread publication."""
    return deepcopy(value)


def require_keys(value: Mapping[str, Any], keys: tuple[str, ...], *, stage: str = "format") -> None:
    """Reject absent structural keys instead of substituting numerical zeros."""
    if not isinstance(value, Mapping):
        raise ContractError("invalid_object", "Expected an object", stage=stage)
    for key in keys:
        if key not in value:
            raise ContractError("missing_field", f"Missing required field: {key}", stage=stage, field=key)


def validate_finite(value: Any, *, field: str = "") -> None:
    """Reject nonfinite JSON numbers recursively; None is explicit unavailability."""
    if isinstance(value, float) and not isfinite(value):
        raise ContractError("nonfinite", "Nonfinite numeric value", field=field)
    if isinstance(value, dict):
        for key, item in value.items():
            validate_finite(item, field=f"{field}.{key}" if field else str(key))
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            validate_finite(item, field=f"{field}[{index}]")


def validate_identity(value: Mapping[str, Any], *, require_step: bool = False) -> None:
    """Require complete immutable result ownership, with step ID only for step payloads."""
    keys = IDENTITY_KEYS if require_step else IDENTITY_KEYS[:-1]
    require_keys(value, keys)
    for key in keys:
        if not isinstance(value[key], str) or not value[key]:
            raise ContractError("invalid_identity", f"Identity {key} must be nonempty text", field=key)


def same_run(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    """Compare message ownership before a controller accepts asynchronous results."""
    return all(left.get(key) == right.get(key) and bool(left.get(key)) for key in IDENTITY_KEYS[:-1])


def validation_status() -> dict[str, str]:
    """A solver invocation cannot self-certify A/B/D or authentic physical validation."""
    return {"A": "UNVERIFIED", "B": "UNVERIFIED", "C": "PENDING_DATA", "D": "UNVERIFIED"}
