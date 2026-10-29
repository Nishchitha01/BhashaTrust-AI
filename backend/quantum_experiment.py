"""Small, safety-gated QpiAI Quantum SDK experiment for dataset selection."""

from __future__ import annotations

import math
import os
from dataclasses import dataclass
from decimal import Decimal
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

from .cost_allocation import get_cost_lines, minor_units_from_money, money_from_minor_units
from .decision_service import DatasetNotFoundError
from .eligibility import check_dataset_eligibility
from .optimizer import Candidate, solve_exact, validate_optimizer_selection
from .qpiai_opt_adapter import build_knapsack_qubo
from .qcloud_adapter import submit_qcloud_circuit
from .schemas import (
    EligibleDatasetsRequest,
    EligibilityResult,
    IngestionDecisionStatus,
    OptimizationExcludedDataset,
    QuantumExperimentRequest,
    QuantumExperimentResponse,
)

MAX_QUANTUM_CANDIDATES = 3
MAX_QUBITS = 10
QAOA_GAMMA = math.pi / 4
QAOA_BETA = math.pi / 8


class QuantumExperimentError(ValueError):
    pass


class RemoteApprovalRequired(QuantumExperimentError):
    pass


@dataclass(frozen=True)
class PreparedExperiment:
    candidates: list[Candidate]
    eligibility: list[EligibilityResult]
    excluded: list[OptimizationExcludedDataset]
    cost_lines: list
    budget_minor: int


def _sdk_imports():
    try:
        os.environ.setdefault(
            "MPLCONFIGDIR",
            str(Path(__file__).resolve().parent.parent / ".runtime" / "matplotlib"),
        )
        from qpiai_quantum.circuit import Circuit
        from qpiai_quantum.simulator import StatevectorSimulator
        sdk_version = version("qpiai-quantum")
    except (ImportError, PackageNotFoundError) as error:
        raise QuantumExperimentError("QpiAI Quantum SDK is not installed.") from error
    return Circuit, StatevectorSimulator, sdk_version


def prepare_experiment(request: QuantumExperimentRequest) -> PreparedExperiment:
    """Apply the licensing engine before building any quantum-model input."""
    eligibility = check_dataset_eligibility(
        EligibleDatasetsRequest(
            purpose=request.purpose,
            context=request.context,
            geography=request.geography,
            redistribution_requested=request.redistribution_requested,
            derivative_work_requested=request.derivative_work_requested,
            dataset_ids=request.dataset_ids,
        )
    )
    all_eligibility = eligibility.eligible_datasets + eligibility.excluded_datasets
    cost_lines = []
    excluded = [
        OptimizationExcludedDataset(
            dataset_id=item.dataset_id,
            decision=item.decision,
            reason_codes=item.reason_codes,
            explanation=item.explanation,
        )
        for item in eligibility.excluded_datasets
    ]

    for approved in eligibility.eligible_datasets:
        try:
            cost_lines.extend(get_cost_lines([approved.dataset_id], "USD"))
        except LookupError as error:
            excluded.append(
                OptimizationExcludedDataset(
                    dataset_id=approved.dataset_id,
                    decision=IngestionDecisionStatus.APPROVED,
                    reason_codes=["COST_UNAVAILABLE"],
                    explanation=str(error),
                )
            )

    candidates = [
        Candidate(
            dataset_id=line.dataset_id,
            cost_minor_units=minor_units_from_money(line.cost),
            utility_score=line.utility_score,
        )
        for line in cost_lines
    ]
    if len(candidates) > MAX_QUANTUM_CANDIDATES:
        raise QuantumExperimentError(
            f"The local experiment supports at most {MAX_QUANTUM_CANDIDATES} priced, eligible datasets."
        )
    return PreparedExperiment(
        candidates=candidates,
        eligibility=all_eligibility,
        excluded=excluded,
        cost_lines=cost_lines,
        budget_minor=minor_units_from_money(request.budget),
    )


def _normalize_costs(candidates: list[Candidate], budget_minor: int) -> tuple[list[Candidate], int, int]:
    divisor = math.gcd(budget_minor, *(candidate.cost_minor_units for candidate in candidates))
    divisor = max(divisor, 1)
    scaled = [
        Candidate(
            dataset_id=item.dataset_id,
            cost_minor_units=item.cost_minor_units // divisor,
            utility_score=item.utility_score,
        )
        for item in candidates
    ]
    return scaled, budget_minor // divisor, divisor


def _qubo_hamiltonian(qubo: dict[tuple[int, int], int], variable_count: int):
    constant = 0.0
    z_terms = [0.0] * variable_count
    zz_terms: dict[tuple[int, int], float] = {}
    for (left, right), coefficient in qubo.items():
        value = float(coefficient)
        if left == right:
            constant += value / 2
            z_terms[left] -= value / 2
        else:
            constant += value / 4
            z_terms[left] -= value / 4
            z_terms[right] -= value / 4
            zz_terms[(left, right)] = zz_terms.get((left, right), 0.0) + value / 4
    scale = max([abs(value) for value in z_terms] + [abs(value) for value in zz_terms.values()] + [1.0])
    return constant, [value / scale for value in z_terms], {
        edge: value / scale for edge, value in zz_terms.items()
    }


