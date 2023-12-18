import json
import logging
import os
from typing import Any, Coroutine, Dict, List

from fractal_database.models import ReplicationTarget
from fractal_database.replication.tasks import replicate_fixture
from fractal_database_matrix.representations import (
    AppSpace,
    MatrixSpace,
    MatrixSubSpace,
)
from taskiq import SendTaskError

logger = logging.getLogger(__name__)

# we wanted to have another abstract model that subclasses ReplicationTarget
# but we ran into a problem with the Django not allowing us to set set Meta.abstract = True
# on the subclass which made it difficult to identify concrete ReplicationTarget classes
# from Database.get_all_replication_targets()


async def push_replication_log(target, fixture: List[Dict[str, Any]]) -> None:
    """
    Pushes a replication log to the replication target as a replicate. Uses taskiq
    to "kick" a replication task that all devices in the object's
    configured room will load.
    """
    # we have to serialize the fixture to json because Matrix has a non-standard
    # JSON encoding that doesn't allow floats
    replication_event = json.dumps(fixture)

    if not target.metadata.get("room_id"):
        logger.warning(f"Unable to replicate, no room_id found for {target.name}")
        return

    room_id = target.metadata["room_id"]
    print(f"Pushing fixture(s): {replication_event} to {room_id}")
    try:
        os.environ["MATRIX_ACCESS_TOKEN"] = target.access_token
        os.environ["MATRIX_HOMESERVER_URL"] = target.homeserver
        await replicate_fixture.kicker().with_labels(room_id=room_id).kiq(replication_event)
    except SendTaskError as e:
        raise Exception(e.__cause__)


class MatrixRootReplicationTarget(ReplicationTarget, MatrixSpace):
    async def push_replication_log(self, fixture: List[Dict[str, Any]]) -> None:
        return await push_replication_log(self, fixture)


class MatrixNestedReplicationTarget(ReplicationTarget, MatrixSubSpace):
    async def push_replication_log(self, fixture: List[Dict[str, Any]]) -> None:
        return await push_replication_log(self, fixture)
