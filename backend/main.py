import json
import hmac
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from . import manifests as corpus_manifests
from .audit import append_audit_event, list_audit_events, verify_audit_chain
from .blockchain_service import (
    BlockchainError,
    BlockchainTransactionFailed,
    BlockchainTransactionPending,
    anchor_record as anchor_record_on_chain,
    get_chain_record,
    hash_rights_record,
    load_config as load_blockchain_config,
)
from .database import connect, initialize_database
from .decision_service import (
    DatasetNotFoundError,
    POLICY_VERSION,
    evaluate_dataset_request,
)
from .demo_data import DEMO_DATASETS
from .cost_allocation import (
    CostDataUnavailableError,
    calculate_allocation,
    get_cost_lines,
)
from .cost_allocation import minor_units_from_money, money_from_minor_units
from .eligibility import check_dataset_eligibility
from .manifests import write_manifests
from .optimizer import OptimizationSafetyError, run_optimization
from .quantum_experiment import (
    QuantumExperimentError,
    RemoteApprovalRequired,
    run_quantum_experiment,
)
from .qcloud_adapter import QCloudError
from .schemas import (
    AuditIntegrityResponse,
    CostAllocationRequest,
    CostAllocationResponse,
    DashboardSummaryResponse,
    DatasetCreate,
    DatasetRecord,
    EvidenceReviewQueueItem,
    EvidenceReviewQueueResponse,
    EvidenceReviewRequest,
    EvidenceReviewResponse,
    EvidenceReviewStatus,
    EvidenceSubmissionCreate,
    EvidenceSubmissionResponse,
    EvidenceStatusItem,
    EvidenceStatusResponse,
    EligibleDatasetsRequest,
    EligibleDatasetsResponse,
    IngestionDecisionRequest,
    IngestionDecisionResponse,
    IngestionDecisionStatus,
    LicenseCheck,
    LicenseDecision,
    ManifestGenerationResponse,
    ManifestRequest,
    OptimizationRequest,
    OptimizationResponse,
    QuantumExperimentRequest,
    QuantumExperimentResponse,
    RightsPayload,
    RightsAnchorRequest,
    RightsAnchorResponse,
    RightsLedgerRecordResponse,
    RightsRecordResponse,
    RightsSubmission,
    ReviewerStatusResponse,
)
from .signatures import canonicalize_rights, verify_rights_signature

REVIEWER_ENV_FILES = (
    Path(__file__).resolve().parent / ".env",
    Path(__file__).resolve().parent.parent / "backend.env",
)
SYNTHETIC_EVIDENCE_TYPE = "synthetic demo license reference"


def _decode(row) -> DatasetRecord:
    values = dict(row)
    values["permitted_purposes"] = json.loads(values["permitted_purposes"])
    values["geographic_restrictions"] = json.loads(values["geographic_restrictions"])
    values["modalities"] = (
        json.loads(values.pop("modalities_json"))
        if values.get("modalities_json") is not None
        else None
    )
    for key in (
        "commercial_use",
        "redistribution_allowed",
        "derivative_work_allowed",
        "revoked",
        "evidence_verified",
        "evidence_independently_verified",
        "synthetic",
        "synthetic_test_fixture",
    ):
        if values[key] is not None:
            values[key] = bool(values[key])
    values.pop("modalities_json", None)
    return DatasetRecord(**values)


