from copy import deepcopy
from typing import TYPE_CHECKING, Any, Dict, Optional
from uuid import UUID

from django.core.serializers import serialize
from fractal.matrix import MatrixClient
from fractal_database.representations import Representation, get_nested_attr
from nio import RoomCreateError, RoomPutStateError

if TYPE_CHECKING:
    from fractal_database.models import (
        ReplicatedModel,
        ReplicationTarget,
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
    def get_repr_metadata_properties(cls) -> Dict[str, str]:
        return {"name": "name", "uuid": "uuid"}

    @classmethod
    @property
    def repr_method(cls):
        return f"{cls.module}.{cls.__name__}"

    async def put_state(
        self,
        room_id: str,
        target: "MatrixReplicationTarget",
        state_type: str,
        content: dict[str, Any],
    ) -> None:
        async with MatrixClient(target.homeserver, target.access_token) as client:
            res = await client.room_put_state(
                room_id,
                state_type,
                content=content,
            )
            if isinstance(res, RoomPutStateError):
                raise Exception(res.message)
            return None

    async def create_room(
        self,
        target: "MatrixReplicationTarget",
        name: str,
        space: bool = False,
        initial_state: Optional[list[dict[str, Any]]] = None,
    ) -> str:
        async with MatrixClient(target.homeserver, target.access_token) as client:
            res = await client.room_create(
                name=name,
                space=space,
                initial_state=initial_state if initial_state else self.initial_state,
            )
            if isinstance(res, RoomCreateError):
                raise Exception(res.message)

            room_id = res.room_id
            print(f"Successfully created representation of {name} in Matrix: {room_id}")

        return res.room_id

    async def add_subspace(
        self, target: "MatrixReplicationTarget", parent_room_id: str, child_room_id: str
    ) -> None:
        # fetch the non-base class version of the target so it will contain the Matrix specific properties

        async with MatrixClient(target.homeserver, target.access_token) as client:
            res = await client.room_put_state(
                parent_room_id,
                "m.space.child",
                {"via": [target.homeserver]},
                state_key=child_room_id,
            )
            if isinstance(res, RoomPutStateError):
                raise Exception(res.message)

            print(
                f"Successfully added child space {child_room_id} to parent space {parent_room_id}"
            )


class MatrixRoom(MatrixRepresentation):
    async def create_representation(
        self, repr_log: "RepresentationLog", target_id: UUID
    ) -> dict[str, str]:
        """
        Creates a Matrix room for the ReplicatedModel "instance" that inherits from this class
        """
        try:
            name = repr_log.metadata["name"]
        except KeyError:
            raise Exception("name and uuid must be specified in metadata")

        target = await repr_log.target_type.model_class().objects.aget(uuid=repr_log.target_id)
        room_id = await self.create_room(
            target=target,
            name=name,
            space=False,
        )

        print("Created Matrix room for", name)
        return {"room_id": room_id}


class MatrixSpace(MatrixRepresentation):
    initial_state = [
        {
            "type": "f.database",
            "content": {},
        },
        {"type": "f.database.target", "content": {}},
    ]

    async def create_representation(
        self, repr_log: "RepresentationLog", target_id: UUID
    ) -> dict[str, str]:
        """
        Creates a Matrix space for the ReplicatedModel "instance" that inherits from this class
        """
        try:
            name = repr_log.metadata["name"]
        except KeyError:
            raise Exception("name and uuid must be specified in metadata")

        target = (
            await repr_log.target_type.model_class()
            .objects.select_related("database")
            .prefetch_related("database__devices")
            .aget(uuid=repr_log.target_id)
        )

        initial_state = deepcopy(self.initial_state)
        room_id = await self.create_room(
            target=target,
            name=name,
            space=True,
            initial_state=initial_state,
        )

        target.metadata["room_id"] = room_id

        initial_state[0]["content"]["fixture"] = serialize("json", [target.database])
        initial_state[1]["content"]["fixture"] = serialize("json", [target])

        await self.put_state(room_id, target, "f.database", initial_state[0]["content"])
        await self.put_state(room_id, target, "f.database.target", initial_state[1]["content"])

        print("Created Matrix space for", name)
        return {"room_id": room_id}

    @classmethod
    def get_repr_metadata_properties(cls) -> Dict[str, str]:
        return {"name": "database.name", "uuid": "uuid"}


class MatrixSubSpace(MatrixSpace):
    @classmethod
    def create_representation_logs(
        cls,
        instance: "ReplicatedModel",
        target: "ReplicationTarget",
        metadata_props: Dict[str, str],
    ):
        """
        Create the representation logs (tasks) for creating a Matrix space
        """
        from fractal_database.models import RepresentationLog

        logs = MatrixSpace.create_representation_logs(instance, target, metadata_props)

        metadata = {
            prop_name: get_nested_attr(instance, prop)
            for prop_name, prop in metadata_props.items()
        }

        subspace_create = RepresentationLog.objects.create(
            instance=instance, method=cls.repr_method, target=target, metadata=metadata
        )
        logs.append(subspace_create)
        return logs

    async def create_representation(self, repr_log: "RepresentationLog", target_id: UUID) -> None:
        """
        Creates a Matrix space for the ReplicatedModel "instance" that inherits from this class
        """
        print(repr_log.metadata)
        parent_room_id = repr_log.metadata["parent_repr_id"]
        model_class = repr_log.content_type.model_class()
        target_model = repr_log.target_type.model_class()
        instance = await model_class.objects.aget(uuid=repr_log.object_id)
        target = await target_model.objects.aget(uuid=target_id)
        child_room_id = instance.metadata["room_id"]
        await self.add_subspace(target, parent_room_id, child_room_id)

    @classmethod
    def get_repr_metadata_properties(cls) -> Dict[str, str]:
        return {
            "name": "database.name",
            "uuid": "uuid",
            "parent_repr_id": "database.parent_repr_id",
        }


class AppSpace(MatrixSpace):
    initial_state = [
        {
            "type": "f.database.app",
            "content": {},
        }
    ]

    @classmethod
    def get_repr_metadata_properties(cls) -> Dict[str, str]:
        return {"name": "database.name", "uuid": "uuid"}
