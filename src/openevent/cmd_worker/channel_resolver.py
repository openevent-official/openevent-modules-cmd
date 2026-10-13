from __future__ import annotations


class ChannelValidationError(RuntimeError):
    pass


class ChannelResolver:
    def __init__(self, openevent_client, principal: int, token: str, channel_ids: tuple[int, ...]):
        self.openevent_client = openevent_client
        self.principal = principal
        self.token = token
        self.channel_ids = set(channel_ids)

    def validate_startup(self) -> None:
        from openevent.sdk.proto import openevent_pb2

        for channel_id in sorted(self.channel_ids):
            response = self.openevent_client.get_channel(self.principal, self.token, channel_id)
            channel = response.channel
            if channel.protocol != "cmd.v3":
                raise ChannelValidationError(f"channel {channel_id} protocol must be cmd.v3")
            if int(channel.visibility) == int(openevent_pb2.VISIBILITY_PUBLIC):
                raise ChannelValidationError(f"channel {channel_id} must not be public")
            if self.principal not in {int(member) for member in channel.members}:
                raise ChannelValidationError(f"worker principal is not a member of channel {channel_id}")

    def owns(self, channel_id: int) -> bool:
        return channel_id in self.channel_ids
