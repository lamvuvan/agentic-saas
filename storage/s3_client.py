"""Async S3 / MinIO client using aioboto3."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator

import aioboto3

from src.core.config import get_settings

logger = logging.getLogger(__name__)

_session: aioboto3.Session | None = None


def _get_session() -> aioboto3.Session:
    global _session
    if _session is None:
        _session = aioboto3.Session()
    return _session


@asynccontextmanager
async def get_s3_client() -> AsyncGenerator:
    """Yield an async S3 client configured for MinIO."""
    settings = get_settings()
    session = _get_session()
    async with session.client(
        "s3",
        endpoint_url=settings.storage.s3_endpoint,
        aws_access_key_id=settings.storage.s3_access_key,
        aws_secret_access_key=settings.storage.s3_secret_key,
        region_name=settings.storage.s3_region,
    ) as client:
        yield client


async def ensure_bucket() -> None:
    """Create the default bucket if it does not exist."""
    settings = get_settings()
    async with get_s3_client() as client:
        try:
            await client.head_bucket(Bucket=settings.storage.s3_bucket)
        except Exception:
            await client.create_bucket(Bucket=settings.storage.s3_bucket)
            logger.info("Created S3 bucket: %s", settings.storage.s3_bucket)
