import json
import logging
from typing import Any, Dict, List

from asgiref.sync import sync_to_async
from django.db import models
from fractal_database.models import ReplicationTarget, RepresentationLog, RootDatabase
from fractal_database.replication.tasks import replicate_fixture

logger = logging.getLogger(__name__)


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

        redo = False
        if not self.metadata.get("room_id"):
            database = await self.content_type.model_class().objects.aget(uuid=self.object_id)
            print(f"Unable to replicate, no representation found for Database {database.name}")
            # trigger a replication cycle since we dont have a representation yet
            # this will create the representation logs we need to enable replication to our first target
            try:
                database = await RootDatabase.objects.aget(uuid=database.uuid)
            except RootDatabase.DoesNotExist:
                pass
                # database = self.database

            await sync_to_async(database.schedule_replication)(created=True)
            repr_log = await RepresentationLog.objects.aget(target_id=self.uuid)
            await repr_log.apply()
            await self.arefresh_from_db()
            # await sync_to_async(self.schedule_replication)(created=True)
            redo = True

        room_id = self.metadata["room_id"]
        await replicate_fixture.kicker().with_labels(room_id=room_id).kiq(replication_event)
        if redo:
            await self.replicate()

    async def replicate(self) -> None:
        """
        Get the pending replication logs and their associated representation logs.

        Apply the representation logs then push the replication logs.
        """
        transaction_logs_querysets = await self.get_repl_logs_by_txn()

        # collect all of the payloads from the replication logs into a single array
        for queryset in transaction_logs_querysets:
            fixture = []
            async for log in queryset:
                print("Querying for representation logs...")
                async for repr_log in log.repr_logs.filter(deleted=False).order_by(
                    "date_created"
                ):
                    await repr_log.apply(self)
                fixture.append(log.payload[0])

            await self.push_replication_log(fixture)

            # bulk update all of the logs in the queryset to deleted
            await queryset.aupdate(deleted=True)
