from datetime import date

from .schemas import DatasetRecord, DecisionStatus, LicenseCheck, LicenseDecision


def evaluate_license(dataset: DatasetRecord, request: LicenseCheck) -> LicenseDecision:
    """Conservative demo policy: approve only when all relevant evidence is explicit."""
    reasons: list[str] = []
    blocking: list[str] = []
    review: list[str] = []

    if dataset.revoked:
        blocking.append("License is revoked.")
    if dataset.expires_on and dataset.expires_on < date.today():
        blocking.append("License has expired.")
    if request.purpose not in dataset.permitted_purposes:
        blocking.append(f"Purpose '{request.purpose}' is not explicitly permitted.")
    if request.commercial and dataset.commercial_use is False:
        blocking.append("Commercial use is not permitted.")
    elif request.commercial and dataset.commercial_use is None:
        review.append("Commercial-use permission is unspecified.")
    if request.redistribution_requested and dataset.redistribution_allowed is False:
        blocking.append("Redistribution is not permitted.")
    elif request.redistribution_requested and dataset.redistribution_allowed is None:
        review.append("Redistribution permission is unspecified.")
    if request.derivative_work_requested and dataset.derivative_work_allowed is False:
        blocking.append("Derivative works are not permitted.")
    elif request.derivative_work_requested and dataset.derivative_work_allowed is None:
        review.append("Derivative-work permission is unspecified.")
    if dataset.geographic_restrictions and request.geography:
        if request.geography not in dataset.geographic_restrictions:
            blocking.append(f"Use in '{request.geography}' is outside the recorded geographic restrictions.")
    elif dataset.geographic_restrictions and not request.geography:
        review.append("Request geography is required to check geographic restrictions.")
    if not dataset.evidence_independently_verified:
        review.append("Licensing evidence has not been independently verified.")

    if blocking:
        status = DecisionStatus.REJECTED
        reasons = blocking + review
    elif review:
        status = DecisionStatus.REVIEW_REQUIRED
        reasons = review
    else:
        status = DecisionStatus.ACCEPTED
        reasons = ["Recorded permissions support this request."]

    return LicenseDecision(dataset_id=dataset.id, status=status, reasons=reasons)
