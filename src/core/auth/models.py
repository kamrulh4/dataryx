from pydantic import BaseModel, SecretStr


class Token(BaseModel):
    access_token: str
    token_type: str


class TokenData(BaseModel):
    email: str | None = None


class User(BaseModel):
    email: str
    id: int | None = None
    full_name: str | None = None
    disabled: bool | None = False
    is_admin: bool | None = False


class UserCreate(BaseModel):
    email: str
    password: str
    full_name: str | None = None


class LoginRequest(BaseModel):
    email: str
    password: str


class SecretInput(BaseModel):
    name: str
    value: SecretStr


class Secret(SecretInput):
    user_id: str


class SecretInDB(BaseModel):
    id: str
    name: str
    encrypted_value: str
    user_id: str
