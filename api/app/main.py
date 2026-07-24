import logging
import math
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import (
    PasswordPolicyError,
    create_access_token,
    generate_otp_code,
    get_current_user,
    hash_otp_code,
    hash_password,
    validate_password_policy,
    verify_otp_code,
    verify_password,
)
from app.config import settings
from app.db import Base, engine, get_db, async_session
from app.models import Item, OtpChallenge, User
from app.nexway import NexwaySmsError, get_sms_delivery_status, send_sms
from app.schemas import (
    ItemCreate,
    ItemOut,
    LoginRequest,
    OtpChallengeResponse,
    OtpDeliveryStatusResponse,
    OtpVerifyRequest,
    TokenResponse,
    UserOut,
    UserRegister,
)

DEMO_USERNAME = "demo"
DEMO_PASSWORD = "Hackathon1!Safe"
DEMO_PHONE_NUMBER = "07020213632"
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
    try:
        validate_password_policy(body.password, body.username)
    except PasswordPolicyError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc))

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
    now = datetime.now(timezone.utc)
    result = await db.execute(select(User).where(User.username == body.username).with_for_update())
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="ユーザー名またはパスワードが正しくありません",
        )

    if user.login_locked_until is not None:
        locked_until = user.login_locked_until
        if locked_until.tzinfo is None:
            locked_until = locked_until.replace(tzinfo=timezone.utc)
        if now < locked_until:
            retry_after = max(1, math.ceil((locked_until - now).total_seconds()))
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="ログイン試行回数が上限に達しました。しばらくしてから再試行してください",
                headers={"Retry-After": str(retry_after)},
            )
        user.failed_login_attempts = 0
        user.login_locked_until = None

    if not verify_password(body.password, user.password_hash):
        user.failed_login_attempts += 1
        if user.failed_login_attempts >= settings.login_max_attempts:
            user.login_locked_until = now + timedelta(minutes=settings.login_lock_minutes)
            await db.commit()
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="ログイン試行回数が上限に達しました。しばらくしてから再試行してください",
                headers={"Retry-After": str(settings.login_lock_minutes * 60)},
            )
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="ユーザー名またはパスワードが正しくありません",
        )

    user.failed_login_attempts = 0
    user.login_locked_until = None

    previous_otp_sent_at = user.last_otp_sent_at
    if previous_otp_sent_at is not None:
        if previous_otp_sent_at.tzinfo is None:
            previous_otp_sent_at = previous_otp_sent_at.replace(tzinfo=timezone.utc)
        retry_after = math.ceil(
            settings.otp_resend_interval_seconds - (now - previous_otp_sent_at).total_seconds()
        )
        if retry_after > 0:
            await db.commit()
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="SMSは連続して送信できません。しばらくしてから再試行してください",
                headers={"Retry-After": str(retry_after)},
            )

    code = generate_otp_code()
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=settings.otp_expire_minutes)
    challenge = OtpChallenge(
        user_id=user.id,
        code_hash=hash_otp_code(code),
        expires_at=expires_at,
        attempts_remaining=settings.otp_max_attempts,
    )
    user.last_otp_sent_at = now
    db.add(challenge)
    await db.commit()
    await db.refresh(challenge)

    try:
        sms_result = await send_sms(
            to=user.phone_number,
            text=f"ワンタイムパスワードは {code} です。",
            user_reference=f"ip1-{user.username}"[:40],
        )
    except NexwaySmsError as exc:
        user.last_otp_sent_at = previous_otp_sent_at
        await db.delete(challenge)
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"SMS送信に失敗しました: {exc.message}",
        )

    challenge.delivery_order_id = sms_result["delivery_order_id"]
    challenge.delivery_status = "pending"
    await db.commit()

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
        delivery_status=challenge.delivery_status,
    )


@app.get("/auth/otp/{challenge_id}/status", response_model=OtpDeliveryStatusResponse)
async def otp_delivery_status(challenge_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(OtpChallenge).where(OtpChallenge.id == challenge_id))
    challenge = result.scalar_one_or_none()
    if challenge is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="OTPチャレンジが見つかりません")

    if datetime.now(timezone.utc) > challenge.expires_at.replace(tzinfo=timezone.utc):
        return OtpDeliveryStatusResponse(
            delivery_status="failed",
            message="ワンタイムパスワードの有効期限が切れました",
        )

    if challenge.delivery_status == "pending" and challenge.delivery_order_id is not None:
        try:
            delivery_status, delivery_error = await get_sms_delivery_status(challenge.delivery_order_id)
        except NexwaySmsError as exc:
            logger.warning(
                "SMS配信結果の取得に失敗: challenge_id=%s error=%s",
                challenge.id,
                exc.message,
            )
        else:
            challenge.delivery_status = delivery_status
            challenge.delivery_error = delivery_error
            await db.commit()

    messages = {
        "pending": "SMSの配信結果を確認しています",
        "delivered": "SMSを配信しました。届いたコードを入力してください",
        "failed": challenge.delivery_error or "SMSを配信できませんでした",
    }
    return OtpDeliveryStatusResponse(
        delivery_status=challenge.delivery_status,
        message=messages[challenge.delivery_status],
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
