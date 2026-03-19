"""File upload, download, and presigned URL generation."""

from __future__ import annotations

import logging
import uuid
from pathlib import PurePosixPath
from typing import Any

from src.core.config import get_settings
from src.core.exceptions import FileTooLargeError, FileTypeNotAllowedError
from src.storage.s3_client import get_s3_client

logger = logging.getLogger(__name__)


async def upload_file(
    *,
    file_content: bytes,
    filename: str,
    content_type: str,
    merchant_id: str,
) -> dict[str, Any]:
    """Validate & upload a file to MinIO.

    Returns metadata dict with ``file_id``, ``key``, ``presigned_url``, etc.
    """
    settings = get_settings()

    # ── Validate content type ────────────────────────────────
    if content_type not in settings.storage.allowed_content_types:
        raise FileTypeNotAllowedError(
            f"Content type '{content_type}' is not allowed. "
            f"Allowed: {settings.storage.allowed_content_types}"
        )

    # ── Validate size ────────────────────────────────────────
    max_bytes = settings.storage.max_file_size_mb * 1024 * 1024
    if len(file_content) > max_bytes:
        raise FileTooLargeError(
            f"File size ({len(file_content)} bytes) exceeds limit "
            f"({settings.storage.max_file_size_mb} MB)"
        )

    # ── Build S3 key ─────────────────────────────────────────
    file_id = str(uuid.uuid4())
    ext = PurePosixPath(filename).suffix or ""
    s3_key = f"{merchant_id}/uploads/{file_id}{ext}"

    # ── Upload ───────────────────────────────────────────────
    async with get_s3_client() as client:
        await client.put_object(
            Bucket=settings.storage.s3_bucket,
            Key=s3_key,
            Body=file_content,
            ContentType=content_type,
        )

        presigned_url = await client.generate_presigned_url(
            "get_object",
            Params={"Bucket": settings.storage.s3_bucket, "Key": s3_key},
            ExpiresIn=settings.storage.presigned_url_expiry,
        )

    return {
        "file_id": file_id,
        "filename": filename,
        "content_type": content_type,
        "size_bytes": len(file_content),
        "s3_key": s3_key,
        "presigned_url": presigned_url,
    }


async def download_file(s3_key: str) -> bytes:
    """Download a file from MinIO by its S3 key."""
    settings = get_settings()
    async with get_s3_client() as client:
        response = await client.get_object(
            Bucket=settings.storage.s3_bucket,
            Key=s3_key,
        )
        return await response["Body"].read()


async def get_presigned_url(s3_key: str) -> str:
    """Generate a new presigned URL for an existing file."""
    settings = get_settings()
    async with get_s3_client() as client:
        return await client.generate_presigned_url(
            "get_object",
            Params={"Bucket": settings.storage.s3_bucket, "Key": s3_key},
            ExpiresIn=settings.storage.presigned_url_expiry,
        )


async def delete_file(s3_key: str) -> None:
    """Delete a file from MinIO."""
    settings = get_settings()
    async with get_s3_client() as client:
        await client.delete_object(
            Bucket=settings.storage.s3_bucket,
            Key=s3_key,
        )
