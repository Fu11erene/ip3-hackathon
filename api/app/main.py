from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import Base, engine, get_db
from app.models import Item
from app.schemas import ItemCreate, ItemOut


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
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
