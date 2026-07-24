from pydantic import BaseModel, ConfigDict


class ItemCreate(BaseModel):
    name: str


class ItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
