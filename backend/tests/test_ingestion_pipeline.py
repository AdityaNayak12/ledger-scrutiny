from datetime import date
from decimal import Decimal
from hashlib import sha256
from sqlite3 import IntegrityError as SQLiteIntegrityError

import pytest
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.base import Base
from app.db.models import (
    Entity,
    ImportBatch,
    JournalEntry,
    LedgerAccount,
    Organization,
    TrialBalanceSnapshot,
)
from app.ingestion import batches as batches_module
from app.ingestion.batches import (
    BatchConflictError,
    BatchLifecycleError,
    BatchValidationError,
    activate_import_batch,
    create_import_batch,
    fail_import_batch,
    is_duplicate_import_batch,
    stage_import_batch,
)
from app.ingestion.reconciliation import (
    build_active_dataset_report,
    build_reconciliation_report,
    compute_dataset_fingerprint,
)
from app.ingestion.schema import JournalEntryRecord, JournalLineRecord, JournalLineSide


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

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


def test_duplicate_integrity_error_reloads_existing_batch(session, monkeypatch):
    first = _stage(
        session,
        start=date(2025, 4, 1),
        end=date(2025, 6, 30),
        contents=b"reloadable duplicate",
    )
    session.commit()
    original_execute = session.execute
    hidden_first_lookup = [True]

    def execute(statement, *args, **kwargs):
        if hidden_first_lookup[0] and "content_sha256" in str(statement):
            hidden_first_lookup[0] = False
            return original_execute(select(ImportBatch).where(ImportBatch.id == -1))
        return original_execute(statement, *args, **kwargs)

    monkeypatch.setattr(session, "execute", execute)
    duplicate = _stage(
        session,
        start=date(2025, 4, 1),
        end=date(2025, 6, 30),
        contents=b"reloadable duplicate",
    )

    assert duplicate.id == first.id
    assert is_duplicate_import_batch(duplicate)


def test_duplicate_integrity_error_with_mismatched_coverage_is_rejected(session, monkeypatch):
    first = _stage(
        session,
        start=date(2025, 4, 1),
        end=date(2025, 6, 30),
        contents=b"racing coverage duplicate",
    )
    session.commit()
    original_execute = session.execute
    hidden_first_lookup = [True]

    def execute(statement, *args, **kwargs):
        if hidden_first_lookup[0] and "content_sha256" in str(statement):
            hidden_first_lookup[0] = False
            return original_execute(select(ImportBatch).where(ImportBatch.id == -1))
        return original_execute(statement, *args, **kwargs)

    monkeypatch.setattr(session, "execute", execute)
    with pytest.raises(BatchConflictError, match="coverage"):
        stage_import_batch(
            session,
            entity_id=_entity_id(session),
            period_start=date(2025, 4, 1),
            period_end=date(2025, 6, 30),
            source="gl_upload",
            source_family="gl_upload",
            original_filename="replay.xlsx",
            contents=b"racing coverage duplicate",
            uploaded_by_user_id=None,
            coverage_start=date(2025, 7, 1),
            coverage_end=date(2025, 9, 30),
        )

    session.commit()
    session.expire_all()
    persisted = session.get(ImportBatch, first.id)
    assert persisted.status == "STAGED"
    assert persisted.coverage_start == date(2025, 4, 1)
    assert persisted.coverage_end == date(2025, 6, 30)
    assert session.scalar(select(func.count()).select_from(ImportBatch)) == 1


