import json
import logging
import subprocess
from typing import TYPE_CHECKING, Any, Dict, List, Optional

import docker
import fractal_database_matrix
import tldextract
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
    docker_compose,
)
from fractal_database_matrix.broker.broker import FractalMatrixBroker
from taskiq import SendTaskError
from taskiq.middlewares.retry_middleware import SimpleRetryMiddleware
from taskiq_matrix.matrix_result_backend import MatrixResultBackend

from .exceptions import MatrixHomeserverAlreadyExists

if TYPE_CHECKING:
    from fractal.gateway.models import Gateway, Link

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
    def create(cls, device: Optional["Device"] = None, **ckwargs) -> "MatrixHomeserver":
        name = ckwargs.pop("name", "Synapse")
        url = ckwargs.pop("url", cls.SYNAPSE_LOCAL)

        gateway = None
        link = None
        if not device:
            device = Device.current_device()

        url = tldextract.extract(url)
        # registered_domains have the domain + suffix joined together.
        # some domains like localhost don't have a registered domain (dont have a suffix)
        # so if registered_domain is "" then use the domain
        domain = url.registered_domain or url.domain
        subdomain = url.subdomain

        # only attempt to create a link if the current database has the gateways attribute
        try:
            from fractal.gateway.models import Domain, Gateway
        except ImportError:
            logger.warning(
                "Gateway model not found. Your Matrix Homeserver will only be accessible locally"
            )
        else:

            # FIXME: handle multiple gateways
            gateway = Gateway.objects.first()
            if not gateway:
                logger.warning(
                    "No Gateway found. Your Matrix Homeserver will only be accessible locally"
                )
            else:
                try:
                    domain = gateway.get_domain(domain)
                except Domain.DoesNotExist:
                    raise Exception("Domain %s not found" % domain)

                link = gateway.create_link(domain, subdomain, override_link=True)
                fqdn = link.fqdn

        # if the url has localhost in it, use the cls.SYNAPSE_LOCAL since links
        # for localhost wont have a valid cert which doesn't work with the FractaLMatrixClient
        if "localhost" in url.domain:
            fqdn = cls.SYNAPSE_LOCAL

        # ensure that the homeserver doesn't already exist
        try:
            cls.objects.get(url=url)
        except cls.DoesNotExist:
            pass
        else:
            raise MatrixHomeserverAlreadyExists(fqdn)

        with transaction.atomic():
            homeserver = cls.objects.create(name=name, url=fqdn, type=cls.__name__, **ckwargs)

            # add the specified device as a member to the homeserver database
            device.add_membership(homeserver)

            if gateway:
                homeserver.gateways.add(gateway)

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
        return homeserver

    def _build_images(self) -> None:
        docker_compose("build", _cwd=self.SYNAPSE_COMPOSE_FILE_PATH)

    def _render_compose_file(self) -> str:
        """ """
        self._build_images()

        # FIXME: handle multiple links
        if not hasattr(self.config, "links") or not hasattr(self, "gateways"):
            raise Exception("No links or gateways found for MatrixHomeserver %s" % self)

        with open(f"{self.SYNAPSE_COMPOSE_FILE_PATH}/docker-compose.yml") as f:
            compose_file = yaml.safe_load(f)

        gateway = self.gateways.first()
        if not gateway:
            raise Exception("No gateway found for MatrixHomeserver %s" % self)

        link: Optional["Link"] = self.config.links.first()  # type: ignore
        if not link:
            logger.warning(
                "Matrix Homeserver %s ServiceInstanceConfig does not have any links or gateways. Your Matrix Homeserver will only work locally."
                % self
            )
            return yaml.dump(compose_file)

        # TODO: handle passing an environment file that contains any
        # environment variables set on the config

        for service in compose_file["services"]:
            if "expose" in compose_file["services"][service]:
                expose = compose_file["services"][service]["expose"][0]
                compose_file["services"][service]["environment"]["SERVER_NAME"] = link.fqdn
                break
        else:
            raise Exception("No service with expose key found in compose file")

        snippet = yaml.safe_load(link.generate_compose_snippet(gateway, expose))
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
            database_type = self._get_database_type()
            db_origin = database_type.origin_channel()
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

    async def push_replication_log(self, fixture: Dict[str, Any]) -> None:
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
            await self.kick_task(replicate_fixture, replication_event, room_id)
        except SendTaskError as e:
            raise Exception(e.__cause__)

    async def kick_task(self, task_func, *targs, task_labels: Optional[dict] = None, **tkwargs):
        if not task_labels:
            task_labels = {}

        # ensure that the homeserver is fetched FIXME
        await sync_to_async(lambda: self.homeserver)()

        try:
            creds = await self.aget_creds()
        except Exception as e:
            raise Exception(f"Cannot push replication log: {e}")

        broker = (
            FractalMatrixBroker()
            .with_matrix_config(
                homeserver_url=self.homeserver.url,
                access_token=creds.access_token,
            )
            .with_result_backend(
                MatrixResultBackend(
                    homeserver_url=self.homeserver.url,
                    access_token=creds.access_token,
                    result_ex_time=3600,
                )
            )
            .with_middlewares(SimpleRetryMiddleware(default_retry_count=3))
        )

        if "room_id" not in task_labels:
            task_labels["room_id"] = self.device_space

        room_id = task_labels["room_id"]

        logger.debug("Kicking task %s to room %s" % (task_func, room_id))
        return (
            await task_func.kicker()
            .with_broker(broker)
            .with_labels(**task_labels)
            .kiq(*targs, **tkwargs)
        )

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
