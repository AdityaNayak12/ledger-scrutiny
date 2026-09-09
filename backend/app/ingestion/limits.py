"""Resource limits for untrusted uploads and external ingestion responses."""

import io
from zipfile import BadZipFile, ZipFile, is_zipfile

from fastapi import UploadFile


MAX_UPLOAD_BYTES = 32 * 1024 * 1024
MAX_SIGNED_PDF_BYTES = 16 * 1024 * 1024
MAX_TALLY_RESPONSE_BYTES = 32 * 1024 * 1024
MAX_XLSX_UNCOMPRESSED_BYTES = 128 * 1024 * 1024
MAX_XLSX_COMPRESSION_RATIO = 10
_READ_CHUNK_BYTES = 1024 * 1024


class UploadTooLargeError(ValueError):
    """Raised when an uploaded part exceeds its materialization limit."""


async def read_upload_limited(file: UploadFile, limit: int) -> bytes:
    """Read an upload while never materializing more than ``limit + 1`` bytes."""
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(min(_READ_CHUNK_BYTES, limit - total + 1))
        if not chunk:
            return b"".join(chunks)
        total += len(chunk)
        if total > limit:
            raise UploadTooLargeError(f"Upload exceeds the maximum allowed size of {limit // (1024 * 1024)} MiB.")
        chunks.append(chunk)


def validate_xlsx_zip(file_bytes: bytes) -> None:
    """Reject ZIP members whose expansion can exhaust parser memory or disk."""
    if not is_zipfile(io.BytesIO(file_bytes)):
        return

    total_uncompressed = 0
    total_compressed = 0
    try:
        with ZipFile(io.BytesIO(file_bytes)) as archive:
            for member in archive.infolist():
                if member.file_size > MAX_XLSX_UNCOMPRESSED_BYTES:
                    raise ValueError("XLSX ZIP member exceeds the maximum allowed uncompressed size.")
                total_uncompressed += member.file_size
                total_compressed += member.compress_size
                if total_uncompressed > MAX_XLSX_UNCOMPRESSED_BYTES:
                    raise ValueError("XLSX ZIP contents exceed the maximum allowed uncompressed size.")
                if member.file_size and (
                    not member.compress_size
                    or member.file_size > member.compress_size * MAX_XLSX_COMPRESSION_RATIO
                ):
                    raise ValueError("XLSX ZIP member exceeds the maximum allowed compression ratio.")
            if total_uncompressed and (
                not total_compressed
                or total_uncompressed > total_compressed * MAX_XLSX_COMPRESSION_RATIO
            ):
                raise ValueError("XLSX ZIP contents exceed the maximum allowed compression ratio.")
    except BadZipFile:
        return
