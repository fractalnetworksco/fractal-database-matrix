from sys import exit
from typing import Optional

from asgiref.sync import async_to_sync
from clicz import cli_method
from django.db import transaction
from fractal.cli.controllers.auth import AuthenticatedController
from fractal.matrix.async_client import get_homeserver_for_matrix_id
from fractal_database.utils import use_django
from nio import DiscoveryInfoError


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
        local_url: Optional[str] = None,
        **kwargs,
    ):
        """
        ---
        Args:
            homeserver_url: URL of the homeserver to replicate to.
            registration_token: Registration token for the homeserver. Necessary for registering your devices with this homeserver.
            confirm: Consent to replicating your data to the provided homeserver.
            set_as_origin: Set the homeserver as your current database's origin.
            local_url: Local URL for the homeserver (prefer using this url for faster replication).
        """
        if not confirm:
            res = input(
                f"Are you sure you want to replicate your data to {homeserver_url}? (yes/no): "
            )
            if res.lower() != "yes":
                exit(0)

            # prompt the user for their login information for the homeserver
            self._login(homeserver_url)

        if not self.check_if_user_is_authenticated():
            print("You must be logged in to replicate your data.")
            exit(1)

        # attempt to get discovery info for the homeserver since the user could have provided a TLD
        client = self.create_matrix_client()
        res = async_to_sync(client.discovery_info)()
        if isinstance(res, DiscoveryInfoError):
            print(
                f"Tried to get discovery info for {homeserver_url} but failed. Will use the provided URL."
            )
        else:
            homeserver_url = res.homeserver_url

        from fractal_database.models import Database, Device, LocalReplicationChannel
        from fractal_database_matrix.models import (
            MatrixHomeserver,
            MatrixReplicationChannel,
        )

        current_database = Database.current_db()
        current_device = Device.current_device()

        try:
            homeserver = MatrixHomeserver.objects.get(url=homeserver_url)
            if not homeserver.replication_enabled:
                homeserver.update(replication_enabled=True, local_url=local_url)
        except MatrixHomeserver.DoesNotExist:
            with transaction.atomic():
                # create the homeserver
                homeserver = MatrixHomeserver.objects.create(
                    name=f"Synapse@{homeserver_url}",
                    url=homeserver_url,
                    type=MatrixHomeserver.__name__,
                    registration_token=registration_token,
                    parent_db=current_database,
                    replication_enabled=True,
                    local_url=local_url,
                )
                current_device.add_membership(homeserver)
        else:
            # add membership to the homeserver
            if not current_device.has_membership(homeserver):
                current_device.add_membership(homeserver)

        # now that the user is logged into a matrix server, ensure that the current device is owned
        # by the user
        if not current_device.owner_matrix_id:
            current_device.update(owner_matrix_id=self.matrix_id)
            # current_device.owner_matrix_id = self.matrix_id
            # current_device.save()

        # create the matrix channel for the homeserver if it doesn't exist
        try:
            current_db_matrix_channel = MatrixReplicationChannel.objects.get(
                homeserver=homeserver, database=current_database
            )
        except MatrixReplicationChannel.DoesNotExist:
            current_db_matrix_channel = current_database.create_channel(
                MatrixReplicationChannel, homeserver=homeserver, source=True, target=True
            )

        # fetch all of the groups (excluding the current database since it already has a primary target)
        databases = Database.objects.exclude(pk=current_database.pk)
        with transaction.atomic():
            for database in databases:
                database.set_origin_channel(current_db_matrix_channel)

                if not MatrixReplicationChannel.objects.filter(
                    homeserver=homeserver, database=database, source=True, target=True
                ).exists():
                    # create a matrix channel
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
