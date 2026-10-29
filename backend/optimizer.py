from dataclasses import dataclass
from decimal import Decimal

from .cost_allocation import get_cost_lines, minor_units_from_money, money_from_minor_units
from .database import connect
from .decision_service import DatasetNotFoundError, POLICY_VERSION
from .eligibility import check_dataset_eligibility
from .qpiai_opt_adapter import QpiOptError, solve_with_qpiai
from .schemas import (
    EligibleDatasetsRequest,
    EligibilityResult,
    IngestionDecisionStatus,
    OptimizationExcludedDataset,
    OptimizationRequest,
    OptimizationResponse,
    OptimizationSelectedDataset,
)

MAX_EXACT_DATASETS = 20
ALGORITHM_NAME = "exhaustive_subset_search"


class OptimizationSafetyError(ValueError):
    def __init__(self, message: str, audit_payload: dict):
        super().__init__(message)
        self.audit_payload = audit_payload


@dataclass(frozen=True)
class Candidate:
    dataset_id: int
    cost_minor_units: int
    utility_score: int


def solve_exact(candidates: list[Candidate], budget_minor_units: int) -> tuple[list[int], int]:
    """Enumerate feasible subsets; exact for candidate sets up to MAX_EXACT_DATASETS."""
    if len(candidates) > MAX_EXACT_DATASETS:
        raise ValueError(f"Exact optimizer limit is {MAX_EXACT_DATASETS} candidates.")

    best_ids: tuple[int, ...] = ()
    best_utility = 0
    best_cost = 0
    selected: list[int] = []

    def visit(index: int, current_cost: int, current_utility: int) -> None:
        nonlocal best_ids, best_utility, best_cost
        if index == len(candidates):
            ids = tuple(sorted(selected))
            if (
                current_utility > best_utility
                or (current_utility == best_utility and current_cost < best_cost)
                or (current_utility == best_utility and current_cost == best_cost and ids < best_ids)
            ):
                best_ids = ids
                best_utility = current_utility
                best_cost = current_cost
            return

        candidate = candidates[index]
        visit(index + 1, current_cost, current_utility)
        if current_cost + candidate.cost_minor_units <= budget_minor_units:
            selected.append(candidate.dataset_id)
            visit(
                index + 1,
                current_cost + candidate.cost_minor_units,
                current_utility + candidate.utility_score,
            )
            selected.pop()

    visit(0, 0, 0)
    return list(best_ids), best_utility


def validate_optimizer_selection(
    selected_ids: list[int],
    eligibility_results: list[EligibilityResult],
    costs_by_id: dict[int, int],
    budget_minor_units: int,
) -> list[str]:
    errors = []
    if len(selected_ids) != len(set(selected_ids)):
        errors.append("Selected dataset IDs contain duplicates.")
    verified = {item.dataset_id: item for item in eligibility_results}
    for dataset_id in selected_ids:
        result = verified.get(dataset_id)
        if result is None or not result.eligible or result.decision != IngestionDecisionStatus.APPROVED:
            errors.append(f"Dataset {dataset_id} is not currently approved.")
        if dataset_id not in costs_by_id:
            errors.append(f"Dataset {dataset_id} has no validated cost.")
    total_cost = sum(costs_by_id.get(dataset_id, 0) for dataset_id in selected_ids)
    if total_cost > budget_minor_units:
        errors.append("Selected dataset costs exceed the configured budget.")
    return errors


def _excluded(item: EligibilityResult) -> OptimizationExcludedDataset:
    return OptimizationExcludedDataset(
        dataset_id=item.dataset_id,
        decision=item.decision,
        reason_codes=item.reason_codes,
        explanation=item.explanation,
    )


