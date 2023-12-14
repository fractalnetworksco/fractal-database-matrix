import json
import logging
from typing import TYPE_CHECKING, Any, Dict, List

from asgiref.sync import sync_to_async
from django.db import models
from fractal_database.models import ReplicationTarget, RepresentationLog, RootDatabase
from fractal_database.replication.tasks import replicate_fixture
from fractal_database_matrix.representations import MatrixReplicationTargetSpace

if TYPE_CHECKING:
    from fractal_database.models import Database

logger = logging.getLogger(__name__)


class MatrixReplicationTarget(ReplicationTarget, MatrixReplicationTargetSpace):
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

        redo = False
        if not self.metadata.get("room_id"):
            logger.warning(f"Unable to replicate, no room_id found for {self.name}")
            return

        room_id = self.metadata["room_id"]
        print(f"Pushing fixture(s): {replication_event} to {room_id}")
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
            logger.debug("Querying for representation logs...")
            async for log in queryset:
                async for repr_log in log.repr_logs.select_related(
                    "content_type", "target_type"
                ).filter(deleted=False).order_by("date_created"):
                    try:
                        await repr_log.apply()
                        # after applying a representation for this target,
                        # we need to refresh ourself to get any latest metadata
                        if repr_log.content_type.model_class() == self.__class__:
                            logger.info("Refreshing {self} after applying representation")
                            await self.arefresh_from_db()
                        # call replicate again since apply will create new
                        # replication logs
                        return await self.replicate()
                    except Exception as e:
                        logger.error(f"Error applying representation log: {e}")
                        continue
                fixture.append(log.payload[0])

            try:
                await self.push_replication_log(fixture)
                # bulk update all of the logs in the queryset to deleted
                await queryset.aupdate(deleted=True)
            except Exception as e:
                logger.error(f"Error pushing replication log: {e}")
