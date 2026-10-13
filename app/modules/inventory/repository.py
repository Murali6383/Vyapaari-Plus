from sqlalchemy.orm import Session
from app.modules.inventory.models import StockTransfer, Branch, StockBatch

class InventoryRepository:
    def __init__(self, db: Session):
        self.db = db

    def get_all_transfers(self):
        return self.db.query(StockTransfer).all()

    def get_branches(self):
        return self.db.query(Branch).all()
