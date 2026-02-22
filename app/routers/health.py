from fastapi import APIRouter
from app.database import fetch_val
from app.utils.logging import log

router = APIRouter()


@router.get("/health")
async def health():
    try:
        db_ok = await fetch_val("SELECT 1;")
        return {"status": "ok", "db": bool(db_ok)}
    except Exception as exc:
        log.error("Health check DB failure: %s", exc)
        return {"status": "degraded", "db": False, "error": str(exc)}
