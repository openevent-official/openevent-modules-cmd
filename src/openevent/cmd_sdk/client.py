from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Iterable

if TYPE_CHECKING:
    from openevent.sdk.proto import openevent_pb2

from .codec import (
    dict_to_model, dumps_payload, loads_payload,
    kill_request_input_to_dict, kill_result_input_to_dict,
    output_read_request_input_to_dict, output_read_result_input_to_dict,
    run_accepted_to_dict, run_request_input_to_dict, run_result_input_to_dict,
)
from .model import (
    CmdKillRequestInput, CmdKillResultInput, CmdOutputReadRequestInput,
    CmdOutputReadResultInput, CmdRunRequestInput, CmdRunResultInput, ParsedMessage,
)


@dataclass(frozen=True)
class CmdProtocolClient:
    openevent_client: object

    def _publish(self, principal, token, channel_id, data, recipients, uuid, object_keys=()):
        payload = dumps_payload(data)
        if uuid is None:
            uuid = self.openevent_client.get_uuid()
        return int(self.openevent_client.publish_auto_seq(
            principal=principal, token=token, channel_id=channel_id,
            recipients=recipients, payload=payload, uuid=uuid, object_keys=object_keys,
        ).seq)

    def publish_run_request(
        self, principal: int, token: str, channel_id: int, req: CmdRunRequestInput, *,
        object_keys: Iterable[openevent_pb2.ObjectKey] = (), uuid: int | None = None,
    ) -> int:
        return self._publish(principal, token, channel_id, run_request_input_to_dict(req), [], uuid, object_keys)

    def publish_kill_request(self, principal: int, token: str, channel_id: int, req: CmdKillRequestInput, *, uuid: int | None = None) -> int:
        return self._publish(principal, token, channel_id, kill_request_input_to_dict(req), [], uuid)

    def publish_output_read_request(self, principal: int, token: str, channel_id: int, req: CmdOutputReadRequestInput, *, uuid: int | None = None) -> int:
        return self._publish(principal, token, channel_id, output_read_request_input_to_dict(req), [], uuid)

    def publish_run_accepted(self, principal: int, token: str, channel_id: int, request_principal: int, prev_seq: int, *, uuid: int | None = None) -> int:
        return self._publish(principal, token, channel_id, run_accepted_to_dict(prev_seq), [request_principal], uuid)

    def publish_run_result(self, principal: int, token: str, channel_id: int, request_principal: int, result_payload: CmdRunResultInput, *, uuid: int | None = None) -> int:
        return self._publish(principal, token, channel_id, run_result_input_to_dict(result_payload), [request_principal], uuid)

    def publish_kill_result(self, principal: int, token: str, channel_id: int, request_principal: int, result_payload: CmdKillResultInput, *, uuid: int | None = None) -> int:
        return self._publish(principal, token, channel_id, kill_result_input_to_dict(result_payload), [request_principal], uuid)

    def publish_output_read_result(self, principal: int, token: str, channel_id: int, request_principal: int, result_payload: CmdOutputReadResultInput, *, uuid: int | None = None) -> int:
        return self._publish(principal, token, channel_id, output_read_result_input_to_dict(result_payload), [request_principal], uuid)


def create_client(openevent_client: object) -> CmdProtocolClient:
    return CmdProtocolClient(openevent_client=openevent_client)


def parse_message(message) -> ParsedMessage:
    payload = dict_to_model(loads_payload(message.payload))
    return ParsedMessage(
        seq=int(message.seq), channel_id=int(message.channel_id),
        principal=int(message.principal),
        recipients=tuple(int(value) for value in message.recipients), payload=payload,
        object_keys=tuple(message.object_keys),
    )
