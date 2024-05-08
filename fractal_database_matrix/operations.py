import logging
from secrets import token_hex
from typing import TYPE_CHECKING, Any, Optional, Sequence

from fractal.cli.controllers.auth import AuthenticatedController
from fractal.matrix import MatrixClient
from fractal_database.models import DurableOperation, ReplicatedModel, ReplicationTarget
from fractal_database.operations import Operation
from nio import RoomCreateError, RoomPutStateError, RoomVisibility

if TYPE_CHECKING:
    from fractal_database.models import (
        App,
        Device,
        DeviceMembership,
        DurableOperation,
        ReplicatedModel,
        ReplicationTarget,
    )
    from fractal_database_matrix.models import (
        MatrixCredentials,
        MatrixReplicationTarget,
    )

logger = logging.getLogger(__name__)


class MatrixOperation(Operation):
    async def put_state(
        self,
        room_id: str,
        target: "MatrixReplicationTarget",
        state_type: str,
        content: dict[str, Any],
    ) -> None:
        creds = AuthenticatedController.get_creds()
        if not creds:
            raise Exception("You must be logged in to put state")

        access_token, homeserver_url, _ = creds

        async with MatrixClient(homeserver_url, access_token) as client:
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
        public: bool = False,
        invite: Sequence[str] = (),
    ) -> str:
        if public:
            visibility = RoomVisibility.public
        else:
            visibility = RoomVisibility.private
        if initial_state is None:
            initial_state = [
                {
                    "type": "f.database",
                    "content": {},
                }
            ]

        creds = AuthenticatedController.get_creds()
        if not creds:
            raise Exception("You must be logged in to create a room")

        access_token, homeserver_url, _ = creds

        # verify that matrix IDs passed in invite are all lowercase
        if invite:
            if not any([matrix_id.split("@")[1].islower() for matrix_id in invite]):
                raise Exception("Matrix IDs must be lowercase")

        async with MatrixClient(homeserver_url, access_token) as client:
            res = await client.room_create(
                name=name,
                space=space,
                initial_state=initial_state,
                visibility=visibility,
            )
            if isinstance(res, RoomCreateError):
                raise Exception(res.message)

            room_id = res.room_id

            for account in invite:
                await client.invite(account, room_id, admin=True)

            logger.info(
                "Successfully created %s for %s in Matrix: %s"
                % ("Room" if not space else "Space", name, room_id)
            )

        return room_id

    async def add_subspace(
        self, target: "MatrixReplicationTarget", parent_room_id: str, child_room_id: str
    ) -> None:
        creds = AuthenticatedController.get_creds()
        if not creds:
            raise Exception("You must be logged in to add a subspace")

        access_token, homeserver_url, _ = creds

        async with MatrixClient(homeserver_url, access_token) as client:
            res = await client.room_put_state(
                parent_room_id,
                "m.space.child",
                {"via": [target.homeserver]},
                state_key=child_room_id,
            )
            if isinstance(res, RoomPutStateError):
                raise Exception(res.message)

            logger.info(
                "Successfully added child space %s to parent space %s"
                % (child_room_id, parent_room_id)
            )

    async def accept_invite_as_device(
        self, device_creds: "MatrixCredentials", room_id: str, homeserver_url: str
    ):
        device_matrix_id = device_creds.matrix_id
        # accept invite on behalf of device
        async with MatrixClient(
            homeserver_url=homeserver_url,
            access_token=device_creds.access_token,
        ) as client:
            logger.info("Accepting invite for %s as %s" % (room_id, device_matrix_id))
            await client.join_room(room_id)

    async def invite_device(self, device_matrix_id: str, room_id: str) -> None:
        from fractal.cli.controllers.auth import AuthenticatedController

        # FIXME: Once user has accounts on many homeservers, we need to strip the
        # host off of the room id and try to find credentials that match that host
        creds = AuthenticatedController.get_creds()
        if creds:
            access_token, homeserver_url, owner_matrix_id = creds
        else:
            raise Exception("You must be logged in to Matrix to invite a device")

        async with MatrixClient(
            homeserver_url=homeserver_url,
            access_token=access_token,
        ) as client:
            logger.info("Inviting %s to %s" % (device_matrix_id, room_id))
            await client.invite(user_id=device_matrix_id, room_id=room_id, admin=True)

    async def register_device_account(
        self,
        device_name: str,
    ) -> tuple[str, str, str]:
        creds = AuthenticatedController.get_creds()
        if creds:
            access_token, homeserver_url, _ = creds
        else:
            raise Exception("You must be logged in to Matrix to register a device account")

        async with MatrixClient(
            homeserver_url=homeserver_url,
            access_token=access_token,
        ) as client:
            registration_token = await client.generate_registration_token()
            await client.whoami()
            homeserver_name = client.user_id.split(":")[1]
            device_matrix_id = f"@{device_name.lower()}:{homeserver_name}"
            password = token_hex(32)  # FIXME
            access_token = await client.register_with_token(
                matrix_id=device_matrix_id,
                password=password,
                registration_token=registration_token,
                device_name=device_name,
            )
            return access_token, device_matrix_id, password

    async def set_display_name(
        self,
        homeserver_url: str,
        creds: "MatrixCredentials",
        display_name: str,
        owner_matrix_id: Optional[str] = None,
    ):
        if owner_matrix_id:
            # get local part of owner_matrix_id without the @
            owner_username = owner_matrix_id.split("@")[1].split(":")[0]
            display_name = f"{owner_username}'s {display_name}"

        async with MatrixClient(
            homeserver_url=homeserver_url,
            access_token=creds.access_token,
        ) as client:
            await client.set_displayname(display_name)


