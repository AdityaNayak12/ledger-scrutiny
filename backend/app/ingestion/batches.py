"""Shared import-batch lifecycle helpers.

Raw source rows are immutable. Only lifecycle status and validation report
change after a batch is staged.
"""

from datetime import date, datetime
from hashlib import sha256
from typing import Any, Mapping

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models import FinancialPeriod, ImportBatch
from app.ingestion.schema import BatchKind, BatchStatus, SourceFamily


PARSER_VERSION = "2"
_DUPLICATE_MARKER = "_duplicate_import_batch"
_HASH_UNIQUE_CONSTRAINT = "uq_import_batch_entity_content_sha256"


class BatchLifecycleError(ValueError):
    """Base error for an invalid batch transition."""


class BatchConflictError(BatchLifecycleError):
    """An active dataset conflicts with a candidate batch."""


class BatchValidationError(BatchLifecycleError):
    """A candidate batch failed validation before activation."""


def is_duplicate_import_batch(batch: ImportBatch) -> bool:
    """Return whether this staging result reused an existing hash-identical batch."""
    return bool(getattr(batch, _DUPLICATE_MARKER, False))


def _mark_duplicate(batch: ImportBatch) -> ImportBatch:
    setattr(batch, _DUPLICATE_MARKER, True)
    return batch


def _find_duplicate_import_batch(session: Session, entity_id: int, content_sha256: str) -> ImportBatch | None:
    return session.execute(
        select(ImportBatch).where(
            ImportBatch.entity_id == entity_id,
            ImportBatch.content_sha256 == content_sha256,
        ).order_by(ImportBatch.id.desc())
    ).scalars().first()


def _is_hash_uniqueness_error(error: IntegrityError) -> bool:
    constraint_name = getattr(getattr(error.orig, "diag", None), "constraint_name", None)
    if constraint_name is not None:
        return str(constraint_name) == _HASH_UNIQUE_CONSTRAINT

    message = str(error.orig).lower()
    return _HASH_UNIQUE_CONSTRAINT in message or (
        "unique constraint failed" in message
        and "import_batches.entity_id" in message
        and "import_batches.content_sha256" in message
    )


def _enum_value(value: Any) -> Any:
    return value.value if hasattr(value, "value") else value


def _as_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    return value


def _source_family(source: str, source_family: str | SourceFamily | None) -> str:
    if source_family is not None:
        return str(_enum_value(source_family))
    return SourceFamily.TALLY.value if source.lower().startswith("tally") else SourceFamily.GL_UPLOAD.value


def _batch_dates(batch: ImportBatch) -> tuple[date | None, date | None]:
    start = _as_date(batch.coverage_start)
    end = _as_date(batch.coverage_end)
    period = batch.financial_period
    if period is not None:
        start = start or _as_date(period.period_start)
        end = end or _as_date(period.period_end)
    return start, end


def _validate_duplicate_scope(
    duplicate: ImportBatch,
    *,
    period_start: Any,
    period_end: Any,
    coverage_start: Any,
    coverage_end: Any,
    source: str,
    source_family: str | SourceFamily | None,
    kind: str | BatchKind,
) -> None:
    requested_period = (_as_date(period_start), _as_date(period_end))
    duplicate_period = (
        _as_date(duplicate.financial_period.period_start),
        _as_date(duplicate.financial_period.period_end),
    )
    if duplicate_period != requested_period:
        raise BatchConflictError(
            f"Exact duplicate batch {duplicate.id} does not match the requested period."
        )

    requested_coverage = (
        _as_date(coverage_start) if coverage_start is not None else requested_period[0],
        _as_date(coverage_end) if coverage_end is not None else requested_period[1],
    )
    if _batch_dates(duplicate) != requested_coverage:
        raise BatchConflictError(
            f"Exact duplicate batch {duplicate.id} does not match the requested coverage."
        )

    requested_family = _source_family(source, source_family)
    if _source_family(duplicate.source, duplicate.source_family) != requested_family:
        raise BatchConflictError(
            f"Exact duplicate batch {duplicate.id} does not match the requested source family."
        )

    duplicate_kind = duplicate.kind or BatchKind.JOURNAL.value
    if duplicate_kind != _enum_value(kind):
        raise BatchConflictError(
            f"Exact duplicate batch {duplicate.id} does not match the requested batch kind."
        )


