import json
import logging
import os
from typing import Any, Dict, List, Union

from asgiref.sync import sync_to_async
from django.db import models
from django.db.models.manager import BaseManager
from fractal_database.models import BaseModel, Database, Device, ReplicationTarget
from fractal_database.replication.tasks import replicate_fixture
from taskiq import SendTaskError
from taskiq_matrix.matrix_broker import MatrixBroker

logger = logging.getLogger(__name__)


class MatrixCredentials(BaseModel):
    matrix_id = models.CharField(max_length=255)
    password = models.CharField(max_length=255, blank=True, null=True)
    access_token = models.CharField(max_length=255)
    target = models.ForeignKey(
        "fractal_database_matrix.MatrixReplicationTarget", on_delete=models.CASCADE
    )
    device = models.ForeignKey(Device, on_delete=models.CASCADE)

    async def accept_invite(self, room_id: str):
        from fractal_database.signals import _accept_invite

        await _accept_invite(self, room_id, self.target.homeserver)


class InMemoryMatrixCredentials(MatrixCredentials):
    homeserver = os.environ["MATRIX_HOMESERVER_URL"]

    class Meta:
        proxy = True

    def save(self, *args, **kwargs):
        # we don't want to save the in-memory credentials
        raise Exception("Cannot save in-memory credentials")


class MatrixReplicationTarget(ReplicationTarget):
    # type hint for the credentials foreign key relationship
    matrixcredentials_set: BaseManager[MatrixCredentials]

    registration_token = models.CharField(max_length=255, blank=True, null=True)
    homeserver = models.CharField(max_length=255)

    def get_creds(self) -> Union[MatrixCredentials, InMemoryMatrixCredentials]:
        current_db = Database.current_db()
        current_device = Device.current_device()
        if current_db.is_root:
            return self.matrixcredentials_set.get(target=self, device=current_device)
        else:
            try:
                return InMemoryMatrixCredentials(
                    matrix_id=os.environ["MATRIX_USER_ID"],
                    access_token=os.environ["MATRIX_ACCESS_TOKEN"],
                )
            except KeyError as e:
                raise Exception(f"Required environment variable not set: {e}")

    async def aget_creds(self):
        return await sync_to_async(self.get_creds)()

    async def push_replication_log(self, fixture: List[Dict[str, Any]]) -> None:
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
        creds = await self.aget_creds()
        broker = MatrixBroker().with_matrix_config(
            room_id=room_id,
            homeserver_url=self.homeserver,
            access_token=creds.access_token,
        )
        try:
            await replicate_fixture.kicker().with_broker(broker).with_labels(room_id=room_id).kiq(
                replication_event
            )
        except SendTaskError as e:
            raise Exception(e.__cause__)

    def get_representation_module(self) -> str:
        # if creating a representation for a target that is not the primary target of the current_db
        # we need to use the sub-space representation
        from fractal_database.models import Database

        if Database.current_db().primary_target() != self:
            return "fractal_database_matrix.representations.MatrixSubSpace"
        return "fractal_database_matrix.representations.MatrixSpace"
