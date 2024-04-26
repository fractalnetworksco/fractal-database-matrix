import json
import logging
import os
from typing import Any, Dict, List, Union

from asgiref.sync import sync_to_async
from django.db import models
from django.db.models.manager import BaseManager
from fractal_database.models import (
    BaseModel,
    Database,
    Device,
    ReplicatedModel,
    ReplicationTarget,
    RepresentationLog,
)
from fractal_database_matrix.broker.broker import FractalMatrixBroker
from taskiq import SendTaskError

logger = logging.getLogger(__name__)


class MatrixCredentials(BaseModel):
    matrix_id = models.CharField(max_length=255)
    password = models.CharField(max_length=255, blank=True, null=True)
    access_token = models.CharField(max_length=255)
    targets = models.ManyToManyField("fractal_database_matrix.MatrixReplicationTarget")
    device = models.ForeignKey(Device, on_delete=models.CASCADE)

    async def accept_invite(self, room_id: str, target: "MatrixReplicationTarget"):
        from fractal_database.signals import _accept_invite

        await _accept_invite(self, room_id, target.homeserver)


class InMemoryMatrixCredentials(MatrixCredentials):
    homeserver: str = ""

    class Meta:
        proxy = True

    def save(self, *args, **kwargs):
        # we don't want to save the in-memory credentials
        raise Exception("Cannot save in-memory credentials")


class MatrixReplicationTarget(ReplicationTarget):
    # type hint for the credentials foreign key relationship
    matrixcredentials_set: BaseManager[MatrixCredentials]

    registration_token = models.CharField(max_length=255, blank=True, null=True)
    homeserver = models.CharField(max_length=255, null=False)

    def get_creds(self) -> Union[MatrixCredentials, InMemoryMatrixCredentials]:
        current_db = Database.current_db()
        current_device = Device.current_device()
        if current_db.is_root:
            try:
                return self.matrixcredentials_set.get(device=current_device)
            except MatrixCredentials.DoesNotExist:
                raise Exception(
                    f"Target {self} does not have Matrix Credentials for current device {current_device}. Add credentials then call schedule_replication() on {self}"
                )

        else:
            try:
                return InMemoryMatrixCredentials(
                    homeserver=os.environ["MATRIX_HOMESERVER_URL"],
                    matrix_id=os.environ["MATRIX_USER_ID"],
                    access_token=os.environ["MATRIX_ACCESS_TOKEN"],
                )
            except KeyError as e:
                raise Exception(f"Required environment variable not set: {e}")

    async def aget_creds(self):
        return await sync_to_async(self.get_creds)()

    def create_representation_logs(self, instance: "ReplicatedModel"):
        """
        Create the representation logs (tasks) for creating a Matrix space
        """
        repr_logs = []
        # get the representation module specified by the provided instance
        logger.info("Fetching representation module for %s" % instance)
        repr_module = instance.get_representation_module()
        if not repr_module:
            # provided instance doesn't specify a representation module
            return []

        # create an instance of the representation module
        repr_type = RepresentationLog._get_repr_instance(repr_module)

        # call the create_representation_logs method on representation instance
        current_db_primary = Database.current_db().primary_target()
        if current_db_primary != self:
            repr_logs.extend(repr_type.create_representation_logs(instance, current_db_primary))
        else:
            repr_logs.extend(repr_type.create_representation_logs(instance, self))

        return repr_logs

    async def push_replication_log(self, fixture: List[Dict[str, Any]]) -> None:
        """
        Pushes a replication log to the replication self as a replicate. Uses taskiq
        to "kick" a replication task that all devices in the object's
        configured room will load.
        """
        from fractal_database.replication.tasks import replicate_fixture

        # we have to serialize the fixture to json because Matrix has a non-standard
        # JSON encoding that doesn't allow floats
        replication_event = json.dumps(fixture)

        if not self.metadata.get("room_id"):
            logger.warning(f"Unable to replicate, no room_id found for {self.name}")
            return

        room_id = self.metadata["room_id"]
        logger.info(
            "Target %s is pushing fixture(s): %s to room %s on homeserver %s"
            % (self, replication_event, room_id, self.homeserver)
        )

        try:
            creds = await self.aget_creds()
        except Exception as e:
            raise Exception(f"Cannot push replication log: {e}")

        broker = FractalMatrixBroker().with_matrix_config(
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
        from fractal_database.models import Database

        # if creating a representation for a target that is not the primary target of the current_db
        # we need to use the sub-space representation
        if Database.current_db().primary_target() != self:
            return "fractal_database_matrix.representations.MatrixSubSpace"
        return "fractal_database_matrix.representations.MatrixSpace"


class BaseMatrixReplicationTarget(MatrixReplicationTarget):

    class Meta:
        abstract = True