class CreateMatrixRoom(MatrixOperation):
    async def run(self, operation: "DurableOperation") -> dict[str, str]:
        """
        Creates a Matrix room for the ReplicatedModel "instance" using the target.
        """
        try:
            name = operation.metadata["name"]
            public = operation.metadata.get("public", False)
            metadata_label = operation.metadata.get("metadata_label", "room_id")
        except KeyError:
            raise Exception("name must be specified in metadata")

        target: "MatrixReplicationTarget" = (
            await operation.target_type.model_class()
            .objects.select_related("database")
            .prefetch_related("database__devices", "matrixcredentials_set")
            .aget(pk=operation.target_id)
        )  # type: ignore

        matrix_ids_to_invite = [target.matrix_id for target in target.matrixcredentials_set.all()]
        room_id = await self.create_room(
            target=target,
            name=name,
            space=False,
            public=public,
            invite=matrix_ids_to_invite,
        )

        # accept invites to room
        for account in target.matrixcredentials_set.all():
            await self.accept_invite_as_device(account, room_id, target.homeserver)

        logger.info("Successfully created Matrix Room for %s" % name)
        return {metadata_label: room_id}


class CreateMatrixSpace(MatrixOperation):
    async def run(self, operation: "DurableOperation") -> dict[str, str]:
        """
        Creates a Matrix space for the ReplicatedModel "instance" that inherits from this class
        """
        try:
            name = operation.metadata["name"]
            metadata_label = operation.metadata.get("metadata_label", "room_id")
        except KeyError:
            raise Exception("name must be specified in metadata")

        target: "MatrixReplicationTarget" = (
            await operation.target_type.model_class()
            .objects.select_related("database")
            .prefetch_related("database__devices", "matrixcredentials_set")
            .aget(pk=operation.target_id)
        )  # type: ignore

        logger.info("Creating Matrix space for %s on target %s" % (name, target))

        initial_state = [
            {
                "type": "f.database",
                "content": {},
            },
            {"type": "f.database.target", "content": {}},
        ]
        room_id = await self.create_room(
            target=target,
            name=name,
            space=True,
            initial_state=initial_state,
        )

        target.metadata[metadata_label] = room_id

        if target.database:
            initial_state[0]["content"]["fixture"] = await target.database.ato_fixture(
                json=True, with_relations=True
            )
        initial_state[1]["content"]["fixture"] = await target.ato_fixture(
            json=True, with_relations=True
        )

        await self.put_state(room_id, target, "f.database", initial_state[0]["content"])
        await self.put_state(room_id, target, "f.database.target", initial_state[1]["content"])

        logger.info("Successfully created Matrix Space for %s on target %s" % (name, target))
        return {
            metadata_label: room_id,
        }


