"""Tables for the expense tracker.

Three of them: the transactions themselves, the merchant → category rules we
learn as we go, and a log of every email we were handed so nothing can go
missing quietly.
"""

import logging
import os
from datetime import date as _date, datetime

from sqlalchemy import (
    Date,
    DateTime,
    Integer,
    Numeric,
    String,
    Text,
    create_engine,
    func,
    inspect,
    select,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

logger = logging.getLogger(__name__)

DATABASE_URL = os.environ["DATABASE_URL"]

# Railway hands out the old-style prefix; SQLAlchemy 2 wants the new one.
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

engine = create_engine(DATABASE_URL, pool_pre_ping=True, future=True)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, future=True)


class Base(DeclarativeBase):
    pass


class Transaction(Base):
    __tablename__ = "transactions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # Every row has one, including manual and statement rows — they get a
    # synthetic key — so the unique index is what stops double-inserts.
    gmail_id: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    date: Mapped[_date] = mapped_column(Date, nullable=False, index=True)
    merchant: Mapped[str] = mapped_column(String(200), nullable=False)
    merchant_raw: Mapped[str] = mapped_column(String(300), nullable=False)
    # Negative for credits and refunds, so summing the column gives net spend.
    amount: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    category: Mapped[str] = mapped_column(String(60), nullable=False)
    card_last4: Mapped[str | None] = mapped_column(String(8))
    source: Mapped[str] = mapped_column(String(20), default="email")
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "date": self.date.isoformat(),
            "merchant": self.merchant,
            "merchant_raw": self.merchant_raw,
            "amount": float(self.amount),
            "category": self.category,
            "card_last4": self.card_last4,
            "source": self.source,
            "note": self.note or "",
        }


class MerchantRule(Base):
    """What we have learned about a merchant, so Claude is asked once."""

    __tablename__ = "merchant_rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    pattern: Mapped[str] = mapped_column(String(200), unique=True, nullable=False)
    category: Mapped[str] = mapped_column(String(60), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    def to_dict(self) -> dict:
        return {"id": self.id, "pattern": self.pattern, "category": self.category}


class IngestLog(Base):
    """One row per email handed to us: inserted, ignored or failed, and why."""

    __tablename__ = "ingest_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    gmail_id: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    subject: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "gmail_id": self.gmail_id,
            "subject": self.subject or "",
            "status": self.status,
            "reason": self.reason or "",
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


def init_db() -> None:
    Base.metadata.create_all(engine)
    _add_missing_columns()


def _add_missing_columns() -> None:
    """Add columns that appeared after a table was first created.

    create_all() only creates missing tables, never missing columns, so a
    database from an earlier version keeps whatever it had. Adding them by
    hand here means a deploy never needs a migration step.
    """
    wanted = {
        "transactions": {
            "card_last4": "VARCHAR(8)",
            "source": "VARCHAR(20)",
            "note": "TEXT",
        },
    }
    inspector = inspect(engine)
    for table, columns in wanted.items():
        if not inspector.has_table(table):
            continue
        have = {c["name"] for c in inspector.get_columns(table)}
        for name, ddl in columns.items():
            if name in have:
                continue
            with engine.begin() as conn:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"))
            logger.info("Added %s.%s", table, name)
