import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

class Settings:
    def __init__(self):
        self.app_name = os.getenv("APP_NAME", "Vyapaari+")
        self.app_version = os.getenv("APP_VERSION", "1.0.0")
        self.app_env = os.getenv("APP_ENV", "development")
        self.debug = os.getenv("APP_DEBUG", "true").lower() == "true"
        self.secret_key = os.getenv("APP_SECRET_KEY", "vyapaari-secret-key-change-in-production")
        
        # SQLite Database
        self.db_path = BASE_DIR / "vyapaari.db"
        self.database_url = os.getenv("DATABASE_URL", f"sqlite:///{self.db_path}")
        
        # Logging
        self.log_dir = BASE_DIR / "logs"
        self.log_level = os.getenv("LOG_LEVEL", "INFO")

settings = Settings()
