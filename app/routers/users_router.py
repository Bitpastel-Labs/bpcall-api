from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from sqlalchemy import or_

from app.database import get_db
from app.models import User
from app.schemas import UserOut, UserUpdate
from app.auth import get_current_user

router = APIRouter(prefix="/api/users", tags=["users"])


@router.get("/me", response_model=UserOut)
def get_me(current_user: User = Depends(get_current_user)):
    return current_user


@router.put("/me", response_model=UserOut)
def update_me(
    updates: UserUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if updates.display_name is not None:
        current_user.display_name = updates.display_name
    if updates.avatar_url is not None:
        current_user.avatar_url = updates.avatar_url
    if updates.bio is not None:
        current_user.bio = updates.bio
    db.commit()
    db.refresh(current_user)
    return current_user


@router.get("/search", response_model=list[UserOut])
def search_users(
    q: str = Query(min_length=1),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    users = db.query(User).filter(
        User.id != current_user.id,
        or_(
            User.username.ilike(f"%{q}%"),
            User.email.ilike(f"%{q}%"),
        ),
    ).limit(20).all()
    return users
