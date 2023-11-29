import os
from typing import TYPE_CHECKING, Set

from asgiref.sync import sync_to_async
from fractal import MatrixClient
from fractal_database.representations import Representation
from nio import RoomCreateError

if TYPE_CHECKING:
    from fractal_database.models import (
        ReplicatedModel,
        ReplicatedModelRepresentation,
        RepresentationLog,
    )
    from fractal_database_matrix.models import MatrixReplicationTarget


class MatrixRepresentation(Representation):
    module = __name__
    initial_state = [
        {
            "type": "f.database",
            "content": {},
        }
    ]

    @classmethod
    def get_repr_metadata_properties(cls) -> Set[str]:
        return {"name", "uuid"}

    @classmethod
    @property
    def repr_method(cls):
        return f"{cls.module}.{cls.__name__}:create_representation"

    @staticmethod
    async def create_room(
        instance: "ReplicatedModel",
        target: "MatrixReplicationTarget",
        name: str,
        uuid: str,
        space: bool = False,
    ) -> "ReplicatedModelRepresentation":
        from fractal_database.models import ReplicatedModelRepresentation

        async with MatrixClient(target.homeserver, target.access_token) as client:
            res = await client.room_create(
                name=name, space=space, initial_state=MatrixRepresentation.initial_state
            )
            if isinstance(res, RoomCreateError):
                raise Exception(res.message)

            room_id = res.room_id
            print(f"Successfully created representation of {name} in Matrix: {room_id}")

        return await ReplicatedModelRepresentation.objects.acreate(
            instance=instance,
            object_id=uuid,
            metadata={"room_id": room_id},
            target=target,
        )


class MatrixRoom(MatrixRepresentation):
    @classmethod
    async def create_representation(
        cls, repr_log: "RepresentationLog", target: "MatrixReplicationTarget"
    ):
        """
        Creates a Matrix room for the ReplicatedModel "instance" that inherits from this class
        """
        try:
            name = repr_log.metadata["name"]
            uuid = repr_log.metadata["uuid"]
        except KeyError:
            raise Exception("name and uuid must be specified in metadata")

        instance = await sync_to_async(lambda: repr_log.instance)()

        repr = await MatrixRepresentation.create_room(
            instance=instance,
            target=target,
            name=name,
            uuid=uuid,
            space=False,
        )

        print("Created Matrix room for", name)


class MatrixSpace(MatrixRepresentation):
    @classmethod
    async def create_representation(
        cls, repr_log: "RepresentationLog", target: "MatrixReplicationTarget"
    ):
        """
        Creates a Matrix space for the ReplicatedModel "instance" that inherits from this class
        """
        try:
            name = repr_log.metadata["name"]
            uuid = repr_log.metadata["uuid"]
        except KeyError:
            raise Exception("name and uuid must be specified in metadata")

        instance = await sync_to_async(lambda: repr_log.instance)()

        repr = await MatrixRepresentation.create_room(
            instance=instance,
            target=target,
            name=name,
            uuid=uuid,
            space=True,
        )

        print("Created Matrix space for", name)