def _build_qaoa_circuit(Circuit, qubo, variable_count: int, gamma: float, beta: float):
    _constant, z_terms, zz_terms = _qubo_hamiltonian(qubo, variable_count)
    circuit = Circuit(variable_count, name="BhashaTrust-QAOA-p1")
    for qubit in range(variable_count):
        circuit.h(qubit)
    for qubit, coefficient in enumerate(z_terms):
        if coefficient:
            circuit.rz(qubit, 2 * gamma * coefficient)
    for (left, right), coefficient in zz_terms.items():
        if coefficient:
            circuit.rzz(left, right, 2 * gamma * coefficient)
    for qubit in range(variable_count):
        circuit.rx(qubit, 2 * beta)
    circuit.measure_all()
    return circuit


def _bits(bitstring: str, count: int) -> list[int] | None:
    value = bitstring.replace(" ", "")
    if len(value) != count or any(bit not in "01" for bit in value):
        return None
    return [int(value[-(index + 1)]) for index in range(count)]


def _qubo_energy(bits: list[int], qubo: dict[tuple[int, int], int]) -> int:
    return sum(coefficient * bits[left] * bits[right] for (left, right), coefficient in qubo.items())


def _choose_feasible_sample(
    counts: dict[str, int],
    qubo: dict[tuple[int, int], int],
    variable_count: int,
    candidate_count: int,
    candidates: list[Candidate],
    budget_minor: int,
) -> tuple[list[int], int] | None:
    options = []
    for bitstring, count in counts.items():
        decoded = _bits(bitstring, variable_count)
        if decoded is None:
            continue
        selected = [
            candidates[index].dataset_id
            for index, bit in enumerate(decoded[:candidate_count])
            if bit
        ]
        cost = sum(item.cost_minor_units for item in candidates if item.dataset_id in selected)
        if cost <= budget_minor:
            energy = _qubo_energy(decoded, qubo)
            utility = sum(item.utility_score for item in candidates if item.dataset_id in selected)
            options.append((energy, -int(count), -utility, cost, tuple(selected)))
    if not options:
        return None
    best = min(options)
    return list(best[4]), -best[2]


def validate_experiment_selection(
    selected_ids: list[int],
    eligibility: list[EligibilityResult],
    costs_by_id: dict[int, int],
    budget_minor: int,
) -> list[str]:
    return validate_optimizer_selection(selected_ids, eligibility, costs_by_id, budget_minor)


