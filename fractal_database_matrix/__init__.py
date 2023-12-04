import logging
import os
from importlib import import_module
from typing import TYPE_CHECKING, Any, Dict, Iterable

from fractal.matrix import MatrixClient
from nio import AsyncClient

if TYPE_CHECKING:
    from fractal_database.models import (
        ReplicatedModel,
        ReplicationLog,
        ReplicationTarget,
    )

HOMESERVER_URL = os.environ.get("MATRIX_HOMESERVER_URL")

logger = logging.getLogger(__name__)


async def get_or_create_representation(repr_metadata: Dict[str, Any], client: AsyncClient):
    if repr_metadata.get("matrix"):
        return repr_metadata["matrix"]

    # create representation in Matrix


async def replicate(
    log: "ReplicationLog",
    instance: "ReplicatedModel",
    target: "ReplicationTarget",
    defered_replications: Dict[str, Iterable["ReplicationLog"]],
):
    print(f"Running on_commit replication for {log}")
    repr_log = log.repr_log
    async with MatrixClient(HOMESERVER_URL) as client:
        representation = log.payload
        # not every object has a representation
        # so skip trying to apply it if it doesn't
        if repr_log:
            print(f"Applying {repr_log}")
            module, method = repr_log.method.split(":")
            module, cls = module.rsplit(".", 1)
            try:
                target_module = import_module(module)
                repr_cls = getattr(target_module, cls)
                target_method = getattr(repr_cls, method)
            except (ModuleNotFoundError, TypeError) as err:
                logger.error(f"Could not import module {repr_log.method}: {err}")

            # try:
            representation = await target_method(instance, target)
            # except Exception as err:
            # logger.error(f"Could not apply representation log {repr_log}: {err}")

            # log.payload.append(serialize("json", [representation]))

        # TODO combine the representation log and replication log into one
        print(defered_replications)
        await send_replication_log(representation, client)


async def send_replication_log(log: "ReplicationLog", client: AsyncClient):
    print("Sending replication event to Matrix")
    print(log)
