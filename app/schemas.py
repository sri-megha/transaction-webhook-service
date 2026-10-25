from datetime import datetime, timezone
from decimal import Decimal

from pydantic import BaseModel, Field, field_serializer


def format_utc(value: datetime | None) -> str | None:
    if value is None:
        return None
    # SQLite drops tzinfo on the way back out; everything we store is UTC.
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class TransactionWebhook(BaseModel):
    transaction_id: str = Field(min_length=1, max_length=64)
    source_account: str = Field(min_length=1, max_length=64)
    destination_account: str = Field(min_length=1, max_length=64)
    amount: Decimal = Field(gt=0, max_digits=18, decimal_places=2)
    currency: str = Field(pattern=r"^[A-Z]{3}$")


class TransactionOut(BaseModel):
    transaction_id: str
    source_account: str
    destination_account: str
    amount: float
    currency: str
    status: str
    created_at: datetime
    processed_at: datetime | None

    model_config = {"from_attributes": True}

    @field_serializer("created_at", "processed_at")
    def _serialize_time(self, value: datetime | None) -> str | None:
        return format_utc(value)
