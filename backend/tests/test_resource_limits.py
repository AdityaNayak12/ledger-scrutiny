import io
import zipfile
from datetime import date

import httpx
import pytest
from starlette.datastructures import Headers
from starlette.datastructures import UploadFile

import app.ingestion.limits as limits
from app.ingestion.limits import (
    MAX_SIGNED_PDF_BYTES,
    MAX_TALLY_RESPONSE_BYTES,
    MAX_UPLOAD_BYTES,
    MAX_XLSX_COMPRESSION_RATIO,
    MAX_XLSX_UNCOMPRESSED_BYTES,
    UploadTooLargeError,
    read_upload_limited,
    validate_xlsx_zip,
)


def test_resource_limits_match_pilot_contract():
    assert MAX_UPLOAD_BYTES == 32 * 1024 * 1024
    assert MAX_SIGNED_PDF_BYTES == 16 * 1024 * 1024
    assert MAX_TALLY_RESPONSE_BYTES == 32 * 1024 * 1024
    assert MAX_XLSX_UNCOMPRESSED_BYTES == 128 * 1024 * 1024
    assert MAX_XLSX_COMPRESSION_RATIO == 10


def _zip_bytes(content: bytes) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("xl/workbook.xml", content)
    return output.getvalue()


@pytest.mark.anyio
async def test_read_upload_limited_rejects_after_limit_without_materializing_more():
    limit = 4
    upload = UploadFile(
        io.BytesIO(b"12345"),
        filename="upload.bin",
        headers=Headers({"content-type": "application/octet-stream"}),
    )

    with pytest.raises(UploadTooLargeError, match="maximum allowed size"):
        await read_upload_limited(upload, limit)


def test_validate_xlsx_zip_rejects_member_expansion_before_parser(monkeypatch):
    monkeypatch.setattr(limits, "MAX_XLSX_UNCOMPRESSED_BYTES", 100)
    content = b"x" * 101

    with pytest.raises(ValueError, match="maximum allowed uncompressed size"):
        validate_xlsx_zip(_zip_bytes(content))


def test_validate_xlsx_zip_rejects_compression_ratio_before_parser(monkeypatch):
    monkeypatch.setattr(limits, "MAX_XLSX_COMPRESSION_RATIO", 10)
    content = b"x" * 1001

    with pytest.raises(ValueError, match="compression ratio"):
        validate_xlsx_zip(_zip_bytes(content))


def test_tally_stream_response_overflow_is_rejected(monkeypatch):
    from app.ingestion.tally_http import fetch_trial_balance
    from app.ingestion.tally_parser import TallyConnectorError

    monkeypatch.setattr("app.ingestion.tally_http.MAX_TALLY_RESPONSE_BYTES", 5)

    class StreamResponse:
        status_code = 200
        headers = {"content-length": "5"}

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def raise_for_status(self):
            return None

        def iter_bytes(self):
            yield b"123"
            yield b"45"
            yield b"6"

    monkeypatch.setattr("app.ingestion.tally_http.httpx.stream", lambda *args, **kwargs: StreamResponse())

    with pytest.raises(TallyConnectorError, match="maximum allowed size"):
        fetch_trial_balance(
            endpoint="http://8.8.8.8:9000",
            company_name="Example Company",
            period_start=date(2025, 4, 1),
            period_end=date(2026, 3, 31),
        )


def test_tally_declared_response_size_is_rejected_before_reading(monkeypatch):
    from app.ingestion.tally_http import fetch_trial_balance
    from app.ingestion.tally_parser import TallyConnectorError

    monkeypatch.setattr("app.ingestion.tally_http.MAX_TALLY_RESPONSE_BYTES", 5)

    class StreamResponse:
        headers = {"content-length": "6"}

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def raise_for_status(self):
            return None

        def iter_bytes(self):
            raise AssertionError("declared oversized response must not be read")

    monkeypatch.setattr("app.ingestion.tally_http.httpx.stream", lambda *args, **kwargs: StreamResponse())

    with pytest.raises(TallyConnectorError, match="maximum allowed size"):
        fetch_trial_balance(
            endpoint="http://8.8.8.8:9000",
            company_name="Example Company",
            period_start=date(2025, 4, 1),
            period_end=date(2026, 3, 31),
        )
