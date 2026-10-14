
import enum
from datetime import datetime, timezone

from sqlalchemy import (
    String,
    Integer,
    Float,
    DateTime,
    Enum,
    ForeignKey,
    Text,
    Index,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.mixins import TimestampMixin


def utc_now() -> datetime:
    """Return a timezone-aware UTC timestamp."""
    return datetime.now(timezone.utc)


class TransferStatus(str, enum.Enum):
    DRAFT = "DRAFT"
    DISPATCHED = "DISPATCHED"
    IN_TRANSIT = "IN_TRANSIT"
    RECEIVED = "RECEIVED"
    RECONCILED = "RECONCILED"
    DISPUTED = "DISPUTED"


class Branch(Base, TimestampMixin):
    __tablename__ = "branches"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    code: Mapped[str] = mapped_column(
        String(20),
        unique=True,
        nullable=False,
    )
    address: Mapped[str | None] = mapped_column(Text, nullable=True)
    gstin: Mapped[str | None] = mapped_column(String(15), nullable=True)
    is_warehouse: Mapped[bool] = mapped_column(default=False, nullable=False)


class StockBatch(Base, TimestampMixin):
    __tablename__ = "stock_batches"

    __table_args__ = (
        Index("ix_stock_batches_branch_product", "branch_id", "product_id"),
        Index("ix_stock_batches_batch_number", "batch_number"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    product_id: Mapped[str] = mapped_column(
        String(36),
        index=True,
        nullable=False,
    )
    branch_id: Mapped[str] = mapped_column(
        ForeignKey("branches.id"),
        nullable=False,
    )
    batch_number: Mapped[str] = mapped_column(String(50), nullable=False)
    quantity: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
    )
    purchase_price: Mapped[float] = mapped_column(
        Float,
        default=0.0,
        nullable=False,
    )
    selling_price: Mapped[float] = mapped_column(
        Float,
        default=0.0,
        nullable=False,
    )
    expiry_date: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )


class StockTransfer(Base, TimestampMixin):
    """Model for inter-store and branch stock transfers."""

    __tablename__ = "stock_transfers"

    __table_args__ = (
        Index(
            "ix_stock_transfers_source_status",
            "source_branch_id",
            "status",
        ),
        Index(
            "ix_stock_transfers_destination_status",
            "destination_branch_id",
            "status",
        ),
        Index("ix_stock_transfers_dispatched_at", "dispatched_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)

    transfer_number: Mapped[str] = mapped_column(
        String(50),
        unique=True,
        index=True,
        nullable=False,
    )

    source_branch_id: Mapped[str] = mapped_column(
        ForeignKey("branches.id"),
        nullable=False,
    )

    destination_branch_id: Mapped[str] = mapped_column(
        ForeignKey("branches.id"),
        nullable=False,
    )

    status: Mapped[TransferStatus] = mapped_column(
        Enum(TransferStatus),
        default=TransferStatus.DRAFT,
        nullable=False,
        index=True,
    )

    dispatched_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    received_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    dispatched_by: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
    )

    received_by: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
    )

    vehicle_number: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
    )

    driver_contact: Mapped[str | None] = mapped_column(
        String(20),
        nullable=True,
    )

    # Delivery documents
    delivery_challan_number: Mapped[str | None] = mapped_column(
        String(50),
        unique=True,
        nullable=True,
    )

    gate_pass_number: Mapped[str | None] = mapped_column(
        String(50),
        unique=True,
        nullable=True,
    )

    # Carrier and delivery tracking
    carrier_name: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
    )

    estimated_delivery_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    delivered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    items: Mapped[list["TransferItem"]] = relationship(
        "TransferItem",
        back_populates="transfer",
        cascade="all, delete-orphan",
    )

    reconciliation: Mapped["ReconciliationLog | None"] = relationship(
        "ReconciliationLog",
        back_populates="transfer",
        uselist=False,
        cascade="all, delete-orphan",
    )

    carrier_logs: Mapped[list["CarrierLog"]] = relationship(
        "CarrierLog",
        back_populates="transfer",
        cascade="all, delete-orphan",
        order_by="CarrierLog.logged_at",
    )


class TransferItem(Base, TimestampMixin):
    """Items included in a stock transfer."""

    __tablename__ = "transfer_items"

    __table_args__ = (
        Index("ix_transfer_items_transfer_id", "transfer_id"),
        Index(
            "ix_transfer_items_product_batch",
            "product_id",
            "batch_number",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)

    transfer_id: Mapped[str] = mapped_column(
        ForeignKey("stock_transfers.id"),
        nullable=False,
    )

    product_id: Mapped[str] = mapped_column(
        String(36),
        nullable=False,
    )

    product_name: Mapped[str] = mapped_column(
        String(150),
        nullable=False,
    )

    batch_number: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
    )

    quantity_dispatched: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    quantity_received: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
    )

    unit_cost: Mapped[float] = mapped_column(
        Float,
        default=0.0,
        nullable=False,
    )

    transfer: Mapped["StockTransfer"] = relationship(
        "StockTransfer",
        back_populates="items",
    )
    
    quantity_damaged: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
    )


class ReconciliationLog(Base, TimestampMixin):
    """Reconciliation record for inter-branch inventory transfers."""

    __tablename__ = "reconciliation_logs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)

    transfer_id: Mapped[str] = mapped_column(
        ForeignKey("stock_transfers.id"),
        unique=True,
        nullable=False,
    )

    total_dispatched_units: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
    )

    total_received_units: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
    )

    variance_units: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
    )

    total_transit_loss_value: Mapped[float] = mapped_column(
        Float,
        default=0.0,
        nullable=False,
    )

    reconciled_by: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
    )

    reconciled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    resolution_status: Mapped[str] = mapped_column(
        String(50),
        default="PENDING",
        nullable=False,
    )

    remarks: Mapped[str | None] = mapped_column(Text, nullable=True)

    transfer: Mapped["StockTransfer"] = relationship(
        "StockTransfer",
        back_populates="reconciliation",
    )


class CarrierLog(Base, TimestampMixin):
    """Record carrier movement and delivery events."""

    __tablename__ = "carrier_logs"

    __table_args__ = (
        Index(
            "ix_carrier_logs_transfer_logged",
            "transfer_id",
            "logged_at",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)

    transfer_id: Mapped[str] = mapped_column(
        ForeignKey("stock_transfers.id"),
        nullable=False,
    )

    event_type: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
    )

    location: Mapped[str | None] = mapped_column(
        String(200),
        nullable=True,
    )

    details: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    logged_by: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
    )

    logged_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )

    transfer: Mapped["StockTransfer"] = relationship(
        "StockTransfer",
        back_populates="carrier_logs",
    )