def test_unrelated_foreign_key_integrity_error_is_not_treated_as_duplicate(session, monkeypatch):
    first = _stage(
        session,
        start=date(2025, 4, 1),
        end=date(2025, 6, 30),
        contents=b"unrelated integrity failure",
    )
    first_id = first.id
    session.commit()
    original_find_duplicate = batches_module._find_duplicate_import_batch
    lookup_count = [0]

    def find_duplicate(session, entity_id, content_sha256):
        lookup_count[0] += 1
        if lookup_count[0] == 1:
            return None
        return original_find_duplicate(session, entity_id, content_sha256)

    monkeypatch.setattr(batches_module, "_find_duplicate_import_batch", find_duplicate)

    original_flush = session.flush

    def fail_flush(*args, **kwargs):
        if any(isinstance(instance, ImportBatch) for instance in session.new):
            raise IntegrityError(
                "INSERT INTO import_batches",
                {},
                SQLiteIntegrityError("FOREIGN KEY constraint failed"),
            )
        return original_flush(*args, **kwargs)

    monkeypatch.setattr(session, "flush", fail_flush)
    with pytest.raises(IntegrityError):
        stage_import_batch(
            session,
            entity_id=_entity_id(session),
            period_start=date(2025, 4, 1),
            period_end=date(2025, 6, 30),
            source="gl_upload",
            source_family="gl_upload",
            original_filename="invalid-user.xlsx",
            contents=b"unrelated integrity failure",
            uploaded_by_user_id=999_999,
            coverage_start=date(2025, 4, 1),
            coverage_end=date(2025, 6, 30),
        )

    monkeypatch.setattr(session, "flush", original_flush)
    session.rollback()
    session.expire_all()
    persisted = session.get(ImportBatch, first_id)
    assert persisted is not None
    assert persisted.status == "STAGED"
    assert session.scalar(select(func.count()).select_from(ImportBatch)) == 1


def test_exact_hash_replay_with_explicit_coverage_mismatch_is_rejected_without_mutation(session):
    active = _stage(
        session,
        start=date(2025, 4, 1),
        end=date(2025, 6, 30),
        contents=b"explicit coverage replay",
    )
    activate_import_batch(session, active, validation_report={"readiness": "READY"})
    session.commit()
    original_report = dict(active.validation_report)

    with pytest.raises(BatchConflictError, match="coverage"):
        stage_import_batch(
            session,
            entity_id=_entity_id(session),
            period_start=date(2025, 4, 1),
            period_end=date(2025, 6, 30),
            source="gl_upload",
            source_family="gl_upload",
            original_filename="replay.xlsx",
            contents=b"explicit coverage replay",
            uploaded_by_user_id=None,
            coverage_start=date(2025, 7, 1),
            coverage_end=date(2025, 9, 30),
        )

    session.commit()
    session.expire_all()
    persisted = session.get(ImportBatch, active.id)
    assert persisted.status == "ACTIVE"
    assert persisted.validation_report == original_report
    assert persisted.coverage_start == date(2025, 4, 1)
    assert persisted.coverage_end == date(2025, 6, 30)
    assert session.scalar(select(func.count()).select_from(ImportBatch)) == 1


@pytest.mark.parametrize("status", ["STAGED", "FAILED", "SUPERSEDED"])
def test_compatibility_duplicate_non_active_batch_is_rejected(session, status):
    contents = f"non-active-{status}".encode()
    first = create_import_batch(
        session,
        entity_id=_entity_id(session),
        period_start=date(2025, 4, 1),
        period_end=date(2025, 6, 30),
        source="gl_upload",
        original_filename="journal.xlsx",
        contents=contents,
        uploaded_by_user_id=None,
    )
    first.status = status
    session.commit()

    with pytest.raises(BatchLifecycleError, match="no active import"):
        create_import_batch(
            session,
            entity_id=_entity_id(session),
            period_start=date(2025, 4, 1),
            period_end=date(2025, 6, 30),
            source="gl_upload",
            original_filename="journal.xlsx",
            contents=contents,
            uploaded_by_user_id=None,
        )

    assert session.scalar(
        select(func.count()).select_from(ImportBatch).where(ImportBatch.content_sha256 == first.content_sha256)
    ) == 1
    assert first.status == status


