from pydantic import BaseModel, EmailStr
from datetime import datetime
from typing import Optional


# --- Auth ---

class SignupRequest(BaseModel):
    email: EmailStr
    username: str
    password: str


class LoginRequest(BaseModel):
    identifier: str  # email or username
    password: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class RefreshRequest(BaseModel):
    refresh_token: str


# --- Users ---

class UserOut(BaseModel):
    id: int
    email: str
    username: str
    display_name: Optional[str] = None
    avatar_url: Optional[str] = None
    bio: Optional[str] = None
    is_online: bool = False
    created_at: datetime

    model_config = {"from_attributes": True}


class UserUpdate(BaseModel):
    display_name: Optional[str] = None
    avatar_url: Optional[str] = None
    bio: Optional[str] = None


# --- Connections ---

class ConnectionRequest(BaseModel):
    user_id: int


class ConnectionOut(BaseModel):
    id: int
    requester: UserOut
    receiver: UserOut
    status: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# --- Chat Rooms ---

class CreateRoomRequest(BaseModel):
    name: str
    member_ids: list[int]


class RoomMemberOut(BaseModel):
    id: int
    user: UserOut
    role: str = "member"
    joined_at: datetime

    model_config = {"from_attributes": True}


class RoomOut(BaseModel):
    id: int
    name: Optional[str] = None
    is_direct: bool
    creator_id: int
    created_at: datetime
    members: list[RoomMemberOut] = []

    model_config = {"from_attributes": True}


class RoomListOut(BaseModel):
    id: int
    name: Optional[str] = None
    is_direct: bool
    created_at: datetime

    model_config = {"from_attributes": True}


# --- Messages ---

class MessageOut(BaseModel):
    id: int
    room_id: int
    sender_id: int
    sender: UserOut
    content: str
    status: str = "sent"
    created_at: datetime

    model_config = {"from_attributes": True}


class UnreadCountOut(BaseModel):
    room_id: int
    count: int


# --- Call Logs ---

class CallLogOut(BaseModel):
    id: int
    room_id: int
    initiated_by: int
    call_type: str
    started_at: datetime
    ended_at: Optional[datetime] = None

    model_config = {"from_attributes": True}
