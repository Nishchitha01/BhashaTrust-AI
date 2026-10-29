from __future__ import annotations

from datetime import date
from decimal import Decimal
from enum import StrEnum
import re
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator


class DecisionStatus(StrEnum):
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    REVIEW_REQUIRED = "review_required"


class IngestionDecisionStatus(StrEnum):
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    NEEDS_REVIEW = "NEEDS_REVIEW"


class DatasetCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    language: str = Field(min_length=2, max_length=80)
    owner: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=2000)
    license_name: str = Field(min_length=1, max_length=120)
    permitted_purposes: list[str] = Field(default_factory=list)
    commercial_use: bool | None = None
    redistribution_allowed: bool | None = None
    derivative_work_allowed: bool | None = None
    geographic_restrictions: list[str] = Field(default_factory=list)
    expires_on: date | None = None
    revoked: bool = False
    evidence_verified: bool = False
    evidence_independently_verified: bool = False
    synthetic: bool = True
    source_name: str | None = Field(default=None, max_length=200)
    source_url: str | None = Field(default=None, max_length=2048)
    source_dataset_id: str | None = Field(default=None, max_length=200)
    source_version: str | None = Field(default=None, max_length=120)
    language_variety: str | None = Field(default=None, max_length=120)
    modalities: list[str] | None = Field(default=None, max_length=10)
    collection_method: str | None = Field(default=None, max_length=200)
    data_contributor_claim: str | None = Field(default=None, max_length=200)
    license_version: str | None = Field(default=None, max_length=120)
    intended_use_notes: str | None = Field(default=None, max_length=1000)
    known_restrictions: str | None = Field(default=None, max_length=1000)
    collection_period: str | None = Field(default=None, max_length=120)
    provenance_notes: str | None = Field(default=None, max_length=1000)

    @field_validator(
        "source_name",
        "source_dataset_id",
        "source_version",
        "language_variety",
        "collection_method",
        "data_contributor_claim",
        "license_version",
        "intended_use_notes",
        "known_restrictions",
        "collection_period",
        "provenance_notes",
        mode="before",
    )
    @classmethod
    def normalize_provenance_text(cls, value: str | None) -> str | None:
        if isinstance(value, str):
            return value.strip() or None
        return value

    @field_validator("source_url")
    @classmethod
    def validate_source_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            return None
        parsed = urlsplit(value)
        try:
            port = parsed.port
        except ValueError as error:
            raise ValueError("Source URL must be a valid HTTP(S) URL.") from error
        if (
            parsed.scheme.lower() not in ("http", "https")
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or port is not None and not 1 <= port <= 65535
        ):
            raise ValueError("Source URL must be HTTP(S), without credentials, query strings, or fragments.")
        return value

    @field_validator("modalities")
    @classmethod
    def validate_modalities(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        normalized = [item.strip() for item in value]
        if any(not item for item in normalized):
            raise ValueError("Modalities cannot contain empty values.")
        if len(normalized) != len(set(normalized)):
            raise ValueError("Modalities must be unique.")
        return normalized or None


class DatasetRecord(DatasetCreate):
    id: int
    owner: str = Field(default="", max_length=200)
    provenance_status: Literal["NOT_RECORDED", "REGISTRANT_SUPPLIED"] = "NOT_RECORDED"
    metadata_claimed_at: str | None = None
    metadata_origin: Literal["REGISTRANT"] | None = None
    synthetic_test_fixture: bool = False


class LicenseCheck(BaseModel):
    purpose: str = Field(min_length=1, max_length=120)
    commercial: bool = False
    geography: str | None = None
    redistribution_requested: bool = False
    derivative_work_requested: bool = False


class LicenseDecision(BaseModel):
    dataset_id: int
    status: DecisionStatus
    reasons: list[str]


class RightsPayload(BaseModel):
    """Rights assertions signed by a submitting key; not proof of legal ownership."""

    model_config = ConfigDict(extra="forbid")

    dataset_id: int = Field(gt=0)
    grantor: str = Field(min_length=1, max_length=200)
    license_name: str = Field(min_length=1, max_length=120)
    permitted_purposes: list[str] = Field(default_factory=list)
    commercial_use: bool | None = None
    redistribution_allowed: bool | None = None
    derivative_work_allowed: bool | None = None
    geographic_restrictions: list[str] = Field(default_factory=list)
    expires_on: date | None = None
    revoked: bool = False


class RightsSubmission(BaseModel):
    model_config = ConfigDict(extra="forbid")

    payload: RightsPayload
    signer_id: str = Field(min_length=1, max_length=200)
    public_key: str = Field(min_length=1, max_length=256)
    signature: str = Field(min_length=1, max_length=256)


class EvidenceSubmissionCreate(BaseModel):
    evidence_type: str = Field(min_length=1, max_length=80)
    description: str = Field(min_length=1, max_length=500)
    reference: str = Field(min_length=1, max_length=2048)
    submitter_id: str | None = Field(default=None, min_length=1, max_length=120)

    @field_validator("reference")
    @classmethod
    def validate_reference(cls, value: str) -> str:
        parsed = urlsplit(value)
        try:
            port = parsed.port
        except ValueError as error:
            raise ValueError("Evidence reference must be a valid HTTPS URL.") from error
        if (
            parsed.scheme.lower() != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or port is not None and not 1 <= port <= 65535
        ):
            raise ValueError(
                "Evidence references must be HTTPS URLs without embedded credentials, query strings, or fragments."
            )
        return value


class EvidenceReviewStatus(StrEnum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    MORE_EVIDENCE_REQUESTED = "MORE_EVIDENCE_REQUESTED"


class EvidenceSubmissionResponse(BaseModel):
    id: int
    dataset_id: int
    evidence_type: str
    description: str
    reference: str
    submitter_id: str | None
    submitted_at: str
    review_status: EvidenceReviewStatus
    reviewer_id: str | None
    reviewed_at: str | None
    review_reason: str | None


class EvidenceStatusItem(BaseModel):
    id: int
    evidence_type: str
    submitted_at: str
    review_status: EvidenceReviewStatus
    reviewed_at: str | None
    review_reason: str | None


class EvidenceStatusResponse(BaseModel):
    dataset_id: int
    submissions: list[EvidenceStatusItem]


class EvidenceReviewRequest(BaseModel):
    decision: Literal["APPROVED", "REJECTED", "MORE_EVIDENCE_REQUESTED"]
    reason: str = Field(min_length=1, max_length=1000)

    @field_validator("reason")
    @classmethod
    def validate_reason(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("A review reason is required.")
        if re.search(r"\b(?:bearer\s+\S+|(?:api[_ -]?key|secret|password|token)\s*[:=]\s*\S+)", value, re.IGNORECASE):
            raise ValueError("Review reasons must not contain credentials or secrets.")
        return value.strip()


class EvidenceReviewResponse(BaseModel):
    evidence: EvidenceSubmissionResponse
    dataset_evidence_approved: bool


class ReviewerStatusResponse(BaseModel):
    configured: bool


class EvidenceReviewQueueItem(BaseModel):
    evidence: EvidenceSubmissionResponse
    dataset_name: str
    owner_claim: str
    current_decision: IngestionDecisionStatus
    current_reason_codes: list[str]
    current_explanation: str


class EvidenceReviewQueueResponse(BaseModel):
    reviewer_id: str
    items: list[EvidenceReviewQueueItem]


class RightsRecordResponse(BaseModel):
    id: int
    payload: RightsPayload
    signer_id: str
    public_key: str
    signature: str
    signature_valid: bool
    verification_message: str
    ownership_proven: bool = False
    created_at: str


class RightsAnchorRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    purpose: str = Field(min_length=1, max_length=120)
    context: Literal["research", "commercial"]
    geography: str | None = Field(default=None, max_length=120)
    redistribution_requested: bool = False
    derivative_work_requested: bool = False


class RightsLedgerRecordResponse(RightsRecordResponse):
    policy_version: str
    licensing_decision: IngestionDecisionStatus
    decision_reason_codes: list[str]
    decision_scope: dict
    current_record_hash: str
    anchored_record_hash: str | None = None
    ledger_status: Literal["DATABASE_ONLY", "PENDING", "ANCHORED", "FAILED"]
    network_name: str | None = None
    contract_address: str | None = None
    transaction_hash: str | None = None
    block_number: int | None = None
    authorized_sender: str | None = None
    verification_status: Literal["NOT_CHECKED", "NOT_ANCHORED", "MATCH", "MISMATCH", "UNAVAILABLE"]
    verified: bool | None = None
    anchor_error: str | None = None
    anchor_updated_at: str | None = None


class RightsAnchorResponse(BaseModel):
    record_id: int
    dataset_id: int
    policy_version: str
    current_record_hash: str
    anchored_record_hash: str | None = None
    ledger_status: Literal["DATABASE_ONLY", "PENDING", "ANCHORED", "FAILED"]
    network_name: str | None = None
    contract_address: str | None = None
    transaction_hash: str | None = None
    block_number: int | None = None
    authorized_sender: str | None = None
    verification_status: Literal["NOT_CHECKED", "NOT_ANCHORED", "MATCH", "MISMATCH", "UNAVAILABLE"]
    verified: bool | None = None
    error_message: str | None = None
    updated_at: str | None = None
    ownership_proven: bool = False


class AuditIntegrityResponse(BaseModel):
    intact: bool
    event_count: int
    first_invalid_event_id: int | None = None


class IngestionDecisionRequest(BaseModel):
    dataset_id: int = Field(gt=0)
    purpose: str = Field(min_length=1, max_length=120)
    context: Literal["research", "commercial"]
    geography: str | None = Field(default=None, max_length=120)
    redistribution_requested: bool = False
    derivative_work_requested: bool = False


class IngestionDecisionResponse(BaseModel):
    dataset_id: int
    decision: IngestionDecisionStatus
    reason_codes: list[str]
    human_readable_explanation: str
    policy_version: str
    evidence_verification_status: str
    rights_ledger_reference: dict | None
    audit_event_reference: int | None = None
    timestamp: str


class ManifestRequest(BaseModel):
    purpose: str = Field(min_length=1, max_length=120)
    context: Literal["research", "commercial"]
    geography: str | None = Field(default=None, max_length=120)
    redistribution_requested: bool = False
    derivative_work_requested: bool = False


class ManifestEntry(BaseModel):
    dataset_id: int
    decision: IngestionDecisionStatus
    reason_codes: list[str]
    policy_version: str
    timestamp: str
    evidence_verification_status: str
    evidence_references: list[dict]
    audit_event_reference: int
    synthetic_test_fixture: bool = False


class CorpusManifest(BaseModel):
    manifest_type: str
    policy_version: str
    generated_at: str
    purpose: str
    context: Literal["research", "commercial"]
    geography: str | None
    redistribution_requested: bool = False
    derivative_work_requested: bool = False
    synthetic_test_output: bool = False
    entries: list[ManifestEntry]


class ManifestGenerationResponse(BaseModel):
    generated_at: str
    policy_version: str
    counts: dict[str, int]
    files: dict[str, str]
    audit_event_reference: int


class EligibleDatasetsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    purpose: str = Field(min_length=1, max_length=120)
    context: Literal["research", "commercial"]
    geography: str | None = Field(default=None, max_length=120)
    redistribution_requested: bool = False
    derivative_work_requested: bool = False
    dataset_ids: list[int] | None = None

    @field_validator("dataset_ids")
    @classmethod
    def dataset_ids_must_be_unique_and_positive(cls, value):
        if value is not None and (any(dataset_id < 1 for dataset_id in value) or len(value) != len(set(value))):
            raise ValueError("dataset_ids must contain unique positive IDs")
        return value


class CostAllocationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dataset_ids: list[int] = Field(min_length=1)
    purpose: str = Field(default="research_training", min_length=1, max_length=120)
    context: Literal["research", "commercial"] = "research"
    geography: str | None = Field(default=None, max_length=120)
    redistribution_requested: bool = False
    derivative_work_requested: bool = False
    budget: Decimal = Field(ge=0, max_digits=12, decimal_places=2)
    currency: Literal["USD"] = "USD"

    @field_validator("dataset_ids")
    @classmethod
    def dataset_ids_must_be_unique_and_positive(cls, value):
        if any(dataset_id < 1 for dataset_id in value) or len(value) != len(set(value)):
            raise ValueError("dataset_ids must contain unique positive IDs")
        return value


class OptimizationRequest(EligibleDatasetsRequest):
    budget: Decimal = Field(ge=0, max_digits=12, decimal_places=2)
    currency: Literal["USD"] = "USD"
    solver: Literal["local", "qpiai_opt"] = "local"
    # A remote QpiAI solver call can consume paid credits. It requires an explicit
    # per-request opt-in in addition to configured credentials.
    confirm_remote_job: bool = False


class CostLine(BaseModel):
    dataset_id: int
    dataset_name: str
    cost: Decimal
    currency: str
    utility_score: int
    synthetic_demo_cost: bool
    cost_source: str


class CostAllocationResponse(BaseModel):
    budget: Decimal
    currency: str
    items: list[CostLine]
    total_cost: Decimal
    remaining_budget: Decimal
    budget_status: Literal["WITHIN_BUDGET", "OVER_BUDGET"]


class EligibilityResult(BaseModel):
    dataset_id: int
    decision: IngestionDecisionStatus
    eligible: bool
    reason_codes: list[str]
    explanation: str
    evidence_verification_status: str
    rights_ledger_reference: dict | None


class EligibleDatasetsResponse(BaseModel):
    purpose: str
    context: Literal["research", "commercial"]
    geography: str | None
    policy_version: str
    eligible_datasets: list[EligibilityResult]
    excluded_datasets: list[EligibilityResult]


class OptimizationExcludedDataset(BaseModel):
    dataset_id: int
    decision: IngestionDecisionStatus | None
    reason_codes: list[str]
    explanation: str


class OptimizationSelectedDataset(CostLine):
    pass


class OptimizationResponse(BaseModel):
    optimization_id: int
    selected_dataset_ids: list[int]
    selected_datasets: list[OptimizationSelectedDataset]
    excluded_datasets: list[OptimizationExcludedDataset]
    total_utility: int
    total_licensing_cost: Decimal
    currency: str
    budget: Decimal
    remaining_budget: Decimal
    budget_status: Literal["WITHIN_BUDGET", "OVER_BUDGET"]
    eligibility_verification_results: list[EligibilityResult]
    algorithm: str
    optimality_guaranteed: bool
    input_size: int
    validation_passed: bool
    audit_event_reference: int
    requested_solver: Literal["local", "qpiai_opt"] = "local"
    solver_used: Literal["local_baseline", "qpiai_opt", "none"] = "local_baseline"
    result_origin: Literal["local_baseline", "qpiai_opt"] = "local_baseline"
    execution_status: Literal["completed", "fallback", "not_run"] = "completed"
    fallback_reason: str | None = None
    solver_job_id: str | None = None
    solver_status: str | None = None


class QuantumExperimentRequest(EligibleDatasetsRequest):
    dataset_ids: list[int] = Field(min_length=1, max_length=20)
    budget: Decimal = Field(ge=0, max_digits=12, decimal_places=2)
    mode: Literal["local_simulation", "qcloud"] = "local_simulation"
    shots: int = Field(default=256, ge=32, le=4096)
    seed: int = Field(default=1729, ge=0, le=2_147_483_647)
    confirm_remote_job: bool = False


class QuantumExperimentResponse(BaseModel):
    experiment_id: int
    status: Literal["completed", "no_eligible_datasets", "no_feasible_sample"]
    execution_mode: Literal["local_statevector_simulation", "qcloud", "not_run"]
    result_kind: Literal["simulation_result", "qcloud_measurements", "not_run"]
    remote_job_id: str | None = None
    sdk_name: str
    sdk_version: str | None
    candidate_dataset_ids: list[int]
    eligibility_results: list[EligibilityResult]
    excluded_datasets: list[OptimizationExcludedDataset]
    selected_dataset_ids: list[int]
    total_utility: int
    total_licensing_cost: Decimal
    budget: Decimal
    remaining_budget: Decimal
    budget_status: Literal["WITHIN_BUDGET", "OVER_BUDGET"]
    baseline_selected_dataset_ids: list[int]
    baseline_total_utility: int
    sample_counts: dict[str, int]
    experiment_configuration: dict
    limitations: list[str]
    validation_passed: bool
    validation_errors: list[str]
    audit_event_reference: int


class DashboardSummaryResponse(BaseModel):
    dataset_count: int
    approved_count: int
    rejected_count: int
    needs_review_count: int
    decision_scope: dict
    total_licensing_cost: Decimal
    currency: Literal["USD"]
    synthetic_cost_dataset_count: int
    dataset_statuses: list[dict]
    dataset_costs: list[dict]
    recent_audit_activity: list[dict]
    latest_optimization: dict | None
