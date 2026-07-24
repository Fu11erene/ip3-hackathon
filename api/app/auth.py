import hashlib
import hmac
import re
import secrets
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import get_db
from app.models import User

bearer_scheme = HTTPBearer(auto_error=False)

COMMON_PASSWORDS = {
    "123456789012",
    "password",
    "password123",
    "qwerty123456",
    "letmein123456",
}


class PasswordPolicyError(ValueError):
    pass


def validate_password_policy(password: str, username: str) -> None:
    violations = []

    if len(password) < 12:
        violations.append("12文字以上")
    if len(password) > 64:
        violations.append("64文字以下")
    if len(password.encode("utf-8")) > 72:
        violations.append("UTF-8で72バイト以下")
    if not re.search(r"[A-Z]", password):
        violations.append("英大文字を1文字以上")
    if not re.search(r"[a-z]", password):
        violations.append("英小文字を1文字以上")
    if not re.search(r"[0-9]", password):
        violations.append("数字を1文字以上")
    if not re.search(r"[^A-Za-z0-9\s]", password):
        violations.append("記号を1文字以上")
    if re.search(r"\s", password):
        violations.append("空白を含めない")

    normalized_password = re.sub(r"[^a-z0-9]", "", password.lower())
    normalized_username = re.sub(r"[^a-z0-9]", "", username.lower())
    if len(normalized_username) >= 3 and normalized_username in normalized_password:
        violations.append("ユーザー名を含めない")
    if password.lower() in COMMON_PASSWORDS:
        violations.append("一般的な弱いパスワードを使用しない")

    if violations:
        raise PasswordPolicyError("パスワードは次の条件を満たしてください: " + "、".join(violations))


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, password_hash: str) -> bool:
    return bcrypt.checkpw(password.encode(), password_hash.encode())


def generate_otp_code() -> str:
    return "".join(str(secrets.randbelow(10)) for _ in range(settings.otp_code_length))


def hash_otp_code(code: str) -> str:
    return hmac.new(settings.secret_key.encode(), code.encode(), hashlib.sha256).hexdigest()


def verify_otp_code(code: str, code_hash: str) -> bool:
    return hmac.compare_digest(hash_otp_code(code), code_hash)


def create_access_token(username: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.access_token_expire_minutes)
    payload = {"sub": username, "exp": expire}
    return jwt.encode(payload, settings.secret_key, algorithm="HS256")


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    unauthorized = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="認証情報が無効です",
        headers={"WWW-Authenticate": "Bearer"},
    )
    if credentials is None:
        raise unauthorized

    try:
        payload = jwt.decode(credentials.credentials, settings.secret_key, algorithms=["HS256"])
        username = payload.get("sub")
        if username is None:
            raise unauthorized
    except jwt.PyJWTError:
        raise unauthorized

    result = await db.execute(select(User).where(User.username == username))
    user = result.scalar_one_or_none()
    if user is None:
        raise unauthorized
    return user
