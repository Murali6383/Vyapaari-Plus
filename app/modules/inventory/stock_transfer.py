
import uuid
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.core.exceptions import EntityNotFoundError, ValidationError
from app.modules.inventory.models import (
    CarrierLog,
    StockTransfer,
    TransferItem,
    TransferStatus,
)


class StockTransferService:
    """Service handling inter-store stock transfer lifecycle and tracking."""

    def __init__(self, db: Session):
        self.db = db

    @staticmethod
    def _utc_now() -> datetime:
        """Return a timezone-aware UTC timestamp."""
        return datetime.now(timezone.utc)

    def _get_transfer(self, transfer_id: str) -> StockTransfer:
        """Fetch a transfer or raise a not-found exception."""
        transfer = (
            self.db.query(StockTransfer)
            .filter(StockTransfer.id == transfer_id)
            .first()
        )

        if not transfer:
            raise EntityNotFoundError("StockTransfer", transfer_id)

        return transfer

    @staticmethod
    def _document_number(prefix: str) -> str:
        """Generate a unique document reference."""
        date_part = datetime.now(timezone.utc).strftime("%Y%m%d")
        unique_part = uuid.uuid4().hex[:10].upper()
        return f"{prefix}-{date_part}-{unique_part}"

    def _add_carrier_log(
        self,
        transfer: StockTransfer,
        event_type: str,
        location: str = None,
        details: str = None,
        logged_by: str = None,
    ) -> CarrierLog:
        """Add a tracking event to the current database transaction."""
        log = CarrierLog(
            id=str(uuid.uuid4()),
            transfer_id=transfer.id,
            event_type=event_type,
            location=location,
            details=details,
            logged_by=logged_by,
            logged_at=self._utc_now(),
        )
        self.db.add(log)
        return log

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

        transfer = StockTransfer(
            id=str(uuid.uuid4()),
            transfer_number=self._document_number("TRF"),
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

            self._add_carrier_log(
                transfer=transfer,
                event_type="TRANSFER_CREATED",
                details="Stock transfer request created.",
                logged_by=dispatched_by,
            )

            self.db.commit()
            self.db.refresh(transfer)
            return transfer

        except Exception:
            self.db.rollback()
            raise

    def generate_delivery_documents(
        self,
        transfer_id: str,
    ) -> StockTransfer:
        """Generate a delivery challan and gate pass for a draft transfer."""

        transfer = self._get_transfer(transfer_id)

        if transfer.status != TransferStatus.DRAFT:
            raise ValidationError(
                "Delivery documents can only be generated "
                "for a DRAFT transfer."
            )

        if not transfer.items:
            raise ValidationError(
                "Cannot generate documents for a transfer without items."
            )

        try:
            if not transfer.delivery_challan_number:
                transfer.delivery_challan_number = (
                    self._document_number("CHL")
                )

            if not transfer.gate_pass_number:
                transfer.gate_pass_number = self._document_number("GATE")

            self._add_carrier_log(
                transfer=transfer,
                event_type="DOCUMENTS_GENERATED",
                details=(
                    f"Challan: {transfer.delivery_challan_number}; "
                    f"Gate pass: {transfer.gate_pass_number}"
                ),
            )

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
        """Dispatch a draft transfer and generate documents if needed."""

        transfer = self._get_transfer(transfer_id)

        if transfer.status != TransferStatus.DRAFT:
            raise ValidationError(
                f"Cannot dispatch transfer with status: "
                f"{transfer.status.value}"
            )

        if not transfer.items:
            raise ValidationError(
                "Cannot dispatch a transfer without items."
            )

        try:
            # Automatically generate documents when not generated earlier.
            if not transfer.delivery_challan_number:
                transfer.delivery_challan_number = (
                    self._document_number("CHL")
                )

            if not transfer.gate_pass_number:
                transfer.gate_pass_number = self._document_number("GATE")

            transfer.status = TransferStatus.DISPATCHED
            transfer.dispatched_at = self._utc_now()
            transfer.vehicle_number = vehicle_number
            transfer.driver_contact = driver_contact

            self._add_carrier_log(
                transfer=transfer,
                event_type="DISPATCHED",
                details=(
                    f"Vehicle: {vehicle_number or 'Not provided'}; "
                    f"Challan: {transfer.delivery_challan_number}; "
                    f"Gate pass: {transfer.gate_pass_number}"
                ),
                logged_by=transfer.dispatched_by,
            )

            self.db.commit()
            self.db.refresh(transfer)
            return transfer

        except Exception:
            self.db.rollback()
            raise

    def start_transit(self, transfer_id: str) -> StockTransfer:
        """Mark a dispatched transfer as being in transit."""

        transfer = self._get_transfer(transfer_id)

        if transfer.status != TransferStatus.DISPATCHED:
            raise ValidationError(
                "Only DISPATCHED transfers can enter transit. "
                f"Current status: {transfer.status.value}"
            )

        try:
            transfer.status = TransferStatus.IN_TRANSIT

            self._add_carrier_log(
                transfer=transfer,
                event_type="IN_TRANSIT",
                details="Transfer has started transit.",
            )

            self.db.commit()
            self.db.refresh(transfer)
            return transfer

        except Exception:
            self.db.rollback()
            raise

    def update_carrier_tracking(
        self,
        transfer_id: str,
        carrier_name: str = None,
        estimated_delivery_at: datetime = None,
        vehicle_number: str = None,
        driver_contact: str = None,
        updated_by: str = None,
    ) -> StockTransfer:
        """Update carrier, vehicle, driver and estimated delivery details."""

        transfer = self._get_transfer(transfer_id)

        if transfer.status not in (
            TransferStatus.DISPATCHED,
            TransferStatus.IN_TRANSIT,
        ):
            raise ValidationError(
                "Carrier tracking can only be updated for "
                "DISPATCHED or IN_TRANSIT transfers."
            )

        if carrier_name is not None and not carrier_name.strip():
            raise ValidationError("Carrier name cannot be empty.")

        if estimated_delivery_at is not None:
            if not isinstance(estimated_delivery_at, datetime):
                raise ValidationError(
                    "Estimated delivery time must be a datetime."
                )

            if estimated_delivery_at.tzinfo is None:
                raise ValidationError(
                    "Estimated delivery time must include a timezone."
                )

        try:
            if carrier_name is not None:
                transfer.carrier_name = carrier_name.strip()

            if estimated_delivery_at is not None:
                transfer.estimated_delivery_at = estimated_delivery_at

            if vehicle_number is not None:
                transfer.vehicle_number = vehicle_number

            if driver_contact is not None:
                transfer.driver_contact = driver_contact

            self._add_carrier_log(
                transfer=transfer,
                event_type="TRACKING_UPDATED",
                details=(
                    f"Carrier: {transfer.carrier_name or 'Not provided'}; "
                    f"Vehicle: {transfer.vehicle_number or 'Not provided'}"
                ),
                logged_by=updated_by,
            )

            self.db.commit()
            self.db.refresh(transfer)
            return transfer

        except Exception:
            self.db.rollback()
            raise

    def record_carrier_event(
        self,
        transfer_id: str,
        event_type: str,
        location: str = None,
        details: str = None,
        logged_by: str = None,
    ) -> CarrierLog:
        """Record a carrier checkpoint or delivery movement event."""

        transfer = self._get_transfer(transfer_id)

        if transfer.status not in (
            TransferStatus.DISPATCHED,
            TransferStatus.IN_TRANSIT,
        ):
            raise ValidationError(
                "Carrier events can only be recorded for "
                "DISPATCHED or IN_TRANSIT transfers."
            )

        if not isinstance(event_type, str) or not event_type.strip():
            raise ValidationError("Carrier event type is required.")

        try:
            log = self._add_carrier_log(
                transfer=transfer,
                event_type=event_type.strip().upper(),
                location=location,
                details=details,
                logged_by=logged_by,
            )

            self.db.commit()
            self.db.refresh(log)
            return log

        except Exception:
            self.db.rollback()
            raise

    def get_tracking_history(
        self,
        transfer_id: str,
    ) -> list[CarrierLog]:
        """Return tracking events ordered by event time."""

        self._get_transfer(transfer_id)

        return (
            self.db.query(CarrierLog)
            .filter(CarrierLog.transfer_id == transfer_id)
            .order_by(CarrierLog.logged_at.asc(), CarrierLog.id.asc())
            .all()
        )

    def receive_and_record_delivery(
        self,
        transfer_id: str,
        received_items: list[dict],
        received_by: str = None,
    ) -> StockTransfer:
        """Record received quantities after validating the delivery."""

        transfer = self._get_transfer(transfer_id)

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
        transfer_items_by_id = {
            item.id: item for item in transfer.items
        }
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

            dispatched_quantity = (
                transfer_items_by_id[item_id].quantity_dispatched
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
            transfer.received_at = self._utc_now()
            transfer.delivered_at = transfer.received_at
            transfer.received_by = received_by

            total_dispatched = sum(
                item.quantity_dispatched for item in transfer.items
            )
            total_received = sum(
                item.quantity_received for item in transfer.items
            )

            self._add_carrier_log(
                transfer=transfer,
                event_type="DELIVERED",
                details=(
                    f"Dispatched units: {total_dispatched}; "
                    f"Received units: {total_received}"
                ),
                logged_by=received_by,
            )

            self.db.commit()
            self.db.refresh(transfer)
            return transfer

        except Exception:
            self.db.rollback()
            raise
