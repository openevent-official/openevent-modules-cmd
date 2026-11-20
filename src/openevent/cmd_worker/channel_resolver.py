from __future__ import annotations

from .config import CmdWorkerConfig


class ChannelValidationError(RuntimeError):
    pass


def validate_channels(openevent_client, config: CmdWorkerConfig) -> None:
    from openevent.sdk.proto import openevent_pb2

    for channel_id in config.channel_ids:
        response = openevent_client.get_channel(config.principal, config.token, channel_id)
        channel = response.channel
        if channel.protocol != "cmd.v4":
            raise ChannelValidationError(f"channel {channel_id} protocol must be cmd.v4")
        if channel.visibility == openevent_pb2.VISIBILITY_PUBLIC:
            raise ChannelValidationError(f"channel {channel_id} must not be public")
        if config.principal not in channel.members:
            raise ChannelValidationError(f"worker principal is not a member of channel {channel_id}")
