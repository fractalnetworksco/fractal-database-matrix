from typing import TYPE_CHECKING, Set
from uuid import UUID

from fractal.matrix import MatrixClient
from fractal_database.representations import Representation
from nio import RoomCreateError

if TYPE_CHECKING:
    from fractal_database.models import RepresentationLog


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
        target_id: UUID,
        name: str,
        space: bool = False,
    ) -> str:
        from fractal_database_matrix.models import MatrixReplicationTarget

        # fetch the non-base class version of the target so it will contain the Matrix specific properties
        target = await MatrixReplicationTarget.objects.aget(uuid=target_id)

        async with MatrixClient(target.homeserver, target.access_token) as client:
            res = await client.room_create(
                name=name, space=space, initial_state=MatrixRepresentation.initial_state
            )
            if isinstance(res, RoomCreateError):
                raise Exception(res.message)

            room_id = res.room_id
            print(f"Successfully created representation of {name} in Matrix: {room_id}")

        return res.room_id


class MatrixRoom(MatrixRepresentation):
    @classmethod
    async def create_representation(
        cls, repr_log: "RepresentationLog", target_id: UUID
    ) -> dict[str, str]:
        """
        Creates a Matrix room for the ReplicatedModel "instance" that inherits from this class
        """
        try:
            name = repr_log.metadata["name"]
        except KeyError:
            raise Exception("name and uuid must be specified in metadata")

        room_id = await MatrixRepresentation.create_room(
            target_id=target_id,
            name=name,
            space=False,
        )

        print("Created Matrix room for", name)
        return {"room_id": room_id}


class MatrixSpace(MatrixRepresentation):
    @classmethod
    async def create_representation(
        cls, repr_log: "RepresentationLog", target_id: UUID
    ) -> dict[str, str]:
        """
        Creates a Matrix space for the ReplicatedModel "instance" that inherits from this class
        """
        try:
            name = repr_log.metadata["name"]
        except KeyError:
            raise Exception("name and uuid must be specified in metadata")

        room_id = await MatrixRepresentation.create_room(
            target_id=target_id,
            name=name,
            space=True,
        )

        print("Created Matrix space for", name)
        return {"room_id": room_id}