def run_quantum_experiment(
    request: QuantumExperimentRequest,
) -> tuple[QuantumExperimentResponse, dict]:
    if request.mode == "qcloud" and not request.confirm_remote_job:
        raise RemoteApprovalRequired(
            "QCloud execution may consume credits. Explicit confirmation is required; no remote call was made."
        )

    prepared = prepare_experiment(request)
    try:
        Circuit, Simulator, sdk_version = _sdk_imports()
    except QuantumExperimentError:
        raise

    candidates = prepared.candidates
    baseline_ids, baseline_utility = solve_exact(candidates, prepared.budget_minor)
    if not candidates:
        result = QuantumExperimentResponse(
            experiment_id=0,
            status="no_eligible_datasets",
            execution_mode="not_run",
            result_kind="not_run",
            sdk_name="qpiai-quantum",
            sdk_version=sdk_version,
            candidate_dataset_ids=[],
            eligibility_results=prepared.eligibility,
            excluded_datasets=prepared.excluded,
            selected_dataset_ids=[],
            total_utility=0,
            total_licensing_cost=Decimal("0.00"),
            budget=request.budget,
            remaining_budget=request.budget,
            budget_status="WITHIN_BUDGET",
            baseline_selected_dataset_ids=[],
            baseline_total_utility=0,
            sample_counts={},
            experiment_configuration={"algorithm": "qaoa_p1", "shots": request.shots, "seed": request.seed},
            limitations=["No currently approved datasets with configured costs were available."],
            validation_passed=True,
            validation_errors=[],
            audit_event_reference=0,
        )
        return result, _audit_payload(request, result, prepared, [])

    scaled_candidates, scaled_budget, cost_scale = _normalize_costs(candidates, prepared.budget_minor)
    qubo, variable_count = build_knapsack_qubo(scaled_candidates, scaled_budget)
    if variable_count > MAX_QUBITS:
        raise QuantumExperimentError(
            f"This experiment would require {variable_count} qubits; the prototype limit is {MAX_QUBITS}."
        )

    best_parameters = {"gamma": QAOA_GAMMA, "beta": QAOA_BETA}
    final_circuit = _build_qaoa_circuit(
        Circuit,
        qubo,
        variable_count,
        best_parameters["gamma"],
        best_parameters["beta"],
    )

    if request.mode == "qcloud":
        sample_counts, remote_job_id = submit_qcloud_circuit(
            final_circuit,
            request.shots,
            experiment_name=f"BhashaTrust dataset selection {request.seed}",
        )
        execution_mode = "qcloud"
        result_kind = "qcloud_measurements"
    else:
        sampled = Simulator().run(
            final_circuit,
            shots=request.shots,
            seed=request.seed + 50_000,
            name=f"BhashaTrust dataset selection {request.seed}",
        )
        sample_counts = sampled.get_counts() or {}
        remote_job_id = None
        execution_mode = "local_statevector_simulation"
        result_kind = "simulation_result"

    selected = _choose_feasible_sample(
        sample_counts,
        qubo,
        variable_count,
        len(candidates),
        scaled_candidates,
        scaled_budget,
    )
    status = "completed"
    selected_ids = selected[0] if selected else []
    total_utility = selected[1] if selected else 0
    if not selected:
        status = "no_feasible_sample"

    rechecked = check_dataset_eligibility(
        EligibleDatasetsRequest(
            purpose=request.purpose,
            context=request.context,
            geography=request.geography,
            redistribution_requested=request.redistribution_requested,
            derivative_work_requested=request.derivative_work_requested,
            dataset_ids=selected_ids,
        )
    )
    current_eligibility = {item.dataset_id: item for item in prepared.eligibility}
    current_eligibility.update({item.dataset_id: item for item in rechecked.eligible_datasets})
    current_eligibility.update({item.dataset_id: item for item in rechecked.excluded_datasets})
    final_eligibility = [current_eligibility[key] for key in sorted(current_eligibility)]
    costs_by_id = {item.dataset_id: item.cost_minor_units for item in candidates}
    validation_errors = validate_experiment_selection(
        selected_ids, final_eligibility, costs_by_id, prepared.budget_minor
    )
    total_cost = sum(costs_by_id.get(dataset_id, 0) for dataset_id in selected_ids)

    result = QuantumExperimentResponse(
        experiment_id=0,
        status=status,
        execution_mode=execution_mode,
        result_kind=result_kind,
        remote_job_id=remote_job_id,
        sdk_name="qpiai-quantum",
        sdk_version=sdk_version,
        candidate_dataset_ids=[item.dataset_id for item in candidates],
        eligibility_results=final_eligibility,
        excluded_datasets=prepared.excluded,
        selected_dataset_ids=selected_ids,
        total_utility=total_utility,
        total_licensing_cost=money_from_minor_units(total_cost),
        budget=request.budget,
        remaining_budget=money_from_minor_units(prepared.budget_minor - total_cost),
        budget_status="WITHIN_BUDGET" if total_cost <= prepared.budget_minor else "OVER_BUDGET",
        baseline_selected_dataset_ids=baseline_ids,
        baseline_total_utility=baseline_utility,
        sample_counts=sample_counts,
        experiment_configuration={
            "algorithm": "qaoa_p1",
            "parameter_selection": "fixed_reproducible_angles",
            "gamma": best_parameters["gamma"],
            "beta": best_parameters["beta"],
            "shots": request.shots,
            "seed": request.seed,
            "cost_scale_minor_units": cost_scale,
            "qubits": variable_count,
            "qcloud_job_id": remote_job_id,
        },
        limitations=[
            "This is a small p=1 QAOA experiment, not evidence of quantum advantage.",
            "Synthetic utility scores and demo licensing costs are used where present.",
            "Fixed QAOA angles and finite-shot sampling are approximate; the local exhaustive optimizer is the baseline.",
            "Only the most frequent feasible sampled bitstring, ranked by QUBO energy, is reported.",
        ],
        validation_passed=not validation_errors,
        validation_errors=validation_errors,
        audit_event_reference=0,
    )
    audit_payload = _audit_payload(request, result, prepared, validation_errors)
    audit_payload["validation_outcome"] = "passed" if not validation_errors else "rejected"
    if validation_errors:
        raise QuantumExperimentError("Quantum experiment result failed independent eligibility or budget validation.")
    return result, audit_payload


def _audit_payload(request, result, prepared, validation_errors):
    return {
        "request": request.model_dump(mode="json"),
        "execution_mode": result.execution_mode,
        "candidate_dataset_ids": [item.dataset_id for item in prepared.candidates],
        "selected_dataset_ids": result.selected_dataset_ids,
        "configuration": result.experiment_configuration,
        "result_kind": result.result_kind,
        "sample_counts": result.sample_counts,
        "result": result.model_dump(mode="json"),
        "validation_errors": validation_errors,
        "validation_outcome": "passed" if not validation_errors else "rejected",
        "limitations": result.limitations,
    }
