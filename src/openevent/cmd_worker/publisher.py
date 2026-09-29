from __future__ import annotations

import logging

import grpc

from openevent.cmd_sdk.codec import (
    dumps_payload, kill_result_input_to_dict, output_read_result_input_to_dict,
    run_accepted_to_dict, run_result_input_to_dict,
)

LOG = logging.getLogger(__name__)
_DEFINITIVE_FAILURES = frozenset({
    grpc.StatusCode.UNAUTHENTICATED, grpc.StatusCode.PERMISSION_DENIED,
    grpc.StatusCode.NOT_FOUND, grpc.StatusCode.INVALID_ARGUMENT,
    grpc.StatusCode.RESOURCE_EXHAUSTED, grpc.StatusCode.ABORTED,
})


class PublishFatal(RuntimeError):
    pass


class ReliablePublisher:
    def __init__(self, client, principal: int, token: str, max_payload_bytes: int):
        self.client = client
        self.principal = principal
        self.token = token
        self.max_payload_bytes = max_payload_bytes

    def publish_run_accepted(self, channel_id, request_principal, prev_seq) -> None:
        self._publish(channel_id, request_principal, run_accepted_to_dict(prev_seq))

    def publish_run_result(self, channel_id, request_principal, result) -> None:
        self._publish(channel_id, request_principal, run_result_input_to_dict(result))

    def publish_kill_result(self, channel_id, request_principal, result) -> None:
        self._publish(channel_id, request_principal, kill_result_input_to_dict(result))

    def publish_output_read_result(self, channel_id, request_principal, result) -> None:
        self._publish(channel_id, request_principal, output_read_result_input_to_dict(result))

    def _publish(self, channel_id, request_principal, data) -> None:
        # Freeze the timestamp, encoded payload and UUID for both attempts.
        payload = dumps_payload(data)
        if len(payload) > self.max_payload_bytes:
            raise PublishFatal(f"{data['kind']} payload exceeds max_payload_bytes")
        uuid = self.client.get_uuid()
        for attempt in range(2):
            try:
                self.client.publish_auto_seq(
                    principal=self.principal, token=self.token, channel_id=channel_id,
                    recipients=[request_principal], payload=payload, uuid=uuid,
                )
                return
            except grpc.RpcError as exc:
                if exc.code() == grpc.StatusCode.ALREADY_EXISTS:
                    return
                if attempt or exc.code() in _DEFINITIVE_FAILURES:
                    raise PublishFatal(f"failed to publish {data['kind']} for prev_seq={data['prev_seq']}") from exc
                LOG.warning("publish outcome unknown; retrying the same UUID", extra={"uuid": uuid, "kind": data['kind']})
        raise AssertionError("publish attempts exhausted")
