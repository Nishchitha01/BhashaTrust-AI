import os
import sqlite3
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent
DB_PATH = Path(os.environ.get("BHASHATRUST_DB_PATH", PROJECT_ROOT / "data" / "bhashatrust.db"))


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def initialize_database() -> None:
    with connect() as connection:
        connection.execute(
            """CREATE TABLE IF NOT EXISTS datasets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                language TEXT NOT NULL,
                owner TEXT NOT NULL DEFAULT '',
                description TEXT NOT NULL,
                license_name TEXT NOT NULL,
                permitted_purposes TEXT NOT NULL,
                commercial_use INTEGER,
                redistribution_allowed INTEGER,
                derivative_work_allowed INTEGER,
                geographic_restrictions TEXT NOT NULL,
                expires_on TEXT,
                revoked INTEGER NOT NULL DEFAULT 0,
                evidence_verified INTEGER NOT NULL DEFAULT 0,
                evidence_independently_verified INTEGER NOT NULL DEFAULT 0,
                synthetic INTEGER NOT NULL DEFAULT 1
            )"""
        )
        dataset_columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(datasets)")
        }
        if "owner" not in dataset_columns:
            connection.execute(
                "ALTER TABLE datasets ADD COLUMN owner TEXT NOT NULL DEFAULT ''"
            )
        if "evidence_independently_verified" not in dataset_columns:
            connection.execute(
                "ALTER TABLE datasets ADD COLUMN evidence_independently_verified INTEGER NOT NULL DEFAULT 0"
            )
        if "redistribution_allowed" not in dataset_columns:
            connection.execute("ALTER TABLE datasets ADD COLUMN redistribution_allowed INTEGER")
        if "derivative_work_allowed" not in dataset_columns:
            connection.execute("ALTER TABLE datasets ADD COLUMN derivative_work_allowed INTEGER")
        provenance_columns = {
            "source_name": "TEXT",
            "source_url": "TEXT",
            "source_dataset_id": "TEXT",
            "source_version": "TEXT",
            "language_variety": "TEXT",
            "modalities_json": "TEXT",
            "collection_method": "TEXT",
            "data_contributor_claim": "TEXT",
            "license_version": "TEXT",
            "intended_use_notes": "TEXT",
            "known_restrictions": "TEXT",
            "collection_period": "TEXT",
            "provenance_notes": "TEXT",
            "provenance_status": "TEXT NOT NULL DEFAULT 'NOT_RECORDED'",
            "metadata_claimed_at": "TEXT",
            "metadata_origin": "TEXT",
            "synthetic_test_fixture": "INTEGER NOT NULL DEFAULT 0",
        }
        for column, definition in provenance_columns.items():
            if column not in dataset_columns:
                connection.execute(f"ALTER TABLE datasets ADD COLUMN {column} {definition}")
        connection.execute(
            """CREATE TABLE IF NOT EXISTS evidence_submissions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                dataset_id INTEGER NOT NULL REFERENCES datasets(id),
                evidence_type TEXT NOT NULL,
                description TEXT NOT NULL,
                reference TEXT NOT NULL,
                submitter_id TEXT,
                submitted_at TEXT NOT NULL,
                review_status TEXT NOT NULL DEFAULT 'PENDING'
                    CHECK (review_status IN ('PENDING', 'APPROVED', 'REJECTED', 'MORE_EVIDENCE_REQUESTED')),
                reviewer_id TEXT,
                reviewed_at TEXT,
                review_reason TEXT
            )"""
        )
        connection.execute(
            """UPDATE datasets
               SET evidence_verified = 0, evidence_independently_verified = 0
               WHERE evidence_independently_verified = 1
                 AND (
                    NOT EXISTS (
                        SELECT 1 FROM evidence_submissions e WHERE e.dataset_id = datasets.id
                    )
                    OR EXISTS (
                        SELECT 1 FROM evidence_submissions e
                        WHERE e.dataset_id = datasets.id AND e.review_status != 'APPROVED'
                    )
                 )"""
        )
        connection.execute(
            """CREATE TABLE IF NOT EXISTS rights_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                dataset_id INTEGER NOT NULL REFERENCES datasets(id),
                payload_json TEXT NOT NULL,
                signer_id TEXT NOT NULL,
                public_key TEXT NOT NULL,
                signature TEXT NOT NULL,
                signature_valid INTEGER NOT NULL,
                verification_message TEXT NOT NULL,
                created_at TEXT NOT NULL
            )"""
        )
        connection.execute(
            """CREATE TABLE IF NOT EXISTS audit_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_type TEXT NOT NULL,
                entity_type TEXT NOT NULL,
                entity_id TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                previous_hash TEXT NOT NULL,
                event_hash TEXT NOT NULL,
                created_at TEXT NOT NULL
            )"""
        )
        connection.execute(
            """CREATE TABLE IF NOT EXISTS optimization_results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                result_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                audit_event_id INTEGER NOT NULL REFERENCES audit_events(id)
            )"""
        )
        # Additive blockchain metadata; rights_records and their signatures remain unchanged.
        connection.execute(
            """CREATE TABLE IF NOT EXISTS rights_chain_anchors (
                record_id INTEGER PRIMARY KEY REFERENCES rights_records(id),
                record_hash TEXT NOT NULL,
                policy_version TEXT NOT NULL,
                status TEXT NOT NULL CHECK (status IN ('PENDING', 'ANCHORED', 'FAILED')),
                network_name TEXT NOT NULL,
                contract_address TEXT,
                transaction_hash TEXT,
                block_number INTEGER,
                authorized_sender TEXT,
                anchored_at TEXT,
                error_message TEXT,
                updated_at TEXT NOT NULL
            )"""
        )
def reset_database() -> None:
    """Test helper: remove the database file at the configured project path."""
    if DB_PATH.exists():
        DB_PATH.unlink()
    initialize_database()
