"""
Logging setup — writes to console + rotating file.
"""

import logging
import os
from logging.handlers import RotatingFileHandler
from app.config import settings


def setup_logging(name: str = "ambassador") -> logging.Logger:
    log_dir = settings.LOG_DIR
    os.makedirs(log_dir, exist_ok=True)

    logger = logging.getLogger(name)
    logger.setLevel(getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO))

    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    console = logging.StreamHandler()
    console.setFormatter(fmt)
    logger.addHandler(console)

    fh = RotatingFileHandler(
        os.path.join(log_dir, f"{name}.log"),
        maxBytes=5_000_000,
        backupCount=3,
        encoding="utf-8",
    )
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    return logger


log = setup_logging()
