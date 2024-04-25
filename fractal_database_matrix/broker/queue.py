import json
import logging
from typing import List, Optional, Tuple

from taskiq_matrix.matrix_queue import BroadcastQueue, Task
from taskiq_matrix.utils import send_message

logger = logging.getLogger(__name__)


class ReplicationQueue(BroadcastQueue):
    """
    Replication queues are broadcast queues whose checkpoints are device specific.
    """

    def __init__(
        self,
        homeserver_url: str,
        access_token: str,
        *args,
        **kwargs,
    ):
        self.name = "replication"
        super().__init__(self.name, homeserver_url, access_token, *args, **kwargs)
        self.checkpoint.type = f"{self.checkpoint.type}.{self.device_name}"

    def prune_old_objects(self, fixture: List[dict]) -> List[dict]:
        # dictionary to store the latest version of each object
        latest_versions = {}

        # iterate through each item in the original data
        for item in fixture:
            model = item["model"]
            pk = item["pk"]
            version = item["fields"]["object_version"]

            # create a unique key for each object
            key = (model, pk)

            # update the dictionary only if this version is higher than what's already recorded
            if (
                key not in latest_versions
                or latest_versions[key]["fields"]["object_version"] < version
            ):
                latest_versions[key] = item

        # extract the values to get the pruned list of objects
        return list(latest_versions.values())

    async def get_unacked_tasks(
        self, timeout: int = 30000, exclude_self: bool = True
    ) -> Tuple[str, List[Task]]:
        _, unacked_tasks = await super().get_unacked_tasks(timeout, exclude_self)

        for task in unacked_tasks:
            task_name = task.data["task_name"]
            # ignore any tasks that aren't the replicate fixture task
            if task_name != "fractal_database.replication.tasks:replicate_fixture":
                continue

            fixture = json.loads(task.data["args"][0])
            task.data["args"][0] = json.dumps(self.prune_old_objects(fixture))

        return self.name, unacked_tasks

    async def ack_msg(
        self, task_id: str, room_id: str, tasks_to_ack: Optional[list[str]] = None
    ) -> None:
        """
        Acks a given task id.

        FIXME: ack list of tasks_to_ack
        """
        message = json.dumps(
            {
                "task_id": task_id,
                "task": "{}",
            }
        )
        logger.debug(
            f"Sending ack for task {task_id} to room: {room_id}\nAck type: {self.task_types.ack}.{task_id}",
        )
        await send_message(
            self.client,
            room_id,
            message=message,
            msgtype=f"{self.task_types.ack}.{task_id}",
            task_id=task_id,
            queue=self.name,
        )