def test_same_hash_remains_importable_for_another_entity(session):
    first = _stage(session, start=date(2025, 4, 1), end=date(2025, 6, 30), contents=b"cross-entity")
    organization_id = session.scalar(select(Entity.organization_id).where(Entity.id == _entity_id(session)))
    other = Entity(
        organization_id=organization_id,
        name="Second Pipeline Entity",
        materiality_threshold=Decimal("0.00"),
    )
    session.add(other)
    session.commit()

    second = stage_import_batch(
        session,
        entity_id=other.id,
        period_start=date(2025, 4, 1),
        period_end=date(2025, 6, 30),
        source="gl_upload",
        source_family="gl_upload",
        original_filename="journal.xlsx",
        contents=b"cross-entity",
        uploaded_by_user_id=None,
        coverage_start=date(2025, 4, 1),
        coverage_end=date(2025, 6, 30),
    )

    assert second.id != first.id
    assert not is_duplicate_import_batch(second)


@pytest.mark.parametrize(
    ("replay_start", "replay_end", "replay_family", "replay_kind", "expected_error"),
    [
        (date(2025, 7, 1), date(2025, 9, 30), "gl_upload", "journal", "period"),
        (date(2025, 4, 1), date(2025, 6, 30), "tally", "journal", "source family"),
        (date(2025, 4, 1), date(2025, 6, 30), "gl_upload", "balance_checkpoint", "batch kind"),
    ],
)
def test_exact_hash_replay_with_different_scope_is_rejected_without_mutation(
    session, replay_start, replay_end, replay_family, replay_kind, expected_error
):
    active = _stage(
        session,
        start=date(2025, 4, 1),
        end=date(2025, 6, 30),
        contents=b"scope-bound-replay",
    )
    activate_import_batch(session, active, validation_report={"readiness": "READY"})
    session.commit()
    original_report = dict(active.validation_report)

    with pytest.raises(BatchConflictError, match=expected_error):
        stage_import_batch(
            session,
            entity_id=_entity_id(session),
            period_start=replay_start,
            period_end=replay_end,
            source="tally" if replay_family == "tally" else "gl_upload",
            source_family=replay_family,
            original_filename="replay.xlsx",
            contents=b"scope-bound-replay",
            uploaded_by_user_id=None,
            kind=replay_kind,
            coverage_start=replay_start,
            coverage_end=replay_end,
        )

    session.commit()
    session.expire_all()
    persisted = session.get(ImportBatch, active.id)
    assert persisted.status == "ACTIVE"
    assert persisted.validation_report == original_report
    assert persisted.raw_source_bytes == b"scope-bound-replay"
    assert session.scalar(select(func.count()).select_from(ImportBatch)) == 1


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


def test_checkpoint_activation_does_not_apply_journal_collision_rules(session):
    journal = _stage(session, start=date(2025, 4, 1), end=date(2025, 6, 30), contents=b"journal-kind")
    activate_import_batch(session, journal)
    checkpoint = stage_import_batch(
        session,
        entity_id=_entity_id(session),
        period_start=date(2025, 4, 1),
        period_end=date(2025, 6, 30),
        source="tally",
        source_family="tally",
        original_filename="opening.xlsx",
        contents=b"checkpoint-kind",
        uploaded_by_user_id=None,
        kind="balance_checkpoint",
        coverage_start=date(2025, 4, 1),
        coverage_end=date(2025, 6, 30),
    )

    activate_import_batch(session, checkpoint)

    assert journal.status == "ACTIVE"
    assert checkpoint.status == "ACTIVE"


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


def test_active_report_counts_distinct_accounts_across_batches(session):
    baseline = {"present": True, "complete": True, "balance_date": "2025-03-31"}
    first_entry = JournalEntryRecord(
        source_document_id="DOC-A",
        posting_date=date(2025, 4, 1),
        lines=(
            JournalLineRecord(1, "1100", Decimal("10.00"), JournalLineSide.DEBIT),
            JournalLineRecord(2, "4000", Decimal("-10.00"), JournalLineSide.CREDIT),
        ),
    )
    second_entry = JournalEntryRecord(
        source_document_id="DOC-B",
        posting_date=date(2025, 7, 1),
        lines=(
            JournalLineRecord(3, "4000", Decimal("20.00"), JournalLineSide.DEBIT),
            JournalLineRecord(4, "5000", Decimal("-20.00"), JournalLineSide.CREDIT),
        ),
    )
    first = _stage(
        session,
        start=date(2025, 4, 1),
        end=date(2025, 6, 30),
        contents=b"report-q1",
    )
    first.validation_report = build_reconciliation_report((first_entry,), baseline_coverage=baseline)
    activate_import_batch(session, first)
    second = _stage(
        session,
        start=date(2025, 7, 1),
        end=date(2025, 9, 30),
        contents=b"report-q2",
    )
    second.validation_report = build_reconciliation_report((second_entry,), baseline_coverage=baseline)
    activate_import_batch(session, second)

    report = build_active_dataset_report(session, _entity_id(session))

    assert report["account_count"] == 3
    assert report["unmapped_account_count"] == 0


