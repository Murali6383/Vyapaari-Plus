import uuid
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.modules.inventory.models import (
    StockTransfer,
    TransferItem,
    TransferStatus,
)
from app.core.exceptions import EntityNotFoundError, ValidationError


class StockTransferService:
    """Service handling the lifecycle of inter-store stock transfers."""

    def __init__(self, db: Session):
        self.db = db

    def create_transfer_request(
        self,
        source_branch_id: str,
        destination_branch_id: str,
        items: list[dict],
        dispatched_by: str = None,
        notes: str = None,
    ) -> StockTransfer:
        """Create a transfer request after validating all items."""

        if not source_branch_id or not destination_branch_id:
            raise ValidationError(
                "Source and destination branch IDs are required."
            )

        if source_branch_id == destination_branch_id:
            raise ValidationError(
                "Source and destination branch cannot be the same."
            )

        if not isinstance(items, list) or not items:
            raise ValidationError(
                "Transfer must contain at least one item."
            )

        # Validate every item before creating database records.
        for item_data in items:
            if not isinstance(item_data, dict):
                raise ValidationError(
                    "Each transfer item must be a dictionary."
                )

            if not item_data.get("product_id"):
                raise ValidationError("Product ID is required.")

            quantity = item_data.get("quantity_dispatched")

            if (
                isinstance(quantity, bool)
                or not isinstance(quantity, int)
                or quantity <= 0
            ):
                raise ValidationError(
                    "Dispatched quantity must be a positive integer."
                )

            unit_cost = item_data.get("unit_cost", 0.0)

            if (
                isinstance(unit_cost, bool)
                or not isinstance(unit_cost, (int, float))
                or unit_cost < 0
            ):
                raise ValidationError(
                    "Unit cost must be a non-negative number."
                )

        transfer_number = (
            f"TRF-{datetime.now(timezone.utc).strftime('%Y%m%d')}-"
            f"{uuid.uuid4().hex[:6].upper()}"
        )

        transfer = StockTransfer(
            id=str(uuid.uuid4()),
            transfer_number=transfer_number,
            source_branch_id=source_branch_id,
            destination_branch_id=destination_branch_id,
            status=TransferStatus.DRAFT,
            dispatched_by=dispatched_by,
            notes=notes,
        )

        try:
            self.db.add(transfer)
            self.db.flush()

            for item_data in items:
                item = TransferItem(
                    id=str(uuid.uuid4()),
                    transfer_id=transfer.id,
                    product_id=item_data["product_id"],
                    product_name=item_data.get(
                        "product_name", "Unknown Product"
                    ),
                    batch_number=item_data.get("batch_number"),
                    quantity_dispatched=item_data["quantity_dispatched"],
                    quantity_received=0,
                    unit_cost=item_data.get("unit_cost", 0.0),
                )
                self.db.add(item)

            self.db.commit()
            self.db.refresh(transfer)
            return transfer

        except Exception:
            self.db.rollback()
            raise

    def dispatch_transfer(
        self,
        transfer_id: str,
        vehicle_number: str = None,
        driver_contact: str = None,
    ) -> StockTransfer:
        """Dispatch a draft transfer for delivery."""

        transfer = (
            self.db.query(StockTransfer)
            .filter(StockTransfer.id == transfer_id)
            .first()
        )

        if not transfer:
            raise EntityNotFoundError("StockTransfer", transfer_id)

        if transfer.status != TransferStatus.DRAFT:
            raise ValidationError(
                f"Cannot dispatch transfer with status: "
                f"{transfer.status.value}"
            )

        if not transfer.items:
            raise ValidationError(
                "Cannot dispatch a transfer without items."
            )

        transfer.status = TransferStatus.IN_TRANSIT
        transfer.dispatched_at = datetime.now(timezone.utc)
        transfer.vehicle_number = vehicle_number
        transfer.driver_contact = driver_contact

        try:
            self.db.commit()
            self.db.refresh(transfer)
            return transfer
        except Exception:
            self.db.rollback()
            raise

    def receive_and_record_delivery(
        self,
        transfer_id: str,
        received_items: list[dict],
        received_by: str = None,
    ) -> StockTransfer:
        """Record received quantities after validating the delivery."""

        transfer = (
            self.db.query(StockTransfer)
            .filter(StockTransfer.id == transfer_id)
            .first()
        )

        if not transfer:
            raise EntityNotFoundError("StockTransfer", transfer_id)

        if transfer.status != TransferStatus.IN_TRANSIT:
            raise ValidationError(
                "Cannot receive transfer that is not IN_TRANSIT. "
                f"Current status: {transfer.status.value}"
            )

        if not isinstance(received_items, list) or not received_items:
            raise ValidationError(
                "Received items must be a non-empty list."
            )

        expected_item_ids = {item.id for item in transfer.items}
        received_dict = {}

        for received_item in received_items:
            if not isinstance(received_item, dict):
                raise ValidationError(
                    "Each received item must be a dictionary."
                )

            item_id = received_item.get("item_id")

            if not item_id or item_id not in expected_item_ids:
                raise ValidationError(
                    f"Invalid transfer item ID: {item_id}"
                )

            if item_id in received_dict:
                raise ValidationError(
                    f"Duplicate receipt entry for item: {item_id}"
                )

            quantity = received_item.get("quantity_received")

            if (
                isinstance(quantity, bool)
                or not isinstance(quantity, int)
                or quantity < 0
            ):
                raise ValidationError(
                    "Received quantity must be a non-negative integer."
                )

            dispatched_quantity = next(
                item.quantity_dispatched
                for item in transfer.items
                if item.id == item_id
            )

            if quantity > dispatched_quantity:
                raise ValidationError(
                    "Received quantity cannot exceed dispatched quantity."
                )

            received_dict[item_id] = quantity

        missing_item_ids = expected_item_ids - set(received_dict)

        if missing_item_ids:
            raise ValidationError(
                "Missing receipt entries for items: "
                f"{sorted(missing_item_ids)}"
            )

        try:
            for item in transfer.items:
                item.quantity_received = received_dict[item.id]

            transfer.status = TransferStatus.RECEIVED
            transfer.received_at = datetime.now(timezone.utc)
            transfer.received_by = received_by

            self.db.commit()
            self.db.refresh(transfer)
            return transfer

        except Exception:
            self.db.rollback()
            raise