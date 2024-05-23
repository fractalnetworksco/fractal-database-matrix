from sys import exit
from typing import Optional

from clicz import cli_method
from django.db import transaction
from fractal.cli.controllers.auth import AuthenticatedController
from fractal_database.utils import use_django


class ReplicationController(AuthenticatedController):
    PLUGIN_NAME = "replicate"

    @use_django
    @cli_method
    def to(
        self,
        homeserver_url: str,
        registration_token: str,
        confirm: bool = False,
        set_as_origin: bool = False,
        **kwargs,
    ):
        """
        ---
        Args:
            homeserver_url: URL of the homeserver to replicate to.
            registration_token: Registration token for the homeserver. Necessary for registering your devices with this homeserver.
            confirm: Consent to replicating your data to the provided homeserver.
            set_as_origin: Set the homeserver as your current database's origin.
        """
        if not confirm:
            res = input(
                f"Are you sure you want to replicate your data to {homeserver_url}? (yes/no): "
            )
            if res.lower() != "yes":
                exit(0)

            # prompt the user for their login information for the homeserver
            self._login(homeserver_url)

        from fractal_database.models import Database, Device, LocalReplicationChannel
        from fractal_database_matrix.models import (
            MatrixHomeserver,
            MatrixReplicationChannel,
        )

        current_database = Database.current_db()
        current_device = Device.current_device()

        try:
            homeserver = MatrixHomeserver.objects.get(url=homeserver_url)
        except MatrixHomeserver.DoesNotExist:
            homeserver = MatrixHomeserver.objects.create(
                name="Synapse",
                url=homeserver_url,
                type=MatrixHomeserver.__name__,
                registration_token=registration_token,
            )

        # now that the user is logged into a matrix server, ensure that the current device is owned
        # by the user
        if not current_device.owner_matrix_id:
            current_device.owner_matrix_id = self.matrix_id
            current_device.save()

        # create the matrix replication channel for the current database
        # NOTE: this needs to happen first before we go and create the group channels since
        # their representations may depend on the current database having its first matrix channel
        with transaction.atomic():
            current_db_matrix_channel = current_database.create_channel(
                MatrixReplicationChannel,
                set_as_origin=set_as_origin,
                homeserver=homeserver,
                source=True,
                target=True,
            )
            local_channel = LocalReplicationChannel.objects.get(database=current_database)
            current_db_matrix_channel.replay_replication_logs_from(local_channel)

        # fetch all of the groups (excluding the current database since it already has a primary target)
        databases = Database.objects.exclude(pk=current_database.pk)
        with transaction.atomic():
            for database in databases:
                # create a group target for each group that doesn't have one
                matrix_channel = database.create_channel(
                    MatrixReplicationChannel,
                    homeserver=homeserver,
                    source=True,
                    target=True,
                )

                # attempt to fetch the dummy target for the group
                try:
                    local_channel = LocalReplicationChannel.objects.get(database=database)
                except LocalReplicationChannel.DoesNotExist:
                    pass
                else:
                    # replay the replication logs from the dummy target
                    # this replicates any existing data in the group to the new target
                    matrix_channel.replay_replication_logs_from(local_channel)


Controller = ReplicationController
