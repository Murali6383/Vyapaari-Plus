from datetime import datetime
from typing import Any

from fastapi import Depends, FastAPI, status
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

# Import models so SQLAlchemy registers their tables.
from app.modules.inventory.models import (
    CarrierLog,
    ReconciliationApproval,
    ReconciliationLog,
    StockTransfer,
)
from app.modules.inventory.service import InventoryService
from app.core.exceptions import (
    EntityNotFoundError,
    ValidationError,
    VyapaariException,
)
from app.db.session import get_db_session, init_db

app = FastAPI(
    title="Vyapaari+ Inventory Transfer API",
    version="1.0.0",
    description=(
        "Inter-branch stock transfers, transit tracking, "
        "delivery receipt and inventory reconciliation."
    ),
)


@app.on_event("startup")
def startup() -> None:
    init_db()


@app.exception_handler(VyapaariException)
async def handle_domain_error(request, exc: VyapaariException):
    code_to_status = {
        "NOT_FOUND": 404,
        "VALIDATION_ERROR": 422,
        "INSUFFICIENT_STOCK": 409,
    }
    return JSONResponse(
        status_code=code_to_status.get(exc.code, 400),
        content={"error": exc.code, "detail": exc.message},
    )


class TransferItemInput(BaseModel):
    product_id: str = Field(min_length=1, max_length=100)
    product_name: str = Field(default="Unknown Product", max_length=200)
    quantity_dispatched: int = Field(gt=0)
    unit_cost: float = Field(default=0.0, ge=0)
    batch_number: str | None = None


class CreateTransferInput(BaseModel):
    source_branch_id: str = Field(min_length=1)
    destination_branch_id: str = Field(min_length=1)
    items: list[TransferItemInput] = Field(min_length=1)
    dispatched_by: str | None = None
    notes: str | None = None


class DispatchInput(BaseModel):
    vehicle_number: str | None = None
    driver_contact: str | None = None


class TrackingInput(BaseModel):
    carrier_name: str | None = None
    estimated_delivery_at: datetime | None = None
    vehicle_number: str | None = None
    driver_contact: str | None = None
    updated_by: str | None = None


class CarrierEventInput(BaseModel):
    event_type: str = Field(min_length=1, max_length=100)
    location: str | None = None
    details: str | None = None
    logged_by: str | None = None


class ReceivedItemInput(BaseModel):
    item_id: str = Field(min_length=1)
    quantity_received: int = Field(ge=0)
    quantity_damaged: int = Field(default=0, ge=0)


class ReceiveInput(BaseModel):
    received_items: list[ReceivedItemInput] = Field(min_length=1)
    received_by: str | None = None


class ReconcileInput(BaseModel):
    reconciled_by: str = Field(min_length=1)
    resolution_status: str | None = None
    remarks: str | None = None


class ApprovalInput(BaseModel):
    approver: str = Field(min_length=1)
    decision: str = Field(pattern="^(APPROVED|REJECTED|approved|rejected)$")
    remarks: str | None = None


def transfer_to_dict(transfer: StockTransfer) -> dict[str, Any]:
    return {
        "id": transfer.id,
        "transfer_number": transfer.transfer_number,
        "source_branch_id": transfer.source_branch_id,
        "destination_branch_id": transfer.destination_branch_id,
        "status": transfer.status.value,
        "dispatched_by": transfer.dispatched_by,
        "received_by": transfer.received_by,
        "vehicle_number": transfer.vehicle_number,
        "driver_contact": transfer.driver_contact,
        "carrier_name": transfer.carrier_name,
        "estimated_delivery_at": transfer.estimated_delivery_at,
        "dispatched_at": transfer.dispatched_at,
        "received_at": transfer.received_at,
        "delivery_challan_number": transfer.delivery_challan_number,
        "gate_pass_number": transfer.gate_pass_number,
        "notes": transfer.notes,
        "items": [
            {
                "id": item.id,
                "product_id": item.product_id,
                "product_name": item.product_name,
                "batch_number": item.batch_number,
                "quantity_dispatched": item.quantity_dispatched,
                "quantity_received": item.quantity_received,
                "quantity_damaged": item.quantity_damaged,
                "unit_cost": item.unit_cost,
            }
            for item in transfer.items
        ],
    }


def carrier_log_to_dict(log: CarrierLog) -> dict[str, Any]:
    return {
        "id": log.id,
        "transfer_id": log.transfer_id,
        "event_type": log.event_type,
        "location": log.location,
        "details": log.details,
        "logged_by": log.logged_by,
        "logged_at": log.logged_at,
    }


@app.get("/health")
def health():
    return {"status": "ok", "service": "inventory-transfer-api"}


