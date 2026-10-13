class VyapaariException(Exception):
    """Base exception for all Vyapaari+ domain errors."""
    def __init__(self, message: str, code: str = "INTERNAL_ERROR"):
        super().__init__(message)
        self.message = message
        self.code = code

class EntityNotFoundError(VyapaariException):
    def __init__(self, entity: str, identifier: str):
        super().__init__(f"{entity} with ID {identifier} not found.", "NOT_FOUND")

class ValidationError(VyapaariException):
    def __init__(self, message: str):
        super().__init__(message, "VALIDATION_ERROR")

class InsufficientStockError(VyapaariException):
    def __init__(self, product_id: str, available: int, requested: int):
        super().__init__(
            f"Insufficient stock for product {product_id}. Available: {available}, Requested: {requested}",
            "INSUFFICIENT_STOCK"
        )
