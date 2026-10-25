import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request, status
from sqlalchemy.exc import IntegrityError

from app.config import PROCESSING_DELAY_SECONDS, SWEEP_INTERVAL_SECONDS
from app.db import SessionLocal, init_db
from app.models import Transaction, utcnow
from app.processor import TransactionProcessor
from app.schemas import TransactionOut, TransactionWebhook, format_utc

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

processor = TransactionProcessor(SessionLocal, PROCESSING_DELAY_SECONDS, SWEEP_INTERVAL_SECONDS)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    # The first sweep runs right away, which resumes anything left
    # unfinished by a previous run.
    processor.start()
    yield
    await processor.stop()


app = FastAPI(title="Transaction Webhook Service", lifespan=lifespan)


@app.middleware("http")
async def add_response_time(request: Request, call_next):
    # Time spent inside the service, without network latency to the client.
    started = time.perf_counter()
    response = await call_next(request)
    elapsed_ms = (time.perf_counter() - started) * 1000
    response.headers["X-Response-Time-Ms"] = f"{elapsed_ms:.1f}"
    return response


@app.get("/")
async def health():
    return {"status": "HEALTHY", "current_time": format_utc(utcnow())}


@app.post("/v1/webhooks/transactions", status_code=status.HTTP_202_ACCEPTED)
async def receive_transaction(payload: TransactionWebhook):
    async with SessionLocal() as session:
        session.add(Transaction(**payload.model_dump()))
        try:
            await session.commit()
        except IntegrityError:
            # Same transaction_id seen before. Processors retry webhooks,
            # so this is expected and we just acknowledge it again.
            return {"message": "already received", "transaction_id": payload.transaction_id}

    processor.submit(payload.transaction_id)
    return {"message": "accepted", "transaction_id": payload.transaction_id}


@app.get("/v1/transactions/{transaction_id}", response_model=list[TransactionOut])
async def get_transaction(transaction_id: str):
    async with SessionLocal() as session:
        txn = await session.get(Transaction, transaction_id)

    if txn is None:
        raise HTTPException(status_code=404, detail="transaction not found")
    return [txn]
