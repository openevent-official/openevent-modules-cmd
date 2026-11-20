import pytest

from openevent.cmd_sdk import CmdSDKError, parse_message
from openevent.sdk.proto.openevent_pb2 import EventMessage


@pytest.mark.parametrize("payload", [b'{"kind":[],"ts_ms":1}', b'{"kind":{},"ts_ms":1}'])
def test_parse_message_rejects_non_string_kind(payload):
    with pytest.raises(CmdSDKError) as error:
        parse_message(EventMessage(payload=payload))
    assert error.value.code == "INVALID_KIND"
