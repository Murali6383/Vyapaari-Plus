
from datetime import datetime, timezone

import pytest

from app.core.exceptions import ValidationError
from app.modules.inventory.models import (
    Branch,
    CarrierLog,
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
    assert dispatched.delivery_challan_number.startswith("CHL-")
    assert dispatched.gate_pass_number.startswith("GATE-")

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
    assert received.delivered_at is not None

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


def test_dispatch_generates_delivery_documents(db_session):
    """Dispatch should generate a challan, gate pass and audit event."""
    create_branches(db_session)
    service = StockTransferService(db_session)

    transfer = service.create_transfer_request(
        "b1",
        "b2",
        [{"product_id": "p1", "quantity_dispatched": 5}],
    )

    dispatched = service.dispatch_transfer(
        transfer.id,
        vehicle_number="TN-32-AB-1234",
    )

    assert dispatched.status == TransferStatus.DISPATCHED
    assert dispatched.delivery_challan_number.startswith("CHL-")
    assert dispatched.gate_pass_number.startswith("GATE-")

    event_types = {
        log.event_type for log in dispatched.carrier_logs
    }
    assert "DISPATCHED" in event_types


def test_update_carrier_tracking(db_session):
    """Carrier, ETA and vehicle details should be updated."""
    create_branches(db_session)
    service = StockTransferService(db_session)

    transfer = service.create_transfer_request(
        "b1",
        "b2",
        [{"product_id": "p1", "quantity_dispatched": 5}],
    )

    service.dispatch_transfer(transfer.id)

    eta = datetime(
        2026, 10, 15, 12, 0, tzinfo=timezone.utc
    )

    updated = service.update_carrier_tracking(
        transfer.id,
        carrier_name="FastTrack Logistics",
        estimated_delivery_at=eta,
        vehicle_number="TN-32-AB-1234",
    )

    assert updated.carrier_name == "FastTrack Logistics"
    assert updated.vehicle_number == "TN-32-AB-1234"
    assert updated.estimated_delivery_at is not None


def test_record_and_get_carrier_events(db_session):
    """Carrier events should be saved and returned in tracking history."""
    create_branches(db_session)
    service = StockTransferService(db_session)

    transfer = service.create_transfer_request(
        "b1",
        "b2",
        [{"product_id": "p1", "quantity_dispatched": 5}],
    )

    service.dispatch_transfer(transfer.id)
    service.start_transit(transfer.id)

    service.record_carrier_event(
        transfer.id,
        event_type="LOCATION_UPDATE",
        location="Chennai",
        details="Shipment reached Chennai hub",
        logged_by="Transport Manager",
    )

    history = service.get_tracking_history(transfer.id)

    assert len(history) >= 1
    assert any(
        event.event_type == "LOCATION_UPDATE"
        and event.location == "Chennai"
        for event in history
    )


def test_carrier_event_rejected_for_draft_transfer(db_session):
    """Carrier events should not be recorded for a draft transfer."""
    create_branches(db_session)
    service = StockTransferService(db_session)

    transfer = service.create_transfer_request(
        "b1",
        "b2",
        [{"product_id": "p1", "quantity_dispatched": 5}],
    )

    with pytest.raises(ValidationError):
        service.record_carrier_event(
            transfer.id,
            event_type="LOCATION_UPDATE",
            location="Chennai",
        )

    assert transfer.status == TransferStatus.DRAFT
def test_damaged_and_missing_stock_reconciliation(db_session):
    """Damaged and missing units must be accounted for separately."""
    create_branches(db_session)

    service = StockTransferService(db_session)
    reconciler = MultiBranchReconciliationEngine(db_session)

    transfer = service.create_transfer_request(
        "b1",
        "b2",
        [
            {
                "product_id": "p1",
                "product_name": "Test Product",
                "quantity_dispatched": 10,
                "unit_cost": 100.0,
            }
        ],
    )

    service.dispatch_transfer(transfer.id)
    service.start_transit(transfer.id)

    service.receive_and_record_delivery(
        transfer.id,
        [
            {
                "item_id": transfer.items[0].id,
                "quantity_received": 7,
                "quantity_damaged": 2,
            }
        ],
        received_by="Store Manager",
    )

    db_session.refresh(transfer)
    item = transfer.items[0]

    assert item.quantity_received == 7
    assert item.quantity_damaged == 2

    # 10 dispatched = 7 good + 2 damaged + 1 missing.
    log = reconciler.reconcile_transfer(
        transfer.id,
        reconciled_by="Inventory Auditor",
    )

    assert log.total_dispatched_units == 10
    assert log.total_received_units == 7
    assert log.variance_units == 3
    assert log.total_transit_loss_value == 300.0
    assert "Damaged units: 2" in log.remarks
    assert "Missing units: 1" in log.remarks
    assert transfer.status == TransferStatus.DISPUTED