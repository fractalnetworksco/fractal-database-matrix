# Making Fractal Database Matrix Replication Asynchronous

So how I wanted it to work was to make replicate simply a matrix task. This raises a question though: If I'm kicking a task into Matrix, what room am I kicking it to? After a bit of thinking, I decided that I'd kick the task into the current device's room in whatever the current database is. So for the homerun api, it's whatever device room is created as an operation when creating the root database. However, there's another problem. Initially, we're not replicating to Matrix at all, so there won't be a device room for the current device. Obviously you can't kick any tasks if your Matrix Replication Channel hasn't applied any operations or replicated yet.

## The Solution -- Replicate Async

```python
async def replicate_async(self) -> None:
    # manually fetch the "root" database by getting the database
    # configured as the true current database
    try:
        config = await DatabaseConfig.objects.select_related(
            "current_db", "current_device"
        ).aget()
    except DatabaseConfig.DoesNotExist:
        logger.error("No database config found")
        return None

    root_database: "Database" = config.current_db
    current_device: "Device" = config.current_device

    # get origin channel for the root database
    origin_channel = await root_database.aorigin_channel()
    if not origin_channel:
        logger.warning("No origin channel found for root database")
        return None

    # get current device's room for the origin channel
    # this is the room that we'll kick the replicate_async task into
    membership = await root_database.device_memberships.aget(device=current_device)
    device_room = membership.metadata.get(str(origin_channel.id))

    # if device room isn't found on root database's origin channel, then
    # synchronously replicate the origin channel so that the room can be created.
    # once created, we can kick the replicate_async task into the current device's room
    if not device_room:
        await origin_channel.replicate()
        # now that replication has been done, device room should exist,
        # so recall this method to kick the replicate_async task into the device's room
        return await self.replicate_async()

    from fractal_database.replication.tasks import replicate_async

    task_labels = {
        "room_id": device_room,
    }

    try:
        # kick replicate task into the device's room
        await self.kick_task(
            replicate_async,
            str(self.id),
            self._meta.label_lower,
            task_labels=task_labels,
        )
    except SendTaskError as e:
        raise Exception(e.__cause__)
```

### Going through this line by line

I'm considering the "root" database to be the Database that has been written into the SQL Database. This is so we avoid any `threadlocal` current databases, etc. From there, I pull off the database and the current device from the config object.

```python
# manually fetch the "root" database by getting the database
# configured as the true current database
try:
    config = await DatabaseConfig.objects.select_related(
        "current_db", "current_device"
    ).aget()
except DatabaseConfig.DoesNotExist:
    logger.error("No database config found")
    return None

root_database: "Database" = config.current_db
current_device: "Device" = config.current_device
```

<hr />

The origin channel is the first channel that a database was replicated from. The origin channel is always a channel that isn't of type `LocalReplicationChannel`, so in our case, this will give us the first `MatrixReplicationChannel` for our root database.

```python
# get origin channel for the root database
origin_channel = await root_database.aorigin_channel()
if not origin_channel:
    logger.warning("No origin channel found for root database")
    return None
```

<hr />

From here, I need to get the device room that is on this `MatrixReplicationChannel`. When a `MatrixReplicationChannel` is created, there is always an operation to create a device room for the current device. This room_id is saved on the device membership of the device. So we need to fetch the current device's membership to the root database:

```python
# get current device's room for the origin channel
# this is the room that we'll kick the replicate_async task into
membership = await root_database.device_memberships.aget(device=current_device)
# the device room is saved in the metadata as {channel_uuid: !someroomid:matrixserver.org}
device_room = membership.metadata.get(str(origin_channel.id))
```

<hr />

There's a problem with this, however. What if we're just now creating the `MatrixReplicationChannel` for the root database, and its operations haven't been applied yet? In this case, there won't be a room_id in the membership for that channel yet.

What I do is simply ensure that replicate is called on the channel before attempting anything further. `origin_channel.replicate()` will cause all durable operations on the channel to be applied synchronously. One of those operations is to create a device subroom for the device, then save that created room_id onto the device membership. So, by the time `origin_channel.replicate()` finishes, the device will have a room. So I simply recall the same function we're in, which in this case will mean that the device_room will be returned which keeps us from recursing forever. *NOTE: The current device needs to be a member of whatever the current database is, which is usually set in the post_migrate signal*

```python
# if device room isn't found on root database's origin channel, then
# synchronously replicate the origin channel so that the room can be created.
# once created, we can kick the replicate_async task into the current device's room
if not device_room:
    await origin_channel.replicate()
    # now that replication has been done, device room should exist,
    # so recall this method to kick the replicate_async task into the device's room
    return await self.replicate_async()
```

<hr />

From here, now that we have a room to kick a task to, it's as simple as kicking the task into the room:

```python
from fractal_database.replication.tasks import replicate_async

task_labels = {
    "room_id": device_room,
}

try:
    # kick replicate task into the device's room
    await self.kick_task(
        replicate_async,
        str(self.id),
        self._meta.label_lower,
        task_labels=task_labels,
    )
except SendTaskError as e:
    raise Exception(e.__cause__)
```

<hr />

The task itself is pretty simple. Simply pass the channel uuid and channel type, and the task will fetch it from the database and call replicate on it.

```python
async def _replicate_async(channel_id: str, channel_type: str, **kwargs) -> None:
    from django.apps import apps

    # fetch the channel model based on the provided channel_type
    try:
        channel_class: "type[ReplicationChannel]" = apps.get_model(channel_type)  # type: ignore
    except Exception as e:
        logger.exception("Failed to find channel class for given type %s: %s", channel_type, e)
        return

    # avoid any django lazy loading by fetching foreign keys as well
    select_related_fields = [field.name for field in channel_class._get_relationship_fields()]
    try:
        channel = await channel_class.objects.select_related(*select_related_fields).aget(
            id=channel_id
        )
    except channel_class.DoesNotExist:
        logger.warning("Failed to find channel %s of type %s", channel_id, channel_type)
        return

    # runs all durable operations and then replicate to the respective rooms tied to the channel
    await channel.replicate()
```

The device process (the one launched with `homeserver device launch`) will pick up the task, and execute it. The device also mounts the same database so all of the `ReplicationLogs` and `DurableOperations` are shared. The device also mounts `~/.local/share/fractal/` so it can use the user's credentials to create rooms, etc.
