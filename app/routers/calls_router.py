from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import User, CallLog, ChatRoomMember
from app.schemas import CallLogOut
from app.auth import get_current_user

router = APIRouter(prefix="/api/calls", tags=["calls"])


@router.get("/history", response_model=list[CallLogOut])
def call_history(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    room_ids = [
        m.room_id for m in
        db.query(ChatRoomMember).filter(ChatRoomMember.user_id == current_user.id).all()
    ]
    return db.query(CallLog).filter(
        CallLog.room_id.in_(room_ids)
    ).order_by(CallLog.started_at.desc()).limit(50).all()
