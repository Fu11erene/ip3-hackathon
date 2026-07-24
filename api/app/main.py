from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import create_access_token, get_current_user, hash_password, verify_password
from app.db import Base, engine, get_db, async_session
from app.models import Item, User
from app.schemas import (
    ItemCreate,
    ItemOut,
    LoginRequest,
    TokenResponse,
    UserOut,
    UserRegister,
)

DEMO_USERNAME = "demo"
DEMO_PASSWORD = "password"


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with async_session() as session:
        result = await session.execute(select(User).where(User.username == DEMO_USERNAME))
        if result.scalar_one_or_none() is None:
            session.add(User(username=DEMO_USERNAME, password_hash=hash_password(DEMO_PASSWORD)))
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

    user = User(username=body.username, password_hash=hash_password(body.password))
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


@app.post("/auth/login", response_model=TokenResponse)
async def login(body: LoginRequest, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).where(User.username == body.username))
    user = result.scalar_one_or_none()
    if user is None or not verify_password(body.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="ユーザー名またはパスワードが正しくありません",
        )
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
