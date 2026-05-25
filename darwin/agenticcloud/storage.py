"""Persistent storage of attestations.

Every signed attestation is written to a local SQLite database so it
can be queried and audited later. This is what turns DAC from a
stateless RPC into an evidentiary system of record.

Schema is intentionally minimal for v0:
- attestations: the full signed payload, plus indexed columns we'd
  reasonably query by (issued_at, signer_key_id, status, workload_id,
  substrate_id, attestation_id).

The 'signed_attestation_json' column holds the entire SignedAttestation
as JSON so verification is independent of schema changes — you can
always re-verify a stored attestation by deserializing that column.

In v0.2 we'll add a SQLAlchemy-backed Postgres implementation behind
the same AttestationStore interface for multi-tenant deployments.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from darwin.agenticcloud.types import SignedAttestation

DEFAULT_DB_PATH = Path.home() / ".darwin" / "agenticcloud" / "attestations.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS attestations (
    attestation_id        TEXT PRIMARY KEY,
    workload_id           TEXT NOT NULL,
    signer_key_id         TEXT NOT NULL,
    substrate_id          TEXT NOT NULL,
    status                TEXT NOT NULL,
    issued_at             REAL NOT NULL,
    cost_usd              REAL NOT NULL,
    wall_time_sec         REAL NOT NULL,
    schema_version        TEXT NOT NULL,
    signed_attestation_json TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_attestations_issued_at
    ON attestations(issued_at DESC);

CREATE INDEX IF NOT EXISTS idx_attestations_workload_id
    ON attestations(workload_id);

CREATE INDEX IF NOT EXISTS idx_attestations_signer_key_id
    ON attestations(signer_key_id);

CREATE INDEX IF NOT EXISTS idx_attestations_status
    ON attestations(status);
"""


@dataclass(frozen=True)
class StoredAttestation:
    """A row in the attestations table, returned by queries."""

    attestation_id: str
    workload_id: str
    signer_key_id: str
    substrate_id: str
    status: str
    issued_at: float
    cost_usd: float
    wall_time_sec: float
    schema_version: str
    signed_attestation: dict  # the full SignedAttestation re-hydrated


class AttestationStore:
    """SQLite-backed attestation store.

    Thread-safe at the connection level via SQLite's serialized access.
    For higher concurrency in v0.2 we move to a connection pool or
    swap the backend to Postgres.
    """

    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path = db_path or DEFAULT_DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(SCHEMA)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path, isolation_level=None)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    # -------------------------------------------------------------
    # Writes
    # -------------------------------------------------------------
    def save(self, signed: SignedAttestation) -> None:
        """Persist a signed attestation. Idempotent on attestation_id."""
        a = signed.attestation
        er = a["execution_result"]
        signed_json = json.dumps(
            {
                "attestation": signed.attestation,
                "signature_b64": signed.signature_b64,
                "public_key_b64": signed.public_key_b64,
            }
        )
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO attestations (
                    attestation_id, workload_id, signer_key_id, substrate_id,
                    status, issued_at, cost_usd, wall_time_sec,
                    schema_version, signed_attestation_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    a["attestation_id"],
                    er["workload_id"],
                    a["signer_key_id"],
                    er["substrate_id"],
                    er["status"],
                    a["issued_at"],
                    er["cost_usd"],
                    er["wall_time_sec"],
                    a["schema"],
                    signed_json,
                ),
            )

    # -------------------------------------------------------------
    # Reads
    # -------------------------------------------------------------
    def get(self, attestation_id: str) -> StoredAttestation | None:
        """Fetch a single attestation by ID. Returns None if not found."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM attestations WHERE attestation_id = ?",
                (attestation_id,),
            ).fetchone()
        return _row_to_stored(row) if row else None

    def list_recent(self, limit: int = 50) -> list[StoredAttestation]:
        """Return the most recent attestations, newest first."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM attestations ORDER BY issued_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [_row_to_stored(r) for r in rows]

    def list_by_status(self, status: str, limit: int = 50) -> list[StoredAttestation]:
        """Return attestations with a given status (e.g. 'cost_exceeded')."""
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM attestations
                WHERE status = ?
                ORDER BY issued_at DESC
                LIMIT ?
                """,
                (status, limit),
            ).fetchall()
        return [_row_to_stored(r) for r in rows]

    def list_by_signer(self, signer_key_id: str, limit: int = 50) -> list[StoredAttestation]:
        """Return attestations issued by a given signer."""
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM attestations
                WHERE signer_key_id = ?
                ORDER BY issued_at DESC
                LIMIT ?
                """,
                (signer_key_id, limit),
            ).fetchall()
        return [_row_to_stored(r) for r in rows]

    def count(self) -> int:
        """Total number of stored attestations."""
        with self._connect() as conn:
            return conn.execute("SELECT COUNT(*) FROM attestations").fetchone()[0]

    def total_cost_usd(self, signer_key_id: str | None = None) -> float:
        """Sum of cost_usd across all (or one signer's) attestations."""
        with self._connect() as conn:
            if signer_key_id is None:
                row = conn.execute(
                    "SELECT COALESCE(SUM(cost_usd), 0) FROM attestations"
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT COALESCE(SUM(cost_usd), 0) FROM attestations WHERE signer_key_id = ?",
                    (signer_key_id,),
                ).fetchone()
        return float(row[0])


def _row_to_stored(row: sqlite3.Row) -> StoredAttestation:
    return StoredAttestation(
        attestation_id=row["attestation_id"],
        workload_id=row["workload_id"],
        signer_key_id=row["signer_key_id"],
        substrate_id=row["substrate_id"],
        status=row["status"],
        issued_at=row["issued_at"],
        cost_usd=row["cost_usd"],
        wall_time_sec=row["wall_time_sec"],
        schema_version=row["schema_version"],
        signed_attestation=json.loads(row["signed_attestation_json"]),
    )
