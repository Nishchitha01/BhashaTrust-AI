"""Optional adapter for the official QpiAI Opt platform SDK.

Platform calls are disabled unless the caller opts in on the individual
optimization request and credentials are set. This module deliberately does
not log credentials or raw SDK responses.
"""

from __future__ import annotations

from dataclasses import dataclass
import importlib
import math
import os
from numbers import Real
from pathlib import Path
from typing import Any, Protocol


class CandidateLike(Protocol):
    cost_minor_units: int
    utility_score: int


class QpiOptError(RuntimeError):
    """A safe-to-report QpiAI integration failure."""


@dataclass(frozen=True)
class QpiOptResult:
    selected_indexes: list[int]
    job_id: str | None
    status: str | None


def _project_environment() -> dict[str, str]:
    """Read simple KEY=value entries from backend-local and root env files."""
    backend_dir = Path(__file__).resolve().parent
    env_paths = (backend_dir / ".env", backend_dir.parent / "backend.env")
    values: dict[str, str] = {}
    for env_path in env_paths:
        try:
            for raw_line in env_path.read_text(encoding="utf-8").splitlines():
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                values[key.strip()] = value.strip().strip("\"'")
        except FileNotFoundError:
            continue
    return values


def _setting(name: str) -> str:
    return os.environ.get(name) or _project_environment().get(name, "")


def _load_sdk():
    try:
        modeller_module = importlib.import_module("qpiai_opt.opt_modeller")
        solvers_module = importlib.import_module("qpiai_opt.opt_solvers")
        return (
            modeller_module.Binary,
            modeller_module.Model,
            solvers_module.Client,
            solvers_module.solve,
        )
    except (ImportError, AttributeError) as exc:
        raise QpiOptError("The current qpiai-opt SDK is not installed.") from exc


def build_knapsack_model(candidates: list[CandidateLike], budget_minor_units: int):
    """Build the dataset-selection MILP used by QpiAI Opt."""
    if budget_minor_units < 0:
        raise ValueError("Budget cannot be negative.")
    if not candidates:
        raise ValueError("At least one candidate is required.")

    Binary, Model, _Client, _solve = _load_sdk()
    variables = {
        candidate.dataset_id: Binary(f"dataset_{candidate.dataset_id}")
        for candidate in candidates
    }
    model = Model("BhashaTrust dataset selection")
    model.maximize(
        sum(candidate.utility_score * variables[candidate.dataset_id] for candidate in candidates)
    )
    model.add_constraint(
        sum(candidate.cost_minor_units * variables[candidate.dataset_id] for candidate in candidates)
        <= budget_minor_units,
        label="licensing_budget",
    )
    return model, {dataset_id: f"dataset_{dataset_id}" for dataset_id in variables}


def build_knapsack_qubo(candidates: list[CandidateLike], budget_minor_units: int):
    """Convert the binary knapsack objective into a QUBO with integer slack bits.

    Minimize -sum(q_i*x_i) + A*(sum(c_i*x_i)+sum(2^k*s_k)-B)^2,
    where A is larger than the maximum possible utility. The equality's slack
    encoding makes feasible budget values representable exactly in minor units.
    """
    if budget_minor_units < 0:
        raise ValueError("Budget cannot be negative.")
    if not candidates:
        return {}, 0

    slack_weights: list[int] = []
    represented = 0
    bit_value = 1
    while represented < budget_minor_units:
        weight = min(bit_value, budget_minor_units - represented)
        slack_weights.append(weight)
        represented += weight
        bit_value <<= 1

    variable_count = len(candidates) + len(slack_weights)
    coefficients = [candidate.cost_minor_units for candidate in candidates] + slack_weights
    penalty = sum(max(0, candidate.utility_score) for candidate in candidates) + 1
    qubo: dict[tuple[int, int], int] = {}
    for index, coefficient in enumerate(coefficients):
        linear = penalty * (coefficient * coefficient - 2 * budget_minor_units * coefficient)
        if index < len(candidates):
            linear -= candidates[index].utility_score
        qubo[(index, index)] = linear
    for left in range(variable_count):
        for right in range(left + 1, variable_count):
            qubo[(left, right)] = 2 * penalty * coefficients[left] * coefficients[right]
    return qubo, variable_count


def decode_solution(raw_solution: Any, candidate_count: int, variable_count: int) -> list[int]:
    """Decode the SDK's QUBO bit vector and return selected dataset indexes."""
    if isinstance(raw_solution, dict):
        # Some service versions wrap the vector under a `solution` key.
        raw_solution = raw_solution.get("solution")
    if isinstance(raw_solution, (tuple, list)) and len(raw_solution) == 1 and isinstance(raw_solution[0], (tuple, list)):
        raw_solution = raw_solution[0]
    if not isinstance(raw_solution, (tuple, list)) or len(raw_solution) != variable_count:
        raise QpiOptError("QpiAI returned an unsupported solution format.")
    bits = []
    for value in raw_solution:
        if value not in (0, 1, 0.0, 1.0, False, True):
            raise QpiOptError("QpiAI returned non-binary decision variables.")
        bits.append(int(value))
    return [index for index, bit in enumerate(bits[:candidate_count]) if bit]


def solve_with_qpiai(candidates: list[CandidateLike], budget_minor_units: int) -> QpiOptResult:
    """Submit one explicitly authorized platform solve and validate its shape."""
    api_key = _setting("QPIAI_OPT_API_KEY")
    if not api_key:
        raise QpiOptError("QpiAI Opt credentials are not configured.")
    if not candidates:
        return QpiOptResult(selected_indexes=[], job_id=None, status="no_candidates")

    _Binary, _Model, Client, solve = _load_sdk()
    model, variable_names = build_knapsack_model(candidates, budget_minor_units)
    try:
        result = solve(model, mode="platform", client=Client(api_key=api_key))
        if getattr(result, "execution_mode", None) != "platform":
            raise QpiOptError("QpiAI returned a result from an unexpected execution mode.")
        solution = getattr(result, "solution", None)
        if not isinstance(solution, dict) or set(solution) != set(variable_names.values()):
            raise QpiOptError("QpiAI returned an unsupported solution format.")

        selected_indexes = []
        candidate_indexes = {
            candidate.dataset_id: index for index, candidate in enumerate(candidates)
        }
        for dataset_id, variable_name in variable_names.items():
            value = solution[variable_name]
            if (
                isinstance(value, bool)
                or not isinstance(value, Real)
                or not math.isfinite(float(value))
                or value not in (0, 1)
            ):
                raise QpiOptError("QpiAI returned non-binary decision variables.")
            if value == 1:
                selected_indexes.append(candidate_indexes[dataset_id])

        raw_status = getattr(result, "model_status", None) or getattr(result, "status", None)
        raw_job_id = getattr(result, "job_id", None)
        return QpiOptResult(
            selected_indexes=selected_indexes,
            job_id=str(raw_job_id) if raw_job_id is not None else None,
            status=str(raw_status) if raw_status is not None else None,
        )
    except QpiOptError:
        raise
    except Exception:
        # SDK exceptions can contain request metadata, so expose only a safe summary.
        raise QpiOptError("QpiAI Opt request failed.") from None
