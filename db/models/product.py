"""Product model — belongs to a Merchant."""

from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import ForeignKey, Numeric, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.db.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class Product(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "products"

    merchant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("merchants.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    category: Mapped[str | None] = mapped_column(String(100))
    price: Mapped[Decimal] = mapped_column(Numeric(15, 2), default=0)
    description: Mapped[str | None] = mapped_column(Text)
    product_type: Mapped[str] = mapped_column(
        String(50), nullable=False, default="General",
        comment="FnB | Clothing | General",
    )
    attributes: Mapped[dict | None] = mapped_column(
        JSONB, default=dict,
        comment="Flexible attrs: size, color, ingredients, etc.",
    )
    is_active: Mapped[bool] = mapped_column(default=True)

    # Relationships
    merchant = relationship("Merchant", back_populates="products")

    def __repr__(self) -> str:
        return f"<Product {self.name} ({self.product_type})>"
