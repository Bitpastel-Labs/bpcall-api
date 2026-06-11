from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import or_, and_

from app.database import get_db
from app.models import User, Connection, ChatRoom, ChatRoomMember
from app.schemas import ConnectionRequest, ConnectionOut
from app.auth import get_current_user
from app.websocket import manager

router = APIRouter(prefix="/api/connections", tags=["connections"])


def _user_payload(user: User) -> dict:
    return {
        "id": user.id,
        "username": user.username,
        "display_name": user.display_name,
        "email": user.email,
        "avatar_url": user.avatar_url,
    }


@router.post("/request", response_model=ConnectionOut)
async def send_request(
    req: ConnectionRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if req.user_id == current_user.id:
        raise HTTPException(status_code=400, detail="Cannot connect with yourself")

    target = db.query(User).filter(User.id == req.user_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="User not found")

    existing = db.query(Connection).filter(
        or_(
            and_(Connection.requester_id == current_user.id, Connection.receiver_id == req.user_id),
            and_(Connection.requester_id == req.user_id, Connection.receiver_id == current_user.id),
        ),
        Connection.status.in_(["pending", "accepted"]),
    ).first()
    if existing:
        raise HTTPException(status_code=400, detail="Connection already exists")

    conn = Connection(requester_id=current_user.id, receiver_id=req.user_id)
    db.add(conn)
    db.commit()
    db.refresh(conn)

    # Notify the receiver in real-time
    await manager.send_to_user(req.user_id, "connection_request", {
        "connection_id": conn.id,
        "from_user": _user_payload(current_user),
        "message": f"{current_user.display_name or current_user.username} wants to connect with you",
    })

    return conn


@router.get("/pending", response_model=list[ConnectionOut])
def get_pending(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return db.query(Connection).filter(
        Connection.receiver_id == current_user.id,
        Connection.status == "pending",
    ).all()


@router.get("/sent", response_model=list[ConnectionOut])
def get_sent(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return db.query(Connection).filter(
        Connection.requester_id == current_user.id,
        Connection.status == "pending",
    ).all()


@router.get("", response_model=list[ConnectionOut])
def get_connections(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return db.query(Connection).filter(
        or_(
            Connection.requester_id == current_user.id,
            Connection.receiver_id == current_user.id,
        ),
        Connection.status == "accepted",
    ).all()


@router.put("/{connection_id}/accept", response_model=ConnectionOut)
async def accept_connection(
    connection_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    conn = db.query(Connection).filter(
        Connection.id == connection_id,
        Connection.receiver_id == current_user.id,
        Connection.status == "pending",
    ).first()
    if not conn:
        raise HTTPException(status_code=404, detail="Connection request not found")

    conn.status = "accepted"

    # Auto-create direct chat room
    room = ChatRoom(
        name=None,
        creator_id=current_user.id,
        is_direct=True,
    )
    db.add(room)
    db.flush()
    db.add(ChatRoomMember(room_id=room.id, user_id=conn.requester_id))
    db.add(ChatRoomMember(room_id=room.id, user_id=conn.receiver_id))

    db.commit()
    db.refresh(conn)

    # Notify the requester that their request was accepted
    await manager.send_to_user(conn.requester_id, "connection_accepted", {
        "connection_id": conn.id,
        "by_user": _user_payload(current_user),
        "message": f"{current_user.display_name or current_user.username} accepted your connection request",
    })

    return conn


@router.put("/{connection_id}/reject", response_model=ConnectionOut)
async def reject_connection(
    connection_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    conn = db.query(Connection).filter(
        Connection.id == connection_id,
        Connection.receiver_id == current_user.id,
        Connection.status == "pending",
    ).first()
    if not conn:
        raise HTTPException(status_code=404, detail="Connection request not found")

    conn.status = "rejected"
    db.commit()
    db.refresh(conn)

    # Notify the requester that their request was declined
    await manager.send_to_user(conn.requester_id, "connection_rejected", {
        "connection_id": conn.id,
        "by_user": _user_payload(current_user),
        "message": f"{current_user.display_name or current_user.username} declined your connection request",
    })

    return conn


@router.delete("/{connection_id}")
def remove_connection(
    connection_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    conn = db.query(Connection).filter(
        Connection.id == connection_id,
        or_(
            Connection.requester_id == current_user.id,
            Connection.receiver_id == current_user.id,
        ),
        Connection.status == "accepted",
    ).first()
    if not conn:
        raise HTTPException(status_code=404, detail="Connection not found")

    db.delete(conn)
    db.commit()
    return {"detail": "Connection removed"}