def _financial_year(start: date | None) -> int | None:
    if start is None:
        return None
    return start.year if start.month >= 4 else start.year - 1


def _is_journal(batch: ImportBatch) -> bool:
    return batch.kind in (None, BatchKind.JOURNAL.value)


def _get_batch(session: Session, batch_or_id: ImportBatch | int) -> ImportBatch:
    if isinstance(batch_or_id, ImportBatch):
        return batch_or_id
    batch = session.get(ImportBatch, batch_or_id)
    if batch is None:
        raise BatchLifecycleError(f"Import batch {batch_or_id} not found.")
    return batch


def stage_import_batch(
    session: Session,
    *,
    entity_id: int,
    period_start: Any,
    period_end: Any,
    source: str,
    original_filename: str,
    contents: bytes,
    uploaded_by_user_id: int | None,
    validation_report: dict | None = None,
    kind: str | BatchKind = BatchKind.JOURNAL.value,
    batch_kind: str | BatchKind | None = None,
    coverage_start: date | None = None,
    coverage_end: date | None = None,
    source_family: str | SourceFamily | None = None,
    source_metadata: Mapping[str, Any] | None = None,
) -> ImportBatch:
    """Create one staged raw-retaining batch, or return its hash duplicate."""
    period_start_date = _as_date(period_start)
    period_end_date = _as_date(period_end)
    if (
        period_start_date is not None
        and period_end_date is not None
        and period_start_date > period_end_date
    ):
        raise BatchLifecycleError("Import batch period_start must be on or before period_end.")
    coverage_start_date = _as_date(coverage_start) if coverage_start is not None else period_start_date
    coverage_end_date = _as_date(coverage_end) if coverage_end is not None else period_end_date
    if (
        coverage_start_date is not None
        and coverage_end_date is not None
        and coverage_start_date > coverage_end_date
    ):
        raise BatchLifecycleError("Import batch coverage_start must be on or before coverage_end.")
    contents = bytes(contents)
    content_sha256 = sha256(contents).hexdigest()
    duplicate = _find_duplicate_import_batch(session, entity_id, content_sha256)
    if duplicate is not None:
        _validate_duplicate_scope(
            duplicate,
            period_start=period_start,
            period_end=period_end,
            coverage_start=coverage_start,
            coverage_end=coverage_end,
            source=source,
            source_family=source_family,
            kind=batch_kind if batch_kind is not None else kind,
        )
        return _mark_duplicate(duplicate)

    try:
        with session.begin_nested():
            period = session.execute(
                select(FinancialPeriod).where(
                    FinancialPeriod.entity_id == entity_id,
                    FinancialPeriod.period_start == period_start,
                    FinancialPeriod.period_end == period_end,
                ).order_by(FinancialPeriod.id.desc())
            ).scalars().first()
            if period is None:
                period = FinancialPeriod(
                    entity_id=entity_id,
                    period_start=period_start,
                    period_end=period_end,
                    source=source,
                )
                session.add(period)
                session.flush()

            batch = ImportBatch(
                entity_id=entity_id,
                financial_period_id=period.id,
                uploaded_by_user_id=uploaded_by_user_id,
                source=source,
                original_filename=original_filename or "unnamed-upload",
                content_sha256=content_sha256,
                parser_version=PARSER_VERSION,
                status=BatchStatus.STAGED.value,
                kind=_enum_value(batch_kind if batch_kind is not None else kind),
                raw_source_bytes=contents,
                coverage_start=coverage_start or _as_date(period_start),
                coverage_end=coverage_end or _as_date(period_end),
                source_family=_source_family(source, source_family),
                source_metadata=dict(source_metadata or {}),
                validation_report=dict(validation_report or {}),
            )
            session.add(batch)
            session.flush()
    except IntegrityError as error:
        if not _is_hash_uniqueness_error(error):
            raise
        duplicate = _find_duplicate_import_batch(session, entity_id, content_sha256)
        if duplicate is not None:
            _validate_duplicate_scope(
                duplicate,
                period_start=period_start,
                period_end=period_end,
                coverage_start=coverage_start,
                coverage_end=coverage_end,
                source=source,
                source_family=source_family,
                kind=batch_kind if batch_kind is not None else kind,
            )
            return _mark_duplicate(duplicate)
        raise

    setattr(batch, _DUPLICATE_MARKER, False)
    return batch


