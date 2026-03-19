"""Seed script — creates default admin user, merchant, and API key.

Usage (standalone):
    python -m src.db.seed

Also callable from lifespan with a shared engine:
    await seed(engine=existing_engine)
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from src.core.config import get_settings
from src.core.security import generate_api_key, hash_api_key, hash_password
from src.db.models import AdminUser, APIKey, Base, Merchant

logger = logging.getLogger(__name__)


async def seed(engine: AsyncEngine | None = None) -> None:
    """Seed default data. If *engine* is ``None``, creates a temporary one."""
    settings = get_settings()
    _own_engine = engine is None
    if _own_engine:
        engine = create_async_engine(settings.database.postgres_url)

    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    # Ensure tables exist (safe to call multiple times)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with session_factory() as session:
        # ── 1. Admin user ────────────────────────────────────
        existing_admin = await session.execute(
            select(AdminUser).where(AdminUser.username == settings.auth.admin_username)
        )
        if existing_admin.scalars().first() is None:
            admin = AdminUser(
                username=settings.auth.admin_username,
                password_hash=hash_password(settings.auth.admin_password),
                role="admin",
            )
            session.add(admin)
            await session.flush()
            logger.info("Created admin user: %s", settings.auth.admin_username)
        else:
            logger.info("Admin user '%s' already exists, skipping", settings.auth.admin_username)

        # ── 2. Default merchant ──────────────────────────────
        existing_merchant = await session.execute(
            select(Merchant).where(Merchant.name == "Default Merchant")
        )
        merchant = existing_merchant.scalars().first()
        if merchant is None:
            merchant = Merchant(
                name="Default Merchant",
                business_type="General",
                phone="0900000000",
                email="admin@kiotviet.vn",
            )
            session.add(merchant)
            await session.flush()
            logger.info("Created default merchant: %s (id=%s)", merchant.name, merchant.id)
        else:
            logger.info("Default merchant already exists (id=%s), skipping", merchant.id)

        # ── 3. Default API key ───────────────────────────────
        existing_key = await session.execute(
            select(APIKey).where(APIKey.merchant_id == merchant.id, APIKey.is_active.is_(True))
        )
        if existing_key.scalars().first() is None:
            raw_key = generate_api_key()
            api_key = APIKey(
                merchant_id=merchant.id,
                key_hash=hash_api_key(raw_key),
                description="Default tenant API key (seeded)",
                expires_at=datetime.now(timezone.utc) + timedelta(days=365),
            )
            session.add(api_key)
            logger.info("Created API key: %s  <-- SAVE THIS!", raw_key)
        else:
            logger.info("Active API key for default merchant already exists, skipping")

        await session.commit()

    if _own_engine:
        await engine.dispose()

    logger.info("Seed complete.")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    asyncio.run(seed())
