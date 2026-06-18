# models.py — SQLAlchemy ORM definitions for the telecom dashboard

import enum
from datetime import datetime

from sqlalchemy import (
    Column, Integer, String, Float, Boolean,
    DateTime, ForeignKey, Enum, Text
)
from sqlalchemy.orm import relationship

from database import Base


# ─── ENUMS (POSTGRES SAFE) ─────────────────────────────────────────────────────

class PackageType(str, enum.Enum):
    data = "data"
    minutes = "minutes"
    sms = "sms"
    combo = "combo"


class TransactionStatus(str, enum.Enum):
    pending = "pending"
    completed = "completed"
    failed = "failed"


# ─── MODELS ────────────────────────────────────────────────────────────────────

class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)

    username = Column(String(80), unique=True, nullable=False)

    phone_number = Column(String(20), unique=True, nullable=False, index=True)

    balance = Column(Float, default=0.0, nullable=False)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    transactions = relationship(
        "Transaction",
        back_populates="user",
        lazy="selectin"
    )

    def __repr__(self):
        return f"<User {self.phone_number}>"


class Package(Base):
    __tablename__ = "packages"

    id = Column(Integer, primary_key=True, index=True)

    name = Column(String(120), nullable=False)

    # IMPORTANT: fixed Postgres enum stability
    type = Column(
        Enum(
            PackageType,
            name="package_type",
            native_enum=True,
            create_type=True
        ),
        nullable=False,
        index=True
    )

    data_gb = Column(Float, nullable=True)
    minutes = Column(Integer, nullable=True)
    sms = Column(Integer, nullable=True)

    price = Column(Float, nullable=False)

    validity_days = Column(Integer, default=30, nullable=False)

    # FIXED: prevents NOT NULL violation
    is_active = Column(Boolean, default=True, nullable=False)

    description = Column(Text, nullable=True)

    transactions = relationship(
        "Transaction",
        back_populates="package",
        lazy="selectin"
    )

    def __repr__(self):
        return f"<Package {self.name}>"


class Transaction(Base):
    __tablename__ = "transactions"

    id = Column(Integer, primary_key=True, index=True)

    user_id = Column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )

    package_id = Column(
        Integer,
        ForeignKey("packages.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )

    amount = Column(Float, nullable=False)

    # CRITICAL FIX: stable enum name prevents asyncpg mismatch
    status = Column(
        Enum(
            TransactionStatus,
            name="transaction_status",
            native_enum=True,
            create_type=True
        ),
        nullable=False,
        default=TransactionStatus.pending
    )

    reference_code = Column(String(64), nullable=True, index=True)

    created_at = Column(
        DateTime,
        default=datetime.utcnow,
        nullable=False
    )

    user = relationship("User", back_populates="transactions")
    package = relationship("Package", back_populates="transactions")

    def __repr__(self):
        return f"<Transaction {self.id} {self.status}>"