import json
import logging
from typing import Any, Dict, List, Union

from asgiref.sync import sync_to_async
from django.db import models
from django.db.models.manager import BaseManager
from fractal_database.models import (
    BaseModel,
    Database,
    Device,
    DurableOperation,
    ReplicatedModel,
    ReplicationTarget,
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
    homeserver = models.CharField(max_length=255, null=False, blank=False, default=None)

    def __str__(self):
        if self.metadata.get("room_id"):
            return f"{self.name} ({self.metadata['room_id']})"
        else:
            return self.name

    def get_creds(self) -> Union[MatrixCredentials, InMemoryMatrixCredentials]:
        current_device = Device.current_device()
        try:
            return self.matrixcredentials_set.get(device=current_device)
        except MatrixCredentials.DoesNotExist:
            raise Exception(
                f"Target {self} does not have Matrix Credentials for current device {current_device}. Add credentials then call schedule_replication() on {self}"
            )

        # else:
        #     try:
        #         return InMemoryMatrixCredentials(
        #             homeserver=os.environ["MATRIX_HOMESERVER_URL"],
        #             matrix_id=os.environ["MATRIX_USER_ID"],
        #             access_token=os.environ["MATRIX_ACCESS_TOKEN"],
        #         )
        #     except KeyError as e:
        #         raise Exception(f"Required environment variable not set: {e}")

    async def aget_creds(self):
        return await sync_to_async(self.get_creds)()

    def create_durable_operations(self, instance: "ReplicatedModel"):
        """
        Create the durable operations (tasks) for an instance.
        """
        durable_operations = []
        # get the operation module specified by the provided instance
        logger.info("Fetching operation module for %s" % instance)
        operation_module = instance.get_operation_module()
        if not operation_module:
            # provided instance doesn't specify an operation module
            return []

        # create an instance of the operation module
        operation = DurableOperation.get_operation(operation_module)

        # call the create_representation_logs method on operation instance
        #     operation_module.extend(operation.create_durable_operations(instance, current_db_primary))
        # else:

        durable_operations.extend(operation.create_durable_operations(instance, self))
        current_db_primary = Database.current_db().primary_target()
        # if current_db_primary != self and instance == self:
        #     # if the current target is not the primary target of the current_db
        #     # it should be added to the primary target as a subspace
        #     operation = DurableOperation.get_operation(
        #         "fractal_database_matrix.operations.AddExistingMatrixSubSpace"
        #     )
        #     durable_operations.extend(
        #         operation.create_durable_operations(instance, current_db_primary)
        #     )

        return durable_operations

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

        try:
            room_id = self.device_space
        except Exception:
            logger.warning("Unable to replicate, no room_id found for %s" % self.name)
            return None

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

    def get_operation_module(self) -> str:
        return "fractal_database_matrix.operations.CreateMatrixDatabase"


class BaseMatrixReplicationTarget(MatrixReplicationTarget):

    class Meta:
        abstract = True


# class DeviceReplicationTarget(BaseMatrixReplicationTarget):
#     """ """

#     device = models.ForeignKey(
#         "fractal_database.Device",
#         on_delete=models.CASCADE,
#         related_name="device_replication_targets",
#     )

#     def repr_metadata_props(self) -> Dict[str, str]:
#         metadata = super().repr_metadata_props()
#         metadata["name"] = self.name
#         return metadata

#     def get_operation_module(self) -> str:
#         return "fractal_database_matrix.operations.DeviceRoom"

#     def create_durable_operations(self, instance: "ReplicatedModel"):
#         """
#         Create the representation logs (tasks) for creating a Matrix space
#         """
#         from fractal_database.models import DurableOperation

#         repr_logs = []
#         # get the representation module specified by the provided instance
#         logger.info("Fetching operation module for %s" % instance)
#         repr_module = instance.get_operation_module()
#         if not repr_module:
#             # provided instance doesn't specify a representation module
#             return []

#         # create an instance of the representation module
#         repr_type = DurableOperation.get_module_instance(repr_module)

#         primary_target = self.database.primary_target()  # type: ignore

#         # call the create_representation_logs method on representation instance
#         repr_logs.extend(repr_type.create_durable_operations(instance, primary_target))

#         return repr_logs
