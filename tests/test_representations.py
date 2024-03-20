from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from asgiref.sync import sync_to_async
from django.contrib.contenttypes.fields import GenericForeignKey, GenericRelation
from fractal.cli.controllers.auth import AuthController, AuthenticatedController
from fractal.matrix.async_client import FractalAsyncClient
from fractal_database.models import (
    ReplicatedModel,
    ReplicationTarget,
    RepresentationLog,
)
from fractal_database.representations import Representation
from fractal_database_matrix.models import MatrixCredentials, MatrixReplicationTarget
from fractal_database_matrix.representations import (
    MatrixRepresentation,
    MatrixRoom,
    MatrixSpace,
    MatrixSubSpace,
)

pytestmark = pytest.mark.django_db(transaction=True)


async def test_put_state_no_creds():
    matrix_representation = MatrixRepresentation()

    room_id = "room_id"
    target = MagicMock()
    state_type = "state_type"
    content = {}

    with patch(
        "fractal.cli.controllers.auth.AuthenticatedController.get_creds", return_value=None
    ):
        with pytest.raises(Exception) as e:
            await matrix_representation.put_state(room_id, target, state_type, content)
        assert str(e.value) == "You must be logged in to put state"


async def test_create_room_no_creds():
    matrix_representation = MatrixRepresentation()

    target = MagicMock()
    name = "Test Room"
    initial_state = []
    public = False
    invite = []

    with patch(
        "fractal.cli.controllers.auth.AuthenticatedController.get_creds", return_value=None
    ):
        with pytest.raises(Exception) as e:
            await matrix_representation.create_room(
                target, name, initial_state=initial_state, public=public, invite=invite
            )
        assert str(e.value) == "You must be logged in to create a room"


async def test_create_room_invalid_invite_uppercase(
    logged_in_db_auth_controller: AuthenticatedController,
):
    matrix_representation = MatrixRepresentation()
    creds = AuthenticatedController().get_creds()

    target = MagicMock()
    name = "Test Room"
    initial_state = []
    public = False
    # Simulate uppercase Matrix IDs in the invite list
    invite = ["@Test:Upper.com"]

    with patch(
        "fractal.cli.controllers.auth.AuthenticatedController.get_creds", return_value=creds
    ):
        with pytest.raises(Exception) as e:
            await matrix_representation.create_room(
                target, name, initial_state=initial_state, public=public, invite=invite
            )
        assert str(e.value) == "Matrix IDs must be lowercase"


async def test_create_room_representation_success():
    matrix_representation = MatrixRepresentation()
    target = MagicMock()
    room_id = "test room"

    with patch(
        "fractal.cli.controllers.auth.AuthenticatedController.get_creds",
        return_value=("access_token", "homeserver_url", "matrix_id"),
    ), patch(
        "fractal.matrix.async_client.FractalAsyncClient.room_create",
        return_value=MagicMock(room_id="room_id"),
    ), patch(
        "builtins.print"
    ) as mocked_print:
        await matrix_representation.create_room(target, room_id, invite=["@test:room.com"])

        mocked_print.assert_called_with(
            f"Successfully created representation of {room_id} in Matrix: room_id"
        )


async def test_add_subspace_no_creds():
    matrix_representation = MatrixRepresentation()

    target = MagicMock()
    parent_room_id = "parent_room_id"
    child_room_id = "child_room_id"

    with patch(
        "fractal.cli.controllers.auth.AuthenticatedController.get_creds", return_value=None
    ):
        with pytest.raises(Exception) as e:
            await matrix_representation.add_subspace(target, parent_room_id, child_room_id)
        assert str(e.value) == "You must be logged in to add a subspace"


