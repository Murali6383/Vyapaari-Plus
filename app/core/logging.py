import sys
import logging
from app.config import settings

def setup_logging():
    settings.log_dir.mkdir(parents=True, exist_ok=True)
    try:
        from loguru import logger
        logger.remove()
        logger.add(
            sys.stderr,
            format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>",
            level=settings.log_level
        )
        logger.add(
            settings.log_dir / "vyapaari.log",
            rotation="10 MB",
            retention="30 days",
            level=settings.log_level
        )
        return logger
    except ImportError:
        logger = logging.getLogger("vyapaari")
        logger.setLevel(getattr(logging, settings.log_level, logging.INFO))
        if not logger.handlers:
            handler = logging.StreamHandler(sys.stderr)
            formatter = logging.Formatter("%(asctime)s | %(levelname)-8s | %(name)s - %(message)s")
            handler.setFormatter(formatter)
            logger.addHandler(handler)
        return logger
