import httpx
import logging
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)

# Configurable backend URL. Fallback to localhost:8000 for local test/dev.
AUTH_SERVICE_URL = "https://ds-auth.billsheba.com"


class AuthService:
    def __init__(self):
        self.client = httpx.Client(timeout=10.0)
        self.token: Optional[str] = None
        self.user_info: Optional[Dict[str, Any]] = None
        self.subscription_info: Optional[Dict[str, Any]] = None

    def set_token(self, token: str):
        self.token = token
        self.client.headers.update({"Authorization": f"Bearer {token}"})

    def clear_token(self):
        self.token = None
        if "Authorization" in self.client.headers:
            del self.client.headers["Authorization"]
        self.user_info = None
        self.subscription_info = None

    def register(self, email: str, password: str, full_name: str) -> Dict[str, Any]:
        """Register a new user account."""
        resp = self.client.post(
            f"{AUTH_SERVICE_URL}/register",
            json={"email": email, "password": password, "full_name": full_name},
        )
        if resp.status_code != 200:
            detail = resp.json().get("detail", "Registration failed")
            raise Exception(detail)
        return resp.json()

    def login(self, email: str, password: str) -> str:
        """Login and return access token."""
        resp = self.client.post(
            f"{AUTH_SERVICE_URL}/login", json={"email": email, "password": password}
        )
        if resp.status_code != 200:
            detail = resp.json().get(
                "detail", "Login failed. Please check your credentials."
            )
            raise Exception(detail)

        token_data = resp.json()
        token = token_data.get("access_token")
        self.set_token(token)
        return token

    def get_profile(self) -> Dict[str, Any]:
        """Fetch consolidated user profile and subscription details."""
        if not self.token:
            raise Exception("Not authenticated")
        resp = self.client.get(f"{AUTH_SERVICE_URL}/profile")
        if resp.status_code != 200:
            raise Exception("Failed to fetch profile details.")

        data = resp.json()
        self.user_info = data.get("user")
        self.subscription_info = data.get("subscription")
        return data

    def decrement_run(self) -> bool:
        """Decrement run count after executing a data flow."""
        if not self.token:
            return False
        try:
            resp = self.client.post(f"{AUTH_SERVICE_URL}/subscription/decrement")
            if resp.status_code == 200:
                # Refresh profile info to reflect updated limit
                self.get_profile()
                return True
            else:
                logger.error(f"Failed to decrement run limit: {resp.text}")
                return False
        except Exception as e:
            logger.error(f"Error calling decrement_run: {e}")
            return False


# Singleton instance for the application
auth_service = AuthService()
