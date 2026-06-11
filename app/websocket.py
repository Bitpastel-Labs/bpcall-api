from fastapi import WebSocket
from sqlalchemy.orm import Session
from datetime import datetime, timezone
import json

from app.models import User, Message, ChatRoomMember, CallLog


class ConnectionManager:
    def __init__(self):
        # user_id -> WebSocket
        self.active_connections: dict[int, WebSocket] = {}

    async def connect(self, websocket: WebSocket, user_id: int, db: Session):
        await websocket.accept()
        self.active_connections[user_id] = websocket

        # Fresh read from DB
        db.expire_all()

        # Mark user online
        user = db.query(User).filter(User.id == user_id).first()
        if user:
            user.is_online = True
            db.commit()

        # Notify connections about online status
        await self._broadcast_presence(user_id, True, db)

        # Deliver any pending "sent" messages to this user and notify senders
        await self._deliver_pending_messages(user_id, db)

    async def disconnect(self, user_id: int, db: Session):
        self.active_connections.pop(user_id, None)

        db.expire_all()

        user = db.query(User).filter(User.id == user_id).first()
        if user:
            user.is_online = False
            db.commit()
        await self._broadcast_presence(user_id, False, db)

    async def _broadcast_presence(self, user_id: int, is_online: bool, db: Session):
        """Notify all of user's room members about presence change."""
        room_ids = [
            m.room_id for m in
            db.query(ChatRoomMember).filter(ChatRoomMember.user_id == user_id).all()
        ]
        member_ids = set()
        for rid in room_ids:
            members = db.query(ChatRoomMember).filter(ChatRoomMember.room_id == rid).all()
            for m in members:
                if m.user_id != user_id:
                    member_ids.add(m.user_id)

        msg = json.dumps({
            "type": "presence",
            "payload": {"user_id": user_id, "is_online": is_online},
        })
        for mid in member_ids:
            ws = self.active_connections.get(mid)
            if ws:
                try:
                    await ws.send_text(msg)
                except Exception:
                    pass

    async def _deliver_pending_messages(self, user_id: int, db: Session):
        """When a user comes online, mark 'sent' messages in their rooms as 'delivered' and notify senders."""
        # Find all rooms this user is in
        room_ids = [
            m.room_id for m in
            db.query(ChatRoomMember).filter(ChatRoomMember.user_id == user_id).all()
        ]
        if not room_ids:
            return

        # Find all "sent" messages from OTHER users in those rooms
        pending_messages = db.query(Message).filter(
            Message.room_id.in_(room_ids),
            Message.sender_id != user_id,
            Message.status == "sent",
        ).all()

        if not pending_messages:
            return

        # Group by sender so we can batch-notify
        sender_rooms: dict[int, set[int]] = {}
        for msg in pending_messages:
            msg.status = "delivered"
            if msg.sender_id not in sender_rooms:
                sender_rooms[msg.sender_id] = set()
            sender_rooms[msg.sender_id].add(msg.room_id)

        db.commit()

        # Notify each sender about their delivered messages
        for sender_id, room_ids_set in sender_rooms.items():
            for rid in room_ids_set:
                # Get all message IDs that were just delivered in this room
                delivered_ids = [
                    m.id for m in pending_messages
                    if m.sender_id == sender_id and m.room_id == rid
                ]
                await self.send_to_user(sender_id, "messages_delivered", {
                    "room_id": rid,
                    "message_ids": delivered_ids,
                    "delivered_to": user_id,
                })

    async def send_to_user(self, user_id: int, msg_type: str, payload: dict):
        """Send a message to a specific user if they're online."""
        ws = self.active_connections.get(user_id)
        if ws:
            try:
                await ws.send_text(json.dumps({"type": msg_type, "payload": payload}))
            except Exception:
                pass

    async def handle_message(self, user_id: int, data: dict, db: Session):
        msg_type = data.get("type")
        payload = data.get("payload", {})

        if msg_type == "chat_message":
            await self._handle_chat_message(user_id, payload, db)
        elif msg_type == "typing":
            await self._handle_typing(user_id, payload, db)
        elif msg_type in ("call_initiate", "call_accept", "call_reject", "call_end"):
            await self._handle_call_signal(user_id, msg_type, payload, db)
        elif msg_type in ("webrtc_offer", "webrtc_answer", "webrtc_ice_candidate"):
            await self._relay_webrtc(user_id, msg_type, payload)

    async def _handle_chat_message(self, sender_id: int, payload: dict, db: Session):
        room_id = payload.get("room_id")
        content = payload.get("content")
        if not room_id or not content:
            return

        # Verify sender is member
        member = db.query(ChatRoomMember).filter(
            ChatRoomMember.room_id == room_id,
            ChatRoomMember.user_id == sender_id,
        ).first()
        if not member:
            return

        # Persist message
        message = Message(room_id=room_id, sender_id=sender_id, content=content, status="sent")
        db.add(message)
        db.commit()
        db.refresh(message)

        # Broadcast to room members and track delivery
        members = db.query(ChatRoomMember).filter(ChatRoomMember.room_id == room_id).all()
        sender = db.query(User).filter(User.id == sender_id).first()
        delivered_to_any = False
        out = json.dumps({
            "type": "chat_message",
            "payload": {
                "id": message.id,
                "room_id": room_id,
                "sender_id": sender_id,
                "sender_username": sender.username if sender else "unknown",
                "sender_display_name": sender.display_name if sender else "Unknown",
                "sender_avatar_url": sender.avatar_url if sender else None,
                "content": content,
                "status": "sent",
                "created_at": message.created_at.isoformat(),
            },
        })
        for m in members:
            ws = self.active_connections.get(m.user_id)
            if ws:
                try:
                    await ws.send_text(out)
                    if m.user_id != sender_id:
                        delivered_to_any = True
                except Exception:
                    pass

        # If delivered to at least one recipient, update status and notify sender
        if delivered_to_any:
            message.status = "delivered"
            db.commit()
            await self.send_to_user(sender_id, "message_status", {
                "message_id": message.id,
                "room_id": room_id,
                "status": "delivered",
            })

    async def _handle_typing(self, sender_id: int, payload: dict, db: Session):
        room_id = payload.get("room_id")
        if not room_id:
            return

        sender = db.query(User).filter(User.id == sender_id).first()
        sender_name = sender.display_name or sender.username if sender else "Someone"
        members = db.query(ChatRoomMember).filter(ChatRoomMember.room_id == room_id).all()
        out = json.dumps({
            "type": "typing",
            "payload": {"room_id": room_id, "user_id": sender_id, "user_name": sender_name},
        })
        for m in members:
            if m.user_id != sender_id:
                ws = self.active_connections.get(m.user_id)
                if ws:
                    try:
                        await ws.send_text(out)
                    except Exception:
                        pass

    async def _handle_call_signal(self, user_id: int, msg_type: str, payload: dict, db: Session):
        room_id = payload.get("room_id")
        if not room_id:
            return

        if msg_type == "call_initiate":
            call_type = payload.get("call_type", "audio")
            call_log = CallLog(room_id=room_id, initiated_by=user_id, call_type=call_type)
            db.add(call_log)
            db.commit()
            db.refresh(call_log)
            payload["call_log_id"] = call_log.id
            caller = db.query(User).filter(User.id == user_id).first()
            if caller:
                payload["from_user_name"] = caller.display_name or caller.username
                payload["from_user_username"] = caller.username
            # System message in chat
            await self._send_system_message(
                room_id, user_id,
                f"📞 {caller.display_name or caller.username if caller else 'Someone'} started a {call_type} call",
                db
            )

        if msg_type == "call_accept":
            acceptor = db.query(User).filter(User.id == user_id).first()
            if acceptor:
                payload["from_user_name"] = acceptor.display_name or acceptor.username
                payload["from_user_username"] = acceptor.username

        if msg_type == "call_end":
            call_log_id = payload.get("call_log_id")
            if call_log_id:
                call_log = db.query(CallLog).filter(CallLog.id == call_log_id).first()
                if call_log and not call_log.ended_at:
                    call_log.ended_at = datetime.now(timezone.utc)
                    db.commit()
            caller = db.query(User).filter(User.id == user_id).first()
            await self._send_system_message(
                room_id, user_id,
                f"📞 Call ended",
                db
            )

        members = db.query(ChatRoomMember).filter(ChatRoomMember.room_id == room_id).all()
        out = json.dumps({
            "type": msg_type,
            "payload": {**payload, "from_user_id": user_id},
        })
        for m in members:
            if m.user_id != user_id:
                ws = self.active_connections.get(m.user_id)
                if ws:
                    try:
                        await ws.send_text(out)
                    except Exception:
                        pass

    async def _send_system_message(self, room_id: int, sender_id: int, content: str, db: Session):
        """Send a system/info message to a room and broadcast it."""
        message = Message(room_id=room_id, sender_id=sender_id, content=content, status="delivered")
        db.add(message)
        db.commit()
        db.refresh(message)

        sender = db.query(User).filter(User.id == sender_id).first()
        members = db.query(ChatRoomMember).filter(ChatRoomMember.room_id == room_id).all()
        out = json.dumps({
            "type": "chat_message",
            "payload": {
                "id": message.id,
                "room_id": room_id,
                "sender_id": sender_id,
                "sender_username": sender.username if sender else "system",
                "sender_display_name": sender.display_name if sender else "System",
                "content": content,
                "status": "delivered",
                "created_at": message.created_at.isoformat(),
                "is_system": True,
            },
        })
        for m in members:
            ws = self.active_connections.get(m.user_id)
            if ws:
                try:
                    await ws.send_text(out)
                except Exception:
                    pass

    async def _relay_webrtc(self, sender_id: int, msg_type: str, payload: dict):
        target_user_id = payload.get("target_user_id")
        if not target_user_id:
            return
        ws = self.active_connections.get(target_user_id)
        if ws:
            out = json.dumps({
                "type": msg_type,
                "payload": {**payload, "from_user_id": sender_id},
            })
            try:
                await ws.send_text(out)
            except Exception:
                pass


manager = ConnectionManager()
