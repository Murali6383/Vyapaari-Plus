from sqlalchemy.orm import Session
from app.modules.inventory.stock_transfer import StockTransferService
from app.modules.inventory.reconciliation import MultiBranchReconciliationEngine

class InventoryService:
    def __init__(self, db: Session):
        self.db = db
        self.transfers = StockTransferService(db)
        self.reconciliation = MultiBranchReconciliationEngine(db)
