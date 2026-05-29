# routes/auth.py

import httpx
import logging
from fastapi import APIRouter, Depends, HTTPException, status, Request
from sqlalchemy.orm import Session

from core.auth.jwt import get_current_active_user
from core.auth.models import Token, User, UserCreate, LoginRequest
from core.database import models as db_models
from core.database.connection import get_db
from core.configs.settings import AUTH_SERVICE_URL

logger = logging.getLogger(__name__)

router = APIRouter()

def sync_local_user(db: Session, vps_user_data: dict) -> db_models.User:
    """Helper to ensure a local record exists for the authenticated user email."""
    email = vps_user_data.get("email")
    if not email:
        return None
        
    local_user = db.query(db_models.User).filter(db_models.User.email == email).first()
    if not local_user:
        local_user = db_models.User(
            email=email,
            full_name=vps_user_data.get("full_name"),
            disabled=False,
            is_admin=vps_user_data.get("is_admin", False)
        )
        db.add(local_user)
        db.commit()
        db.refresh(local_user)
    else:
        # Update if changed
        updated = False
        if local_user.full_name != vps_user_data.get("full_name"):
            local_user.full_name = vps_user_data.get("full_name")
            updated = True
        if local_user.is_admin != vps_user_data.get("is_admin", False):
            local_user.is_admin = vps_user_data.get("is_admin", False)
            updated = True
        if updated:
            db.commit()
            db.refresh(local_user)
    return local_user

@router.post("/register", response_model=User)
async def register_user(user_data: UserCreate, db: Session = Depends(get_db)):
    """Proxy registration to VPS."""
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{AUTH_SERVICE_URL}/register",
                json=user_data.dict(),
                timeout=10.0
            )
            if resp.status_code != 200:
                logger.warning(f"VPS registration failed: {resp.text}")
                raise HTTPException(status_code=resp.status_code, detail=resp.json().get("detail", "Registration failed"))
            
            vps_user = resp.json()
            local_sync = sync_local_user(db, vps_user)
            
            return User(
                email=local_sync.email,
                id=local_sync.id,
                full_name=local_sync.full_name,
                disabled=local_sync.disabled,
                is_admin=local_sync.is_admin
            )
    except httpx.RequestError:
        raise HTTPException(status_code=503, detail="Authentication service unavailable")

@router.post("/token", response_model=Token)
async def login_for_access_token(login_data: LoginRequest, db: Session = Depends(get_db)):
    """Proxy login to VPS."""
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{AUTH_SERVICE_URL}/login",
                json={"email": login_data.email, "password": login_data.password},
                timeout=10.0
            )
            
            if resp.status_code != 200:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Incorrect email or password",
                    headers={"WWW-Authenticate": "Bearer"},
                )
            
            token_data = resp.json()
            
            # Sync user profile immediately after login to ensure local record exists
            profile_resp = await client.get(
                f"{AUTH_SERVICE_URL}/me",
                headers={"Authorization": f"Bearer {token_data['access_token']}"}
            )
            if profile_resp.status_code == 200:
                sync_local_user(db, profile_resp.json())
                
            return Token(**token_data)
    except httpx.RequestError:
        raise HTTPException(status_code=503, detail="Authentication service unavailable")

@router.get("/users/me", response_model=User)
async def read_users_me(current_user: User = Depends(get_current_active_user)):
    return current_user

@router.get("/subscription")
async def get_user_subscription(request: Request, current_user: User = Depends(get_current_active_user)):
    """Proxy subscription info from VPS profile."""
    try:
        auth_header = request.headers.get("Authorization")
        if not auth_header:
            raise HTTPException(status_code=401, detail="Missing auth header")

        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{AUTH_SERVICE_URL}/subscription",
                headers={"Authorization": auth_header},
                timeout=10.0
            )
            if resp.status_code == 200:
                return resp.json()
    except Exception as e:
        logger.error(f"Error fetching subscription: {e}")
        
    return {"plan_id": "free", "display_name": "Free Plan", "active": True}

@router.get("/subscription/types")
async def get_subscription_types():
    """Fallback subscription types for the UI."""
    return {
        "plans": [
            {
                "plan_id": "free",
                "display_name": "Free",
                "paypal_link": ""
            },
            {
                "plan_id": "pro",
                "display_name": "Pro",
                "paypal_link": "https://www.paypal.com/ncp/payment/7HSHNZT23E6P6"
            }
        ]
    }
