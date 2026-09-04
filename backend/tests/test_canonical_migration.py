import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest


BACKEND_DIR = Path(__file__).resolve().parents[1]


def _create_legacy_schema(db_path: Path, canonical_table: str | None = None, populated: bool = False) -> None:
    connection = sqlite3.connect(db_path)
    connection.executescript(
        """
        CREATE TABLE organizations (id INTEGER PRIMARY KEY, name VARCHAR(255) NOT NULL);
        CREATE TABLE users (
            id INTEGER PRIMARY KEY,
            organization_id INTEGER NOT NULL,
            email VARCHAR(255) NOT NULL,
            hashed_password VARCHAR(255),
            created_at DATETIME NOT NULL
        );
        CREATE TABLE entities (
            id INTEGER PRIMARY KEY,
            organization_id INTEGER NOT NULL,
            name VARCHAR(255) NOT NULL,
            materiality_threshold NUMERIC(15, 2) NOT NULL
        );
        CREATE TABLE financial_periods (
            id INTEGER PRIMARY KEY,
            entity_id INTEGER NOT NULL,
            period_start DATE NOT NULL,
            period_end DATE NOT NULL,
            source VARCHAR(50) NOT NULL
        );
        CREATE TABLE import_batches (
            id INTEGER PRIMARY KEY,
            entity_id INTEGER NOT NULL,
            financial_period_id INTEGER NOT NULL,
            uploaded_by_user_id INTEGER,
            source VARCHAR(50) NOT NULL,
            original_filename VARCHAR(512) NOT NULL,
            content_sha256 VARCHAR(64) NOT NULL,
            parser_version VARCHAR(50) NOT NULL,
            status VARCHAR(30) NOT NULL,
            validation_report JSON NOT NULL,
            created_at DATETIME NOT NULL
        );
        CREATE TABLE ledger_accounts (
            id INTEGER PRIMARY KEY,
            entity_id INTEGER NOT NULL,
            name VARCHAR(255) NOT NULL,
            group_name VARCHAR(255) NOT NULL,
            normal_balance VARCHAR(10) NOT NULL
        );
        CREATE TABLE scrutiny_runs (
            id INTEGER PRIMARY KEY,
            entity_id INTEGER NOT NULL,
            financial_period_id INTEGER NOT NULL,
            import_batch_id INTEGER,
            triggered_by_user_id INTEGER,
            rule_set_version VARCHAR(50) NOT NULL,
            status VARCHAR(30) NOT NULL,
            summary JSON NOT NULL,
            started_at DATETIME NOT NULL,
            completed_at DATETIME
        );
        CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL PRIMARY KEY);
        INSERT INTO organizations VALUES (1, 'Test Org');
        INSERT INTO entities VALUES (1, 1, 'Test Entity', 100);
        INSERT INTO financial_periods VALUES (1, 1, '2025-04-01', '2026-03-31', 'gl_upload');
        INSERT INTO ledger_accounts VALUES (1, 1, 'Cash', 'Cash-in-hand', 'debit');
        INSERT INTO import_batches VALUES (
            1, 1, 1, NULL, 'gl_upload', 'legacy.xlsx',
            'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
            '1', 'ACTIVE', '{}', '2026-01-01 00:00:00'
        );
        INSERT INTO scrutiny_runs VALUES (
            1, 1, 1, 1, NULL, '1', 'COMPLETED', '{}', '2026-01-01 00:00:00', NULL
        );
        INSERT INTO alembic_version VALUES ('20260825_manufacturing_profile');
        """
    )

    if canonical_table:
        if canonical_table == "journal_entries":
            connection.execute("CREATE TABLE journal_entries (id INTEGER PRIMARY KEY)")
        elif canonical_table == "journal_lines":
            connection.execute("CREATE TABLE journal_lines (id INTEGER PRIMARY KEY)")
        elif canonical_table == "balance_checkpoints":
            connection.execute("CREATE TABLE balance_checkpoints (id INTEGER PRIMARY KEY)")
        else:
            raise AssertionError(f"Unknown canonical table: {canonical_table}")
        if populated:
            connection.execute(f"INSERT INTO {canonical_table} (id) VALUES (1)")

    connection.commit()
    connection.close()