class CreateMatrixSubSpace(CreateMatrixSpace):
    @classmethod
    def create_durable_operations(
        cls,
        instance: "ReplicatedModel",
        target: "ReplicationTarget",
    ):
        """
        Create the operations (tasks) for creating a Matrix subspace
        """
        # create operations for the subspace
        create_subspace = CreateMatrixSpace.create_durable_operations(instance, target)

        # create operations for adding the subspace to the parent space
        create_subspace.extend(super().create_durable_operations(instance, target))
        return create_subspace

    async def run(self, operation: "DurableOperation") -> None:
        """
        Creates a Matrix space for the ReplicatedModel "instance" that inherits from this class
        """
        # get the model the object that this operation is for
        # (this is usually a ReplicationTarget model since only ReplicationTargets run operations)
        model_class: "MatrixReplicationTarget" = operation.content_type.model_class()  # type: ignore
        # fetch the replicated model that this operation is for
        instance = await model_class.objects.aget(pk=operation.object_id)
        # get the model for the target that this operation is for
        target_model = operation.target_type.model_class()
        # fetch the target
        target: "MatrixReplicationTarget" = await target_model.objects.prefetch_related(
            "matrixcredentials_set"
        ).aget(
            pk=operation.target_id
        )  # type: ignore

        # pull room ids from metadata
        parent_room_id = target.metadata["room_id"]
        child_room_id = instance.metadata["room_id"]
        if parent_room_id == child_room_id:
            raise Exception("Parent and child room IDs cannot be the same")

        await self.add_subspace(target, parent_room_id, child_room_id)


class CreateDevicesSubSpace(CreateMatrixSubSpace):

    @classmethod
    def create_durable_operations(cls, instance: ReplicatedModel, target: ReplicationTarget):
        from fractal_database.models import DurableOperation

        # create the operation for the creating the subspace
        create_subspace = [
            DurableOperation.objects.create(
                instance=instance,
                module=CreateMatrixSpace.operation_module(),
                target=target,
                metadata={"name": "Devices", "metadata_label": "devices_room_id"},
            )
        ]

        # create the operation for adding the subspace to the parent space
        add_subspace_to_parent = DurableOperation.objects.create(
            instance=instance,
            module=cls.operation_module(),
            target=target,
            metadata=instance.operation_metadata_props(),
        )
        create_subspace.append(add_subspace_to_parent)
        return create_subspace

    async def run(self, operation: DurableOperation) -> None:
        """
        Adds the device space as a subspace to the target's space
        """
        # get the target that this operation is for
        target_model = operation.target_type.model_class()
        target: "MatrixReplicationTarget" = await target_model.objects.prefetch_related(
            "matrixcredentials_set"
        ).aget(
            pk=operation.target_id
        )  # type: ignore

        parent_room_id = target.room
        child_room_id = target.device_space
        if parent_room_id == child_room_id:
            raise Exception("Parent and child room IDs cannot be the same")

        # add the device space to the target's space
        await self.add_subspace(target, parent_room_id, child_room_id)


class CreateAppsSubSpace(CreateMatrixSubSpace):
    @classmethod
    def create_durable_operations(cls, instance: ReplicatedModel, target: ReplicationTarget):
        from fractal_database.models import DurableOperation

        # create the operation for creating the subspace
        create_subspace = [
            DurableOperation.objects.create(
                instance=instance,
                module=CreateMatrixSpace.operation_module(),
                target=target,
                metadata={"name": "Apps", "metadata_label": "apps_room_id"},
            )
        ]

        # create the operation for adding the subspace to the parent space
        add_subspace_to_parent = DurableOperation.objects.create(
            instance=instance,
            module=cls.operation_module(),
            target=target,
            metadata=instance.operation_metadata_props(),
        )
        create_subspace.append(add_subspace_to_parent)
        return create_subspace

    async def run(self, operation: DurableOperation) -> None:
        """
        Adds the apps space as a subspace to the target's space
        """
        # get the target that this operation is for
        target_model = operation.target_type.model_class()
        target: "MatrixReplicationTarget" = await target_model.objects.prefetch_related(
            "matrixcredentials_set"
        ).aget(
            pk=operation.target_id
        )  # type: ignore

        parent_room_id = target.room
        child_room_id = target.app_space
        if parent_room_id == child_room_id:
            raise Exception("Parent and child room IDs cannot be the same")

        # add the apps space to the target's space
        await self.add_subspace(target, parent_room_id, child_room_id)


