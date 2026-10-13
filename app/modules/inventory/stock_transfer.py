import uuid
from datetime import datetime
from sqlalchemy.orm import Session
from app.modules.inventory.models import StockTransfer, TransferItem, TransferStatus, ReconciliationLog
from app.core.exceptions import EntityNotFoundError, ValidationError

class StockTransferService:
    """Service handling lifecycle of inter-store and branch stock transfers."""

    def __init__(self, db: Session):
        self.db = db

    def create_transfer_request(
        self,
        source_branch_id: str,
        destination_branch_id: str,
        items: list[dict],
        dispatched_by: str = None,
        notes: str = None
    ) -> StockTransfer:
        if source_branch_id == destination_branch_id:
            raise ValidationError("Source and destination branch cannot be the same.")
        
        if not items:
            raise ValidationError("Transfer must contain at least one item.")

        transfer_number = f"TRF-{datetime.utcnow().strftime('%Y%m%d')}-{uuid.uuid4().hex[:6].upper()}"
        transfer = StockTransfer(
            id=str(uuid.uuid4()),
            transfer_number=transfer_number,
            source_branch_id=source_branch_id,
            destination_branch_id=destination_branch_id,
            status=TransferStatus.DRAFT,
            dispatched_by=dispatched_by,
            notes=notes
        )
        self.db.add(transfer)

        for item_data in items:
            item = TransferItem(
                id=str(uuid.uuid4()),
                transfer_id=transfer.id,
                product_id=item_data["product_id"],
                product_name=item_data.get("product_name", "Unknown Product"),
                batch_number=item_data.get("batch_number"),
                quantity_dispatched=item_data["quantity_dispatched"],
                quantity_received=0,
                unit_cost=item_data.get("unit_cost", 0.0)
            )
            self.db.add(item)

        self.db.commit()
        self.db.refresh(transfer)
        return transfer

    def dispatch_transfer(
        self,
        transfer_id: str,
        vehicle_number: str = None,
        driver_contact: str = None
    ) -> StockTransfer:
        transfer = self.db.query(StockTransfer).filter(StockTransfer.id == transfer_id).first()
        if not transfer:
            raise EntityNotFoundError("StockTransfer", transfer_id)

        if transfer.status != TransferStatus.DRAFT:
            raise ValidationError(f"Cannot dispatch transfer with status: {transfer.status.value}")

        transfer.status = TransferStatus.IN_TRANSIT
        transfer.dispatched_at = datetime.utcnow()
        transfer.vehicle_number = vehicle_number
        transfer.driver_contact = driver_contact

        self.db.commit()
        self.db.refresh(transfer)
        return transfer

    def receive_and_record_delivery(
        self,
        transfer_id: str,
        received_items: list[dict],
        received_by: str = None
    ) -> StockTransfer:
        transfer = self.db.query(StockTransfer).filter(StockTransfer.id == transfer_id).first()
        if not transfer:
            raise EntityNotFoundError("StockTransfer", transfer_id)

        if transfer.status != TransferStatus.IN_TRANSIT:
            raise ValidationError(f"Cannot receive transfer that is not IN_TRANSIT. Current status: {transfer.status.value}")

        received_dict = {item["item_id"]: item["quantity_received"] for item in received_items}

        for item in transfer.items:
            if item.id in received_dict:
                item.quantity_received = received_dict[item.id]

        transfer.status = TransferStatus.RECEIVED
        transfer.received_at = datetime.utcnow()
        transfer.received_by = received_by

        self.db.commit()
        self.db.refresh(transfer)
        return transfer
