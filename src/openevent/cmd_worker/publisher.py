from __future__ import annotations

import logging
import inspect
import time
from dataclasses import dataclass

from openevent.cmd_sdk import parse_message
from openevent.cmd_sdk.client import CmdProtocolClient
from openevent.cmd_sdk.errors import CmdSDKError
from openevent.cmd_sdk.model import (
    KIND_KILL_RESULT,
    KIND_OUTPUT_READ_RESULT,
    KIND_RUN_ACCEPTED,
    KIND_RUN_RESULT,
    CmdKillResult,
    CmdKillResultInput,
    CmdOutputReadResult,
    CmdOutputReadResultInput,
    CmdRunAccepted,
    CmdRunResult,
    CmdRunResultInput,
)

LOG = logging.getLogger(__name__)


class PublishFatal(RuntimeError):
    pass


@dataclass(frozen=True)
class Published:
    seq: int | None
    kind: str
    prev_seq: int


class ReliablePublisher:
    def __init__(
        self,
        protocol_client: CmdProtocolClient,
        openevent_client,
        principal: int,
        token: str,
        channel_ids: tuple[int, ...],
        fetch_error_max_retries: int,
        fetch_error_retry_sleep_ms: int,
    ):
        self.protocol_client = protocol_client
        self.openevent_client = openevent_client
        self.principal = principal
        self.token = token
        self.channel_ids = channel_ids
        self.fetch_error_max_retries = fetch_error_max_retries
        self.fetch_error_retry_sleep_ms = fetch_error_retry_sleep_ms
        self.fetch_supports_channels = _fetch_supports_channels(openevent_client)

    def publish_run_accepted(self, channel_id: int, request_principal: int, prev_seq: int, check_from_seq: int) -> Published:
        return self._publish_with_check(
            channel_id,
            request_principal,
            KIND_RUN_ACCEPTED,
            prev_seq,
            check_from_seq,
            lambda: self.protocol_client.publish_run_accepted(self.principal, self.token, channel_id, request_principal, prev_seq),
        )

    def publish_run_result(self, channel_id: int, request_principal: int, result: CmdRunResultInput, check_from_seq: int) -> Published:
        return self._publish_with_check(
            channel_id,
            request_principal,
            KIND_RUN_RESULT,
            result.prev_seq,
            check_from_seq,
            lambda: self.protocol_client.publish_run_result(self.principal, self.token, channel_id, request_principal, result),
        )

    def publish_kill_result(self, channel_id: int, request_principal: int, result: CmdKillResultInput, check_from_seq: int) -> Published:
        return self._publish_with_check(
            channel_id,
            request_principal,
            KIND_KILL_RESULT,
            result.prev_seq,
            check_from_seq,
            lambda: self.protocol_client.publish_kill_result(self.principal, self.token, channel_id, request_principal, result),
        )

    def publish_output_read_result(
        self,
        channel_id: int,
        request_principal: int,
        result: CmdOutputReadResultInput,
        check_from_seq: int,
    ) -> Published:
        return self._publish_with_check(
            channel_id,
            request_principal,
            KIND_OUTPUT_READ_RESULT,
            result.prev_seq,
            check_from_seq,
            lambda: self.protocol_client.publish_output_read_result(self.principal, self.token, channel_id, request_principal, result),
        )

    def _publish_with_check(self, channel_id: int, request_principal: int, kind: str, prev_seq: int, check_from_seq: int, publish_call) -> Published:
        del request_principal
        try:
            return Published(seq=publish_call(), kind=kind, prev_seq=prev_seq)
        except Exception as exc:  # pragma: no cover - exercised with integration tests
            LOG.warning("publish returned error; checking whether message was committed", extra={"kind": kind, "prev_seq": prev_seq, "error": repr(exc)})
        existing = self._find_existing(channel_id, kind, prev_seq, max(1, check_from_seq))
        if existing is not None:
            return Published(seq=existing, kind=kind, prev_seq=prev_seq)
        try:
            return Published(seq=publish_call(), kind=kind, prev_seq=prev_seq)
        except Exception as exc:
            raise PublishFatal(f"failed to publish {kind} for prev_seq={prev_seq}") from exc

    def _find_existing(self, channel_id: int, kind: str, prev_seq: int, from_seq: int) -> int | None:
        current = max(1, from_seq)
        while True:
            response = self._fetch_with_retries(current)
            found: int | None = None
            for message in response.messages:
                if int(message.channel_id) != channel_id:
                    continue
                try:
                    parsed = parse_message(message)
                except CmdSDKError:
                    continue
                payload = parsed.payload
                if _matches_kind_prev(payload, kind, prev_seq):
                    if found is not None:
                        raise PublishFatal(f"duplicate {kind} for prev_seq={prev_seq}")
                    found = parsed.seq
            if found is not None:
                return found
            next_seq = int(response.next_seq)
            last_seq = int(response.last_seq)
            if next_seq > last_seq:
                return None
            if next_seq <= current:
                raise PublishFatal("Fetch did not advance while checking published message")
            current = next_seq

    def _fetch_with_retries(self, from_seq: int):
        attempts = 0
        while True:
            try:
                kwargs = {"from_seq": from_seq, "limit": 1000, "only_my_recipient": False}
                if self.fetch_supports_channels:
                    kwargs["channels"] = self.channel_ids
                return self.openevent_client.fetch(self.principal, self.token, **kwargs)
            except Exception:
                attempts += 1
                if attempts > self.fetch_error_max_retries:
                    raise
                time.sleep(self.fetch_error_retry_sleep_ms / 1000)


def _matches_kind_prev(payload, kind: str, prev_seq: int) -> bool:
    if kind == KIND_RUN_ACCEPTED and isinstance(payload, CmdRunAccepted):
        return payload.prev_seq == prev_seq
    if kind == KIND_RUN_RESULT and isinstance(payload, CmdRunResult):
        return payload.prev_seq == prev_seq
    if kind == KIND_KILL_RESULT and isinstance(payload, CmdKillResult):
        return payload.prev_seq == prev_seq
    if kind == KIND_OUTPUT_READ_RESULT and isinstance(payload, CmdOutputReadResult):
        return payload.prev_seq == prev_seq
    return False


def _fetch_supports_channels(openevent_client) -> bool:
    try:
        return "channels" in inspect.signature(openevent_client.fetch).parameters
    except (AttributeError, TypeError, ValueError):
        return False
