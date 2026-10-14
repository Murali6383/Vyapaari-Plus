import uuid
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.core.exceptions import EntityNotFoundError, ValidationError
from app.modules.inventory.models import (
    ReconciliationLog,
    StockTransfer,
    TransferStatus,
)


class MultiBranchReconciliationEngine:
    """Reconcile received, damaged and missing stock for transfers."""

    def __init__(self, db: Session):
        self.db = db

    def reconcile_transfer(
        self,
        transfer_id: str,
        reconciled_by: str,
        resolution_status: str = None,
        remarks: str = None,
    ) -> ReconciliationLog:
        transfer = (
            self.db.query(StockTransfer)
            .filter(StockTransfer.id == transfer_id)
            .with_for_update()
            .first()
        )

        if transfer is None:
            raise EntityNotFoundError("StockTransfer", transfer_id)

        if transfer.status not in (
            TransferStatus.RECEIVED,
            TransferStatus.DISPUTED,
        ):
            raise ValidationError(
                "Cannot reconcile before delivery. "
                f"Current status: {transfer.status.value}"
            )

        if not transfer.items:
            raise ValidationError(
                "Cannot reconcile a transfer without items."
            )

        if not reconciled_by or not reconciled_by.strip():
            raise ValidationError("Reconciled-by is required.")

        total_dispatched = 0
        total_received = 0
        total_damaged = 0
        total_lost = 0
        total_loss_value = 0.0

        for item in transfer.items:
            dispatched = item.quantity_dispatched
            received = item.quantity_received
            damaged = item.quantity_damaged or 0

            if dispatched < 0 or received < 0 or damaged < 0:
                raise ValidationError(
                    "Transfer quantities cannot be negative."
                )

            if received + damaged > dispatched:
                raise ValidationError(
                    f"Received and damaged quantities exceed dispatched "
                    f"quantity for item {item.id}."
                )

            lost = dispatched - received - damaged

            total_dispatched += dispatched
            total_received += received
            total_damaged += damaged
            total_lost += lost
            total_loss_value += (damaged + lost) * item.unit_cost

        # Variance includes damaged and missing units.
        variance = total_dispatched - total_received

        if resolution_status is None:
            resolution_status = (
                "RESOLVED" if variance == 0 else "PENDING"
            )
        elif not resolution_status.strip():
            raise ValidationError("Resolution status cannot be empty.")

        now = datetime.now(timezone.utc)

        try:
            log = transfer.reconciliation

            if log is None:
                log = ReconciliationLog(
                    id=str(uuid.uuid4()),
                    transfer_id=transfer.id,
                )
                self.db.add(log)

            log.total_dispatched_units = total_dispatched
            log.total_received_units = total_received
            log.variance_units = variance
            log.total_transit_loss_value = total_loss_value
            log.reconciled_by = reconciled_by.strip()
            log.reconciled_at = now
            log.resolution_status = resolution_status.strip().upper()

            summary = (
                f"Damaged units: {total_damaged}; "
                f"Missing units: {total_lost}."
            )
            log.remarks = (
                f"{remarks.strip()} | {summary}"
                if remarks and remarks.strip()
                else summary
            )

            transfer.status = (
                TransferStatus.RECONCILED
                if variance == 0
                else TransferStatus.DISPUTED
            )

            self.db.commit()
            self.db.refresh(log)
            return log

        except Exception:
            self.db.rollback()
            raise