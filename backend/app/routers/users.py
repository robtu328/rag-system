from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.auth import require_admin
from app.database import get_db
from app.models import Group, User
from app.schemas import UserOut, UserUpdate

router = APIRouter(prefix="/users", tags=["users"])


@router.get("", response_model=list[UserOut])
def list_users(db: Session = Depends(get_db), _admin: User = Depends(require_admin)):
    return db.query(User).order_by(User.email).all()


@router.patch("/{user_id}", response_model=UserOut)
def update_user(
    user_id: str,
    payload: UserUpdate,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    if payload.is_active is False and user.id == admin.id:
        raise HTTPException(status_code=400, detail="You cannot deactivate your own account")
    if payload.is_admin is False and user.id == admin.id:
        raise HTTPException(status_code=400, detail="You cannot remove your own admin rights")

    if payload.is_active is not None:
        user.is_active = payload.is_active
    if payload.is_admin is not None:
        user.is_admin = payload.is_admin
    if payload.group_names is not None:
        groups = []
        for name in payload.group_names:
            group = db.query(Group).filter(Group.name == name).first()
            if not group:
                group = Group(name=name)
                db.add(group)
                db.flush()
            groups.append(group)
        user.groups = groups

    db.commit()
    db.refresh(user)
    return user