class CreateMatrixDatabase(CreateMatrixSpace):
    @classmethod
    def create_durable_operations(
        cls,
        instance: "ReplicatedModel",
        target: "ReplicationTarget",
    ):
        """
        Create the operations (tasks) for creating a Database in Matrix.
        """
        # create the operations for creating the the Database Space itself
        database_space = CreateMatrixSpace.create_durable_operations(instance, target)

        # create the operations for creating the device and app subspaces
        database_space.extend(CreateDevicesSubSpace.create_durable_operations(instance, target))
        database_space.extend(CreateAppsSubSpace.create_durable_operations(instance, target))
        return database_space


class CreateMatrixSubRoom(CreateMatrixSubSpace):
    @classmethod
    def create_durable_operations(
        cls,
        instance: "ReplicatedModel",
        target: "ReplicationTarget",
    ):
        """
        Create the operations (tasks) for creating a Matrix subroom
        (A room that is in a space).
        """
        from fractal_database.models import DurableOperation

        # create the operations for creating the room
        create_subroom = CreateMatrixRoom.create_durable_operations(instance, target)

        # create the operations for adding the room to the parent space
        add_subroom_to_parent = DurableOperation.objects.create(
            instance=instance,
            module=cls.operation_module(),
            target=target,
            metadata=instance.operation_metadata_props(),
        )
        create_subroom.append(add_subroom_to_parent)
        return create_subroom


class AddExistingMatrixSubSpace(CreateMatrixSubSpace):
    @classmethod
    def create_durable_operations(
        cls,
        instance: "ReplicatedModel",
        target: "ReplicationTarget",
    ):
        """
        Create the operations (tasks) for adding an existing Matrix space
        as a subspace to another space.
        """
        from fractal_database.models import DurableOperation

        # create the operation for adding the subspace to the parent space
        add_subspace_to_parent = DurableOperation.objects.create(
            instance=instance,
            module=cls.operation_module(),
            target=target,
            metadata=instance.operation_metadata_props(),
        )
        return [add_subspace_to_parent]


class SetDisplayName(MatrixOperation):
    async def run(self, operation: DurableOperation) -> None:
        """
        Sets the display name of the device in the Matrix room
        """
        try:
            name = operation.metadata["name"]
            display_name = operation.metadata.get("display_name", name)
            owner_matrix_id = operation.metadata.get("owner_matrix_id")
        except KeyError as err:
            raise Exception(f"{err.args[0]} must be specified in metadata")

        model_class = operation.content_type.model_class()  # type: ignore
        device: "Device" = await model_class.objects.prefetch_related(
            "matrixcredentials_set"
        ).aget(
            pk=operation.object_id
        )  # type: ignore

        # fetch target in order to get credentials of users to invite to the apps subspace
        target: "MatrixReplicationTarget" = (
            await operation.target_type.model_class()
            .objects.select_related("database")
            .prefetch_related("database__devices", "matrixcredentials_set")
            .aget(pk=operation.target_id)
        )  # type: ignore

        device_creds = await target.matrixcredentials_set.filter(device=device).afirst()
        if not device_creds:
            logger.error(
                "Failed to find matrix credentials for device %s for %s"
                % (device, target.homeserver)
            )
            raise Exception(
                "Failed to find matrix credentials for device %s for %s" % (device, target)
            )

        await self.set_display_name(
            target.homeserver, device_creds, display_name, owner_matrix_id=owner_matrix_id
        )


