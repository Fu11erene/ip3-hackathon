from pydantic import BaseModel, ConfigDict


class ItemCreate(BaseModel):
    name: str


class ItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str


class UserRegister(BaseModel):
    username: str
    password: str
    phone_number: str


class LoginRequest(BaseModel):
    username: str
    password: str


class OtpChallengeResponse(BaseModel):
    challenge_id: str
    expires_in: int


class OtpVerifyRequest(BaseModel):
    challenge_id: str
    code: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
