from datetime import date, timedelta

from backend.licensing import evaluate_license
from backend.schemas import DatasetRecord, DecisionStatus, LicenseCheck


def dataset(**overrides):
    values = {
        "id": 1,
        "name": "Synthetic test dataset",
        "language": "Hindi",
        "owner": "Synthetic test submitter",
        "description": "Synthetic test fixture",
        "license_name": "Synthetic test license",
        "permitted_purposes": ["research_training"],
        "commercial_use": False,
        "geographic_restrictions": [],
        "expires_on": None,
        "revoked": False,
        "evidence_verified": True,
        "evidence_independently_verified": True,
        "synthetic": True,
    }
    values.update(overrides)
    return DatasetRecord(**values)


def test_explicit_permitted_noncommercial_use_is_accepted():
    result = evaluate_license(dataset(), LicenseCheck(purpose="research_training"))
    assert result.status == DecisionStatus.ACCEPTED


def test_unpermitted_purpose_is_rejected():
    result = evaluate_license(dataset(), LicenseCheck(purpose="commercial_training"))
    assert result.status == DecisionStatus.REJECTED


def test_registry_claim_without_independent_verification_requires_review():
    result = evaluate_license(
        dataset(evidence_verified=True, evidence_independently_verified=False),
        LicenseCheck(purpose="research_training"),
    )
    assert result.status == DecisionStatus.REVIEW_REQUIRED
    assert any("independently verified" in reason.lower() for reason in result.reasons)


def test_redistribution_must_be_explicitly_allowed():
    result = evaluate_license(
        dataset(redistribution_allowed=False),
        LicenseCheck(purpose="research_training", redistribution_requested=True),
    )
    assert result.status == DecisionStatus.REJECTED


def test_unspecified_derivative_permission_requires_review():
    result = evaluate_license(
        dataset(derivative_work_allowed=None),
        LicenseCheck(purpose="research_training", derivative_work_requested=True),
    )
    assert result.status == DecisionStatus.REVIEW_REQUIRED


def test_expired_and_revoked_licenses_are_rejected():
    expired = date.today() - timedelta(days=1)
    result = evaluate_license(
        dataset(expires_on=expired, revoked=True), LicenseCheck(purpose="research_training")
    )
    assert result.status == DecisionStatus.REJECTED
    assert any("expired" in reason.lower() for reason in result.reasons)
    assert any("revoked" in reason.lower() for reason in result.reasons)


def test_unknown_commercial_permission_requires_review():
    result = evaluate_license(
        dataset(commercial_use=None), LicenseCheck(purpose="research_training", commercial=True)
    )
    assert result.status == DecisionStatus.REVIEW_REQUIRED


def test_geographic_restriction_rejects_outside_region():
    result = evaluate_license(
        dataset(geographic_restrictions=["IN"]),
        LicenseCheck(purpose="research_training", geography="US"),
    )
    assert result.status == DecisionStatus.REJECTED