class RegisterDeviceAccount(MatrixOperation):
    @classmethod
    def create_durable_operations(
        cls,
        instance: "ReplicatedModel",
        target: "ReplicationTarget",
    ):
        """
        Create the operations (tasks) for adding an existing Matrix space
        as a subspace to another space.
        """
        from fractal_database.models import DurableOperation

        # create the operation for adding the subspace to the parent space
        operations = [
            DurableOperation.objects.create(
                instance=instance,
                module=cls.operation_module(),
                target=target,
                metadata=instance.operation_metadata_props(),
            )
        ]

        operations.extend(SetDisplayName.create_durable_operations(instance, target))
        return operations

    async def run(self, operation: "DurableOperation") -> dict[str, str]:
        """
        Registers an account for the device
        """
        try:
            name = operation.metadata["name"]
        except KeyError as err:
            raise Exception(f"{err.args[0]} must be specified in metadata")

        model_class = operation.content_type.model_class()  # type: ignore
        device: "Device" = await model_class.objects.prefetch_related(
            "matrixcredentials_set"
        ).aget(
            pk=operation.object_id
        )  # type: ignore

        # fetch target in order to get credentials of users to invite to the apps subspace
        target: "MatrixReplicationTarget" = (
            await operation.target_type.model_class()
            .objects.select_related("database")
            .prefetch_related("database__devices", "matrixcredentials_set")
            .aget(pk=operation.target_id)
        )  # type: ignore

        device_creds = await target.matrixcredentials_set.filter(device=device).afirst()
        if device_creds:
            logger.info(
                "Device account for %s is already registered with homeserver %s"
                % (device, target.homeserver)
            )
            return {}

        access_token, matrix_id, password = await self.register_device_account(name)

        return {
            "access_token": access_token,
            "matrix_id": matrix_id,
            "password": password,
            "target_id": operation.target_id,
        }


class AcceptDeviceSpaceInvite(MatrixOperation):
    async def run(self, operation: "DurableOperation") -> None:
        """
        Accepts an
        """
        model_class = operation.content_type.model_class()  # type: ignore
        instance: "DeviceMembership" = await model_class.objects.prefetch_related("device").aget(
            pk=operation.object_id
        )  # type: ignore

        # fetch target in order to get credentials of users to invite to the apps subspace
        target: "MatrixReplicationTarget" = (
            await operation.target_type.model_class()
            .objects.select_related("database")
            .prefetch_related("database__devices", "matrixcredentials_set")
            .aget(pk=operation.target_id)
        )  # type: ignore

        device_creds = await target.matrixcredentials_set.filter(device=instance.device).afirst()
        if not device_creds:
            raise Exception(f"Failed to find device credentials for {instance.device}")

        # accept invite on behalf of device
        await self.accept_invite_as_device(device_creds, target.device_space, target.homeserver)
        logger.info("Device has successfully joined the devices subspace for target %s" % target)

        return None


class InviteDeviceToDeviceSpace(MatrixOperation):
    async def run(self, operation: "DurableOperation") -> None:
        """
        Sends an invite to the device in the instance (DeviceMembership) to the
        devices subspace on the associated target.
        """
        model_class = operation.content_type.model_class()  # type: ignore
        instance: "DeviceMembership" = await model_class.objects.prefetch_related("device").aget(
            pk=operation.object_id
        )  # type: ignore

        # fetch target in order to get credentials of users to invite to the apps subspace
        target: "MatrixReplicationTarget" = (
            await operation.target_type.model_class()
            .objects.select_related("database")
            .prefetch_related("database__devices", "matrixcredentials_set")
            .aget(pk=operation.target_id)
        )  # type: ignore

        device_creds = await target.matrixcredentials_set.filter(device=instance.device).afirst()
        if not device_creds:
            raise Exception(f"Failed to find device credentials for {instance.device}")

        try:
            await self.invite_device(device_creds.matrix_id, target.device_space)
        except Exception as e:
            # if the device is already in the room, no need to accept the invite
            if "is already in the room" in str(e):
                return None
            raise e

        return None

    @classmethod
    def create_durable_operations(
        cls,
        instance: "ReplicatedModel",
        target: "ReplicationTarget",
    ) -> list["DurableOperation"]:
        """
        Create the operations (tasks) for creating a Matrix space
        """
        # create the operation of inviting the device account into the devices subspace on the target
        invite_ops = super().create_durable_operations(instance, target)

        # create operation for accepting the invite
        invite_ops.extend(AcceptDeviceSpaceInvite.create_durable_operations(instance, target))

        return invite_ops


