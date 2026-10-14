
import pytest

from app.core.exceptions import ValidationError
from app.modules.inventory.models import (
    Branch,
    StockTransfer,
    TransferStatus,
)
from app.modules.inventory.stock_transfer import StockTransferService
from app.modules.inventory.reconciliation import (
    MultiBranchReconciliationEngine,
)


def create_branches(db_session):
    """Create test warehouse and store branches."""
    warehouse = Branch(
        id="b1",
        name="Main Warehouse",
        code="WH-01",
        is_warehouse=True,
    )
    store = Branch(
        id="b2",
        name="Downtown Store",
        code="STR-02",
        is_warehouse=False,
    )

    db_session.add_all([warehouse, store])
    db_session.commit()


def test_stock_transfer_lifecycle(db_session):
    """Test transfer creation, dispatch, transit, receipt and reconciliation."""
    create_branches(db_session)

    service = StockTransferService(db_session)
    reconciler = MultiBranchReconciliationEngine(db_session)

    items = [
        {
            "product_id": "p1",
            "product_name": "Premium Basmati Rice 5kg",
            "quantity_dispatched": 20,
            "unit_cost": 450.0,
        }
    ]

    # 1. Create transfer request
    transfer = service.create_transfer_request(
        "b1",
        "b2",
        items,
        dispatched_by="Ramesh Manager",
    )

    assert transfer.status == TransferStatus.DRAFT
    assert len(transfer.items) == 1
    assert transfer.items[0].quantity_dispatched == 20

    # 2. Dispatch the transfer
    dispatched = service.dispatch_transfer(
        transfer.id,
        vehicle_number="DL-01-AB-1234",
    )

    assert dispatched.status == TransferStatus.DISPATCHED
    assert dispatched.vehicle_number == "DL-01-AB-1234"

    # 3. Start transit
    in_transit = service.start_transit(transfer.id)

    assert in_transit.status == TransferStatus.IN_TRANSIT

    # 4. Receive 19 units; 1 unit is missing/damaged
    received_items = [
        {
            "item_id": transfer.items[0].id,
            "quantity_received": 19,
        }
    ]

    received = service.receive_and_record_delivery(
        transfer.id,
        received_items,
        received_by="Suresh Store",
    )

    assert received.status == TransferStatus.RECEIVED
    assert received.items[0].quantity_received == 19

    # 5. Reconcile the quantity variance
    rec_log = reconciler.reconcile_transfer(
        transfer.id,
        reconciled_by="Auditor Sharma",
        remarks="1 unit damaged in transit",
    )

    assert rec_log.variance_units == 1
    assert rec_log.total_transit_loss_value == 450.0
    assert transfer.status == TransferStatus.DISPUTED


def test_create_transfer_rejects_non_positive_quantity(db_session):
    """Reject zero quantity and avoid creating a transfer."""
    create_branches(db_session)

    service = StockTransferService(db_session)

    with pytest.raises(ValidationError):
        service.create_transfer_request(
            "b1",
            "b2",
            [
                {
                    "product_id": "p1",
                    "quantity_dispatched": 0,
                }
            ],
        )

    assert db_session.query(StockTransfer).count() == 0


def test_receive_rejects_quantity_above_dispatched(db_session):
    """Reject a receipt quantity greater than the dispatched quantity."""
    create_branches(db_session)

    service = StockTransferService(db_session)

    transfer = service.create_transfer_request(
        "b1",
        "b2",
        [
            {
                "product_id": "p1",
                "quantity_dispatched": 5,
            }
        ],
    )

    service.dispatch_transfer(transfer.id)
    service.start_transit(transfer.id)

    with pytest.raises(ValidationError):
        service.receive_and_record_delivery(
            transfer.id,
            [
                {
                    "item_id": transfer.items[0].id,
                    "quantity_received": 6,
                }
            ],
        )

    db_session.rollback()

    refreshed_transfer = (
        db_session.query(StockTransfer)
        .filter(StockTransfer.id == transfer.id)
        .first()
    )

    assert refreshed_transfer.status == TransferStatus.IN_TRANSIT


def test_cannot_start_transit_before_dispatch(db_session):
    """A draft transfer must not enter transit directly."""
    create_branches(db_session)

    service = StockTransferService(db_session)

    transfer = service.create_transfer_request(
        "b1",
        "b2",
        [
            {
                "product_id": "p1",
                "quantity_dispatched": 5,
            }
        ],
    )

    with pytest.raises(ValidationError):
        service.start_transit(transfer.id)

    assert transfer.status == TransferStatus.DRAFT
