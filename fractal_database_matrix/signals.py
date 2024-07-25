import logging
from typing import TYPE_CHECKING

from django.db import transaction
from django.db.models.signals import post_save
from django.dispatch import receiver
from fractal.cli.controllers.auth import AuthenticatedController

from .models import MatrixHomeserver, MatrixReplicationChannel

if TYPE_CHECKING:
    from fractal_database.models import Database

logger = logging.getLogger(__name__)


@receiver(post_save, sender=MatrixHomeserver)
def create_replication_channel_for_new_matrix_homeserver(
    sender: type["MatrixHomeserver"],
    instance: "MatrixHomeserver",
    created: bool,
    raw: bool,
    **kwargs,
):
    from fractal_database.models import Database, Device, LocalReplicationChannel

    if not created or raw:
        return
    if not instance.replication_enabled:
        return
    if not transaction.get_connection().in_atomic_block:
        raise Exception("Not in transaction")

    creds = AuthenticatedController().get_creds()
    if not creds:
        logger.warning("Will not create Matrix replication channel without being logged in")
        return

    # since object is potentially being loaded in via fixture,
    # we need to reload it to get the latest state
    homeserver = sender.objects.get(pk=instance.pk)

    try:
        current_db = Database.current_db()
    except Database.DoesNotExist:
        logger.warning(
            "No current database found. Skipping creation of Matrix replication channel."
        )
        return

    if not current_db.origin_channel():
        # create the channel and set it as the origin channel
        # for both the database and current device
        channel = current_db.create_channel(
            MatrixReplicationChannel,
            homeserver=homeserver,
            source=True,
            target=True,
        )
        current_device = Device.current_device()
        current_device.set_origin_channel(channel)
    else:
        # simply creating a new channel for the homeserver
        channel = current_db.create_channel(
            MatrixReplicationChannel,
            homeserver=homeserver,
            source=True,
            target=True,
        )

    # replay the logs from the local channel onto the new channel (this will replicate everything to the new channel)
    local_channel = LocalReplicationChannel.objects.get(database=current_db)
    channel.replay_replication_logs_from(local_channel)


def create_matrix_replication_target_for_new_database(
    sender: "Database", instance: "Database", created: bool, raw: bool, **kwargs
):
    if not created or raw:
        return

    homeservers = MatrixHomeserver.objects.all()
    if not homeservers:
        return

    for homeserver in homeservers:
        if not homeserver.replication_enabled:
            continue
        instance.create_channel(
            MatrixReplicationChannel,
            homeserver=homeserver,
            source=True,
            target=True,
        )
