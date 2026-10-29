from dataclasses import dataclass
from datetime import datetime, timezone
import json

from .database import connect
from .licensing import evaluate_license
from .schemas import (
    DatasetRecord,
    DecisionStatus,
    IngestionDecisionResponse,
    IngestionDecisionStatus,
    LicenseCheck,
    LicenseDecision,
    RightsPayload,
)
from .signatures import verify_rights_signature

POLICY_VERSION = "1.1.0"


@dataclass
class DecisionEvaluation:
    result: IngestionDecisionResponse
    engine_decision: LicenseDecision
    license_check: LicenseCheck


class DatasetNotFoundError(LookupError):
    pass


def _decode_dataset(row) -> DatasetRecord:
    values = dict(row)
    values["permitted_purposes"] = json.loads(values["permitted_purposes"])
    values["geographic_restrictions"] = json.loads(values["geographic_restrictions"])
    for field in (
        "commercial_use",
        "revoked",
        "evidence_verified",
        "evidence_independently_verified",
        "synthetic",
    ):
        if values[field] is not None:
            values[field] = bool(values[field])
    return DatasetRecord(**values)


def reason_code(reason: str) -> str:
    """Translate an engine explanation into a stable API code, without policy decisions."""
    lowered = reason.lower()
    if "license is revoked" in lowered:
        return "LICENSE_REVOKED"
    if "license has expired" in lowered:
        return "LICENSE_EXPIRED"
    if "not explicitly permitted" in lowered:
        return "PURPOSE_NOT_PERMITTED"
    if "commercial use is not permitted" in lowered:
        return "COMMERCIAL_USE_NOT_PERMITTED"
    if "commercial-use permission is unspecified" in lowered:
        return "COMMERCIAL_PERMISSION_UNSPECIFIED"
    if "outside the recorded geographic restrictions" in lowered:
        return "GEOGRAPHY_NOT_PERMITTED"
    if "request geography is required" in lowered:
        return "GEOGRAPHY_REQUIRED"
    if "licensing evidence has not been independently verified" in lowered:
        return "EVIDENCE_UNVERIFIED"
    if "evidence review rejected this submission" in lowered:
        return "EVIDENCE_REVIEW_REJECTED"
    if "reviewer requested additional evidence" in lowered:
        return "EVIDENCE_MORE_REQUESTED"
    if "rights assertions conflict" in lowered:
        return "EVIDENCE_CONFLICT"
    if "redistribution is not permitted" in lowered:
        return "REDISTRIBUTION_NOT_PERMITTED"
    if "redistribution permission is unspecified" in lowered:
        return "REDISTRIBUTION_PERMISSION_UNSPECIFIED"
    if "derivative works are not permitted" in lowered:
        return "DERIVATIVE_WORKS_NOT_PERMITTED"
    if "derivative-work permission is unspecified" in lowered:
        return "DERIVATIVE_WORK_PERMISSION_UNSPECIFIED"
    if "recorded permissions support this request" in lowered:
        return "PERMISSIONS_SUPPORT_REQUEST"
    return "POLICY_REASON_UNCLASSIFIED"


def _rights_assertions_conflict(dataset: DatasetRecord, payload: RightsPayload) -> bool:
    comparisons = (
        (dataset.commercial_use, payload.commercial_use),
        (dataset.redistribution_allowed, payload.redistribution_allowed),
        (dataset.derivative_work_allowed, payload.derivative_work_allowed),
        (dataset.expires_on, payload.expires_on),
        (dataset.revoked, payload.revoked),
    )
    if any(left is not None and right is not None and left != right for left, right in comparisons):
        return True

    if (
        dataset.permitted_purposes
        and payload.permitted_purposes
        and set(dataset.permitted_purposes) != set(payload.permitted_purposes)
    ):
        return True
    return bool(
        dataset.geographic_restrictions
        and payload.geographic_restrictions
        and set(dataset.geographic_restrictions) != set(payload.geographic_restrictions)
    )


