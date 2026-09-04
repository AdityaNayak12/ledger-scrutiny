from datetime import date
from decimal import Decimal
from hashlib import sha256

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.db.base import Base
from app.db.models import Entity, ImportBatch, Organization
from app.ingestion.batches import (
    BatchConflictError,
    BatchValidationError,
    activate_import_batch,
    create_import_batch,
    stage_import_batch,
)
from app.ingestion.reconciliation import (
    build_reconciliation_report,
    compute_dataset_fingerprint,
)
from app.ingestion.schema import JournalEntryRecord, JournalLineRecord, JournalLineSide


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        organization = Organization(name="Pipeline Test Org")
        session.add(organization)
        session.flush()
        session.add(Entity(
            organization_id=organization.id,
            name="Pipeline Test Entity",
            materiality_threshold=Decimal("0.00"),
        ))
        session.commit()
        yield session
    Base.metadata.drop_all(engine)


def _entity_id(session):
    return session.scalar(select(Entity.id))


def _stage(session, *, start, end, contents=b"journal", source_family="gl_upload"):
    return stage_import_batch(
        session,
        entity_id=_entity_id(session),
        period_start=start,
        period_end=end,
        source="gl_upload",
        source_family=source_family,
        original_filename="journal.xlsx",
        contents=contents,
        uploaded_by_user_id=None,
        coverage_start=start,
        coverage_end=end,
    )


def test_staging_retains_raw_bytes_and_exact_hash_duplicate_is_idempotent(session):
    contents = b"immutable source bytes"
    first = _stage(
        session,
        start=date(2025, 4, 1),
        end=date(2025, 6, 30),
        contents=contents,
    )
    session.commit()

    duplicate = _stage(
        session,
        start=date(2025, 4, 1),
        end=date(2025, 6, 30),
        contents=contents,
    )

    assert duplicate.id == first.id
    assert duplicate.status == "STAGED"
    assert duplicate.raw_source_bytes == contents
    assert duplicate.content_sha256 == sha256(contents).hexdigest()
    assert session.scalar(select(ImportBatch.id).order_by(ImportBatch.id.desc())) == first.id


def test_activation_supersedes_only_replaced_active_coverage(session):
    q1 = _stage(session, start=date(2025, 4, 1), end=date(2025, 6, 30), contents=b"q1")
    activate_import_batch(session, q1)
    q2 = _stage(session, start=date(2025, 7, 1), end=date(2025, 9, 30), contents=b"q2")
    activate_import_batch(session, q2)
    correction = _stage(session, start=date(2025, 4, 1), end=date(2025, 6, 30), contents=b"q1 corrected")
    activate_import_batch(session, correction)
    session.commit()

    assert q1.status == "SUPERSEDED"
    assert q2.status == "ACTIVE"
    assert correction.status == "ACTIVE"


def test_annual_activation_explicitly_replaces_quarterly_coverage(session):
    q1 = _stage(session, start=date(2025, 4, 1), end=date(2025, 6, 30), contents=b"annual-q1")
    activate_import_batch(session, q1)
    q2 = _stage(session, start=date(2025, 7, 1), end=date(2025, 9, 30), contents=b"annual-q2")
    activate_import_batch(session, q2)
    annual = _stage(session, start=date(2025, 4, 1), end=date(2026, 3, 31), contents=b"annual")

    activate_import_batch(session, annual, replace=True)

    assert q1.status == "SUPERSEDED"
    assert q2.status == "SUPERSEDED"
    assert annual.status == "ACTIVE"


def test_compatibility_create_import_batch_stages_without_legacy_parser_metadata(session):
    batch = create_import_batch(
        session,
        entity_id=_entity_id(session),
        period_start=date(2025, 4, 1),
        period_end=date(2025, 6, 30),
        source="gl_upload",
        original_filename="journal.xlsx",
        contents=b"new-lifecycle",
        uploaded_by_user_id=None,
    )

    assert batch.status == "STAGED"


def test_active_overlap_and_source_family_conflict_are_rejected_without_replacing_dataset(session):
    active = _stage(session, start=date(2025, 4, 1), end=date(2025, 6, 30), contents=b"active")
    activate_import_batch(session, active)
    overlap = _stage(session, start=date(2025, 6, 30), end=date(2025, 8, 31), contents=b"overlap")

    with pytest.raises(BatchConflictError, match="overlap"):
        activate_import_batch(session, overlap)
    assert active.status == "ACTIVE"
    assert overlap.status == "FAILED"

    source_conflict = _stage(
        session,
        start=date(2025, 7, 1),
        end=date(2025, 9, 30),
        contents=b"tally",
        source_family="tally",
    )
    with pytest.raises(BatchConflictError, match="source family"):
        activate_import_batch(session, source_conflict)
    assert active.status == "ACTIVE"
    assert source_conflict.status == "FAILED"


def test_failed_replacement_leaves_old_active_batch_untouched(session):
    active = _stage(session, start=date(2025, 4, 1), end=date(2025, 6, 30), contents=b"active")
    activate_import_batch(session, active)
    replacement = _stage(session, start=date(2025, 4, 1), end=date(2025, 6, 30), contents=b"bad")

    with pytest.raises(BatchValidationError, match="INVALID"):
        activate_import_batch(session, replacement, validation_report={"readiness": "INVALID", "errors": ["bad"]})

    assert active.status == "ACTIVE"
    assert replacement.status == "FAILED"


def test_reconciliation_report_totals_and_fingerprint_are_deterministic():
    entries = (
        JournalEntryRecord(
            source_document_id="DOC-2",
            posting_date=date(2025, 5, 1),
            lines=(
                JournalLineRecord(3, "4000", Decimal("30.00"), JournalLineSide.DEBIT),
                JournalLineRecord(4, "5000", Decimal("-20.00"), JournalLineSide.CREDIT),
            ),
        ),
        JournalEntryRecord(
            source_document_id="DOC-1",
            posting_date=date(2025, 4, 1),
            lines=(
                JournalLineRecord(1, "1100", Decimal("100.00"), JournalLineSide.DEBIT),
                JournalLineRecord(2, "4000", Decimal("-100.00"), JournalLineSide.CREDIT),
            ),
        ),
    )
    report = build_reconciliation_report(
        entries,
        baseline_coverage={"present": True, "complete": True, "balance_date": "2025-03-31"},
        active_batch_ids=[8, 3],
    )

    assert report["input"] == 4
    assert report["accepted"] == 4
    assert report["skipped"] == 0
    assert report["rejected"] == 0
    assert report["debit"] == "130.00"
    assert report["credit"] == "120.00"
    assert report["net"] == "10.00"
    assert report["document_count"] == 2
    assert report["unbalanced_document_count"] == 1
    assert report["account_count"] == 3
    assert report["unmapped_account_count"] == 0
    assert report["posting_date_min"] == "2025-04-01"
    assert report["posting_date_max"] == "2025-05-01"
    assert report["readiness"] == "INVALID"

    reversed_report = build_reconciliation_report(
        tuple(reversed(entries)),
        baseline_coverage={"present": True, "complete": True, "balance_date": "2025-03-31"},
        active_batch_ids=[3, 8],
    )
    assert reversed_report["dataset_fingerprint"] == report["dataset_fingerprint"]


def test_batch_fingerprint_ignores_input_order():
    first = {"content_sha256": "a" * 64, "coverage_start": "2025-04-01", "coverage_end": "2025-06-30"}
    second = {"content_sha256": "b" * 64, "coverage_start": "2025-07-01", "coverage_end": "2025-09-30"}
    assert compute_dataset_fingerprint([first, second]) == compute_dataset_fingerprint([second, first])
