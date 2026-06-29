# Kept for backwards compatibility — delegates to audio.backend
from audio.backend import (
    create_loopback,
    create_null_sink,
    get_input_devices,
    get_output_devices,
    loopback_exists,
    null_sink_exists,
    remove_loopback,
    remove_null_sink,
    LOOPBACK_NAME,
    NULL_SINK_NAME,
)

__all__ = [
    "create_loopback",
    "create_null_sink",
    "get_input_devices",
    "get_output_devices",
    "loopback_exists",
    "null_sink_exists",
    "remove_loopback",
    "remove_null_sink",
    "LOOPBACK_NAME",
    "NULL_SINK_NAME",
]