def evaluate_dataset_request(dataset_id: int, request: LicenseCheck) -> DecisionEvaluation:
    with connect() as connection:
        row = connection.execute("SELECT * FROM datasets WHERE id = ?", (dataset_id,)).fetchone()
        if row is None:
            raise DatasetNotFoundError(dataset_id)
        latest_record = connection.execute(
            """SELECT id, payload_json, public_key, signature, signature_valid
               FROM rights_records WHERE dataset_id = ? ORDER BY id DESC LIMIT 1""",
            (dataset_id,),
        ).fetchone()
        evidence_reviews = connection.execute(
            "SELECT review_status FROM evidence_submissions WHERE dataset_id = ? ORDER BY id",
            (dataset_id,),
        ).fetchall()

    dataset = _decode_dataset(row)
    review_statuses = [item["review_status"] for item in evidence_reviews]
    if review_statuses and not all(status == "APPROVED" for status in review_statuses):
        dataset = dataset.model_copy(update={"evidence_independently_verified": False})
    evidence_status = (
        "INDEPENDENTLY_VERIFIED"
        if dataset.evidence_independently_verified
        else "REGISTRY_DECLARED_UNVERIFIED"
        if dataset.evidence_verified
        else "UNVERIFIED"
    )
    evidence_references: list[dict] = []
    rights_reference = None
    evidence_conflicted = False
    signature_valid = False
    signed_payload = None
    if latest_record:
        try:
            candidate_payload = RightsPayload(**json.loads(latest_record["payload_json"]))
        except (json.JSONDecodeError, ValueError):
            candidate_payload = None
        if candidate_payload is not None:
            signature_valid, _ = verify_rights_signature(
                candidate_payload,
                latest_record["public_key"],
                latest_record["signature"],
            )
            signature_valid = signature_valid and bool(latest_record["signature_valid"])
            if signature_valid:
                signed_payload = candidate_payload

    if signed_payload is not None:
        evidence_conflicted = _rights_assertions_conflict(dataset, signed_payload)
        dataset = dataset.model_copy(
            update={
                "license_name": signed_payload.license_name,
                "permitted_purposes": signed_payload.permitted_purposes,
                "commercial_use": signed_payload.commercial_use,
                "redistribution_allowed": signed_payload.redistribution_allowed,
                "derivative_work_allowed": signed_payload.derivative_work_allowed,
                "geographic_restrictions": signed_payload.geographic_restrictions,
                "expires_on": signed_payload.expires_on,
                "revoked": signed_payload.revoked,
                "evidence_independently_verified": (
                    dataset.evidence_independently_verified and not evidence_conflicted
                ),
            }
        )
        evidence_status = (
            "CONFLICTING_RIGHTS_CLAIMS"
            if evidence_conflicted
            else "USER_SIGNED_CLAIM_SIGNATURE_VALID"
        )
        evidence_references = [{"type": "rights_ledger_record", "id": latest_record["id"]}]
        rights_reference = {
            "record_id": latest_record["id"],
            "signature_valid": True,
            "applied_to_decision": True,
        }
    elif latest_record:
        dataset = dataset.model_copy(update={"evidence_verified": False})
        rights_reference = {
            "record_id": latest_record["id"],
            "signature_valid": False,
            "applied_to_decision": False,
        }
        evidence_status = "USER_SIGNED_CLAIM_SIGNATURE_INVALID"

    if review_statuses:
        if "REJECTED" in review_statuses:
            evidence_status = "EVIDENCE_REVIEW_REJECTED"
        elif "MORE_EVIDENCE_REQUESTED" in review_statuses:
            evidence_status = "ADDITIONAL_EVIDENCE_REQUESTED"
        elif not all(status == "APPROVED" for status in review_statuses):
            evidence_status = "EVIDENCE_REVIEW_PENDING"

    engine_decision = evaluate_license(dataset, request)
    if "REJECTED" in review_statuses:
        engine_decision.reasons.insert(0, "Evidence review rejected this submission.")
        if engine_decision.status == DecisionStatus.ACCEPTED:
            engine_decision.status = DecisionStatus.REVIEW_REQUIRED
    elif "MORE_EVIDENCE_REQUESTED" in review_statuses:
        engine_decision.reasons.insert(0, "Reviewer requested additional evidence.")
        if engine_decision.status == DecisionStatus.ACCEPTED:
            engine_decision.status = DecisionStatus.REVIEW_REQUIRED
    if evidence_conflicted:
        engine_decision.reasons.insert(0, "Rights assertions conflict and require manual review.")
        if engine_decision.status == DecisionStatus.ACCEPTED:
            engine_decision.status = DecisionStatus.REVIEW_REQUIRED
    decision = {
        "accepted": IngestionDecisionStatus.APPROVED,
        "rejected": IngestionDecisionStatus.REJECTED,
        "review_required": IngestionDecisionStatus.NEEDS_REVIEW,
    }[engine_decision.status.value]
    now = datetime.now(timezone.utc).isoformat()
    result = IngestionDecisionResponse(
        dataset_id=dataset_id,
        decision=decision,
        reason_codes=[reason_code(reason) for reason in engine_decision.reasons],
        human_readable_explanation=" ".join(engine_decision.reasons),
        policy_version=POLICY_VERSION,
        evidence_verification_status=evidence_status,
        rights_ledger_reference=rights_reference,
        timestamp=now,
    )
    return DecisionEvaluation(result=result, engine_decision=engine_decision, license_check=request)
