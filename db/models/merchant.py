"""Merchant (tenant / store) model."""

from __future__ import annotations

from sqlalchemy import String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.db.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class Merchant(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "merchants"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    business_type: Mapped[str] = mapped_column(
        String(50), nullable=False, default="General",
        comment="FnB | Fashion | General",
    )
    phone: Mapped[str | None] = mapped_column(String(20))
    email: Mapped[str | None] = mapped_column(String(255))
    address: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(default=True)

    # Relationships
    customers = relationship("Customer", back_populates="merchant", lazy="selectin")
    products = relationship("Product", back_populates="merchant", lazy="selectin")
    api_keys = relationship("APIKey", back_populates="merchant", lazy="selectin")

    def __repr__(self) -> str:
        return f"<Merchant {self.name} ({self.business_type})>"