def run_optimization(request: OptimizationRequest) -> tuple[OptimizationResponse, dict]:
    eligibility_request = EligibleDatasetsRequest(
        purpose=request.purpose,
        context=request.context,
        geography=request.geography,
        redistribution_requested=request.redistribution_requested,
        derivative_work_requested=request.derivative_work_requested,
        dataset_ids=request.dataset_ids,
    )
    initial_eligibility = check_dataset_eligibility(eligibility_request)
    eligible_map = {item.dataset_id: item for item in initial_eligibility.eligible_datasets}

    cost_lines = []
    cost_unavailable: dict[int, str] = {}
    for dataset_id in eligible_map:
        try:
            cost_lines.extend(get_cost_lines([dataset_id], request.currency))
        except LookupError as error:
            cost_unavailable[dataset_id] = str(error)

    candidates = [
        Candidate(
            dataset_id=line.dataset_id,
            cost_minor_units=minor_units_from_money(line.cost),
            utility_score=line.utility_score,
        )
        for line in cost_lines
    ]
    if len(candidates) > MAX_EXACT_DATASETS:
        raise ValueError(
            f"There are {len(candidates)} priced eligible datasets; the exact optimizer supports at most "
            f"{MAX_EXACT_DATASETS}."
        )

    budget_minor = minor_units_from_money(request.budget)
    solver_used = "local_baseline"
    algorithm = ALGORITHM_NAME
    execution_status = "completed"
    fallback_reason = None
    selected_ids: list[int]
    total_utility: int
    solver_job_id = None
    solver_status = None
    if request.solver == "qpiai_opt":
        if not candidates:
            fallback_reason = "No eligible priced datasets; no QpiAI request was made."
        elif not request.confirm_remote_job:
            fallback_reason = "Remote execution was not explicitly confirmed; local baseline used."
        else:
            try:
                qpiai_result = solve_with_qpiai(candidates, budget_minor)
                selected_indexes = qpiai_result.selected_indexes
                if (
                    len(selected_indexes) != len(set(selected_indexes))
                    or any(not isinstance(index, int) or index < 0 or index >= len(candidates) for index in selected_indexes)
                ):
                    raise QpiOptError("QpiAI returned invalid candidate indexes.")
                selected_ids = [candidates[index].dataset_id for index in selected_indexes]
                total_utility = sum(
                    candidate.utility_score
                    for candidate in candidates
                    if candidate.dataset_id in selected_ids
                )
                solver_job_id = qpiai_result.job_id
                solver_status = qpiai_result.status
                solver_used = "qpiai_opt"
                algorithm = "qpiai_opt_milp"
            except QpiOptError as error:
                fallback_reason = str(error)
        if solver_used != "qpiai_opt":
            selected_ids, total_utility = solve_exact(candidates, budget_minor)
            execution_status = "fallback"
    else:
        selected_ids, total_utility = solve_exact(candidates, budget_minor)

    # Refresh approvals after solving, immediately before the candidate result is accepted.
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
    all_results = {
        result.dataset_id: result
        for result in initial_eligibility.eligible_datasets + initial_eligibility.excluded_datasets
    }
    all_results.update({result.dataset_id: result for result in rechecked.eligible_datasets})
    all_results.update({result.dataset_id: result for result in rechecked.excluded_datasets})
    eligibility_results = [all_results[dataset_id] for dataset_id in sorted(all_results)]

    costs_by_id = {line.dataset_id: minor_units_from_money(line.cost) for line in cost_lines}
    validation_errors = validate_optimizer_selection(
        selected_ids, eligibility_results, costs_by_id, budget_minor
    )
    total_cost_minor = sum(costs_by_id.get(dataset_id, 0) for dataset_id in selected_ids)
    selected_lines = [
        OptimizationSelectedDataset(**line.model_dump())
        for line in cost_lines
        if line.dataset_id in selected_ids
    ]
    excluded = [_excluded(item) for item in initial_eligibility.excluded_datasets]
    for dataset_id, reason in cost_unavailable.items():
        excluded.append(
            OptimizationExcludedDataset(
                dataset_id=dataset_id,
                decision=IngestionDecisionStatus.APPROVED,
                reason_codes=["COST_UNAVAILABLE"],
                explanation=reason,
            )
        )
    for dataset_id in costs_by_id.keys() - set(selected_ids):
        excluded.append(
            OptimizationExcludedDataset(
                dataset_id=dataset_id,
                decision=IngestionDecisionStatus.APPROVED,
                reason_codes=["NOT_SELECTED_BY_OPTIMUM"],
                explanation=(
                    "The selected solver did not choose this eligible dataset under the budget objective."
                ),
            )
        )

    result = OptimizationResponse(
        optimization_id=0,
        selected_dataset_ids=selected_ids,
        selected_datasets=selected_lines,
        excluded_datasets=excluded,
        total_utility=total_utility,
        total_licensing_cost=money_from_minor_units(total_cost_minor),
        currency=request.currency,
        budget=request.budget,
        remaining_budget=money_from_minor_units(budget_minor - total_cost_minor),
        budget_status="WITHIN_BUDGET" if total_cost_minor <= budget_minor else "OVER_BUDGET",
        eligibility_verification_results=eligibility_results,
        algorithm=algorithm,
        optimality_guaranteed=solver_used == "local_baseline",
        input_size=len(candidates),
        validation_passed=not validation_errors,
        audit_event_reference=0,
        requested_solver=request.solver,
        solver_used=solver_used,
        result_origin=solver_used,
        execution_status=execution_status,
        fallback_reason=fallback_reason,
        solver_job_id=solver_job_id,
        solver_status=solver_status,
    )
    audit_payload = {
        "request": request.model_dump(mode="json"),
        "eligible_input_ids": [candidate.dataset_id for candidate in candidates],
        "objective": {
            "sense": "maximize",
            "expression": "sum(q_i * x_i)",
            "constraint": "sum(c_i * x_i) <= B",
            "binary_variables": True,
            "budget_minor_units": budget_minor,
            "coefficients": [
                {
                    "dataset_id": candidate.dataset_id,
                    "utility_q_i": candidate.utility_score,
                    "cost_c_i_minor_units": candidate.cost_minor_units,
                }
                for candidate in candidates
            ],
        },
        "selected_ids": selected_ids,
        "result": result.model_dump(mode="json"),
        "validation_outcome": "passed" if not validation_errors else "rejected",
        "validation_errors": validation_errors,
        "requested_solver": request.solver,
        "solver_used": solver_used,
        "execution_mode": "platform" if solver_used == "qpiai_opt" else "local_baseline",
        "solver_job_id": solver_job_id,
        "solver_status": solver_status,
        "fallback_reason": fallback_reason,
    }
    if validation_errors:
        raise OptimizationSafetyError("Optimizer result failed safety validation.", audit_payload)
    return result, audit_payload
