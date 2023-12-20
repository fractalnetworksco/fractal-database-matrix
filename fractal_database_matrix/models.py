import json
import logging
import os
from typing import Any, Dict, List

from django.db import models
from fractal_database.models import BaseModel, ReplicationTarget
from fractal_database.replication.tasks import replicate_fixture
from taskiq import SendTaskError
from taskiq_matrix.matrix_broker import MatrixBroker

logger = logging.getLogger(__name__)


class MatrixReplicationTarget(ReplicationTarget):
    registration_token = models.CharField(max_length=255, blank=True, null=True)

    async def push_replication_log(
        self: ReplicationTarget, fixture: List[Dict[str, Any]]
    ) -> None:
        """
        Pushes a replication log to the replication self as a replicate. Uses taskiq
        to "kick" a replication task that all devices in the object's
        configured room will load.
        """
        # we have to serialize the fixture to json because Matrix has a non-standard
        # JSON encoding that doesn't allow floats
        replication_event = json.dumps(fixture)

        if not self.metadata.get("room_id"):
            logger.warning(f"Unable to replicate, no room_id found for {self.name}")
            return

        room_id = self.metadata["room_id"]
        print(f"Pushing fixture(s): {replication_event} to {room_id}")
        broker = MatrixBroker().with_matrix_config(
            room_id=room_id,
            homeserver_url=self.homeserver,
            access_token=self.access_token,
        )
        try:
            await replicate_fixture.kicker().with_broker(broker).with_labels(room_id=room_id).kiq(
                replication_event
            )
        except SendTaskError as e:
            raise Exception(e.__cause__)

    @classmethod
    @property
    def representation_module(cls) -> str:
        if os.environ.get("MATRIX_REPRESENTATION_MODULE"):
            return os.environ["MATRIX_REPRESENTATION_MODULE"]
        elif os.environ.get("MATRIX_PARENT_SPACE_ID"):
            return "fractal_database_matrix.representations.MatrixSubSpace"
        return "fractal_database_matrix.representations.MatrixSpace"


class MatrixCredentials(BaseModel):
    matrix_id = models.CharField(max_length=255)
    password = models.CharField(max_length=255, blank=True, null=True)
    access_token = models.CharField(max_length=255)
    target = models.ForeignKey(MatrixReplicationTarget, on_delete=models.CASCADE)
