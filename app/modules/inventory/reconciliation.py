import uuid
from datetime import datetime, timezone

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.exceptions import EntityNotFoundError, ValidationError
from app.modules.inventory.models import (
    ReconciliationApproval,
    ReconciliationLog,
    StockTransfer,
    TransferStatus,
)

RESOLUTION_PENDING = "PENDING"
RESOLUTION_RESOLVED = "RESOLVED"

DECISION_APPROVED = "APPROVED"
DECISION_REJECTED = "REJECTED"

# Statuses from which a first reconciliation may be started.
RECONCILABLE_STATUSES = (
    TransferStatus.RECEIVED,
    TransferStatus.DISPUTED,
)


class MultiBranchReconciliationEngine:
    """Reconcile received, damaged and missing stock for transfers.

    Reconciliation is a bookkeeping step only. It never changes
    StockBatch quantities: source stock is deducted at dispatch and
    only good received units are added to the destination at receipt.
    Damaged and missing units are never added to usable stock here.
    """

    def __init__(self, db: Session):
        self.db = db

    # ------------------------------------------------------------------
    # Eligibility helpers (no database writes)
    # ------------------------------------------------------------------
    @staticmethod
    def can_reconcile(transfer: StockTransfer) -> bool:
        """True when a transfer may be reconciled for the first time."""
        return (
            transfer.status in RECONCILABLE_STATUSES
            and transfer.reconciliation is None
            and bool(transfer.items)
        )

    @staticmethod
    def can_review_dispute(transfer: StockTransfer) -> bool:
        """True when a disputed transfer awaits an approval decision."""
        log = transfer.reconciliation
        return (
            transfer.status == TransferStatus.DISPUTED
            and log is not None
            and log.resolution_status == RESOLUTION_PENDING
        )

    # ------------------------------------------------------------------
    # Reconciliation
    # ------------------------------------------------------------------
    def reconcile_transfer(
        self,
        transfer_id: str,
        reconciled_by: str,
        resolution_status: str = None,
        remarks: str = None,
    ) -> ReconciliationLog:
        try:
            return self._reconcile_transfer(
                transfer_id,
                reconciled_by,
                resolution_status,
                remarks,
            )
        except Exception:
            # Also releases the row lock after validation failures.
            self.db.rollback()
            raise

    def _reconcile_transfer(
        self,
        transfer_id: str,
        reconciled_by: str,
        resolution_status: str,
        remarks: str,
    ) -> ReconciliationLog:
        transfer = (
            self.db.query(StockTransfer)
            .filter(StockTransfer.id == transfer_id)
            .with_for_update()
            .first()
        )

        if transfer is None:
            raise EntityNotFoundError("StockTransfer", transfer_id)

        # One reconciliation log per transfer. A second attempt must not
        # overwrite the audited figures of the first.
        if transfer.reconciliation is not None:
            raise ValidationError(
                "Transfer has already been reconciled. "
                f"Current status: {transfer.status.value}"
            )

        if transfer.status not in RECONCILABLE_STATUSES:
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

        # Variance = dispatched - good received (includes damaged and
        # missing units).
        variance = total_dispatched - total_received

        if resolution_status is None:
            resolution_status = (
                RESOLUTION_RESOLVED
                if variance == 0
                else RESOLUTION_PENDING
            )
        elif (
            not isinstance(resolution_status, str)
            or not resolution_status.strip()
        ):
            raise ValidationError(
                "Resolution status must be PENDING or RESOLVED."
            )

        resolution_status = resolution_status.strip().upper()

        if resolution_status not in {
            RESOLUTION_PENDING,
            RESOLUTION_RESOLVED,
        }:
            raise ValidationError(
                "Invalid resolution status. "
                "Allowed values: PENDING, RESOLVED."
            )

        # RESOLVED alone is not approval proof, so a transfer with a
        # variance can only become RESOLVED through record_approval().
        if variance != 0 and resolution_status == RESOLUTION_RESOLVED:
            raise ValidationError(
                "A transfer with a variance cannot be marked RESOLVED "
                "during reconciliation. Record an approval decision "
                "instead."
            )

        now = datetime.now(timezone.utc)

        try:
            log = ReconciliationLog(
                id=str(uuid.uuid4()),
                transfer_id=transfer.id,
                total_dispatched_units=total_dispatched,
                total_received_units=total_received,
                variance_units=variance,
                total_transit_loss_value=total_loss_value,
                reconciled_by=reconciled_by.strip(),
                reconciled_at=now,
                resolution_status=resolution_status,
            )

            summary = (
                f"Damaged units: {total_damaged}; "
                f"Missing units: {total_lost}."
            )
            log.remarks = (
                f"{remarks.strip()} | {summary}"
                if remarks and remarks.strip()
                else summary
            )
            self.db.add(log)

            transfer.status = (
                TransferStatus.RECONCILED
                if variance == 0
                else TransferStatus.DISPUTED
            )

            self.db.commit()
            self.db.refresh(log)
            return log

        except IntegrityError:
            # Unique(transfer_id) fired: another session reconciled first.
            self.db.rollback()
            raise ValidationError(
                "Transfer has already been reconciled."
            )
        except Exception:
            self.db.rollback()
            raise

    # ------------------------------------------------------------------
    # Approval audit history
    # ------------------------------------------------------------------
    def record_approval(
        self,
        transfer_id: str,
        approver: str,
        decision: str,
        remarks: str = None,
    ) -> ReconciliationApproval:
        """Record an approval decision for a DISPUTED reconciliation.

        APPROVED resolves the dispute (log RESOLVED, transfer RECONCILED).
        REJECTED keeps it DISPUTED/PENDING. Every decision is stored as a
        permanent ReconciliationApproval row.
        """
        try:
            return self._record_approval(
                transfer_id,
                approver,
                decision,
                remarks,
            )
        except Exception:
            self.db.rollback()
            raise

    def _record_approval(
        self,
        transfer_id: str,
        approver: str,
        decision: str,
        remarks: str,
    ) -> ReconciliationApproval:
        transfer = (
            self.db.query(StockTransfer)
            .filter(StockTransfer.id == transfer_id)
            .with_for_update()
            .first()
        )

        if transfer is None:
            raise EntityNotFoundError("StockTransfer", transfer_id)

        log = transfer.reconciliation

        if log is None:
            raise ValidationError(
                "Transfer has not been reconciled yet."
            )

        if transfer.status != TransferStatus.DISPUTED:
            raise ValidationError(
                "Only DISPUTED transfers can be approved or rejected. "
                f"Current status: {transfer.status.value}"
            )

        if log.resolution_status != RESOLUTION_PENDING:
            raise ValidationError(
                "This reconciliation is already resolved."
            )

        if not approver or not approver.strip():
            raise ValidationError("Approver name is required.")

        if not isinstance(decision, str) or not decision.strip():
            raise ValidationError(
                "Decision must be APPROVED or REJECTED."
            )

        decision = decision.strip().upper()

        if decision not in {DECISION_APPROVED, DECISION_REJECTED}:
            raise ValidationError(
                "Invalid decision. Allowed values: APPROVED, REJECTED."
            )

        clean_remarks = remarks.strip() if remarks else ""

        if decision == DECISION_REJECTED and not clean_remarks:
            raise ValidationError(
                "Remarks are required when rejecting a reconciliation."
            )

        try:
            approval = ReconciliationApproval(
                id=str(uuid.uuid4()),
                transfer_id=transfer.id,
                reconciliation_id=log.id,
                approver=approver.strip(),
                decision=decision,
                remarks=clean_remarks or None,
                decided_at=datetime.now(timezone.utc),
            )
            self.db.add(approval)

            if decision == DECISION_APPROVED:
                log.resolution_status = RESOLUTION_RESOLVED
                transfer.status = TransferStatus.RECONCILED

            self.db.commit()
            self.db.refresh(approval)
            return approval

        except Exception:
            self.db.rollback()
            raise

    def get_approval_history(
        self,
        transfer_id: str,
    ) -> list[ReconciliationApproval]:
        """Return approval decisions for a transfer, oldest first."""
        return (
            self.db.query(ReconciliationApproval)
            .filter(ReconciliationApproval.transfer_id == transfer_id)
            .order_by(
                ReconciliationApproval.decided_at.asc(),
                ReconciliationApproval.id.asc(),
            )
            .all()
        )

    # ------------------------------------------------------------------
    # Read-only summary for UI / reports
    # ------------------------------------------------------------------
    def build_summary(self, transfer_id: str) -> dict:
        """Return reconciliation figures and approval state."""
        transfer = (
            self.db.query(StockTransfer)
            .filter(StockTransfer.id == transfer_id)
            .first()
        )

        if transfer is None:
            raise EntityNotFoundError("StockTransfer", transfer_id)

        log = transfer.reconciliation

        if log is None:
            raise ValidationError(
                "Transfer has not been reconciled yet."
            )

        damaged = sum(item.quantity_damaged or 0 for item in transfer.items)
        missing = sum(
            item.quantity_dispatched
            - item.quantity_received
            - (item.quantity_damaged or 0)
            for item in transfer.items
        )

        approvals = self.get_approval_history(transfer.id)

        if approvals:
            approval_status = approvals[-1].decision
        elif log.variance_units == 0:
            approval_status = "NOT_REQUIRED"
        else:
            approval_status = "PENDING"

        return {
            "transfer_id": transfer.id,
            "transfer_number": transfer.transfer_number,
            "dispatched": log.total_dispatched_units,
            "good_received": log.total_received_units,
            "damaged": damaged,
            "missing": missing,
            "variance": log.variance_units,
            "loss_value": log.total_transit_loss_value,
            "resolution_status": log.resolution_status,
            "final_status": transfer.status.value,
            "reconciled_by": log.reconciled_by,
            "reconciled_at": log.reconciled_at,
            "remarks": log.remarks,
            "approval_status": approval_status,
            "approvals": [
                {
                    "approver": a.approver,
                    "decision": a.decision,
                    "remarks": a.remarks,
                    "decided_at": a.decided_at,
                }
                for a in approvals
            ],
        }
