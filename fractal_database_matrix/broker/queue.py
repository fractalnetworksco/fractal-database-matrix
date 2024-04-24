import json
from typing import List, Tuple

from taskiq_matrix.matrix_queue import BroadcastQueue, Task


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
