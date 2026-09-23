from __future__ import annotations

import json



class ChannelValidationError(RuntimeError):
    pass


class ChannelResolver:
    def __init__(self, openevent_client, principal: int, token: str, channel_ids: tuple[int, ...]):
        self.openevent_client = openevent_client
        self.principal = principal
        self.token = token
        self.channel_ids = set(channel_ids)
        self._validated: set[int] = set()

    def validate_startup(self) -> None:
        from openevent.sdk.proto import openevent_pb2

        for channel_id in sorted(self.channel_ids):
            response = self.openevent_client.get_channel(self.principal, self.token, channel_id)
            channel = response.channel
            if channel.protocol != "cmd.v1":
                raise ChannelValidationError(f"channel {channel_id} protocol must be cmd.v1")
            if int(channel.visibility) == int(openevent_pb2.VISIBILITY_PUBLIC):
                raise ChannelValidationError(f"channel {channel_id} must not be public")
            if self.principal not in {int(member) for member in channel.members}:
                raise ChannelValidationError(f"worker principal is not a member of channel {channel_id}")
            _validate_description(channel.description, channel_id)
            self._validated.add(channel_id)

    def owns(self, channel_id: int) -> bool:
        return channel_id in self._validated


def _validate_description(description: str, channel_id: int) -> None:
    try:
        data = json.loads(description)
    except json.JSONDecodeError as exc:
        raise ChannelValidationError(f"channel {channel_id} description must be JSON") from exc
    if not isinstance(data, dict):
        raise ChannelValidationError(f"channel {channel_id} description must be a JSON object")
    if data.get("version") != "v1":
        raise ChannelValidationError(f"channel {channel_id} description.version must be v1")
    updated_at_ms = data.get("updated_at_ms")
    if not isinstance(updated_at_ms, int) or isinstance(updated_at_ms, bool) or updated_at_ms < 0:
        raise ChannelValidationError(f"channel {channel_id} description.updated_at_ms must be a non-negative integer")
    metadata = data.get("metadata", {})
    if metadata is not None and not isinstance(metadata, dict):
        raise ChannelValidationError(f"channel {channel_id} description.metadata must be an object")