async def test_add_subspace_success_print():
    # Mock required objects
    matrix_representation = MatrixRepresentation()
    target = MagicMock()
    parent_room_id = "parent_room_id"
    child_room_id = "child_room_id"

    # Patch the necessary method
    with patch(
        "fractal.cli.controllers.auth.AuthenticatedController.get_creds",
        return_value=("access_token", "homeserver_url", "matrix_id"),
    ), patch(
        "fractal.matrix.async_client.FractalAsyncClient.room_put_state"
    ) as mocked_room_put_state, patch(
        "builtins.print"
    ) as mocked_print:
        # Invoke the method
        await matrix_representation.add_subspace(target, parent_room_id, child_room_id)

        # Assert that print was called with the correct message
        mocked_print.assert_called_with(
            f"Successfully added child space {child_room_id} to parent space {parent_room_id}"
        )


async def test_create_representation_no_name():
    mock_repr_log = MagicMock()
    mock_repr_log.metadata = {}  # Empty metadata without "name"
    mock_target = MagicMock()
    matrix_room = MatrixRoom()

    with patch.object(matrix_room, "create_room") as mock_create_room:

        with pytest.raises(Exception) as e:
            await matrix_room.create_representation(mock_repr_log, target_id=mock_target)

        assert str(e.value) == "name must be specified in metadata"


async def test_create_representation_success(
    logged_in_db_auth_controller: AuthenticatedController, test_database, test_device
):
    instance = test_database
    target = await MatrixReplicationTarget.objects.acreate(name="test_target")
    # target.matrixcredentials_set = MagicMock()
    creds = await test_device.matrixcredentials_set.aget()
    await sync_to_async(target.matrixcredentials_set.add)(creds)
    method = "some_method"  # method not used in tested function

    # Creating representation log for testing
    repr_log = await RepresentationLog.objects.acreate(
        instance=target,
        method=method,
        target=target,
        metadata=target.repr_metadata_props(),
    )

    matrix_room = MatrixRoom()
    matrix_room.create_room = AsyncMock(return_value="some_room_id")
    with patch("fractal_database.signals._accept_invite", new=AsyncMock()) as mock_accept_invite:
        meta_data = await matrix_room.create_representation(repr_log, "not_used")
    assert meta_data["room_id"] == "some_room_id"
    mock_accept_invite.assert_called()


async def test_create_representation_matrix_space(
    logged_in_db_auth_controller: AuthenticatedController, test_database, test_device
):
    instance = test_database
    target = await MatrixReplicationTarget.objects.acreate(name="test_target")
    creds = await test_device.matrixcredentials_set.aget()
    await sync_to_async(target.matrixcredentials_set.add)(creds)
    method = "some_method"  # method not used in tested function

    # Creating representation log for testing
    repr_log = await RepresentationLog.objects.acreate(
        instance=target,
        method=method,
        target=target,
        metadata=target.repr_metadata_props(),
    )

    matrix_space = MatrixSpace()

    # target.matrixcredentials_set.all.return_value = []  # Mocking empty matrix credentials

    result = await matrix_space.create_representation(repr_log, target)

    assert "room_id" in result


def test_create_representation_logs(
    logged_in_db_auth_controller: AuthenticatedController, test_database, test_device
):

    instance = test_database.primary_target()

    target = instance

    create_subspace = MatrixSubSpace.create_representation_logs(instance, target)
    assert len(create_subspace) == 2
    print(create_subspace[0].method)
    assert create_subspace[0].instance == instance


async def test_create_subspace_reprsentation(
    logged_in_db_auth_controller: AuthenticatedController, test_database, test_device
):
    instance = test_database
    target = await MatrixReplicationTarget.objects.acreate(name="test_target")
    creds = await test_device.matrixcredentials_set.aget()
    await sync_to_async(target.matrixcredentials_set.add)(creds)
    # Field id expects a number
    primary_target = await test_database.aprimary_target()
    target_id = primary_target.pk
    method = "some_method"  # method not used in tested function

    # Creating representation log for testing
    mock_repr_log = await RepresentationLog.objects.acreate(
        instance=target,
        method=method,
        target=primary_target,
        metadata=target.repr_metadata_props(),
    )
    subspace = MatrixSubSpace()

    result = await subspace.create_representation(repr_log=mock_repr_log, target_id=target_id)