def create_import_batch(
    session: Session,
    *,
    entity_id: int,
    period_start: Any,
    period_end: Any,
    source: str,
    original_filename: str,
    contents: bytes,
    uploaded_by_user_id: int | None,
    validation_report: dict | None = None,
    kind: str | BatchKind = BatchKind.JOURNAL.value,
    batch_kind: str | BatchKind | None = None,
    coverage_start: date | None = None,
    coverage_end: date | None = None,
    source_family: str | SourceFamily | None = None,
    source_metadata: Mapping[str, Any] | None = None,
    activate: bool | None = None,
) -> ImportBatch:
    """Compatibility entry point for existing routes.

    Existing callers historically received an active batch. New ingestion
    code should call :func:`stage_import_batch` and activate after validation.
    """
    batch = stage_import_batch(
        session,
        entity_id=entity_id,
        period_start=period_start,
        period_end=period_end,
        source=source,
        original_filename=original_filename,
        contents=contents,
        uploaded_by_user_id=uploaded_by_user_id,
        validation_report=validation_report,
        kind=kind,
        batch_kind=batch_kind,
        coverage_start=coverage_start,
        coverage_end=coverage_end,
        source_family=source_family,
        source_metadata=source_metadata,
    )
    if is_duplicate_import_batch(batch):
        if batch.status != BatchStatus.ACTIVE.value:
            raise BatchLifecycleError(
                f"Exact duplicate batch {batch.id} is {batch.status}; no active import exists."
            )
        return batch
    if activate is None:
        legacy_call = all(
            value is None
            for value in (batch_kind, coverage_start, coverage_end, source_family, source_metadata)
        )
        legacy_call = legacy_call and bool(
            validation_report and {"parser", "connector"}.intersection(validation_report)
        )
        activate = legacy_call
    if activate:
        return activate_import_batch(session, batch, validation_report=validation_report, replace=True)
    return batch


def _mark_failed(session: Session, batch: ImportBatch, message: str) -> None:
    report = dict(batch.validation_report or {})
    errors = list(report.get("errors", []))
    if message not in errors:
        errors.append(message)
    report["errors"] = errors
    report["readiness"] = "INVALID"
    batch.validation_report = report
    batch.status = BatchStatus.FAILED.value
    session.flush()


def _overlaps(first: tuple[date | None, date | None], second: tuple[date | None, date | None]) -> bool:
    first_start, first_end = first
    second_start, second_end = second
    if None in (first_start, first_end, second_start, second_end):
        return False
    return first_start <= second_end and second_start <= first_end


def _active_journal_batches(session: Session, entity_id: int) -> list[ImportBatch]:
    return session.execute(
        select(ImportBatch).where(
            ImportBatch.entity_id == entity_id,
            ImportBatch.status == BatchStatus.ACTIVE.value,
        ).order_by(ImportBatch.id)
    ).scalars().all()


def _dataset_report(session: Session, entity_id: int) -> dict:
    from app.ingestion.reconciliation import compute_dataset_fingerprint

    active = [batch for batch in _active_journal_batches(session, entity_id) if _is_journal(batch)]
    return {
        "active_batch_ids": [batch.id for batch in active],
        "dataset_fingerprint": compute_dataset_fingerprint(active),
    }