def test_active_report_derives_distinct_legacy_accounts_from_snapshots(session):
    first = _stage(session, start=date(2025, 4, 1), end=date(2025, 6, 30), contents=b"legacy-q1")
    second = _stage(session, start=date(2025, 7, 1), end=date(2025, 9, 30), contents=b"legacy-q2")
    entity_id = _entity_id(session)
    accounts = [
        LedgerAccount(entity_id=entity_id, external_code=code, name=code)
        for code in ("1100", "4000", "5000")
    ]
    session.add_all(accounts)
    session.flush()
    session.add_all([
        TrialBalanceSnapshot(
            import_batch_id=None,
            entity_id=entity_id,
            ledger_account_id=accounts[0].id,
            period_start=date(2025, 4, 1),
            period_end=date(2025, 6, 30),
            opening_balance=Decimal("0"),
            total_debits=Decimal("0"),
            total_credits=Decimal("0"),
            closing_balance=Decimal("0"),
        ),
        TrialBalanceSnapshot(
            import_batch_id=None,
            entity_id=entity_id,
            ledger_account_id=accounts[1].id,
            period_start=date(2025, 4, 1),
            period_end=date(2025, 6, 30),
            opening_balance=Decimal("0"),
            total_debits=Decimal("0"),
            total_credits=Decimal("0"),
            closing_balance=Decimal("0"),
        ),
        TrialBalanceSnapshot(
            import_batch_id=None,
            entity_id=entity_id,
            ledger_account_id=accounts[1].id,
            period_start=date(2025, 7, 1),
            period_end=date(2025, 9, 30),
            opening_balance=Decimal("0"),
            total_debits=Decimal("0"),
            total_credits=Decimal("0"),
            closing_balance=Decimal("0"),
        ),
        TrialBalanceSnapshot(
            import_batch_id=None,
            entity_id=entity_id,
            ledger_account_id=accounts[2].id,
            period_start=date(2025, 7, 1),
            period_end=date(2025, 9, 30),
            opening_balance=Decimal("0"),
            total_debits=Decimal("0"),
            total_credits=Decimal("0"),
            closing_balance=Decimal("0"),
        ),
    ])
    first.validation_report = {
        "account_count": 2,
        "unmapped_account_count": 0,
        "baseline_coverage": {"present": True, "complete": True},
        "readiness": "READY",
    }
    second.validation_report = {
        "account_count": 2,
        "unmapped_account_count": 0,
        "baseline_coverage": {"present": True, "complete": True},
        "readiness": "READY",
    }
    activate_import_batch(session, first)
    activate_import_batch(session, second)

    report = build_active_dataset_report(session, entity_id)

    assert report["account_count"] == 3
    assert report["unmapped_account_count"] == 0


def test_active_report_retains_legacy_counts_without_account_rows(session):
    batch = _stage(session, start=date(2025, 4, 1), end=date(2025, 6, 30), contents=b"legacy-counts")
    batch.validation_report = {
        "account_count": 4,
        "unmapped_account_count": 2,
        "baseline_coverage": {"present": True, "complete": True},
        "readiness": "READY",
    }
    activate_import_batch(session, batch)

    report = build_active_dataset_report(session, _entity_id(session))

    assert report["account_count"] == 4
    assert report["unmapped_account_count"] == 2


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


