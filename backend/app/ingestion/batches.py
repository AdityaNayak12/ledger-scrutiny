"""Shared import-batch lifecycle helpers.

Imports are append-only: a new upload supersedes the previously active batch
for the same entity and period instead of deleting the prior audit evidence.
"""
from hashlib import sha256
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import FinancialPeriod, ImportBatch


PARSER_VERSION = "2"


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
) -> ImportBatch:
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
    else:
        period.source = source

    active_batches = session.execute(
        select(ImportBatch).where(
            ImportBatch.financial_period_id == period.id,
            ImportBatch.status == "ACTIVE",
        )
    ).scalars().all()
    for batch in active_batches:
        batch.status = "SUPERSEDED"

    batch = ImportBatch(
        entity_id=entity_id,
        financial_period_id=period.id,
        uploaded_by_user_id=uploaded_by_user_id,
        source=source,
        original_filename=original_filename or "unnamed-upload",
        content_sha256=sha256(contents).hexdigest(),
        parser_version=PARSER_VERSION,
        status="ACTIVE",
        validation_report=validation_report or {},
    )
    session.add(batch)
    session.flush()
    return batch
