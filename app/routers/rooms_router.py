from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import func
from datetime import datetime, timezone

from app.database import get_db
from app.models import User, ChatRoom, ChatRoomMember, Message, Connection
from app.schemas import CreateRoomRequest, RoomOut, RoomListOut, MessageOut, UnreadCountOut
from app.auth import get_current_user
from app.websocket import manager
from sqlalchemy import or_, and_

router = APIRouter(prefix="/api/rooms", tags=["rooms"])


@router.get("", response_model=list[RoomListOut])
def list_rooms(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    room_ids = [
        m.room_id for m in
        db.query(ChatRoomMember).filter(ChatRoomMember.user_id == current_user.id).all()
    ]
    return db.query(ChatRoom).filter(ChatRoom.id.in_(room_ids)).all()


@router.post("", response_model=RoomOut)
async def create_room(
    req: CreateRoomRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    for member_id in req.member_ids:
        if member_id == current_user.id:
            continue
        conn = db.query(Connection).filter(
            or_(
                and_(Connection.requester_id == current_user.id, Connection.receiver_id == member_id),
                and_(Connection.requester_id == member_id, Connection.receiver_id == current_user.id),
            ),
            Connection.status == "accepted",
        ).first()
        if not conn:
            raise HTTPException(status_code=400, detail=f"User {member_id} is not a connection")

    room = ChatRoom(name=req.name, creator_id=current_user.id, is_direct=False)
    db.add(room)
    db.flush()

    db.add(ChatRoomMember(room_id=room.id, user_id=current_user.id, role="admin"))
    for member_id in req.member_ids:
        if member_id != current_user.id:
            db.add(ChatRoomMember(room_id=room.id, user_id=member_id, role="member"))

    db.commit()
    db.refresh(room)

    # Notify all members about the new room
    creator_name = current_user.display_name or current_user.username
    for member_id in req.member_ids:
        if member_id != current_user.id:
            await manager.send_to_user(member_id, "room_created", {
                "room_id": room.id,
                "room_name": room.name,
                "created_by": creator_name,
                "message": f"{creator_name} added you to '{room.name}'",
            })

    return room


@router.get("/unread", response_model=list[UnreadCountOut])
def get_unread_counts(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get unread message counts for all rooms the user is in."""
    memberships = db.query(ChatRoomMember).filter(
        ChatRoomMember.user_id == current_user.id
    ).all()

    results = []
    for m in memberships:
        query = db.query(func.count(Message.id)).filter(
            Message.room_id == m.room_id,
            Message.sender_id != current_user.id,
        )
        if m.last_read_at:
            query = query.filter(Message.created_at > m.last_read_at)
        count = query.scalar() or 0
        if count > 0:
            results.append(UnreadCountOut(room_id=m.room_id, count=count))
    return results


@router.put("/{room_id}/read")
async def mark_room_read(
    room_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Mark messages as read for the current user. Only upgrades to 'read' when ALL recipients have seen it."""
    member = db.query(ChatRoomMember).filter(
        ChatRoomMember.room_id == room_id,
        ChatRoomMember.user_id == current_user.id,
    ).first()
    if not member:
        raise HTTPException(status_code=403, detail="Not a member of this room")

    now = datetime.now(timezone.utc)
    member.last_read_at = now
    db.commit()

    # Get all members except the current user (to check their read status)
    all_members = db.query(ChatRoomMember).filter(
        ChatRoomMember.room_id == room_id,
    ).all()

    # Find messages from others that are still 'sent' or 'delivered'
    unread_messages = db.query(Message).filter(
        Message.room_id == room_id,
        Message.sender_id != current_user.id,
        Message.status.in_(["sent", "delivered"]),
    ).all()

    # For 'sent' -> 'delivered': this user just received them
    sent_messages = [m for m in unread_messages if m.status == "sent"]
    for msg in sent_messages:
        msg.status = "delivered"

    # For 'delivered' -> 'read': check if ALL other members (non-sender) have read each message
    delivered_messages = [m for m in unread_messages if m.status == "delivered"] + sent_messages
    sender_ids_to_notify_read = set()
    sender_ids_to_notify_delivered = set()

    for msg in sent_messages:
        sender_ids_to_notify_delivered.add(msg.sender_id)

    for msg in delivered_messages:
        # Check: have all members (except the sender) read past this message?
        all_read = True
        for m in all_members:
            if m.user_id == msg.sender_id:
                continue  # skip the sender
            if not m.last_read_at or m.last_read_at < msg.created_at:
                all_read = False
                break

        if all_read:
            msg.status = "read"
            sender_ids_to_notify_read.add(msg.sender_id)

    db.commit()

    # Notify senders about delivery status changes
    for sender_id in sender_ids_to_notify_delivered - sender_ids_to_notify_read:
        await manager.send_to_user(sender_id, "messages_delivered", {
            "room_id": room_id,
            "message_ids": [m.id for m in sent_messages if m.sender_id == sender_id],
            "delivered_to": current_user.id,
        })

    # Notify senders about read status (only when ALL members have read)
    for sender_id in sender_ids_to_notify_read:
        await manager.send_to_user(sender_id, "messages_read", {
            "room_id": room_id,
            "read_by": current_user.id,
        })

    return {"detail": "ok"}


@router.get("/{room_id}", response_model=RoomOut)
def get_room(
    room_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    member = db.query(ChatRoomMember).filter(
        ChatRoomMember.room_id == room_id,
        ChatRoomMember.user_id == current_user.id,
    ).first()
    if not member:
        raise HTTPException(status_code=403, detail="Not a member of this room")

    room = db.query(ChatRoom).filter(ChatRoom.id == room_id).first()
    if not room:
        raise HTTPException(status_code=404, detail="Room not found")
    return room


@router.delete("/{room_id}")
async def delete_room(
    room_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Delete a room. Only admins can do this. Cannot delete direct rooms."""
    room = db.query(ChatRoom).filter(ChatRoom.id == room_id).first()
    if not room:
        raise HTTPException(status_code=404, detail="Room not found")
    if room.is_direct:
        raise HTTPException(status_code=400, detail="Cannot delete a direct chat")

    member = db.query(ChatRoomMember).filter(
        ChatRoomMember.room_id == room_id,
        ChatRoomMember.user_id == current_user.id,
    ).first()
    if not member or member.role != "admin":
        raise HTTPException(status_code=403, detail="Only admins can delete rooms")

    # Notify all members before deleting
    members = db.query(ChatRoomMember).filter(ChatRoomMember.room_id == room_id).all()
    for m in members:
        if m.user_id != current_user.id:
            await manager.send_to_user(m.user_id, "room_deleted", {
                "room_id": room_id,
                "room_name": room.name,
                "message": f"'{room.name}' was deleted by {current_user.display_name or current_user.username}",
            })

    # Delete all related data
    db.query(Message).filter(Message.room_id == room_id).delete()
    db.query(ChatRoomMember).filter(ChatRoomMember.room_id == room_id).delete()
    db.delete(room)
    db.commit()
    return {"detail": "Room deleted"}


@router.post("/{room_id}/leave")
async def leave_room(
    room_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Leave a room. Admins cannot leave if they're the only admin — must assign another admin first."""
    room = db.query(ChatRoom).filter(ChatRoom.id == room_id).first()
    if not room:
        raise HTTPException(status_code=404, detail="Room not found")
    if room.is_direct:
        raise HTTPException(status_code=400, detail="Cannot leave a direct chat")

    member = db.query(ChatRoomMember).filter(
        ChatRoomMember.room_id == room_id,
        ChatRoomMember.user_id == current_user.id,
    ).first()
    if not member:
        raise HTTPException(status_code=403, detail="Not a member of this room")

    # If admin, check there's at least one other admin
    if member.role == "admin":
        other_admins = db.query(ChatRoomMember).filter(
            ChatRoomMember.room_id == room_id,
            ChatRoomMember.user_id != current_user.id,
            ChatRoomMember.role == "admin",
        ).count()
        if other_admins == 0:
            raise HTTPException(status_code=400, detail="You're the only admin. Make someone else admin before leaving.")

    db.delete(member)
    db.commit()

    name = current_user.display_name or current_user.username
    # Notify remaining members
    remaining = db.query(ChatRoomMember).filter(ChatRoomMember.room_id == room_id).all()
    for m in remaining:
        await manager.send_to_user(m.user_id, "room_member_left", {
            "room_id": room_id,
            "user_name": name,
            "message": f"{name} left '{room.name}'",
        })

    return {"detail": "Left room"}


@router.post("/{room_id}/members")
async def add_members(
    room_id: int,
    req: CreateRoomRequest,  # reuse — we only need member_ids, name is ignored
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Add members to a room. Only admins can do this."""
    room = db.query(ChatRoom).filter(ChatRoom.id == room_id).first()
    if not room:
        raise HTTPException(status_code=404, detail="Room not found")
    if room.is_direct:
        raise HTTPException(status_code=400, detail="Cannot add members to a direct chat")

    admin_member = db.query(ChatRoomMember).filter(
        ChatRoomMember.room_id == room_id,
        ChatRoomMember.user_id == current_user.id,
    ).first()
    if not admin_member or admin_member.role != "admin":
        raise HTTPException(status_code=403, detail="Only admins can add members")

    added = []
    for member_id in req.member_ids:
        # Check not already a member
        existing = db.query(ChatRoomMember).filter(
            ChatRoomMember.room_id == room_id,
            ChatRoomMember.user_id == member_id,
        ).first()
        if existing:
            continue
        # Check is a connection of the adder
        conn = db.query(Connection).filter(
            or_(
                and_(Connection.requester_id == current_user.id, Connection.receiver_id == member_id),
                and_(Connection.requester_id == member_id, Connection.receiver_id == current_user.id),
            ),
            Connection.status == "accepted",
        ).first()
        if not conn:
            continue
        db.add(ChatRoomMember(room_id=room_id, user_id=member_id, role="member"))
        added.append(member_id)

    db.commit()

    admin_name = current_user.display_name or current_user.username
    for member_id in added:
        await manager.send_to_user(member_id, "room_created", {
            "room_id": room_id,
            "room_name": room.name,
            "created_by": admin_name,
            "message": f"{admin_name} added you to '{room.name}'",
        })

    # Notify existing members
    all_members = db.query(ChatRoomMember).filter(ChatRoomMember.room_id == room_id).all()
    for m in all_members:
        if m.user_id not in added and m.user_id != current_user.id:
            added_names = []
            for aid in added:
                u = db.query(User).filter(User.id == aid).first()
                if u:
                    added_names.append(u.display_name or u.username)
            if added_names:
                await manager.send_to_user(m.user_id, "room_member_left", {
                    "room_id": room_id,
                    "user_name": ", ".join(added_names),
                    "message": f"{admin_name} added {', '.join(added_names)} to '{room.name}'",
                })

    return {"detail": f"Added {len(added)} members"}


@router.delete("/{room_id}/members/{user_id}")
async def remove_member(
    room_id: int,
    user_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Remove a member from a room. Only admins can do this."""
    room = db.query(ChatRoom).filter(ChatRoom.id == room_id).first()
    if not room:
        raise HTTPException(status_code=404, detail="Room not found")

    admin_member = db.query(ChatRoomMember).filter(
        ChatRoomMember.room_id == room_id,
        ChatRoomMember.user_id == current_user.id,
    ).first()
    if not admin_member or admin_member.role != "admin":
        raise HTTPException(status_code=403, detail="Only admins can remove members")

    if user_id == current_user.id:
        raise HTTPException(status_code=400, detail="Cannot remove yourself. Use leave instead.")

    target_member = db.query(ChatRoomMember).filter(
        ChatRoomMember.room_id == room_id,
        ChatRoomMember.user_id == user_id,
    ).first()
    if not target_member:
        raise HTTPException(status_code=404, detail="User is not a member")

    target_user = db.query(User).filter(User.id == user_id).first()
    db.delete(target_member)
    db.commit()

    admin_name = current_user.display_name or current_user.username
    target_name = target_user.display_name or target_user.username if target_user else "Someone"

    # Notify the removed user
    await manager.send_to_user(user_id, "room_deleted", {
        "room_id": room_id,
        "room_name": room.name,
        "message": f"You were removed from '{room.name}' by {admin_name}",
    })

    # Notify remaining members
    remaining = db.query(ChatRoomMember).filter(ChatRoomMember.room_id == room_id).all()
    for m in remaining:
        await manager.send_to_user(m.user_id, "room_member_left", {
            "room_id": room_id,
            "user_name": target_name,
            "message": f"{target_name} was removed from '{room.name}'",
        })

    return {"detail": "Member removed"}


@router.put("/{room_id}/members/{user_id}/role")
async def change_member_role(
    room_id: int,
    user_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Toggle a member's role between admin and member. Only admins can do this."""
    admin_member = db.query(ChatRoomMember).filter(
        ChatRoomMember.room_id == room_id,
        ChatRoomMember.user_id == current_user.id,
    ).first()
    if not admin_member or admin_member.role != "admin":
        raise HTTPException(status_code=403, detail="Only admins can change roles")

    target_member = db.query(ChatRoomMember).filter(
        ChatRoomMember.room_id == room_id,
        ChatRoomMember.user_id == user_id,
    ).first()
    if not target_member:
        raise HTTPException(status_code=404, detail="User is not a member")

    new_role = "admin" if target_member.role == "member" else "member"
    target_member.role = new_role
    db.commit()

    target_user = db.query(User).filter(User.id == user_id).first()
    target_name = target_user.display_name or target_user.username if target_user else "Someone"
    admin_name = current_user.display_name or current_user.username

    # Notify the target user
    await manager.send_to_user(user_id, "room_role_changed", {
        "room_id": room_id,
        "new_role": new_role,
        "message": f"You are now {'an admin' if new_role == 'admin' else 'a member'} of '{db.query(ChatRoom).filter(ChatRoom.id == room_id).first().name}'",
    })

    return {"detail": f"Role changed to {new_role}"}


@router.get("/{room_id}/messages", response_model=list[MessageOut])
def get_messages(
    room_id: int,
    before: str = Query(default=None),
    limit: int = Query(default=50, le=100),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    member = db.query(ChatRoomMember).filter(
        ChatRoomMember.room_id == room_id,
        ChatRoomMember.user_id == current_user.id,
    ).first()
    if not member:
        raise HTTPException(status_code=403, detail="Not a member of this room")

    query = db.query(Message).filter(Message.room_id == room_id)
    if before:
        query = query.filter(Message.created_at < datetime.fromisoformat(before))
    return query.order_by(Message.created_at.desc()).limit(limit).all()[::-1]
