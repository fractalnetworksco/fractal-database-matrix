import json
import logging
import subprocess
from typing import TYPE_CHECKING, Any, Dict, List, Optional

import fractal_database_matrix
import yaml
from asgiref.sync import sync_to_async
from django.db import models, transaction
from fractal_database.models import (
    BaseModel,
    Database,
    Device,
    DurableOperation,
    ReplicatedModel,
    ReplicationChannel,
    Service,
    ServiceInstanceConfig,
)
from fractal_database_matrix.broker.broker import FractalMatrixBroker
from taskiq import SendTaskError

if TYPE_CHECKING:
    from fractal.gateway.models import Gateway

    from .models import MatrixCredentials


logger = logging.getLogger(__name__)

# class MatrixHomeserver(BaseModel):
#     url = models.URLField(primary_key=True) # is the homeserver url


class MatrixHomeserver(Service):
    SYNAPSE_COMPOSE_FILE_PATH = f"{fractal_database_matrix.__path__[0]}/synapse"
    SYNAPSE_LOCAL = "http://localhost:8008"

    credentials: models.QuerySet["MatrixCredentials"]
    gateways: models.QuerySet["Gateway"]

    url = models.URLField()
    priority = models.PositiveIntegerField(default=0, blank=True, null=True)
    registration_token = models.CharField(max_length=255, blank=True, null=True)

    def __str__(self) -> str:
        return f"{self.url} (MatrixHomeserver)"

    @classmethod
    def create(cls, device: Optional["Device"] = None, **ckwargs) -> None:
        name = ckwargs.pop("name", "Synapse")
        url = ckwargs.pop("url", cls.SYNAPSE_LOCAL)

        gateway = None
        link = None
        # only attempt to create a link if the current database has the gateways attribute
        try:
            from fractal.gateway.models import Gateway
        except ImportError:
            logger.warning(
                "Gateway model not found. Your homeserver will only be accessible locally"
            )
        else:
            # FIXME: handle multiple gateways
            gateway = Gateway.objects.first()
            if not gateway:
                raise Exception("No gateway found for MatrixHomeserver %s" % homeserver)
            link = gateway.create_link(url, override_link=True)

        with transaction.atomic():
            homeserver = cls.objects.create(name=name, url=url, type=cls.__name__, **ckwargs)

            if gateway:
                homeserver.gateways.add(gateway)

            if not device:
                device = Device.current_device()

            config = ServiceInstanceConfig.objects.create(
                service=homeserver,
                current_device=device,
                target_state="running",
            )

            # add link to config if it was created
            if link:
                config.links.add(link)

        logger.info(
            "Created homeserver %s with and assigned the current device %s to run the app."
            % (homeserver, device)
        )
        return None

    def _render_compose_file(self, app_config: ServiceInstanceConfig) -> str:
        """ """
        with open(f"{self.SYNAPSE_COMPOSE_FILE_PATH}/docker-compose.yml") as f:
            compose_file = yaml.safe_load(f)

        # FIXME: handle multiple links
        if not hasattr(app_config, "links") or not hasattr(self, "gateways"):
            logger.warning(
                "Matrix Homeserver %s ServiceInstanceConfig does not have any links or gateways. Your Matrix Homeserver will only work locally."
                % self
            )
            return yaml.dump(compose_file)

        link = app_config.links.first()
        if not link:
            logger.warning(
                "Matrix Homeserver %s ServiceInstanceConfig does not have any links or gateways. Your Matrix Homeserver will only work locally."
                % self
            )
            return yaml.dump(compose_file)

        for service in compose_file["services"]:
            if "expose" in compose_file["services"][service]:
                expose = compose_file["services"][service]["expose"][0]
                compose_file["services"][service]["environment"]["SERVER_NAME"] = link.fqdn
                break
        else:
            raise Exception("No service with expose key found in compose file")

        gateway = self.gateways.first()
        if not gateway:
            raise Exception("No gateway found for MatrixHomeserver %s" % self)

        snippet = yaml.safe_load(link.generate_compose_snippet(self.gateways.first(), expose))
        compose_file["services"].update(snippet)
        return yaml.dump(compose_file)

    def save(self, *args, **kwargs):
        # ensure that save is running in a transaction
        if not transaction.get_connection().in_atomic_block:
            with transaction.atomic():
                return self.save(*args, **kwargs)

        # priority is always set to the last priority + 1
        if self._state.adding:
            last_priority = MatrixHomeserver.objects.all().aggregate(models.Max("priority"))[
                "priority__max"
            ]
            self.priority = (last_priority or 0) + 1

        return super().save(*args, **kwargs)

    def operation_metadata_props(self) -> Dict[str, str]:
        """
        Returns the operation metadata properties for this homeserver.
        """
        return {"url": "url"}

    def get_operation_module(self) -> str:
        return "fractal_database_matrix.operations.RegisterOwnedDevices"


class MatrixCredentials(BaseModel):
    matrix_id = models.CharField(max_length=255)
    password = models.CharField(max_length=255, blank=True, null=True)
    access_token = models.CharField(max_length=255)
    homeserver = models.ForeignKey(
        MatrixHomeserver, on_delete=models.CASCADE, related_name="credentials"
    )
    device = models.ForeignKey(Device, on_delete=models.CASCADE)


class InMemoryMatrixCredentials(MatrixCredentials):
    homeserver: str = ""

    class Meta:
        proxy = True

    def save(self, *args, **kwargs):
        # we don't want to save the in-memory credentials
        raise Exception("Cannot save in-memory credentials")


class MatrixReplicationChannel(ReplicationChannel):
    registration_token = models.CharField(max_length=255, blank=True, null=True)
    homeserver = models.ForeignKey(
        MatrixHomeserver, on_delete=models.CASCADE, related_name="channels"
    )

    def __str__(self):
        if self.metadata.get("room_id"):
            return f"{self.name} ({self.metadata['room_id']} - MatrixReplicationTarget)"
        else:
            return f"{self.name} (MatrixReplicationTarget)"

    def get_creds(self) -> MatrixCredentials | InMemoryMatrixCredentials:
        return Device.current_device().get_creds(self.homeserver)

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

        durable_operations.extend(operation.create_durable_operations(instance, self))

        if isinstance(instance, ReplicationChannel):
            db_origin = instance.database.origin_channel()
            # if this channel is not the origin channel for the db,
            # then nest it under the origin channel
            if db_origin and self != db_origin:
                # if the current target is not the primary target of the current_db
                # it should be added to the primary target as a subspace
                operation = DurableOperation.get_operation(
                    "fractal_database_matrix.operations.AddExistingMatrixSubSpace"
                )
                durable_operations.extend(
                    operation.create_durable_operations(instance, db_origin)
                )

        return durable_operations

    async def push_replication_log(self, fixture: List[Dict[str, Any]]) -> None:
        """
        Pushes a replication log to the replication self as a replicate. Uses taskiq
        to "kick" a replication task that all devices in the object's
        configured room will load.
        """
        if not self.target:
            raise Exception("Channel cannot push replication logs if target property is False")

        from fractal_database.replication.tasks import replicate_fixture

        # we have to serialize the fixture to json because Matrix has a non-standard
        # JSON encoding that doesn't allow floats
        replication_event = json.dumps(fixture)

        await sync_to_async(lambda: self.homeserver)()

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
            homeserver_url=self.homeserver.url,
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


class BaseMatrixReplicationChannel(MatrixReplicationChannel):

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
