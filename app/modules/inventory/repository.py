from sqlalchemy.orm import Session

from app.core.exceptions import EntityNotFoundError, ValidationError
from app.modules.inventory.models import (
    Branch,
    StockBatch,
    StockTransfer,
    TransferStatus,
)


class InventoryRepository:
    """Database access methods for branches, batches and transfers."""

    def __init__(self, db: Session):
        self.db = db

    def get_all_transfers(self) -> list[StockTransfer]:
        return (
            self.db.query(StockTransfer)
            .order_by(StockTransfer.created_at.desc())
            .all()
        )

    def get_transfer_by_id(
        self, transfer_id: str
    ) -> StockTransfer:
        transfer = (
            self.db.query(StockTransfer)
            .filter(StockTransfer.id == transfer_id)
            .first()
        )

        if transfer is None:
            raise EntityNotFoundError("StockTransfer", transfer_id)

        return transfer

    def get_transfers_by_status(
        self, status: TransferStatus
    ) -> list[StockTransfer]:
        return (
            self.db.query(StockTransfer)
            .filter(StockTransfer.status == status)
            .order_by(StockTransfer.created_at.desc())
            .all()
        )

    def list_transfers(
        self,
        offset: int = 0,
        limit: int = 50,
    ) -> list[StockTransfer]:
        if offset < 0:
            raise ValidationError("Offset cannot be negative.")

        if limit < 1 or limit > 500:
            raise ValidationError("Limit must be between 1 and 500.")

        return (
            self.db.query(StockTransfer)
            .order_by(StockTransfer.created_at.desc())
            .offset(offset)
            .limit(limit)
            .all()
        )

    def get_branches(self) -> list[Branch]:
        return (
            self.db.query(Branch)
            .order_by(Branch.name.asc())
            .all()
        )

    def get_branch_by_id(self, branch_id: str) -> Branch:
        branch = (
            self.db.query(Branch)
            .filter(Branch.id == branch_id)
            .first()
        )

        if branch is None:
            raise EntityNotFoundError("Branch", branch_id)

        return branch

    def get_stock_batches(
        self,
        branch_id: str = None,
        product_id: str = None,
    ) -> list[StockBatch]:
        query = self.db.query(StockBatch)

        if branch_id is not None:
            query = query.filter(StockBatch.branch_id == branch_id)

        if product_id is not None:
            query = query.filter(StockBatch.product_id == product_id)

        return query.order_by(StockBatch.batch_number.asc()).all()

    def get_batch_by_id(self, batch_id: str) -> StockBatch:
        batch = (
            self.db.query(StockBatch)
            .filter(StockBatch.id == batch_id)
            .first()
        )

        if batch is None:
            raise EntityNotFoundError("StockBatch", batch_id)

        return batch

    def adjust_batch_quantities(
        self,
        adjustments: dict[str, int],
    ) -> list[StockBatch]:
        """Apply quantity adjustments atomically; never allow negative stock.

        adjustments maps batch IDs to signed quantity changes.
        Example: {"batch-id": -2} removes two units.
        """

        if not isinstance(adjustments, dict) or not adjustments:
            raise ValidationError(
                "Provide at least one batch quantity adjustment."
            )

        try:
            updated_batches = []

            for batch_id, delta in adjustments.items():
                if (
                    isinstance(delta, bool)
                    or not isinstance(delta, int)
                ):
                    raise ValidationError(
                        "Batch quantity adjustments must be integers."
                    )

                batch = (
                    self.db.query(StockBatch)
                    .filter(StockBatch.id == batch_id)
                    .with_for_update()
                    .first()
                )

                if batch is None:
                    raise EntityNotFoundError("StockBatch", batch_id)

                new_quantity = batch.quantity + delta

                if new_quantity < 0:
                    raise ValidationError(
                        f"Insufficient stock in batch {batch_id}."
                    )

                batch.quantity = new_quantity
                updated_batches.append(batch)

            self.db.commit()

            for batch in updated_batches:
                self.db.refresh(batch)

            return updated_batches

        except Exception:
            self.db.rollback()
            raise