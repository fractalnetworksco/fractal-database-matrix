import asyncio
import itertools
import logging
from typing import Any, AsyncGenerator, List

from taskiq_matrix.matrix_broker import MatrixBroker
from taskiq_matrix.matrix_queue import Task

from .queue import ReplicationQueue

logger = logging.getLogger(__file__)


class FractalMatrixBroker(MatrixBroker):
    def _init_queues(self):
        super()._init_queues()

        if not hasattr(self, "replication_queue"):
            self.replication_queue = ReplicationQueue(self.homeserver_url, self.access_token)

    async def startup(self) -> None:
        await super().startup()

        # full sync is required for replication queue because it needs to
        # sync any tasks that were sent before the checkpoint was created for
        # this device
        await self.replication_queue.checkpoint.get_or_init_checkpoint(full_sync=True)

    async def shutdown(self) -> None:
        """
        Shuts down the broker.
        """
        await super().shutdown()
        await self.replication_queue.shutdown()

    async def get_tasks(self) -> AsyncGenerator[List[Task], Any]:  # pragma: no cover
        while True:
            tasks = {
                "device_queue": asyncio.create_task(
                    self.device_queue.get_unacked_tasks(), name="device_queue"
                ),
                "broadcast_queue": asyncio.create_task(
                    self.broadcast_queue.get_unacked_tasks(), name="broadcast_queue"
                ),
                "mutex_queue": asyncio.create_task(
                    self.mutex_queue.get_unacked_tasks(), name="mutex_queue"
                ),
                "replication_queue": asyncio.create_task(
                    self.replication_queue.get_unacked_tasks(), name="replication_queue"
                ),
            }
            sync_task_results: List[List[Task]] = []

            while tasks:
                done, _ = await asyncio.wait(tasks.values(), return_when=asyncio.FIRST_COMPLETED)

                for completed_task in done:
                    queue_name = completed_task.get_name()
                    try:
                        queue, pending_tasks = completed_task.result()
                        if pending_tasks:
                            sync_task_results.append(pending_tasks)
                            logger.debug(f"Got {len(pending_tasks)} tasks from {queue}")
                    except Exception as e:
                        logger.exception(f"Sync failed: {e}")

                    tasks[queue_name] = asyncio.create_task(
                        getattr(self, queue_name).get_unacked_tasks(), name=queue_name
                    )

                if sync_task_results:
                    yield list(itertools.chain.from_iterable(sync_task_results))
                    sync_task_results = []  # Reset for the next iteration

                # Optionally, add a short delay before starting the next round
                await asyncio.sleep(0)
