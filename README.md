# Transaction Webhook Service

Receives transaction webhooks from a payment processor, acknowledges them right away, and processes them in the background (with a simulated 30s external call).

**Live URL:** `<add after deploying>`

## Endpoints

| Method | Path | What it does |
|---|---|---|
| `GET` | `/` | Health check |
| `POST` | `/v1/webhooks/transactions` | Accepts a webhook, returns `202` |
| `GET` | `/v1/transactions/{transaction_id}` | Returns the stored transaction and its status |

## Running locally

Needs Python 3.10+.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
uvicorn app.main:app --reload
```

With no `DATABASE_URL` set it uses a local SQLite file, so it runs without any setup. To use Postgres, copy `.env.example` to `.env`, fill it in and export it.

| Variable | Default | |
|---|---|---|
| `DATABASE_URL` | `sqlite+aiosqlite:///./transactions.db` | Any `postgres://` URL works too |
| `PROCESSING_DELAY_SECONDS` | `30` | The simulated external call |
| `SWEEP_INTERVAL_SECONDS` | `60` | How often unfinished transactions are retried |

## Testing

```bash
pytest
```

The tests use a 1 second delay so they finish quickly. To try it by hand:

```bash
curl -X POST localhost:8000/v1/webhooks/transactions \
  -H 'content-type: application/json' \
  -d '{"transaction_id":"txn_abc123def456","source_account":"acc_user_789","destination_account":"acc_merchant_456","amount":1500,"currency":"INR"}'

# right away -> PROCESSING, after ~30s -> PROCESSED
curl localhost:8000/v1/transactions/txn_abc123def456
```

Send the same POST a few times and you will still have only one transaction, processed once.

## How it works

1. The webhook handler validates the body and inserts a row with status `PROCESSING`. That is the only work done in the request, so the response comes back in a few milliseconds.
2. After the insert commits, the transaction id is handed to a background task that waits 30 seconds and then marks the row `PROCESSED` and sets `processed_at`.
3. Every 60 seconds (and once at startup), a sweeper looks for rows that are still `PROCESSING` but not being worked on, and queues them again.

### Idempotency

`transaction_id` is the primary key. A duplicate webhook fails the insert with an `IntegrityError`, and the handler still answers `202` ("already received") without queueing anything. Doing it in the database rather than with an "exists?" check first means two identical webhooks arriving at the same moment can't both get through.

The final update is `... WHERE status = 'PROCESSING'`, so even if a transaction got processed twice somehow (e.g. two instances sweeping at once) the result is the same and `processed_at` isn't overwritten.

### Not losing transactions

The row is written **before** the 202 is sent, so once the processor gets a 202 the transaction is saved. If the process crashes or is redeployed during the 30s wait, the row stays `PROCESSING` and gets picked up again on the next start. If the final DB write fails, the task logs it and the sweeper retries later.

I checked this by killing the server with `kill -9` partway through processing and restarting it. The transaction was resumed and finished.

## Technical choices

- **FastAPI**: async, so the 30s waits don't take up threads, and request validation comes for free from Pydantic.
- **PostgreSQL (Neon) in production, SQLite locally**: through async SQLAlchemy, so the same code runs on both. Postgres gives the unique constraint and durability that the idempotency and reliability guarantees rely on.
- **In-process background tasks instead of a queue (Celery/RQ/SQS)**: with the database as the source of truth, a separate broker would add another service to run without adding much for this scope. The sweeper covers what a queue's redelivery would.
- **Amounts stored as `NUMERIC(18,2)`**, not floats, so money keeps its exact value in storage.

### Trade-offs / what I'd do next

- Processing runs in the web process. At higher volume I'd move it to a separate worker reading from a proper queue (SQS or Redis streams), or have workers claim rows with `SELECT ... FOR UPDATE SKIP LOCKED`.
- If several instances are running, each one's sweeper may pick up the same pending row. The guarded update keeps the result correct, but it wastes work. Claiming rows with a lease column would fix that.
- No webhook signature check. With Razorpay I'd verify the `X-Razorpay-Signature` HMAC before accepting anything.
- Tables are created on startup. A real project would use Alembic migrations.

## Deploying

I deployed it with **Render** (Docker) and **Neon** (Postgres):

1. Create a Neon project and copy its connection string.
2. On Render, create a Web Service from this repo. `render.yaml` and the `Dockerfile` are already set up.
3. Set `DATABASE_URL` to the Neon connection string.

Note: Render's free tier sleeps when idle, so the first request after a while can be slow while it wakes up. Pending transactions are picked up when it comes back.
