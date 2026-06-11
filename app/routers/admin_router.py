from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse
import os

router = APIRouter(prefix="/api/admin", tags=["admin"])

ADMIN_KEY = os.getenv("ADMIN_BACKUP_KEY", "dev-backup-key")
DB_PATH = os.getenv("DATABASE_URL", "sqlite:///./bpcall.db").replace("sqlite:///", "")


@router.get("/backup")
def download_backup(key: str = Query(...)):
    if key != ADMIN_KEY:
        raise HTTPException(status_code=403, detail="Invalid admin key")
    if not os.path.exists(DB_PATH):
        raise HTTPException(status_code=404, detail="Database file not found")
    return FileResponse(DB_PATH, filename="bpcall.db", media_type="application/octet-stream")
