from datetime import datetime, timezone
import json
from pathlib import Path
from uuid import uuid4

from .decision_service import POLICY_VERSION
from .schemas import CorpusManifest, IngestionDecisionStatus, ManifestEntry

PROJECT_ROOT = Path(__file__).resolve().parent.parent
MANIFEST_ROOT = PROJECT_ROOT / "manifests"
MANIFEST_TYPES = {
    "accepted": IngestionDecisionStatus.APPROVED,
    "rejected": IngestionDecisionStatus.REJECTED,
    "needs_review": IngestionDecisionStatus.NEEDS_REVIEW,
}


def write_manifests(
    evaluations: list[tuple[object, int]],
    request,
    synthetic_test_fixture_ids: set[int] | None = None,
) -> tuple[str, dict[str, int], dict[str, str], str]:
    """Write one timestamped JSON manifest per decision category under the project root."""
    generated_at = datetime.now(timezone.utc).isoformat()
    batch_id = uuid4().hex[:8]
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    counts: dict[str, int] = {}
    files: dict[str, str] = {}
    fixture_ids = synthetic_test_fixture_ids or set()
    synthetic_test_output = bool(evaluations) and all(
        evaluation.result.dataset_id in fixture_ids for evaluation, _ in evaluations
    )

    for category, status in MANIFEST_TYPES.items():
        entries: list[ManifestEntry] = []
        for evaluation, audit_event_id in evaluations:
            result = evaluation.result
            if result.decision != status:
                continue
            entries.append(
                ManifestEntry(
                    dataset_id=result.dataset_id,
                    decision=result.decision,
                    reason_codes=result.reason_codes,
                    policy_version=result.policy_version,
                    timestamp=result.timestamp,
                    evidence_verification_status=result.evidence_verification_status,
                    evidence_references=(
                        [{
                            "type": "user_signed_claim",
                            "record_id": result.rights_ledger_reference["record_id"],
                            "signature_valid": result.rights_ledger_reference["signature_valid"],
                        }]
                        if result.rights_ledger_reference
                        and result.rights_ledger_reference["signature_valid"]
                        and result.rights_ledger_reference["applied_to_decision"]
                        else []
                    ),
                    audit_event_reference=audit_event_id,
                    synthetic_test_fixture=result.dataset_id in fixture_ids,
                )
            )

        manifest = CorpusManifest(
            manifest_type=category,
            policy_version=POLICY_VERSION,
            generated_at=generated_at,
            purpose=request.purpose,
            context=request.context,
            geography=request.geography,
            redistribution_requested=request.redistribution_requested,
            derivative_work_requested=request.derivative_work_requested,
            synthetic_test_output=synthetic_test_output,
            entries=entries,
        )
        directory = MANIFEST_ROOT / category
        directory.mkdir(parents=True, exist_ok=True)
        filename = f"manifest-{stamp}-{batch_id}.json"
        path = directory / filename
        # Exclusive creation avoids overwriting any pre-existing user output.
        with path.open("x", encoding="utf-8", newline="\n") as output:
            json.dump(manifest.model_dump(mode="json"), output, ensure_ascii=False, indent=2)
            output.write("\n")
        counts[category] = len(entries)
        try:
            files[category] = path.relative_to(PROJECT_ROOT).as_posix()
        except ValueError:
            # Tests and embedders may inject a temporary output root on another
            # Windows drive; retain an absolute path in that non-project case.
            files[category] = path.as_posix()

    # Each dataset must be assigned to exactly one output category.
    if sum(counts.values()) != len(evaluations):
        raise RuntimeError("Manifest categories do not partition all dataset decisions.")
    return generated_at, counts, files, batch_id
