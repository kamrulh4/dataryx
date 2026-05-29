# jwt.py

import logging
import httpx
from fastapi import Depends, HTTPException, status, Query
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session

from core.auth.models import User
from core.database import models as db_models
from core.database.connection import get_db
from core.configs.settings import AUTH_SERVICE_URL

logger = logging.getLogger(__name__)

security = HTTPBearer()

async def get_current_user(auth: HTTPAuthorizationCredentials = Depends(security), db: Session = Depends(get_db)):
    """Verifies token by calling VPS /me endpoint, then syncs user locally."""
    token = auth.credentials
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{AUTH_SERVICE_URL}/me",
                headers={"Authorization": f"Bearer {token}"},
                timeout=10.0
            )
            
            if response.status_code != 200:
                logger.warning(f"VPS token validation failed: {response.status_code} - {response.text}")
                raise credentials_exception
            
            vps_user_data = response.json()
            email = vps_user_data.get("email")
            if not email:
                raise credentials_exception

            # Synchronize with local database
            local_user = db.query(db_models.User).filter(db_models.User.email == email).first()
            if not local_user:
                # Create local placeholder user for data referencing (flows/runs)
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
                # Update details if changed on VPS
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

            return User(
                email=local_user.email,
                id=local_user.id,
                full_name=local_user.full_name,
                disabled=local_user.disabled,
                is_admin=local_user.is_admin
            )

    except httpx.RequestError as exc:
        logger.error(f"Error connecting to VPS for token validation: {exc}")
        raise HTTPException(status_code=503, detail="Authentication service unavailable")
    except Exception as e:
        logger.error(f"Unexpected error in get_current_user: {e}", exc_info=True)
        raise credentials_exception

def get_current_active_user(current_user: User = Depends(get_current_user)):
    if current_user.disabled:
        raise HTTPException(status_code=400, detail="Inactive user")
    return current_user

async def get_current_admin_user(current_user: User = Depends(get_current_user)):
    """Dependency that requires the current user to be an admin (returned from VPS)"""
    if not current_user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin privileges required"
        )
    return current_user

async def get_current_user_from_query(
    token: str = Query(...), db: Session = Depends(get_db)
):
    credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)
    return await get_current_user(credentials, db)
