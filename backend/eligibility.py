from .database import connect
from .decision_service import DatasetNotFoundError, POLICY_VERSION, evaluate_dataset_request
from .schemas import (
    EligibleDatasetsRequest,
    EligibleDatasetsResponse,
    EligibilityResult,
    LicenseCheck,
    IngestionDecisionStatus,
)


def check_dataset_eligibility(request: EligibleDatasetsRequest) -> EligibleDatasetsResponse:
    if request.dataset_ids is None:
        with connect() as connection:
            dataset_ids = [row["id"] for row in connection.execute("SELECT id FROM datasets ORDER BY id")]
    else:
        dataset_ids = request.dataset_ids

    if dataset_ids:
        placeholders = ",".join("?" for _ in dataset_ids)
        with connect() as connection:
            existing = {
                row["id"]
                for row in connection.execute(
                    f"SELECT id FROM datasets WHERE id IN ({placeholders})", dataset_ids
                )
            }
        missing = [dataset_id for dataset_id in dataset_ids if dataset_id not in existing]
        if missing:
            raise DatasetNotFoundError(missing[0])

    license_check = LicenseCheck(
        purpose=request.purpose,
        commercial=request.context == "commercial",
        geography=request.geography,
        redistribution_requested=request.redistribution_requested,
        derivative_work_requested=request.derivative_work_requested,
    )
    eligible = []
    excluded = []
    for dataset_id in dataset_ids:
        evaluation = evaluate_dataset_request(dataset_id, license_check)
        result = EligibilityResult(
            dataset_id=dataset_id,
            decision=evaluation.result.decision,
            eligible=evaluation.result.decision == IngestionDecisionStatus.APPROVED,
            reason_codes=evaluation.result.reason_codes,
            explanation=evaluation.result.human_readable_explanation,
            evidence_verification_status=evaluation.result.evidence_verification_status,
            rights_ledger_reference=evaluation.result.rights_ledger_reference,
        )
        (eligible if result.eligible else excluded).append(result)

    return EligibleDatasetsResponse(
        purpose=request.purpose,
        context=request.context,
        geography=request.geography,
        policy_version=POLICY_VERSION,
        eligible_datasets=eligible,
        excluded_datasets=excluded,
    )