@app.post("/api/transfers", status_code=status.HTTP_201_CREATED)
def create_transfer(
    payload: CreateTransferInput,
    db: Session = Depends(get_db_session),
):
    service = InventoryService(db)
    transfer = service.transfers.create_transfer_request(
        source_branch_id=payload.source_branch_id,
        destination_branch_id=payload.destination_branch_id,
        items=[item.model_dump() for item in payload.items],
        dispatched_by=payload.dispatched_by,
        notes=payload.notes,
    )
    return transfer_to_dict(transfer)


@app.get("/api/transfers/{transfer_id}")
def get_transfer(
    transfer_id: str,
    db: Session = Depends(get_db_session),
):
    transfer = (
        db.query(StockTransfer)
        .filter(StockTransfer.id == transfer_id)
        .first()
    )
    if transfer is None:
        raise EntityNotFoundError("StockTransfer", transfer_id)
    return transfer_to_dict(transfer)


@app.post("/api/transfers/{transfer_id}/dispatch")
def dispatch_transfer(
    transfer_id: str,
    payload: DispatchInput,
    db: Session = Depends(get_db_session),
):
    transfer = InventoryService(db).transfers.dispatch_transfer(
        transfer_id,
        vehicle_number=payload.vehicle_number,
        driver_contact=payload.driver_contact,
    )
    return transfer_to_dict(transfer)


@app.post("/api/transfers/{transfer_id}/transit")
def start_transit(
    transfer_id: str,
    db: Session = Depends(get_db_session),
):
    transfer = InventoryService(db).transfers.start_transit(transfer_id)
    return transfer_to_dict(transfer)


@app.patch("/api/transfers/{transfer_id}/tracking")
def update_tracking(
    transfer_id: str,
    payload: TrackingInput,
    db: Session = Depends(get_db_session),
):
    transfer = InventoryService(db).transfers.update_carrier_tracking(
        transfer_id,
        **payload.model_dump(exclude_unset=True),
    )
    return transfer_to_dict(transfer)


@app.post("/api/transfers/{transfer_id}/tracking/events")
def record_tracking_event(
    transfer_id: str,
    payload: CarrierEventInput,
    db: Session = Depends(get_db_session),
):
    log = InventoryService(db).transfers.record_carrier_event(
        transfer_id,
        **payload.model_dump(),
    )
    return carrier_log_to_dict(log)


@app.get("/api/transfers/{transfer_id}/tracking")
def get_tracking_history(
    transfer_id: str,
    db: Session = Depends(get_db_session),
):
    logs = InventoryService(db).transfers.get_tracking_history(transfer_id)
    return [carrier_log_to_dict(log) for log in logs]


@app.post("/api/transfers/{transfer_id}/receive")
def receive_transfer(
    transfer_id: str,
    payload: ReceiveInput,
    db: Session = Depends(get_db_session),
):
    transfer = InventoryService(db).transfers.receive_and_record_delivery(
        transfer_id,
        received_items=[
            item.model_dump() for item in payload.received_items
        ],
        received_by=payload.received_by,
    )
    return transfer_to_dict(transfer)


@app.post("/api/transfers/{transfer_id}/reconcile")
def reconcile_transfer(
    transfer_id: str,
    payload: ReconcileInput,
    db: Session = Depends(get_db_session),
):
    log = InventoryService(db).reconciliation.reconcile_transfer(
        transfer_id,
        reconciled_by=payload.reconciled_by,
        resolution_status=payload.resolution_status,
        remarks=payload.remarks,
    )
    return jsonable_encoder({
        "id": log.id,
        "transfer_id": log.transfer_id,
        "total_dispatched_units": log.total_dispatched_units,
        "total_received_units": log.total_received_units,
        "variance_units": log.variance_units,
        "total_transit_loss_value": log.total_transit_loss_value,
        "resolution_status": log.resolution_status,
        "reconciled_by": log.reconciled_by,
        "reconciled_at": log.reconciled_at,
        "remarks": log.remarks,
    })


@app.post("/api/transfers/{transfer_id}/approval")
def approve_reconciliation(
    transfer_id: str,
    payload: ApprovalInput,
    db: Session = Depends(get_db_session),
):
    approval = InventoryService(db).reconciliation.record_approval(
        transfer_id,
        approver=payload.approver,
        decision=payload.decision,
        remarks=payload.remarks,
    )
    return jsonable_encoder({
        "id": approval.id,
        "transfer_id": approval.transfer_id,
        "approver": approval.approver,
        "decision": approval.decision,
        "remarks": approval.remarks,
        "decided_at": approval.decided_at,
    })


@app.get("/api/transfers/{transfer_id}/reconciliation")
def get_reconciliation_summary(
    transfer_id: str,
    db: Session = Depends(get_db_session),
):
    return jsonable_encoder(
        InventoryService(db).reconciliation.build_summary(transfer_id)
    )