class CreateDeviceSubRoom(MatrixOperation):

    @classmethod
    def create_durable_operations(
        cls,
        instance: "ReplicatedModel",
        target: "ReplicationTarget",
    ):
        """
        Create the operations (tasks) for creating a Matrix space
        """
        from fractal_database.models import DurableOperation

        # create the operation of inviting the device account into the devices subspace on the target
        create_subroom_logs = InviteDeviceToDeviceSpace.create_durable_operations(
            instance, target
        )

        # create operation for creating a room for the device
        create_subroom_logs.extend(CreateMatrixRoom.create_durable_operations(instance, target))

        # create operation for adding the created room the parent space
        create_subroom_logs.append(
            DurableOperation.objects.create(
                instance=instance,
                module=cls.operation_module(),
                target=target,
                metadata=instance.operation_metadata_props(),
            )
        )
        return create_subroom_logs

    async def run(self, operation: "DurableOperation") -> None:
        """ """
        try:
            name = operation.metadata["name"]
        except KeyError:
            raise Exception("name must be specified in metadata")

        model_class = operation.content_type.model_class()  # type: ignore
        instance: "DeviceMembership" = await model_class.objects.aget(pk=operation.object_id)  # type: ignore

        # fetch target in order to get credentials of users to invite to the apps subspace
        target: "MatrixReplicationTarget" = (
            await operation.target_type.model_class()
            .objects.select_related("database")
            .prefetch_related("database__devices", "matrixcredentials_set")
            .aget(pk=operation.target_id)
        )  # type: ignore

        device_space = target.device_space
        if not device_space:
            raise Exception(f"Failed to find device room id in target: {target}")

        device_room_id = instance.room_id

        await self.add_subspace(target, parent_room_id=device_space, child_room_id=device_room_id)

        logger.info(
            "Successfully Matrix Device room for %s as a subspace on target %s" % (name, target)
        )
        return None


class CreateAppSpace(CreateMatrixDatabase):

    @classmethod
    def create_durable_operations(
        cls,
        instance: "ReplicatedModel",
        target: "ReplicationTarget",
    ):
        """
        Create the operations (tasks) for creating a Matrix space
        """
        from fractal_database.models import DurableOperation

        create_app_subspace = super().create_durable_operations(instance, target)

        create_app_subspace.append(
            DurableOperation.objects.create(
                instance=instance,
                module=cls.operation_module(),
                target=target,
                metadata=instance.operation_metadata_props(),
            )
        )
        return create_app_subspace

    async def run(self, operation: "DurableOperation") -> dict[str, str]:
        """ """
        try:
            name = operation.metadata["name"]
        except KeyError:
            raise Exception("name must be specified in metadata")

        model_class = operation.content_type.model_class()  # type: ignore
        instance: "App" = await model_class.objects.aget(pk=operation.object_id)

        # fetch target in order to get credentials of users to invite to the apps subspace
        target: "MatrixReplicationTarget" = (
            await operation.target_type.model_class()
            .objects.select_related("database")
            .prefetch_related("database__devices", "matrixcredentials_set")
            .aget(pk=operation.target_id)
        )  # type: ignore

        try:
            app_space = instance.metadata["room_id"]
        except Exception as err:
            raise Exception(
                f"Failed to find app room id in instance metadata: {instance}"
            ) from err

        await self.add_subspace(target, target.app_space, app_space)

        logger.info(
            "Successfully created App Space representation for %s in Matrix representation on target %s"
            % (name, target)
        )
        return {"room_id": app_space}
