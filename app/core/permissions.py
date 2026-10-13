from enum import Enum

class UserRole(str, Enum):
    ADMIN = "ADMIN"
    STORE_MANAGER = "STORE_MANAGER"
    CASHIER = "CASHIER"
    AUDITOR = "AUDITOR"