def test_activation_flush_failure_restores_candidate_report(session, monkeypatch):
    active = _stage(session, start=date(2025, 4, 1), end=date(2025, 6, 30), contents=b"rollback-active")
    activate_import_batch(session, active)
    candidate = _stage(
        session,
        start=date(2025, 4, 1),
        end=date(2025, 6, 30),
        contents=b"rollback-candidate",
    )
    candidate.validation_report = {"original": "report"}
    original_flush = session.flush

    def fail_flush(*args, **kwargs):
        raise RuntimeError("flush failed")

    monkeypatch.setattr(session, "flush", fail_flush)
    with pytest.raises(RuntimeError, match="flush failed"):
        activate_import_batch(session, candidate, validation_report={"replacement": "report"})
    monkeypatch.setattr(session, "flush", original_flush)

    assert active.status == "ACTIVE"
    assert candidate.status == "STAGED"
    assert candidate.validation_report == {"original": "report"}


def test_activation_failure_after_supersession_preserves_active_dataset(session, monkeypatch):
    entity_id = _entity_id(session)
    active = _stage(
        session,
        start=date(2025, 4, 1),
        end=date(2025, 6, 30),
        contents=b"atomic-active",
    )
    session.add(JournalEntry(
        import_batch_id=active.id,
        entity_id=entity_id,
        source_document_id="KEEP-ME",
        posting_date=date(2025, 4, 1),
    ))
    activate_import_batch(session, active, validation_report={"readiness": "READY"})
    session.commit()
    original_report = dict(active.validation_report)

    replacement = _stage(
        session,
        start=date(2025, 4, 1),
        end=date(2025, 6, 30),
        contents=b"atomic-replacement",
    )

    def fail_dataset_report(*args, **kwargs):
        raise RuntimeError("dataset report failed")

    monkeypatch.setattr("app.ingestion.batches._dataset_report", fail_dataset_report)
    with pytest.raises(RuntimeError, match="dataset report failed"):
        activate_import_batch(session, replacement, replace=True)

    session.commit()
    session.expire_all()
    persisted_active = session.get(ImportBatch, active.id)
    persisted_replacement = session.get(ImportBatch, replacement.id)
    assert persisted_active.status == "ACTIVE"
    assert persisted_replacement.status == "STAGED"
    assert persisted_active.validation_report == original_report
    assert session.scalar(
        select(func.count()).select_from(JournalEntry).where(JournalEntry.import_batch_id == active.id)
    ) == 1
    assert session.scalar(
        select(JournalEntry.source_document_id).where(JournalEntry.import_batch_id == active.id)
    ) == "KEEP-ME"
    assert session.scalar(
        select(func.count()).select_from(JournalEntry).where(JournalEntry.import_batch_id == replacement.id)
    ) == 0
    assert session.scalar(
        select(func.count()).select_from(ImportBatch).where(ImportBatch.status == "ACTIVE")
    ) == 1


def test_fail_import_batch_rejects_terminal_statuses(session):
    failed = _stage(session, start=date(2025, 4, 1), end=date(2025, 6, 30), contents=b"terminal-failed")
    fail_import_batch(session, failed, errors=["bad input"])
    session.commit()
    failed_report = dict(failed.validation_report)

    with pytest.raises(BatchLifecycleError):
        fail_import_batch(session, failed, errors=["must not rewrite"])
    assert failed.status == "FAILED"
    assert failed.validation_report == failed_report

    superseded = _stage(session, start=date(2025, 7, 1), end=date(2025, 9, 30), contents=b"terminal-active")
    activate_import_batch(session, superseded)
    replacement = _stage(session, start=date(2025, 7, 1), end=date(2025, 9, 30), contents=b"terminal-replacement")
    activate_import_batch(session, replacement)
    superseded_report = dict(superseded.validation_report)

    with pytest.raises(BatchLifecycleError):
        fail_import_batch(session, superseded, errors=["must not rewrite"])
    assert superseded.status == "SUPERSEDED"
    assert superseded.validation_report == superseded_report


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
