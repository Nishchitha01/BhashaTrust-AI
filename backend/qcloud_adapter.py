"""QpiAI QCloud adapter, separate from the Phase 5 QpiAI Opt adapter."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any


class QCloudError(RuntimeError):
    """Sanitized QCloud configuration, authentication, or execution failure."""


def _read_api_key() -> str:
    """Read the Quantum SDK's API key from environment, backend/.env, or root backend.env."""
    key = (os.environ.get("API_KEY") or os.environ.get("QPIAI_QCLOUD_API_KEY") or "").strip()
    if key:
        return key

    backend_dir = Path(__file__).resolve().parent
    for env_path in (backend_dir / ".env", backend_dir.parent / "backend.env"):
        try:
            for raw_line in env_path.read_text(encoding="utf-8").splitlines():
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                name, value = line.split("=", 1)
                name = name.strip()
                if name in ("API_KEY", "QPIAI_QCLOUD_API_KEY"):
                    parsed_val = value.strip().strip("\"'")
                    if parsed_val:
                        return parsed_val
        except FileNotFoundError:
            continue
    return ""


def submit_qcloud_circuit(circuit: Any, shots: int, experiment_name: str) -> tuple[dict[str, int], str | None]:
    """Authenticate with QCloud and submit an SDK circuit to its CPU simulator.

    Callers must enforce explicit per-request approval before invoking this
    function. No QCloud/Opt key values are logged or returned.
    """
    api_key = _read_api_key()
    if not api_key:
        raise QCloudError("QCloud API_KEY is not configured in backend/.env.")

    try:
        from qpiai_quantum import QpiAIQuantumAuth
        from qpiai_quantum.authentication.exceptions import (
            APIKeyInvalidError,
            AuthError,
        )
        from qpiai_quantum.jobmanager.job_result import JobResult
    except ImportError as error:
        raise QCloudError("The QpiAI Quantum SDK is not installed.") from error

    try:
        QpiAIQuantumAuth.login(api_key=api_key)
    except (APIKeyInvalidError, AuthError) as error:
        raise QCloudError("QCloud authentication failed.") from error
    except Exception as error:
        raise QCloudError("QCloud authentication could not be completed.") from error

    try:
        result = circuit.run(
            shots=shots,
            experiment_name=experiment_name,
            need_statevector=False,
            need_density_matrix=False,
            device_name="QpiAI-QSV-Simulator",
        )
        counts = result.get_counts() if hasattr(result, "get_counts") else getattr(result, "counts", None)
        if not isinstance(counts, dict) or not counts:
            raise QCloudError("QCloud returned no measurement counts.")
        job_id = getattr(result, "job_id", None)
        if job_id is None and isinstance(result, JobResult):
            job_id = getattr(result, "id", None)
        return {str(bits): int(count) for bits, count in counts.items()}, str(job_id) if job_id else None
    except QCloudError:
        raise
    except Exception as error:
        raise QCloudError("QCloud circuit execution failed.") from error

