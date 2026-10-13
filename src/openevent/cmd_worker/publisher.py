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
        data = output_read_result_input_to_dict(result)
        payload = dumps_payload(data)
        if data["status"] == "SUCCESS" and len(payload) > self.max_payload_bytes:
            data.update(
                status="READ_FAILED", bytes=0, eof=False, content_encoding="utf-8",
                content="", error_message="encoded output read result exceeds max_payload_bytes",
            )
            payload = dumps_payload(data)
        self._publish_payload(channel_id, request_principal, data["kind"], data["prev_seq"], payload)

    def _publish(self, channel_id, request_principal, data) -> None:
        self._publish_payload(channel_id, request_principal, data["kind"], data["prev_seq"], dumps_payload(data))

    def _publish_payload(self, channel_id, request_principal, kind, prev_seq, payload: bytes) -> None:
        # Reuse the validated bytes and one UUID for both attempts.
        if len(payload) > self.max_payload_bytes:
            raise PublishFatal(f"{kind} payload exceeds max_payload_bytes")
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
                    raise PublishFatal(f"failed to publish {kind} for prev_seq={prev_seq}") from exc
                LOG.warning("publish outcome unknown; retrying the same UUID", extra={"uuid": uuid, "kind": kind})
        raise AssertionError("publish attempts exhausted")
