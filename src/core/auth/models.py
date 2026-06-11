from pydantic import BaseModel, SecretStr


class SecretInput(BaseModel):
    name: str
    value: SecretStr