def activate_import_batch(
    session: Session,
    batch_or_id: ImportBatch | int,
    *,
    validation_report: dict | None = None,
    replace: bool = False,
    allow_replacement: bool | None = None,
) -> ImportBatch:
    """Atomically activate validated batch and supersede replaced coverage."""
    batch = _get_batch(session, batch_or_id)
    if batch.status == BatchStatus.ACTIVE.value:
        return batch
    if batch.status != BatchStatus.STAGED.value:
        raise BatchLifecycleError(f"Cannot activate batch {batch.id} from status {batch.status}.")

    previous_batch_status = batch.status
    previous_report = dict(batch.validation_report or {})
    if validation_report is not None:
        batch.validation_report = dict(validation_report)
    report = dict(batch.validation_report or {})
    if report.get("readiness") == "INVALID":
        message = f"Batch {batch.id} has INVALID readiness and cannot be activated."
        _mark_failed(session, batch, message)
        raise BatchValidationError(message)

    if allow_replacement is not None:
        replace = allow_replacement

    replaced: list[ImportBatch] = []

    try:
        with session.no_autoflush:
            all_active = [
                active for active in _active_journal_batches(session, batch.entity_id)
                if active.id != batch.id
            ]
            candidate_dates = _batch_dates(batch)
            candidate_family = _source_family(batch.source, batch.source_family)
            candidate_year = _financial_year(candidate_dates[0])
            if batch.kind == BatchKind.BALANCE_CHECKPOINT.value:
                session.flush()
            candidate_baseline_dates = (
                {checkpoint.balance_date for checkpoint in batch.balance_checkpoints}
                if batch.kind == BatchKind.BALANCE_CHECKPOINT.value
                else set()
            )
            for active in all_active:
                active_dates = _batch_dates(active)
                active_family = _source_family(active.source, active.source_family)
                active_year = _financial_year(active_dates[0])
                if (
                    candidate_family != active_family
                    and candidate_year is not None
                    and candidate_year == active_year
                ):
                    raise BatchConflictError(
                        f"Active source family conflict: batch {active.id} uses {active_family}, "
                        f"candidate uses {candidate_family}."
                    )
                active_baseline_dates = (
                    {checkpoint.balance_date for checkpoint in active.balance_checkpoints}
                    if active.kind == BatchKind.BALANCE_CHECKPOINT.value
                    else set()
                )
                if (
                    batch.kind == BatchKind.BALANCE_CHECKPOINT.value
                    and active.kind == BatchKind.BALANCE_CHECKPOINT.value
                    and candidate_family == SourceFamily.GL_UPLOAD.value
                    and active_family == SourceFamily.GL_UPLOAD.value
                    and candidate_dates == active_dates
                    and len(candidate_baseline_dates) == len(active_baseline_dates) == 1
                    and candidate_baseline_dates == active_baseline_dates
                ):
                    replaced.append(active)
                if (
                    _is_journal(batch)
                    and _is_journal(active)
                    and _overlaps(candidate_dates, active_dates)
                ):
                    if candidate_family != active_family:
                        raise BatchConflictError(
                            f"Active journal coverage overlap with source family conflict: batch {active.id}."
                        )
                    exact_coverage = candidate_dates == active_dates
                    if not (replace or exact_coverage):
                        raise BatchConflictError(
                            f"Active journal coverage overlap with batch {active.id}."
                        )
                    replaced.append(active)
    except BatchLifecycleError as error:
        _mark_failed(session, batch, str(error))
        raise

    previous_statuses = {active.id: active.status for active in replaced}
    try:
        for active in replaced:
            active.status = BatchStatus.SUPERSEDED.value
        batch.status = BatchStatus.ACTIVE.value
        session.flush()
        if _is_journal(batch):
            report.update(_dataset_report(session, batch.entity_id))
        batch.validation_report = report
        session.flush()
    except Exception:
        batch.status = previous_batch_status
        batch.validation_report = previous_report
        for active in replaced:
            active.status = previous_statuses[active.id]
        raise
    return batch


def fail_import_batch(
    session: Session,
    batch_or_id: ImportBatch | int,
    *,
    errors: list[str] | tuple[str, ...] = (),
    validation_report: dict | None = None,
) -> ImportBatch:
    """Persist failed validation without touching active batches."""
    batch = _get_batch(session, batch_or_id)
    if batch.status != BatchStatus.STAGED.value:
        raise BatchLifecycleError(f"Cannot fail batch {batch.id} from status {batch.status}.")
    if validation_report is not None:
        batch.validation_report = dict(validation_report)
    message = "; ".join(errors) or "Batch validation failed."
    _mark_failed(session, batch, message)
    return batch


create_staged_import_batch = stage_import_batch
activate_batch = activate_import_batch
