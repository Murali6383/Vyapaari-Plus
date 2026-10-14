import pytest
from app.modules.inventory.models import Branch, TransferStatus
from app.modules.inventory.stock_transfer import StockTransferService
from app.modules.inventory.reconciliation import MultiBranchReconciliationEngine

def test_stock_transfer_lifecycle(db_session):
    # Setup branches
    b1 = Branch(id="b1", name="Main Warehouse", code="WH-01", is_warehouse=True)
    b2 = Branch(id="b2", name="Downtown Store", code="STR-02", is_warehouse=False)
    db_session.add_all([b1, b2])
    db_session.commit()

    service = StockTransferService(db_session)
    reconciler = MultiBranchReconciliationEngine(db_session)

    # 1. Create transfer
    items = [
        {"product_id": "p1", "product_name": "Premium Basmati Rice 5kg", "quantity_dispatched": 20, "unit_cost": 450.0}
    ]
    transfer = service.create_transfer_request("b1", "b2", items, dispatched_by="Ramesh Manager")
    assert transfer.status == TransferStatus.DRAFT
    assert len(transfer.items) == 1

    # 2. Dispatch
    dispatched = service.dispatch_transfer(transfer.id, vehicle_number="DL-01-AB-1234")
    assert dispatched.status == TransferStatus.IN_TRANSIT

    # 3. Receive with 1 unit transit loss
    received_items = [{"item_id": transfer.items[0].id, "quantity_received": 19}]
    received = service.receive_and_record_delivery(transfer.id, received_items, received_by="Suresh Store")
    assert received.status == TransferStatus.RECEIVED

    # 4. Reconcile
    rec_log = reconciler.reconcile_transfer(transfer.id, reconciled_by="Auditor Sharma", remarks="1 unit damaged in transit")
    assert rec_log.variance_units == 1
    assert rec_log.total_transit_loss_value == 450.0
    assert transfer.status == TransferStatus.DISPUTED
def test_create_transfer_rejects_non_positive_quantity(db_session):
    b1 = Branch(id="b1", name="Warehouse", code="WH-01", is_warehouse=True)
    b2 = Branch(id="b2", name="Store", code="STR-02", is_warehouse=False)
    db_session.add_all([b1, b2])
    db_session.commit()

    service = StockTransferService(db_session)

    with pytest.raises(Exception):
        service.create_transfer_request(
            "b1",
            "b2",
            [{"product_id": "p1", "quantity_dispatched": 0}],
        )

    assert db_session.query(
        __import__(
            "app.modules.inventory.models",
            fromlist=["StockTransfer"],
        ).StockTransfer
    ).count() == 0


def test_receive_rejects_quantity_above_dispatched(db_session):
    b1 = Branch(id="b1", name="Warehouse", code="WH-01", is_warehouse=True)
    b2 = Branch(id="b2", name="Store", code="STR-02", is_warehouse=False)
    db_session.add_all([b1, b2])
    db_session.commit()

    service = StockTransferService(db_session)

    transfer = service.create_transfer_request(
        "b1",
        "b2",
        [{"product_id": "p1", "quantity_dispatched": 5}],
    )
    service.dispatch_transfer(transfer.id)

    with pytest.raises(Exception):
        service.receive_and_record_delivery(
            transfer.id,
            [{
                "item_id": transfer.items[0].id,
                "quantity_received": 6,
            }],
        )

    db_session.rollback()

    assert transfer.status == TransferStatus.IN_TRANSIT
