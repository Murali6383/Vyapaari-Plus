from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from app.config import settings
from app.db.base import Base

engine = create_engine(
    settings.database_url,
    connect_args={"check_same_thread": False} if "sqlite" in settings.database_url else {},
    echo=settings.debug
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def get_db_session() -> Session:
    db = SessionLocal()
    try:
        return db
    finally:
        pass

def init_db():
    Base.metadata.create_all(bind=engine)