def _insert_dataset(dataset: DatasetCreate, *, audit_registration: bool = False) -> DatasetRecord:
    values = dataset.model_dump()
    provenance_fields = (
        "source_name",
        "source_url",
        "source_dataset_id",
        "source_version",
        "language_variety",
        "modalities",
        "collection_method",
        "data_contributor_claim",
        "license_version",
        "intended_use_notes",
        "known_restrictions",
        "collection_period",
        "provenance_notes",
    )
    provenance_supplied = any(values[field] for field in provenance_fields)
    metadata_claimed_at = datetime.now(timezone.utc).isoformat() if provenance_supplied else None
    provenance_status = "REGISTRANT_SUPPLIED" if provenance_supplied else "NOT_RECORDED"
    with connect() as connection:
        cursor = connection.execute(
            """INSERT INTO datasets (name, language, owner, description, license_name,
                permitted_purposes, commercial_use, redistribution_allowed,
                derivative_work_allowed, geographic_restrictions,
                expires_on, revoked, evidence_verified, evidence_independently_verified, synthetic,
                source_name, source_url, source_dataset_id, source_version, language_variety,
                modalities_json, collection_method, data_contributor_claim, license_version,
                intended_use_notes, known_restrictions, collection_period, provenance_notes,
                provenance_status, metadata_claimed_at, metadata_origin)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                values["name"], values["language"], values["owner"], values["description"], values["license_name"],
                json.dumps(values["permitted_purposes"]),
                None if values["commercial_use"] is None else int(values["commercial_use"]),
                None if values["redistribution_allowed"] is None else int(values["redistribution_allowed"]),
                None if values["derivative_work_allowed"] is None else int(values["derivative_work_allowed"]),
                json.dumps(values["geographic_restrictions"]),
                values["expires_on"].isoformat() if values["expires_on"] else None,
                int(values["revoked"]), int(values["evidence_verified"]),
                int(values["evidence_independently_verified"]), int(values["synthetic"]),
                values["source_name"], values["source_url"], values["source_dataset_id"],
                values["source_version"], values["language_variety"],
                json.dumps(values["modalities"]) if values["modalities"] is not None else None,
                values["collection_method"], values["data_contributor_claim"],
                values["license_version"], values["intended_use_notes"],
                values["known_restrictions"], values["collection_period"],
                values["provenance_notes"], provenance_status, metadata_claimed_at,
                "REGISTRANT" if provenance_supplied else None,
            ),
        )
        row = connection.execute("SELECT * FROM datasets WHERE id = ?", (cursor.lastrowid,)).fetchone()
        if audit_registration:
            append_audit_event(
                connection,
                "dataset_registered",
                "dataset",
                str(cursor.lastrowid),
                {
                    "dataset_id": cursor.lastrowid,
                    "name": values["name"],
                    "language": values["language"],
                    "owner": values["owner"],
                    "license_name": values["license_name"],
                    "evidence_verified": False,
                    "evidence_independently_verified": False,
                    "provenance_status": provenance_status,
                    "metadata_origin": "REGISTRANT" if provenance_supplied else None,
                    "source_name": values["source_name"],
                    "source_url": values["source_url"],
                    "source_dataset_id": values["source_dataset_id"],
                    "source_version": values["source_version"],
                },
            )
    return _decode(row)


def _decode_rights_record(row) -> RightsRecordResponse:
    payload = RightsPayload(**json.loads(row["payload_json"]))
    cryptographically_valid, verification_message = verify_rights_signature(
        payload,
        row["public_key"],
        row["signature"],
    )
    signature_valid = cryptographically_valid and bool(row["signature_valid"])
    if cryptographically_valid != bool(row["signature_valid"]):
        verification_message = "Stored signature status does not match cryptographic verification."

    return RightsRecordResponse(
        id=row["id"],
        payload=payload,
        signer_id=row["signer_id"],
        public_key=row["public_key"],
        signature=row["signature"],
        signature_valid=signature_valid,
        verification_message=verification_message,
        ownership_proven=False,
        created_at=row["created_at"],
    )


def _get_rights_record_row(record_id: int):
    with connect() as connection:
        return connection.execute("SELECT * FROM rights_records WHERE id = ?", (record_id,)).fetchone()


def _get_anchor_row(record_id: int):
    with connect() as connection:
        return connection.execute(
            "SELECT * FROM rights_chain_anchors WHERE record_id = ?", (record_id,)
        ).fetchone()


def _anchor_status(record_row, anchor_row=None, verification_status="NOT_CHECKED", verified=None, error_message=None):
    record = dict(record_row)
    policy_version = anchor_row["policy_version"] if anchor_row else POLICY_VERSION
    current_hash = hash_rights_record(record, policy_version)
    status = anchor_row["status"] if anchor_row else "DATABASE_ONLY"
    return RightsAnchorResponse(
        record_id=record["id"],
        dataset_id=record["dataset_id"],
        policy_version=policy_version,
        current_record_hash=current_hash,
        anchored_record_hash=(anchor_row["record_hash"] if anchor_row and anchor_row["status"] == "ANCHORED" else None),
        ledger_status=status,
        network_name=anchor_row["network_name"] if anchor_row else None,
        contract_address=anchor_row["contract_address"] if anchor_row else None,
        transaction_hash=anchor_row["transaction_hash"] if anchor_row else None,
        block_number=anchor_row["block_number"] if anchor_row else None,
        authorized_sender=anchor_row["authorized_sender"] if anchor_row else None,
        verification_status=verification_status,
        verified=verified,
        error_message=error_message or (anchor_row["error_message"] if anchor_row else None),
        updated_at=anchor_row["updated_at"] if anchor_row else None,
    )


def _upsert_anchor(record_id: int, *, record_hash: str, policy_version: str, status: str,
                   network_name: str, contract_address: str | None = None,
                   transaction_hash: str | None = None, block_number: int | None = None,
                   authorized_sender: str | None = None, anchored_at: str | None = None,
                   error_message: str | None = None) -> None:
    updated_at = datetime.now(timezone.utc).isoformat()
    with connect() as connection:
        connection.execute(
            """INSERT INTO rights_chain_anchors
                (record_id, record_hash, policy_version, status, network_name, contract_address,
                 transaction_hash, block_number, authorized_sender, anchored_at, error_message, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(record_id) DO UPDATE SET
                 record_hash=excluded.record_hash,
                 policy_version=excluded.policy_version,
                 status=excluded.status,
                 network_name=excluded.network_name,
                 contract_address=excluded.contract_address,
                 transaction_hash=excluded.transaction_hash,
                 block_number=excluded.block_number,
                 authorized_sender=excluded.authorized_sender,
                 anchored_at=excluded.anchored_at,
                 error_message=excluded.error_message,
                 updated_at=excluded.updated_at""",
            (record_id, record_hash, policy_version, status, network_name, contract_address,
             transaction_hash, block_number, authorized_sender, anchored_at, error_message, updated_at),
        )


def _reviewer_configuration() -> tuple[str, str] | None:
        file_settings: dict[str, str] = {}
        for env_file in REVIEWER_ENV_FILES:
            try:
                for raw_line in env_file.read_text(encoding="utf-8").splitlines():
                    line = raw_line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    name, value = line.split("=", 1)
                    file_settings[name.strip()] = value.strip().strip("\"'")
            except FileNotFoundError:
                continue
        token = (
            os.environ.get("BHASHATRUST_REVIEWER_TOKEN")
            or file_settings.get("BHASHATRUST_REVIEWER_TOKEN", "")
        ).strip()
        reviewer_id = (
            os.environ.get("BHASHATRUST_REVIEWER_ID")
            or file_settings.get("BHASHATRUST_REVIEWER_ID", "")
        ).strip()
        if len(token) < 32 or not reviewer_id:
            return None
        return token, reviewer_id


def _require_reviewer(authorization: str | None) -> str:
        configuration = _reviewer_configuration()
        if configuration is None:
            raise HTTPException(status_code=503, detail="Trusted reviewer access is not configured.")
        token, reviewer_id = configuration
        supplied_token = authorization.removeprefix("Bearer ").strip() if authorization else ""
        if not supplied_token or not hmac.compare_digest(
            token.encode("utf-8"), supplied_token.encode("utf-8")
        ):
            raise HTTPException(status_code=401, detail="Reviewer authorization failed.")
        return reviewer_id


def _decode_evidence_submission(row) -> EvidenceSubmissionResponse:
        return EvidenceSubmissionResponse(**dict(row))


def _reopen_evidence_review(connection, dataset_id: int, reason: str) -> None:
        dataset = connection.execute(
            "SELECT evidence_independently_verified FROM datasets WHERE id = ?",
            (dataset_id,),
        ).fetchone()
        evidence_rows = connection.execute(
            "SELECT id, review_status FROM evidence_submissions WHERE dataset_id = ?",
            (dataset_id,),
        ).fetchall()
        was_reviewed = bool(dataset["evidence_independently_verified"]) or any(
            row["review_status"] != EvidenceReviewStatus.PENDING.value for row in evidence_rows
        )
        connection.execute(
            """UPDATE datasets SET evidence_independently_verified = 0, evidence_verified = 0
               WHERE id = ?""",
            (dataset_id,),
        )
        if evidence_rows:
            connection.execute(
                """UPDATE evidence_submissions
                   SET review_status = 'PENDING', reviewer_id = NULL, reviewed_at = NULL, review_reason = NULL
                   WHERE dataset_id = ?""",
                (dataset_id,),
            )
        if was_reviewed:
            append_audit_event(
                connection,
                "evidence_review_reopened",
                "dataset",
                str(dataset_id),
                {"dataset_id": dataset_id, "reason": reason},
            )


def _rights_claim_changes_reviewed_terms(payload: RightsPayload, dataset_row) -> bool:
    dataset_purposes = set(json.loads(dataset_row["permitted_purposes"]))
    dataset_geography = set(json.loads(dataset_row["geographic_restrictions"]))
    payload_values = payload.model_dump(mode="json")
    return any(
            (
                payload.grantor != dataset_row["owner"],
                payload.license_name != dataset_row["license_name"],
                set(payload.permitted_purposes) != dataset_purposes,
                payload.commercial_use != (
                    None if dataset_row["commercial_use"] is None else bool(dataset_row["commercial_use"])
                ),
                payload.redistribution_allowed != (
                    None
                    if dataset_row["redistribution_allowed"] is None
                    else bool(dataset_row["redistribution_allowed"])
                ),
                payload.derivative_work_allowed != (
                    None
                    if dataset_row["derivative_work_allowed"] is None
                    else bool(dataset_row["derivative_work_allowed"])
                ),
                set(payload.geographic_restrictions) != dataset_geography,
                payload_values["expires_on"] != dataset_row["expires_on"],
                payload.revoked != bool(dataset_row["revoked"]),
            )
    )


@asynccontextmanager
async def lifespan(_app: FastAPI):
    initialize_database()
    with connect() as connection:
        count = connection.execute("SELECT COUNT(*) FROM datasets").fetchone()[0]
    if count == 0:
        for item in DEMO_DATASETS:
            _insert_dataset(DatasetCreate(**item))
    yield


app = FastAPI(title="BhashaTrust AI", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "Authorization"],
)


@app.get("/health")
def health():
    return {"status": "ok", "project": "BhashaTrust AI"}


@app.get("/datasets", response_model=list[DatasetRecord])
@app.get("/api/datasets", response_model=list[DatasetRecord])
def list_datasets():
    with connect() as connection:
        rows = connection.execute("SELECT * FROM datasets ORDER BY id").fetchall()
    return [_decode(row) for row in rows]


@app.post("/datasets", response_model=DatasetRecord, status_code=201)
@app.post("/api/datasets", response_model=DatasetRecord, status_code=201)
def register_dataset(dataset: DatasetCreate):
    dataset = dataset.model_copy(
        update={"evidence_verified": False, "evidence_independently_verified": False}
    )
    return _insert_dataset(dataset, audit_registration=True)


@app.get("/datasets/{dataset_id}", response_model=DatasetRecord)
@app.get("/api/datasets/{dataset_id}", response_model=DatasetRecord)
def get_dataset(dataset_id: int):
    with connect() as connection:
        row = connection.execute("SELECT * FROM datasets WHERE id = ?", (dataset_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Dataset not found")
    return _decode(row)


@app.get("/api/dashboard/summary", response_model=DashboardSummaryResponse)
def dashboard_summary(
    purpose: str = Query(default="research_training", min_length=1, max_length=120),
    context: str = Query(default="research", pattern="^(research|commercial)$"),
    geography: str | None = Query(default=None, max_length=120),
    redistribution_requested: bool = False,
    derivative_work_requested: bool = False,
):
    """Read-only dashboard aggregation using current licensing engine results."""
    with connect() as connection:
        dataset_ids = [row["id"] for row in connection.execute("SELECT id FROM datasets ORDER BY id")]

    eligibility = check_dataset_eligibility(
        EligibleDatasetsRequest(
            purpose=purpose,
            context=context,
            geography=geography,
            redistribution_requested=redistribution_requested,
            derivative_work_requested=derivative_work_requested,
            dataset_ids=dataset_ids,
        )
    )
    counts = {status.value: 0 for status in IngestionDecisionStatus}
    for item in eligibility.eligible_datasets + eligibility.excluded_datasets:
        counts[item.decision.value] += 1
    dataset_statuses = [
        {
            "dataset_id": item.dataset_id,
            "decision": item.decision.value,
            "eligible": item.eligible,
            "reason_codes": item.reason_codes,
            "explanation": item.explanation,
        }
        for item in eligibility.eligible_datasets + eligibility.excluded_datasets
    ]

    costs = []
    for dataset_id in dataset_ids:
        try:
            costs.extend(get_cost_lines([dataset_id], "USD"))
        except LookupError:
            continue
    synthetic_lines = [line for line in costs if line.synthetic_demo_cost]
    total_cost_minor = sum(minor_units_from_money(line.cost) for line in synthetic_lines)

    recent_audit = list_audit_events()[-8:][::-1]
    with connect() as connection:
        optimization = connection.execute(
            "SELECT id, result_json, created_at FROM optimization_results ORDER BY id DESC LIMIT 1"
        ).fetchone()
    latest_optimization = None
    if optimization:
        try:
            saved = json.loads(optimization["result_json"])
            latest_optimization = {
                "optimization_id": optimization["id"],
                "created_at": optimization["created_at"],
                "selected_dataset_ids": saved.get("selected_dataset_ids", []),
                "total_utility": saved.get("total_utility"),
                "total_licensing_cost": saved.get("total_licensing_cost"),
                "solver_used": saved.get("solver_used", "local_baseline"),
                "result_origin": saved.get("result_origin", "local_baseline"),
            }
        except (TypeError, ValueError):
            latest_optimization = None

    return DashboardSummaryResponse(
        dataset_count=len(dataset_ids),
        approved_count=counts[IngestionDecisionStatus.APPROVED.value],
        rejected_count=counts[IngestionDecisionStatus.REJECTED.value],
        needs_review_count=counts[IngestionDecisionStatus.NEEDS_REVIEW.value],
        decision_scope={
            "purpose": purpose,
            "context": context,
            "geography": geography,
            "redistribution_requested": redistribution_requested,
            "derivative_work_requested": derivative_work_requested,
        },
        total_licensing_cost=money_from_minor_units(total_cost_minor),
        currency="USD",
        synthetic_cost_dataset_count=len(synthetic_lines),
        dataset_statuses=dataset_statuses,
        dataset_costs=[
            {
                "dataset_id": line.dataset_id,
                "cost": line.cost,
                "currency": line.currency,
                "synthetic_demo_cost": line.synthetic_demo_cost,
                "cost_source": line.cost_source,
            }
            for line in costs
        ],
        recent_audit_activity=recent_audit,
        latest_optimization=latest_optimization,
    )


@app.post("/datasets/{dataset_id}/evaluate", response_model=LicenseDecision)
def evaluate_dataset(dataset_id: int, request: LicenseCheck):
    try:
        evaluation = evaluate_dataset_request(dataset_id, request)
    except DatasetNotFoundError as error:
        raise HTTPException(status_code=404, detail="Dataset not found") from error
    with connect() as connection:
        append_audit_event(
            connection,
            "license_decision",
            "dataset",
            str(dataset_id),
            {
                "request": request.model_dump(mode="json"),
                "decision": evaluation.engine_decision.model_dump(mode="json"),
                "rights_record_id": (
                    evaluation.result.rights_ledger_reference["record_id"]
                    if evaluation.result.rights_ledger_reference
                    and evaluation.result.rights_ledger_reference["signature_valid"]
                    else None
                ),
            },
        )
    return evaluation.engine_decision


@app.post("/api/decisions", response_model=IngestionDecisionResponse)
def create_ingestion_decision(request: IngestionDecisionRequest):
    license_check = LicenseCheck(
        purpose=request.purpose,
        commercial=request.context == "commercial",
        geography=request.geography,
        redistribution_requested=request.redistribution_requested,
        derivative_work_requested=request.derivative_work_requested,
    )
    try:
        evaluation = evaluate_dataset_request(request.dataset_id, license_check)
    except DatasetNotFoundError as error:
        raise HTTPException(status_code=404, detail="Dataset not found") from error

    with connect() as connection:
        event_id = append_audit_event(
            connection,
            "ingestion_decision",
            "dataset",
            str(request.dataset_id),
            {
                "request": request.model_dump(mode="json"),
                "decision": evaluation.result.decision.value,
                "reason_codes": evaluation.result.reason_codes,
                "policy_version": POLICY_VERSION,
                "evidence_verification_status": evaluation.result.evidence_verification_status,
                "rights_ledger_reference": evaluation.result.rights_ledger_reference,
            },
        )
    evaluation.result.audit_event_reference = event_id
    return evaluation.result


@app.post("/api/manifests/generate", response_model=ManifestGenerationResponse)
def generate_corpus_manifests(request: ManifestRequest):
    license_check = LicenseCheck(
        purpose=request.purpose,
        commercial=request.context == "commercial",
        geography=request.geography,
        redistribution_requested=request.redistribution_requested,
        derivative_work_requested=request.derivative_work_requested,
    )
    with connect() as connection:
        dataset_rows = connection.execute(
            "SELECT id, synthetic_test_fixture FROM datasets ORDER BY id"
        ).fetchall()
    synthetic_test_fixture_ids = {
        row["id"] for row in dataset_rows if row["synthetic_test_fixture"]
    }

    evaluations = []
    for row in dataset_rows:
        evaluation = evaluate_dataset_request(row["id"], license_check)
        with connect() as connection:
            decision_event_id = append_audit_event(
                connection,
                "manifest_decision",
                "dataset",
                str(row["id"]),
                {
                    "purpose": request.purpose,
                    "context": request.context,
                    "geography": request.geography,
                    "redistribution_requested": request.redistribution_requested,
                    "derivative_work_requested": request.derivative_work_requested,
                    "decision": evaluation.result.decision.value,
                    "reason_codes": evaluation.result.reason_codes,
                    "policy_version": POLICY_VERSION,
                    "evidence_verification_status": evaluation.result.evidence_verification_status,
                    "rights_ledger_reference": evaluation.result.rights_ledger_reference,
                },
            )
        evaluations.append((evaluation, decision_event_id))

    try:
        generated_at, counts, files, batch_id = write_manifests(
            evaluations,
            request,
            synthetic_test_fixture_ids=synthetic_test_fixture_ids,
        )
    except OSError as error:
        raise HTTPException(status_code=500, detail="Unable to write corpus manifests under the project folder.") from error

    with connect() as connection:
        generation_event_id = append_audit_event(
            connection,
            "manifest_generation",
            "corpus_manifest_batch",
            batch_id,
            {
                "generated_at": generated_at,
                "policy_version": POLICY_VERSION,
                "purpose": request.purpose,
                "context": request.context,
                "geography": request.geography,
                "redistribution_requested": request.redistribution_requested,
                "derivative_work_requested": request.derivative_work_requested,
                "counts": counts,
                "files": files,
            },
        )
    return ManifestGenerationResponse(
        generated_at=generated_at,
        policy_version=POLICY_VERSION,
        counts=counts,
        files=files,
        audit_event_reference=generation_event_id,
    )


@app.get("/api/manifests/latest")
def get_latest_manifests():
    """Read the newest actual manifest file in each decision category."""
    manifests = {}
    for category in corpus_manifests.MANIFEST_TYPES:
        directory = corpus_manifests.MANIFEST_ROOT / category
        paths = list(directory.glob("manifest-*.json")) if directory.is_dir() else []
        if not paths:
            manifests[category] = None
            continue
        path = max(paths, key=lambda item: item.stat().st_mtime_ns)
        try:
            manifests[category] = {
                "filename": path.name,
                "manifest": json.loads(path.read_text(encoding="utf-8")),
            }
        except (OSError, json.JSONDecodeError) as error:
            raise HTTPException(status_code=500, detail=f"Unable to read the {category} manifest.") from error
    return {"manifests": manifests}


@app.post("/api/costs/allocate", response_model=CostAllocationResponse)
def allocate_licensing_costs(request: CostAllocationRequest):
    try:
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
    except DatasetNotFoundError as error:
        raise HTTPException(status_code=404, detail=f"Dataset {error.args[0]} was not found.") from error

    if eligibility.excluded_datasets:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "Cost allocation is restricted to datasets eligible for the requested purpose.",
                "purpose": request.purpose,
                "context": request.context,
                "excluded_datasets": [
                    {
                        "dataset_id": item.dataset_id,
                        "decision": item.decision,
                        "reason_codes": item.reason_codes,
                        "explanation": item.explanation,
                    }
                    for item in eligibility.excluded_datasets
                ],
            },
        )

    try:
        lines = get_cost_lines(request.dataset_ids, request.currency)
    except DatasetNotFoundError as error:
        raise HTTPException(status_code=404, detail=f"Dataset {error.args[0]} was not found.") from error
    except CostDataUnavailableError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error

    allocation = calculate_allocation(lines, request.budget, request.currency)
    with connect() as connection:
        append_audit_event(
            connection,
            "cost_allocation",
            "dataset_selection",
            ",".join(map(str, request.dataset_ids)),
            {
                "request": request.model_dump(mode="json"),
                "result": allocation.model_dump(mode="json"),
                "synthetic_demo_costs": all(item.synthetic_demo_cost for item in lines),
                "eligible_dataset_ids": [item.dataset_id for item in eligibility.eligible_datasets],
                "policy_version": POLICY_VERSION,
            },
        )
    return allocation


@app.post("/api/datasets/eligible", response_model=EligibleDatasetsResponse)
def list_eligible_datasets(request: EligibleDatasetsRequest):
    try:
        return check_dataset_eligibility(request)
    except DatasetNotFoundError as error:
        raise HTTPException(status_code=404, detail=f"Dataset {error.args[0]} was not found.") from error


@app.post("/api/optimizations", response_model=OptimizationResponse)
def optimize_datasets(request: OptimizationRequest):
    try:
        result, audit_payload = run_optimization(request)
    except OptimizationSafetyError as error:
        with connect() as connection:
            append_audit_event(
                connection,
                "optimization_rejected",
                "dataset_selection",
                "pending",
                error.audit_payload,
            )
        raise HTTPException(status_code=409, detail=str(error)) from error
    except DatasetNotFoundError as error:
        raise HTTPException(status_code=404, detail=f"Dataset {error.args[0]} was not found.") from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error

    created_at = datetime.now(timezone.utc).isoformat()
    with connect() as connection:
        audit_id = append_audit_event(
            connection,
            "optimization_run",
            "dataset_selection",
            "pending",
            audit_payload,
        )
        cursor = connection.execute(
            "INSERT INTO optimization_results (result_json, created_at, audit_event_id) VALUES (?, ?, ?)",
            ("{}", created_at, audit_id),
        )
        result.optimization_id = cursor.lastrowid
        result.audit_event_reference = audit_id
        connection.execute(
            "UPDATE optimization_results SET result_json = ? WHERE id = ?",
            (json.dumps(result.model_dump(mode="json"), ensure_ascii=False), cursor.lastrowid),
        )
    return result


@app.get("/api/optimizations/{optimization_id}", response_model=OptimizationResponse)
def get_optimization_result(optimization_id: int):
    with connect() as connection:
        row = connection.execute(
            "SELECT result_json FROM optimization_results WHERE id = ?", (optimization_id,)
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Optimization result not found")
    return OptimizationResponse(**json.loads(row["result_json"]))


@app.post("/api/quantum-experiments", response_model=QuantumExperimentResponse)
def run_quantum_selection_experiment(request: QuantumExperimentRequest):
    try:
        result, audit_payload = run_quantum_experiment(request)
    except RemoteApprovalRequired as error:
        with connect() as connection:
            append_audit_event(
                connection,
                "quantum_experiment_not_started",
                "dataset_selection",
                "pending",
                {
                    "requested_mode": request.mode,
                    "execution_mode": "not_started",
                    "reason": str(error),
                    "remote_job_submitted": False,
                },
            )
        raise HTTPException(status_code=428, detail=str(error)) from error
    except QCloudError as error:
        with connect() as connection:
            append_audit_event(
                connection,
                "quantum_experiment_failed",
                "dataset_selection",
                "pending",
                {"requested_mode": request.mode, "execution_mode": "not_completed", "reason": str(error)},
            )
        raise HTTPException(status_code=503, detail=str(error)) from error
    except DatasetNotFoundError as error:
        raise HTTPException(status_code=404, detail=f"Dataset {error.args[0]} was not found.") from error
    except QuantumExperimentError as error:
        with connect() as connection:
            append_audit_event(
                connection,
                "quantum_experiment_failed",
                "dataset_selection",
                "pending",
                {"requested_mode": request.mode, "execution_mode": "not_completed", "reason": str(error)},
            )
        raise HTTPException(status_code=422, detail=str(error)) from error

    with connect() as connection:
        audit_id = append_audit_event(
            connection,
            "quantum_experiment",
            "dataset_selection",
            "pending",
            audit_payload,
        )
        result.audit_event_reference = audit_id
    return result


@app.post(
    "/datasets/{dataset_id}/rights-records",
    response_model=RightsRecordResponse,
    status_code=201,
)
def submit_rights_record(dataset_id: int, submission: RightsSubmission):
    if submission.payload.dataset_id != dataset_id:
        raise HTTPException(status_code=400, detail="Signed payload dataset_id must match the route.")

    with connect() as connection:
        dataset_row = connection.execute(
            "SELECT * FROM datasets WHERE id = ?", (dataset_id,)
        ).fetchone()
        if not dataset_row:
            raise HTTPException(status_code=404, detail="Dataset not found")
        valid, message = verify_rights_signature(
            submission.payload, submission.public_key, submission.signature
        )
        material_change = valid and _rights_claim_changes_reviewed_terms(
            submission.payload,
            dataset_row,
        )
        if material_change:
            _reopen_evidence_review(
                connection,
                dataset_id,
                "A signed rights claim materially changed the reviewed terms.",
            )
            append_audit_event(
                connection,
                "rights_claim_changed",
                "dataset",
                str(dataset_id),
                {
                    "dataset_id": dataset_id,
                    "signer_id": submission.signer_id,
                    "signature_valid": True,
                    "ownership_proven": False,
                },
            )
        payload_json = canonicalize_rights(submission.payload).decode("utf-8")
        created_at = datetime.now(timezone.utc).isoformat()
        cursor = connection.execute(
            """INSERT INTO rights_records
                (dataset_id, payload_json, signer_id, public_key, signature,
                 signature_valid, verification_message, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                dataset_id,
                payload_json,
                submission.signer_id,
                submission.public_key,
                submission.signature,
                int(valid),
                message,
                created_at,
            ),
        )
        record_id = cursor.lastrowid
        append_audit_event(
            connection,
            "rights_signature_verified",
            "rights_record",
            str(record_id),
            {
                "dataset_id": dataset_id,
                "signer_id": submission.signer_id,
                "signature_valid": valid,
                "ownership_proven": False,
                "message": message,
            },
        )
        saved = connection.execute("SELECT * FROM rights_records WHERE id = ?", (record_id,)).fetchone()
    return _decode_rights_record(saved)


@app.get("/api/reviewer/status", response_model=ReviewerStatusResponse)
def reviewer_status():
    return ReviewerStatusResponse(configured=_reviewer_configuration() is not None)


@app.post(
    "/api/datasets/{dataset_id}/evidence",
    response_model=EvidenceSubmissionResponse,
    status_code=201,
)
def submit_evidence(dataset_id: int, submission: EvidenceSubmissionCreate):
    with connect() as connection:
        exists = connection.execute(
            "SELECT 1 FROM datasets WHERE id = ?", (dataset_id,)
        ).fetchone()
        if not exists:
            raise HTTPException(status_code=404, detail="Dataset not found")

        _reopen_evidence_review(connection, dataset_id, "New evidence was submitted.")
        submitted_at = datetime.now(timezone.utc).isoformat()
        cursor = connection.execute(
            """INSERT INTO evidence_submissions
                (dataset_id, evidence_type, description, reference, submitter_id, submitted_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                dataset_id,
                submission.evidence_type,
                submission.description,
                submission.reference,
                submission.submitter_id,
                submitted_at,
            ),
        )
        evidence_id = cursor.lastrowid
        append_audit_event(
            connection,
            "evidence_submitted",
            "evidence_submission",
            str(evidence_id),
            {
                "dataset_id": dataset_id,
                "evidence_id": evidence_id,
                "evidence_type": submission.evidence_type,
                "submitter_id": submission.submitter_id,
                "review_status": EvidenceReviewStatus.PENDING.value,
            },
        )
        row = connection.execute(
            "SELECT * FROM evidence_submissions WHERE id = ?", (evidence_id,)
        ).fetchone()
    return _decode_evidence_submission(row)


@app.get(
    "/api/datasets/{dataset_id}/evidence/status",
    response_model=EvidenceStatusResponse,
)
def dataset_evidence_status(dataset_id: int):
    with connect() as connection:
        exists = connection.execute(
            "SELECT 1 FROM datasets WHERE id = ?", (dataset_id,)
        ).fetchone()
        if not exists:
            raise HTTPException(status_code=404, detail="Dataset not found")
        rows = connection.execute(
            """SELECT id, evidence_type, submitted_at, review_status, reviewed_at, review_reason
               FROM evidence_submissions WHERE dataset_id = ? ORDER BY id""",
            (dataset_id,),
        ).fetchall()
    return EvidenceStatusResponse(
        dataset_id=dataset_id,
        submissions=[EvidenceStatusItem(**dict(row)) for row in rows],
    )


@app.get("/api/review/queue", response_model=EvidenceReviewQueueResponse)
def evidence_review_queue(
    authorization: str | None = Header(default=None),
    purpose: str = Query(default="research_training", min_length=1, max_length=120),
    context: str = Query(default="research", pattern="^(research|commercial)$"),
):
    reviewer_id = _require_reviewer(authorization)
    with connect() as connection:
        rows = connection.execute(
            """SELECT e.*, d.name AS dataset_name, d.owner AS owner_claim
               FROM evidence_submissions e
               JOIN datasets d ON d.id = e.dataset_id
               ORDER BY CASE e.review_status WHEN 'PENDING' THEN 0 ELSE 1 END, e.id"""
        ).fetchall()

    items = []
    for row in rows:
        evaluation = evaluate_dataset_request(
            row["dataset_id"],
            LicenseCheck(purpose=purpose, commercial=context == "commercial"),
        )
        items.append(
            EvidenceReviewQueueItem(
                evidence=_decode_evidence_submission(row),
                dataset_name=row["dataset_name"],
                owner_claim=row["owner_claim"],
                current_decision=evaluation.result.decision,
                current_reason_codes=evaluation.result.reason_codes,
                current_explanation=evaluation.result.human_readable_explanation,
            )
        )
    return EvidenceReviewQueueResponse(reviewer_id=reviewer_id, items=items)


@app.post(
    "/api/evidence/{evidence_id}/review",
    response_model=EvidenceReviewResponse,
)
def review_evidence(
    evidence_id: int,
    request: EvidenceReviewRequest,
    authorization: str | None = Header(default=None),
):
    reviewer_id = _require_reviewer(authorization)
    reviewed_at = datetime.now(timezone.utc).isoformat()
    with connect() as connection:
        evidence = connection.execute(
            "SELECT * FROM evidence_submissions WHERE id = ?", (evidence_id,)
        ).fetchone()
        if evidence is None:
            raise HTTPException(status_code=404, detail="Evidence submission not found")
        if (
            request.decision == "APPROVED"
            and evidence["evidence_type"].strip().casefold() == SYNTHETIC_EVIDENCE_TYPE
        ):
            raise HTTPException(
                status_code=409,
                detail="Synthetic demo references cannot be approved as evidence.",
            )

        connection.execute(
            """UPDATE evidence_submissions
               SET review_status = ?, reviewer_id = ?, reviewed_at = ?, review_reason = ?
               WHERE id = ?""",
            (
                request.decision,
                reviewer_id,
                reviewed_at,
                request.reason,
                evidence_id,
            ),
        )
        statuses = connection.execute(
            "SELECT review_status FROM evidence_submissions WHERE dataset_id = ?",
            (evidence["dataset_id"],),
        ).fetchall()
        dataset_evidence_approved = bool(statuses) and all(
            row["review_status"] == EvidenceReviewStatus.APPROVED.value for row in statuses
        )
        connection.execute(
            """UPDATE datasets SET evidence_verified = ?, evidence_independently_verified = ?
               WHERE id = ?""",
            (
                int(dataset_evidence_approved),
                int(dataset_evidence_approved),
                evidence["dataset_id"],
            ),
        )
        append_audit_event(
            connection,
            f"evidence_{request.decision.lower()}",
            "evidence_submission",
            str(evidence_id),
            {
                "dataset_id": evidence["dataset_id"],
                "evidence_id": evidence_id,
                "reviewer_id": reviewer_id,
                "decision": request.decision,
                "reason": request.reason,
                "reviewed_at": reviewed_at,
                "dataset_evidence_approved": dataset_evidence_approved,
            },
        )
        updated = connection.execute(
            "SELECT * FROM evidence_submissions WHERE id = ?", (evidence_id,)
        ).fetchone()
    return EvidenceReviewResponse(
        evidence=_decode_evidence_submission(updated),
        dataset_evidence_approved=dataset_evidence_approved,
    )


@app.get("/datasets/{dataset_id}/rights-records", response_model=list[RightsRecordResponse])
def list_rights_records(dataset_id: int):
    with connect() as connection:
        exists = connection.execute("SELECT 1 FROM datasets WHERE id = ?", (dataset_id,)).fetchone()
        if not exists:
            raise HTTPException(status_code=404, detail="Dataset not found")
        rows = connection.execute(
            "SELECT * FROM rights_records WHERE dataset_id = ? ORDER BY id", (dataset_id,)
        ).fetchall()
    return [_decode_rights_record(row) for row in rows]


@app.get(
    "/api/datasets/{dataset_id}/rights-ledger",
    response_model=list[RightsLedgerRecordResponse],
)
def list_rights_ledger(
    dataset_id: int,
    purpose: str = Query(default="research_training", min_length=1, max_length=120),
    context: str = Query(default="research", pattern="^(research|commercial)$"),
    geography: str | None = Query(default=None, max_length=120),
    redistribution_requested: bool = False,
    derivative_work_requested: bool = False,
):
    with connect() as connection:
        exists = connection.execute("SELECT 1 FROM datasets WHERE id = ?", (dataset_id,)).fetchone()
        if not exists:
            raise HTTPException(status_code=404, detail="Dataset not found")
        rows = connection.execute(
            """SELECT r.*, a.status AS anchor_status, a.record_hash AS anchor_record_hash,
                      a.policy_version AS anchor_policy_version, a.network_name,
                      a.contract_address, a.transaction_hash, a.block_number,
                      a.authorized_sender, a.error_message, a.updated_at AS anchor_updated_at
               FROM rights_records r LEFT JOIN rights_chain_anchors a ON a.record_id = r.id
               WHERE r.dataset_id = ? ORDER BY r.id""",
            (dataset_id,),
        ).fetchall()

    decisions = check_dataset_eligibility(
        EligibleDatasetsRequest(
            purpose=purpose,
            context=context,
            geography=geography,
            redistribution_requested=redistribution_requested,
            derivative_work_requested=derivative_work_requested,
            dataset_ids=[dataset_id],
        )
    )
    decision_by_id = {
        item.dataset_id: item
        for item in decisions.eligible_datasets + decisions.excluded_datasets
    }
    results = []
    for row in rows:
        record = dict(row)
        policy_version = record["anchor_policy_version"] or POLICY_VERSION
        current_hash = hash_rights_record(record, policy_version)
        current_decision = decision_by_id[dataset_id]
        base = _decode_rights_record(row).model_dump()
        results.append(
            RightsLedgerRecordResponse(
                **base,
                policy_version=policy_version,
                licensing_decision=current_decision.decision,
                decision_reason_codes=current_decision.reason_codes,
                decision_scope={
                    "purpose": purpose,
                    "context": context,
                    "geography": geography,
                    "redistribution_requested": redistribution_requested,
                    "derivative_work_requested": derivative_work_requested,
                },
                current_record_hash=current_hash,
                anchored_record_hash=(record["anchor_record_hash"] if record["anchor_status"] == "ANCHORED" else None),
                ledger_status=record["anchor_status"] or "DATABASE_ONLY",
                network_name=record["network_name"],
                contract_address=record["contract_address"],
                transaction_hash=record["transaction_hash"],
                block_number=record["block_number"],
                authorized_sender=record["authorized_sender"],
                verification_status="NOT_CHECKED",
                verified=None,
                anchor_error=record["error_message"],
                anchor_updated_at=record["anchor_updated_at"],
            )
        )
    return results


@app.get("/api/rights-records/{record_id}/anchor", response_model=RightsAnchorResponse)
def get_rights_anchor_status(record_id: int):
    record = _get_rights_record_row(record_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Rights record not found")
    return _anchor_status(record, _get_anchor_row(record_id))


@app.post("/api/rights-records/{record_id}/anchor", response_model=RightsAnchorResponse)
def anchor_rights_record(record_id: int, request: RightsAnchorRequest):
    record_row = _get_rights_record_row(record_id)
    if record_row is None:
        raise HTTPException(status_code=404, detail="Rights record not found")
    record = dict(record_row)
    anchor_row = _get_anchor_row(record_id)

    if not record["signature_valid"]:
        raise HTTPException(status_code=409, detail="Only a signature-verified rights record can be anchored.")

    try:
        evaluation = evaluate_dataset_request(
            record["dataset_id"],
            LicenseCheck(
                purpose=request.purpose,
                commercial=request.context == "commercial",
                geography=request.geography,
                redistribution_requested=request.redistribution_requested,
                derivative_work_requested=request.derivative_work_requested,
            ),
        )
    except DatasetNotFoundError as error:
        raise HTTPException(status_code=404, detail="Dataset not found") from error

    applied_record = evaluation.result.rights_ledger_reference
    is_current_record = bool(
        applied_record
        and applied_record.get("applied_to_decision")
        and applied_record.get("record_id") == record_id
    )
    if evaluation.result.decision != IngestionDecisionStatus.APPROVED or not is_current_record:
        with connect() as connection:
            append_audit_event(
                connection,
                "rights_anchor_blocked",
                "rights_record",
                str(record_id),
                {
                    "dataset_id": record["dataset_id"],
                    "purpose": request.purpose,
                    "context": request.context,
                    "decision": evaluation.result.decision.value,
                    "reason_codes": evaluation.result.reason_codes,
                    "policy_version": evaluation.result.policy_version,
                    "reason": "The licensing engine did not approve this current signed record for the requested scope.",
                },
            )
        raise HTTPException(
            status_code=409,
            detail={
                "message": "The rights record was not anchored because the licensing engine did not approve it.",
                "decision": evaluation.result.decision.value,
                "reason_codes": evaluation.result.reason_codes,
            },
        )

    policy_version = evaluation.result.policy_version
    current_hash = hash_rights_record(record, policy_version)
    if anchor_row and anchor_row["status"] == "ANCHORED":
        if anchor_row["record_hash"] != current_hash:
            return _anchor_status(
                record_row,
                anchor_row,
                verification_status="MISMATCH",
                verified=False,
                error_message="The database record differs from the hash already anchored on Hardhat.",
            )
        return _anchor_status(record_row, anchor_row)
    if anchor_row and anchor_row["status"] == "PENDING":
        return _anchor_status(record_row, anchor_row)
    if anchor_row and anchor_row["record_hash"] != current_hash:
        raise HTTPException(
            status_code=409,
            detail="The database record changed after a previous anchor attempt; verify its history before retrying.",
        )

    _upsert_anchor(
        record_id,
        record_hash=current_hash,
        policy_version=policy_version,
        status="PENDING",
        network_name="hardhat-local",
        error_message=None,
    )
    try:
        chain_record = anchor_record_on_chain(record_id, current_hash, policy_version)
        config = load_blockchain_config()
        anchored_at = datetime.fromtimestamp(chain_record.recorded_at, timezone.utc).isoformat()
        _upsert_anchor(
            record_id,
            record_hash=chain_record.record_hash,
            policy_version=chain_record.policy_version,
            status="ANCHORED",
            network_name=config.network_name,
            contract_address=config.contract_address,
            transaction_hash=chain_record.transaction_hash,
            block_number=chain_record.block_number,
            authorized_sender=chain_record.recorded_by,
            anchored_at=anchored_at,
        )
        with connect() as connection:
            append_audit_event(
                connection,
                "rights_record_anchored",
                "rights_record",
                str(record_id),
                {
                    "dataset_id": record["dataset_id"],
                    "record_hash": chain_record.record_hash,
                    "policy_version": chain_record.policy_version,
                    "network_name": config.network_name,
                    "contract_address": config.contract_address,
                    "transaction_hash": chain_record.transaction_hash,
                    "block_number": chain_record.block_number,
                    "authorized_sender": chain_record.recorded_by,
                    "ownership_proven": False,
                },
            )
        return _anchor_status(record_row, _get_anchor_row(record_id), "MATCH", True)
    except BlockchainTransactionPending as error:
        _upsert_anchor(
            record_id,
            record_hash=current_hash,
            policy_version=policy_version,
            status="PENDING",
            network_name="hardhat-local",
            transaction_hash=error.transaction_hash,
            error_message=str(error),
        )
        with connect() as connection:
            append_audit_event(connection, "rights_record_anchor_pending", "rights_record", str(record_id), {
                "record_hash": current_hash, "transaction_hash": error.transaction_hash, "network_name": "hardhat-local"
            })
        return _anchor_status(record_row, _get_anchor_row(record_id))
    except BlockchainError as error:
        transaction_hash = getattr(error, "transaction_hash", None)
        block_number = getattr(error, "block_number", None)
        _upsert_anchor(
            record_id,
            record_hash=current_hash,
            policy_version=policy_version,
            status="FAILED",
            network_name="hardhat-local",
            transaction_hash=transaction_hash,
            block_number=block_number,
            error_message=str(error),
        )
        with connect() as connection:
            append_audit_event(connection, "rights_record_anchor_failed", "rights_record", str(record_id), {
                "record_hash": current_hash,
                "policy_version": policy_version,
                "network_name": "hardhat-local",
                "transaction_hash": transaction_hash,
                "reason": str(error),
            })
        return _anchor_status(
            record_row,
            _get_anchor_row(record_id),
            verification_status="UNAVAILABLE",
            verified=False,
        )


@app.post("/api/rights-records/{record_id}/verify-anchor", response_model=RightsAnchorResponse)
def verify_rights_anchor(record_id: int):
    record_row = _get_rights_record_row(record_id)
    if record_row is None:
        raise HTTPException(status_code=404, detail="Rights record not found")
    record = dict(record_row)
    anchor_row = _get_anchor_row(record_id)
    policy_version = anchor_row["policy_version"] if anchor_row else POLICY_VERSION
    current_hash = hash_rights_record(record, policy_version)
    try:
        chain_record = get_chain_record(record_id)
    except BlockchainError as error:
        result = _anchor_status(
            record_row,
            anchor_row,
            verification_status="UNAVAILABLE",
            verified=None,
            error_message=str(error),
        )
        with connect() as connection:
            append_audit_event(connection, "rights_record_anchor_verification_unavailable", "rights_record", str(record_id), {
                "record_hash": current_hash,
                "network_name": "hardhat-local",
                "reason": str(error),
            })
        return result

    if chain_record is None:
        status = anchor_row["status"] if anchor_row else "DATABASE_ONLY"
        result = _anchor_status(
            record_row,
            anchor_row,
            verification_status="NOT_ANCHORED",
            verified=False,
            error_message="No matching rights record exists on the local Hardhat contract.",
        )
    else:
        verified = chain_record.record_hash == current_hash and chain_record.policy_version == policy_version
        config = load_blockchain_config()
        _upsert_anchor(
            record_id,
            record_hash=chain_record.record_hash,
            policy_version=chain_record.policy_version,
            status="ANCHORED",
            network_name=config.network_name,
            contract_address=config.contract_address,
            transaction_hash=chain_record.transaction_hash or (anchor_row["transaction_hash"] if anchor_row else None),
            block_number=chain_record.block_number or (anchor_row["block_number"] if anchor_row else None),
            authorized_sender=chain_record.recorded_by,
            anchored_at=datetime.fromtimestamp(chain_record.recorded_at, timezone.utc).isoformat(),
            error_message=None if verified else "The current database record does not match the anchored hash or policy version.",
        )
        result = _anchor_status(
            record_row,
            _get_anchor_row(record_id),
            verification_status="MATCH" if verified else "MISMATCH",
            verified=verified,
        )

    with connect() as connection:
        append_audit_event(
            connection,
            "rights_record_anchor_verified",
            "rights_record",
            str(record_id),
            {
                "current_record_hash": current_hash,
                "anchored_record_hash": result.anchored_record_hash,
                "verification_status": result.verification_status,
                "verified": result.verified,
                "network_name": "hardhat-local",
                "ownership_proven": False,
            },
        )
    return result


@app.get("/audit/events")
def get_audit_events():
    return list_audit_events()


@app.get("/audit/verify", response_model=AuditIntegrityResponse)
def get_audit_integrity():
    intact, event_count, first_invalid_event_id = verify_audit_chain()
    return AuditIntegrityResponse(
        intact=intact,
        event_count=event_count,
        first_invalid_event_id=first_invalid_event_id,
    )
