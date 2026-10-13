import hashlib
import secrets

def hash_password(password: str, salt: str = None) -> tuple[str, str]:
    if not salt:
        salt = secrets.token_hex(16)
    hashed = hashlib.sha256((salt + password).encode("utf-8")).hexdigest()
    return hashed, salt

def verify_password(password: str, hashed: str, salt: str) -> bool:
    new_hash, _ = hash_password(password, salt)
    return secrets.compare_digest(new_hash, hashed)
