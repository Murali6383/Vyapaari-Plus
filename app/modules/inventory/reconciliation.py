import uuid
from datetime import datetime, timezone
from sqlalchemy.orm import Session
from app.modules.inventory.models import StockTransfer, TransferStatus, ReconciliationLog
from app.core.exceptions import EntityNotFoundError, ValidationError

class MultiBranchReconciliationEngine:
    """Reconciliation engine detecting transit variance and stock discrepancies."""

    def __init__(self, db: Session):
        self.db = db

    def reconcile_transfer(
        self,
        transfer_id: str,
        reconciled_by: str,
        resolution_status: str = "RESOLVED",
        remarks: str = None
    ) -> ReconciliationLog:
        transfer = self.db.query(StockTransfer).filter(StockTransfer.id == transfer_id).first()
        if not transfer:
            raise EntityNotFoundError("StockTransfer", transfer_id)

        if transfer.status not in (TransferStatus.RECEIVED, TransferStatus.DISPUTED):
            raise ValidationError(f"Cannot reconcile transfer prior to delivery. Current status: {transfer.status.value}")

        total_dispatched = sum(item.quantity_dispatched for item in transfer.items)
        total_received = sum(item.quantity_received for item in transfer.items)
        variance = total_dispatched - total_received

        transit_loss_val = sum(
            max(0, item.quantity_dispatched - item.quantity_received) * item.unit_cost
            for item in transfer.items
        )

        log = transfer.reconciliation
        if not log:
            log = ReconciliationLog(
                id=str(uuid.uuid4()),
                transfer_id=transfer.id,
                total_dispatched_units=total_dispatched,
                total_received_units=total_received,
                variance_units=variance,
                total_transit_loss_value=transit_loss_val,
                reconciled_by=reconciled_by,
                reconciled_at=datetime.now(timezone.utc),
                resolution_status=resolution_status,
                remarks=remarks
            )
            self.db.add(log)
        else:
            log.total_dispatched_units = total_dispatched
            log.total_received_units = total_received
            log.variance_units = variance
            log.total_transit_loss_value = transit_loss_val
            log.reconciled_by = reconciled_by
            log.reconciled_at = datetime.now(timezone.utc)
            log.resolution_status = resolution_status
            log.remarks = remarks

        transfer.status = TransferStatus.RECONCILED if variance == 0 else TransferStatus.DISPUTED
        self.db.commit()
        self.db.refresh(log)
        return log
