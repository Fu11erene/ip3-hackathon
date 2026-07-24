import logging
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import (
    create_access_token,
    generate_otp_code,
    get_current_user,
    hash_otp_code,
    hash_password,
    verify_otp_code,
    verify_password,
)
from app.config import settings
from app.db import Base, engine, get_db, async_session
from app.models import Item, OtpChallenge, User
from app.nexway import NexwaySmsError, send_sms
from app.schemas import (
    ItemCreate,
    ItemOut,
    LoginRequest,
    OtpChallengeResponse,
    OtpVerifyRequest,
    TokenResponse,
    UserOut,
    UserRegister,
)

DEMO_USERNAME = "demo"
DEMO_PASSWORD = "password"
DEMO_PHONE_NUMBER = "09001111101"  # CPaaS NOW 開発環境のテスト用宛先(status: delivered)
logger = logging.getLogger("uvicorn.error")


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with async_session() as session:
        result = await session.execute(select(User).where(User.username == DEMO_USERNAME))
        if result.scalar_one_or_none() is None:
            session.add(
                User(
                    username=DEMO_USERNAME,
                    password_hash=hash_password(DEMO_PASSWORD),
                    phone_number=DEMO_PHONE_NUMBER,
                )
            )
            await session.commit()

    yield


app = FastAPI(title="API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/auth/register", response_model=UserOut, status_code=status.HTTP_201_CREATED)
async def register(body: UserRegister, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).where(User.username == body.username))
    if result.scalar_one_or_none() is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="そのユーザー名は既に使用されています")

    user = User(
        username=body.username,
        password_hash=hash_password(body.password),
        phone_number=body.phone_number,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


@app.post("/auth/login", response_model=OtpChallengeResponse)
async def login(body: LoginRequest, db: AsyncSession = Depends(get_db)):
    """ID/PWを検証し、OTPをSMS送信してチャレンジIDを発行する(フロー②)。"""
    result = await db.execute(select(User).where(User.username == body.username))
    user = result.scalar_one_or_none()
    if user is None or not verify_password(body.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="ユーザー名またはパスワードが正しくありません",
        )

    code = generate_otp_code()
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=settings.otp_expire_minutes)
    challenge = OtpChallenge(
        user_id=user.id,
        code_hash=hash_otp_code(code),
        expires_at=expires_at,
        attempts_remaining=settings.otp_max_attempts,
    )
    db.add(challenge)
    await db.commit()
    await db.refresh(challenge)

    try:
        await send_sms(
            to=user.phone_number,
            text=f"ワンタイムパスワードは {code} です。",
            user_reference=f"ip1-{user.username}"[:40],
        )
    except NexwaySmsError as exc:
        await db.delete(challenge)
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"SMS送信に失敗しました: {exc.message}",
        )

    if settings.log_otp_code:
        logger.warning(
            "開発用OTP: username=%s challenge_id=%s code=%s",
            user.username,
            challenge.id,
            code,
        )

    return OtpChallengeResponse(
        challenge_id=challenge.id,
        expires_in=settings.otp_expire_minutes * 60,
    )


@app.post("/auth/otp/verify", response_model=TokenResponse)
async def verify_otp(body: OtpVerifyRequest, db: AsyncSession = Depends(get_db)):
    """OTPを検証し、成功すればアクセストークンを発行する(フロー④)。"""
    result = await db.execute(select(OtpChallenge).where(OtpChallenge.id == body.challenge_id))
    challenge = result.scalar_one_or_none()

    invalid = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="ワンタイムパスワードが正しくないか、有効期限が切れています",
    )

    if challenge is None:
        raise invalid

    if datetime.now(timezone.utc) > challenge.expires_at.replace(tzinfo=timezone.utc):
        raise invalid

    if challenge.attempts_remaining <= 0:
        raise invalid

    if not verify_otp_code(body.code, challenge.code_hash):
        challenge.attempts_remaining -= 1
        await db.commit()
        raise invalid

    user_result = await db.execute(select(User).where(User.id == challenge.user_id))
    user = user_result.scalar_one()

    await db.delete(challenge)
    await db.commit()

    return TokenResponse(access_token=create_access_token(user.username))


@app.get("/auth/me", response_model=UserOut)
async def me(current_user: User = Depends(get_current_user)):
    return current_user


@app.get("/items", response_model=list[ItemOut])
async def list_items(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Item))
    return result.scalars().all()


@app.post("/items", response_model=ItemOut)
async def create_item(item: ItemCreate, db: AsyncSession = Depends(get_db)):
    obj = Item(name=item.name)
    db.add(obj)
    await db.commit()
    await db.refresh(obj)
    return obj
