import asyncio
import logging

from sqlalchemy import select, update

from app.models import PROCESSED, PROCESSING, Transaction, utcnow

log = logging.getLogger(__name__)


class TransactionProcessor:
    """Runs transaction processing in the background of the web process.

    The webhook handler saves the row first and only then hands the id over,
    so the database is the source of truth. If a task crashes or the process
    restarts, the row is still PROCESSING and the sweeper picks it up again.
    """

    def __init__(self, session_factory, delay_seconds: float, sweep_interval: float):
        self._session_factory = session_factory
        self._delay = delay_seconds
        self._sweep_interval = sweep_interval
        self._in_flight: dict[str, asyncio.Task] = {}
        self._sweeper: asyncio.Task | None = None

    def start(self) -> None:
        self._sweeper = asyncio.create_task(self._sweep_forever())

    async def stop(self) -> None:
        tasks = list(self._in_flight.values())
        if self._sweeper:
            tasks.append(self._sweeper)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    def submit(self, transaction_id: str) -> None:
        if transaction_id in self._in_flight:
            return
        task = asyncio.create_task(self._process(transaction_id))
        self._in_flight[transaction_id] = task
        task.add_done_callback(lambda _: self._in_flight.pop(transaction_id, None))

    async def _process(self, transaction_id: str) -> None:
        try:
            # Stand-in for the slow external call (fraud check, ledger, etc).
            await asyncio.sleep(self._delay)

            async with self._session_factory() as session:
                # The status guard keeps this safe to run more than once.
                await session.execute(
                    update(Transaction)
                    .where(
                        Transaction.transaction_id == transaction_id,
                        Transaction.status == PROCESSING,
                    )
                    .values(status=PROCESSED, processed_at=utcnow())
                )
                await session.commit()
            log.info("processed %s", transaction_id)
        except asyncio.CancelledError:
            raise
        except Exception:
            # Leave it as PROCESSING, the next sweep will retry it.
            log.exception("failed to process %s, will retry", transaction_id)

    async def resume_pending(self) -> int:
        async with self._session_factory() as session:
            result = await session.execute(
                select(Transaction.transaction_id).where(Transaction.status == PROCESSING)
            )
            pending = [txn_id for txn_id in result.scalars() if txn_id not in self._in_flight]

        for txn_id in pending:
            self.submit(txn_id)
        return len(pending)

    async def _sweep_forever(self) -> None:
        while True:
            try:
                resumed = await self.resume_pending()
                if resumed:
                    log.info("picked up %d pending transaction(s)", resumed)
            except Exception:
                log.exception("sweep failed")
            await asyncio.sleep(self._sweep_interval)
