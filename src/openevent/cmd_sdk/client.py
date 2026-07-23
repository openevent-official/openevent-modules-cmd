from __future__ import annotations

from dataclasses import dataclass

from .codec import (
    dict_to_model,
    dumps_payload,
    kill_request_input_to_dict,
    kill_result_input_to_dict,
    loads_payload,
    output_read_request_input_to_dict,
    output_read_result_input_to_dict,
    run_accepted_to_dict,
    run_request_input_to_dict,
    run_result_input_to_dict,
)
from .model import (
    CmdKillRequestInput,
    CmdKillResultInput,
    CmdOutputReadRequestInput,
    CmdOutputReadResultInput,
    CmdRunRequestInput,
    CmdRunResultInput,
    ParsedMessage,
)


@dataclass(frozen=True)
class CmdProtocolClient:
    openevent_client: object

    def publish_run_request(self, principal: int, token: str, channel_id: int, req: CmdRunRequestInput) -> int:
        result = self.openevent_client.publish_auto_seq(
            principal=principal,
            token=token,
            channel_id=channel_id,
            recipients=[],
            payload=dumps_payload(run_request_input_to_dict(req)),
        )
        return int(result.seq)

    def publish_kill_request(self, principal: int, token: str, channel_id: int, req: CmdKillRequestInput) -> int:
        result = self.openevent_client.publish_auto_seq(
            principal=principal,
            token=token,
            channel_id=channel_id,
            recipients=[],
            payload=dumps_payload(kill_request_input_to_dict(req)),
        )
        return int(result.seq)

    def publish_output_read_request(self, principal: int, token: str, channel_id: int, req: CmdOutputReadRequestInput) -> int:
        result = self.openevent_client.publish_auto_seq(
            principal=principal,
            token=token,
            channel_id=channel_id,
            recipients=[],
            payload=dumps_payload(output_read_request_input_to_dict(req)),
        )
        return int(result.seq)

    def publish_run_accepted(self, principal: int, token: str, channel_id: int, request_principal: int, prev_seq: int) -> int:
        result = self.openevent_client.publish_auto_seq(
            principal=principal,
            token=token,
            channel_id=channel_id,
            recipients=[request_principal],
            payload=dumps_payload(run_accepted_to_dict(prev_seq)),
        )
        return int(result.seq)

    def publish_run_result(self, principal: int, token: str, channel_id: int, request_principal: int, result_payload: CmdRunResultInput) -> int:
        result = self.openevent_client.publish_auto_seq(
            principal=principal,
            token=token,
            channel_id=channel_id,
            recipients=[request_principal],
            payload=dumps_payload(run_result_input_to_dict(result_payload)),
        )
        return int(result.seq)

    def publish_kill_result(self, principal: int, token: str, channel_id: int, request_principal: int, result_payload: CmdKillResultInput) -> int:
        result = self.openevent_client.publish_auto_seq(
            principal=principal,
            token=token,
            channel_id=channel_id,
            recipients=[request_principal],
            payload=dumps_payload(kill_result_input_to_dict(result_payload)),
        )
        return int(result.seq)

    def publish_output_read_result(
        self,
        principal: int,
        token: str,
        channel_id: int,
        request_principal: int,
        result_payload: CmdOutputReadResultInput,
    ) -> int:
        result = self.openevent_client.publish_auto_seq(
            principal=principal,
            token=token,
            channel_id=channel_id,
            recipients=[request_principal],
            payload=dumps_payload(output_read_result_input_to_dict(result_payload)),
        )
        return int(result.seq)

def create_client(openevent_client: object) -> CmdProtocolClient:
    return CmdProtocolClient(openevent_client=openevent_client)


def parse_message(message) -> ParsedMessage:
    payload = dict_to_model(loads_payload(message.payload))
    return ParsedMessage(
        seq=int(message.seq),
        channel_id=int(message.channel_id),
        principal=int(message.principal),
        recipients=tuple(int(value) for value in message.recipients),
        payload=payload,
    )
