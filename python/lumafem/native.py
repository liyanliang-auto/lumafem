"""Value-owned dictionary transport to the real independent C++ kernel.

The extension is required. No Python constitutive or structural-solver fallback
is provided. Optional unavailable physical outputs use None, never false zeros.
"""
from __future__ import annotations

import importlib
import json
from typing import Any, Callable

from .contracts import (ContractError, DOCUMENT_KEYS, PREPARED_KEYS, SCHEMA_VERSION,
                        SUMMARY_KEYS, copy_value, require_keys, validate_finite,
                        validate_identity)


def _extension() -> Any:
    try:
        module = importlib.import_module("_lumafem")
    except (ImportError, OSError) as error:
        raise ContractError("native_unavailable", "The LumaFEM C++ extension could not be loaded. "
                            "Run scripts/bootstrap.ps1 and scripts/build.ps1, then use scripts/launch.ps1. "
                            f"Loader detail: {error}", stage="native_adapter") from error
    if getattr(module, "schema_version", None) != SCHEMA_VERSION:
        raise ContractError("native_version", "The native extension uses an incompatible schema. Rebuild it.",
                            stage="native_adapter")
    return module


def _dump(value: Any) -> str:
    validate_finite(value)
    try:
        return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    except (TypeError, ValueError, OverflowError) as error:
        raise ContractError("invalid_transport", str(error), stage="native_adapter") from error


def _load(payload: str) -> dict[str, Any]:
    def reject_constant(token: str) -> None:
        raise ValueError(f"Invalid JSON numeric constant: {token}")

    try:
        value = json.loads(payload, parse_constant=reject_constant)
    except (TypeError, ValueError, json.JSONDecodeError) as error:
        raise ContractError("invalid_native_json", str(error), stage="native_adapter") from error
    if not isinstance(value, dict):
        raise ContractError("invalid_native_result", "The native result must be an object.", stage="native_adapter")
    validate_finite(value)
    return value


def prepare(document: dict[str, Any]) -> dict[str, Any]:
    """Return preparation diagnostics and a complete frozen input snapshot."""
    require_keys(document, DOCUMENT_KEYS, stage="preparation")
    result = _load(_extension().prepare_json(_dump(copy_value(document))))
    require_keys(result, ("ok", "prepared", "diagnostics"), stage="native_adapter")
    if result["ok"]:
        require_keys(result["prepared"], PREPARED_KEYS, stage="native_adapter")
        validate_identity(result["prepared"]["identity"])
    return result


def verify_prepared(prepared: dict[str, Any], *, for_analysis: bool = False) -> dict[str, Any]:
    """Verify a frozen descriptor without changing it or inventing result evidence.

    Default view mode returns verified old producers with analysis_read_only=True.
    That flag belongs to this wrapper; it is never inserted into the hashed input.
    Invalid descriptors and disallowed analysis return ok=False with diagnostics.
    """
    if not isinstance(for_analysis, bool):
        raise ContractError("invalid_transport", "for_analysis must be a boolean.", stage="native_adapter")
    result = _load(_extension().verify_prepared_json(_dump(copy_value(prepared)), for_analysis))
    require_keys(result, ("ok", "prepared", "analysis_read_only", "diagnostics"), stage="native_adapter")
    return result


def analyze(prepared: dict[str, Any],
            on_step: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
            on_progress: Callable[[dict[str, Any]], None] | None = None,
            cancelled: Callable[[], bool] | None = None) -> dict[str, Any]:
    """Run the native solver; on_step returns an actual complete-step receipt.

    Callbacks execute in the calling worker thread with the GIL acquired. A
    callback exception becomes a structured native conclusion. Omitting
    on_step means memory-only analysis, never a claimed saved transaction.
    """
    require_keys(prepared, PREPARED_KEYS, stage="native_adapter")
    validate_identity(prepared["identity"])
    for callback in (on_step, on_progress, cancelled):
        if callback is not None and not callable(callback):
            raise ContractError("invalid_callback", "Callbacks must be callable or None.", stage="native_adapter")

    def accept(payload: str) -> str:
        step = _load(payload)
        validate_identity(step["identity"], require_step=True)
        receipt = on_step(step)  # type: ignore[misc]
        require_keys(receipt, ("ok", "identity", "persisted_sequence", "transaction_id", "diagnostics"), stage="storage")
        validate_identity(receipt["identity"], require_step=True)
        return _dump(receipt)

    def progress(payload: str) -> None:
        on_progress(_load(payload))  # type: ignore[misc]

    result = _load(_extension().analyze_json(_dump(copy_value(prepared)),
                    accept if on_step is not None else None,
                    progress if on_progress is not None else None, cancelled))
    require_keys(result, SUMMARY_KEYS, stage="native_adapter")
    return result