def _create_populated_canonical_tables(db_path: Path) -> None:
    connection = sqlite3.connect(db_path)
    connection.executescript(
        """
        CREATE TABLE journal_entries (
            id INTEGER PRIMARY KEY,
            import_batch_id INTEGER NOT NULL,
            entity_id INTEGER NOT NULL,
            source_document_id VARCHAR(255) NOT NULL,
            posting_date DATE NOT NULL,
            document_date DATE,
            document_type VARCHAR(100),
            narration VARCHAR(1000)
        );
        CREATE TABLE journal_lines (
            id INTEGER PRIMARY KEY,
            journal_entry_id INTEGER NOT NULL,
            ledger_account_id INTEGER NOT NULL,
            source_row_number INTEGER NOT NULL,
            amount NUMERIC(20, 2) NOT NULL,
            side VARCHAR(10) NOT NULL,
            posting_key VARCHAR(20),
            quantity NUMERIC(20, 4),
            currency VARCHAR(10),
            reference VARCHAR(255),
            clearing_document VARCHAR(255),
            profit_center VARCHAR(255),
            cost_center VARCHAR(255),
            text VARCHAR(1000),
            supplier VARCHAR(255),
            wbs VARCHAR(255),
            purchasing_document VARCHAR(255),
            customer VARCHAR(255),
            dimensions JSON NOT NULL,
            source_metadata JSON NOT NULL
        );
        CREATE TABLE balance_checkpoints (
            id INTEGER PRIMARY KEY,
            import_batch_id INTEGER NOT NULL,
            entity_id INTEGER NOT NULL,
            ledger_account_id INTEGER NOT NULL,
            balance_date DATE NOT NULL,
            balance NUMERIC(20, 2) NOT NULL,
            currency VARCHAR(10),
            source_metadata JSON NOT NULL
        );
        INSERT INTO journal_entries VALUES (1, 1, 1, 'DOC-1', '2025-04-01', NULL, 'SA', 'Test');
        INSERT INTO journal_lines VALUES (1, 1, 1, 2, 100, 'debit', '40', NULL, 'INR', NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, '{}', '{}');
        INSERT INTO balance_checkpoints VALUES (1, 1, 1, 1, '2025-03-31', 100, 'INR', '{}');
        """
    )
    connection.commit()
    connection.close()


def _run_upgrade(db_path: Path) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment["DATABASE_URL"] = f"sqlite:///{db_path}"
    environment["PYTHONPATH"] = str(BACKEND_DIR)
    return subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=BACKEND_DIR,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )


@pytest.mark.parametrize("canonical_table", ["journal_entries", "journal_lines", "balance_checkpoints"])
def test_migration_rejects_populated_partial_canonical_tables(tmp_path: Path, canonical_table: str):
    db_path = tmp_path / f"{canonical_table}.db"
    _create_legacy_schema(db_path, canonical_table=canonical_table, populated=True)

    result = _run_upgrade(db_path)

    assert result.returncode != 0
    assert f"Cannot add required column" in result.stderr
    assert canonical_table in result.stderr


def test_migration_repairs_foreign_keys_on_existing_canonical_tables(tmp_path: Path):
    db_path = tmp_path / "foreign-keys.db"
    _create_legacy_schema(db_path)
    _create_populated_canonical_tables(db_path)

    result = _run_upgrade(db_path)

    assert result.returncode == 0, result.stdout + result.stderr
    connection = sqlite3.connect(db_path)
    try:
        foreign_keys = {
            table: {(row[3], row[2], row[6]) for row in connection.execute(f"PRAGMA foreign_key_list({table})")}
            for table in ("journal_entries", "journal_lines", "balance_checkpoints")
        }
    finally:
        connection.close()

    assert foreign_keys["journal_entries"] == {
        ("import_batch_id", "import_batches", "CASCADE"),
        ("entity_id", "entities", "CASCADE"),
    }
    assert foreign_keys["journal_lines"] == {
        ("journal_entry_id", "journal_entries", "CASCADE"),
        ("ledger_account_id", "ledger_accounts", "RESTRICT"),
    }
    assert foreign_keys["balance_checkpoints"] == {
        ("import_batch_id", "import_batches", "CASCADE"),
        ("entity_id", "entities", "CASCADE"),
        ("ledger_account_id", "ledger_accounts", "RESTRICT"),
    }


def test_migration_adds_entity_scoped_import_hash_constraint(tmp_path: Path):
    db_path = tmp_path / "import-hash-constraint.db"
    _create_legacy_schema(db_path)

    result = _run_upgrade(db_path)

    assert result.returncode == 0, result.stdout + result.stderr
    connection = sqlite3.connect(db_path)
    try:
        unique_indexes = [
            row[1]
            for row in connection.execute("PRAGMA index_list(import_batches)")
            if row[2]
        ]
        unique_columns = [
            [column[2] for column in connection.execute(f"PRAGMA index_info('{index_name}')")]
            for index_name in unique_indexes
        ]
    finally:
        connection.close()

    assert ["entity_id", "content_sha256"] in unique_columns
