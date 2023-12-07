import json
from typing import Any, Dict, List

from django.conf import settings
from django.db import models
from fractal_database.models import (
    Database,
    ReplicatedModelRepresentation,
    ReplicationTarget,
)
from fractal_database.replication.tasks import replicate_fixture


class MatrixReplicationTarget(ReplicationTarget):
    event_type = "f.database.event"

    access_token = models.CharField(max_length=255, default=None)
    homeserver = models.CharField(max_length=255, default=None)

    async def push_replication_log(self, fixture: List[Dict[str, Any]]) -> None:
        """
        Pushes a replication log to the Matrix homeserver. Uses taskiq
        to "kick" a replication task that all devices in the object's
        configured room will load.
        """
        # we have to serialize the fixture to json because Matrix has a non-standard
        # JSON encoding that doesn't allow floats
        replication_event = json.dumps(fixture)
        print(f"Pushing fixture(s): {replication_event}")
        representation = await ReplicatedModelRepresentation.objects.aget(
            object_id=self.database_id  # type: ignore
        )
        room_id = representation.metadata["room_id"]

        await replicate_fixture.kicker().with_labels(room_id=room_id).kiq(replication_event)

    async def replicate(self) -> None:
        transaction_logs_querysets = await self.get_repl_logs_by_txn()

        # collect all of the payloads from the replciation logs into a single array
        for queryset in transaction_logs_querysets:
            fixture = []
            async for log in queryset:
                async for repr_log in log.repr_logs.filter(deleted=False).order_by(
                    "date_created"
                ):
                    await repr_log.apply(self)
                fixture.append(log.payload[0])

            await self.push_replication_log(fixture)

            # bulk update all of the logs in the queryset to deleted
            await queryset.aupdate(deleted=True)
