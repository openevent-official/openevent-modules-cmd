from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from openevent.cmd_sdk.model import MAX_TIMEOUT_MS

DEFAULT_MAX_PAYLOAD_BYTES = 16 * 1024 * 1024
MIN_PAYLOAD_BYTES = 16 * 1024
DEFAULT_OUTPUT_DIR = "cmd-worker-output"
DEFAULT_MAX_CONCURRENT_TASKS = 8
DEFAULT_TIMEOUT_MS = 300000
DEFAULT_FETCH_IDLE_SLEEP_MS = 100
DEFAULT_FETCH_ERROR_MAX_RETRIES = 3
DEFAULT_FETCH_ERROR_RETRY_SLEEP_MS = 1000


@dataclass(frozen=True)
class OpenEventConfig:
    addr: str
    max_payload_bytes: int = DEFAULT_MAX_PAYLOAD_BYTES


@dataclass(frozen=True)
class CmdWorkerConfig:
    open_event: OpenEventConfig
    principal: int
    token: str
    channel_ids: tuple[int, ...]
    output_dir: Path
    max_concurrent_tasks: int = DEFAULT_MAX_CONCURRENT_TASKS
    default_timeout_ms: int = DEFAULT_TIMEOUT_MS
    fetch_idle_sleep_ms: int = DEFAULT_FETCH_IDLE_SLEEP_MS
    fetch_error_max_retries: int = DEFAULT_FETCH_ERROR_MAX_RETRIES
    fetch_error_retry_sleep_ms: int = DEFAULT_FETCH_ERROR_RETRY_SLEEP_MS


class ConfigError(ValueError):
    pass


def load_config(path: str | Path) -> CmdWorkerConfig:
    try:
        import yaml
    except ImportError as exc:  # pragma: no cover
        raise ConfigError("PyYAML is required to load cmd-worker YAML config") from exc
    loaded = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise ConfigError("config root must be a mapping")
    return parse_config(loaded)


def parse_config(data: dict[str, Any]) -> CmdWorkerConfig:
    _reject_unknown(
        data,
        {
            "open_event",
            "principal",
            "token",
            "channel_ids",
            "output_dir",
            "max_concurrent_tasks",
            "default_timeout_ms",
            "fetch_idle_sleep_ms",
            "fetch_error_max_retries",
            "fetch_error_retry_sleep_ms",
        },
    )
    open_event_data = _required_dict(data, "open_event")
    _reject_unknown(open_event_data, {"addr", "max_payload_bytes"})
    max_payload_bytes = _optional_positive_int(open_event_data, "max_payload_bytes", DEFAULT_MAX_PAYLOAD_BYTES)
    if max_payload_bytes < MIN_PAYLOAD_BYTES:
        raise ConfigError(f"open_event.max_payload_bytes must be at least {MIN_PAYLOAD_BYTES} (16 KiB)")
    channel_ids = _parse_channel_ids(data.get("channel_ids"))
    default_timeout_ms = _optional_positive_int(data, "default_timeout_ms", DEFAULT_TIMEOUT_MS)
    if default_timeout_ms > MAX_TIMEOUT_MS:
        raise ConfigError(f"default_timeout_ms must be in 1..{MAX_TIMEOUT_MS}")
    return CmdWorkerConfig(
        open_event=OpenEventConfig(addr=_required_str(open_event_data, "addr"), max_payload_bytes=max_payload_bytes),
        principal=_required_positive_int(data, "principal"),
        token=_required_str(data, "token"),
        channel_ids=channel_ids,
        output_dir=Path(_optional_str(data, "output_dir", DEFAULT_OUTPUT_DIR)),
        max_concurrent_tasks=_optional_positive_int(data, "max_concurrent_tasks", DEFAULT_MAX_CONCURRENT_TASKS),
        default_timeout_ms=default_timeout_ms,
        fetch_idle_sleep_ms=_optional_positive_int(data, "fetch_idle_sleep_ms", DEFAULT_FETCH_IDLE_SLEEP_MS),
        fetch_error_max_retries=_optional_positive_int(data, "fetch_error_max_retries", DEFAULT_FETCH_ERROR_MAX_RETRIES),
        fetch_error_retry_sleep_ms=_optional_positive_int(data, "fetch_error_retry_sleep_ms", DEFAULT_FETCH_ERROR_RETRY_SLEEP_MS),
    )


def _parse_channel_ids(value: Any) -> tuple[int, ...]:
    if not isinstance(value, list) or not value:
        raise ConfigError("channel_ids must be a non-empty list")
    ids: list[int] = []
    seen: set[int] = set()
    for item in value:
        if not isinstance(item, int) or isinstance(item, bool) or item <= 0:
            raise ConfigError("channel_ids entries must be positive integers")
        if item in seen:
            raise ConfigError("channel_ids must not contain duplicates")
        seen.add(item)
        ids.append(item)
    return tuple(ids)


def _reject_unknown(data: dict[str, Any], allowed: set[str]) -> None:
    unknown = set(data) - allowed
    if unknown:
        raise ConfigError(f"unknown config fields: {', '.join(sorted(unknown))}")


def _required_dict(data: dict[str, Any], key: str) -> dict[str, Any]:
    if key not in data or not isinstance(data[key], dict):
        raise ConfigError(f"{key} must be a mapping")
    return data[key]


def _required_str(data: dict[str, Any], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value:
        raise ConfigError(f"{key} must be a non-empty string")
    return value


def _optional_str(data: dict[str, Any], key: str, default: str) -> str:
    value = data.get(key, default)
    if not isinstance(value, str) or not value:
        raise ConfigError(f"{key} must be a non-empty string")
    return value


def _required_positive_int(data: dict[str, Any], key: str) -> int:
    value = data.get(key)
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ConfigError(f"{key} must be a positive integer")
    return value


def _optional_positive_int(data: dict[str, Any], key: str, default: int) -> int:
    value = data.get(key, default)
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ConfigError(f"{key} must be a positive integer")
    return value
