# Kept for backwards compatibility — delegates to audio.backend
from audio.backend import (
    create_null_sink,
    get_input_devices,
    get_output_devices,
    null_sink_exists,
    remove_null_sink,
    NULL_SINK_NAME,
)

__all__ = [
    "create_null_sink",
    "get_input_devices",
    "get_output_devices",
    "null_sink_exists",
    "remove_null_sink",
    "NULL_SINK_NAME",
]
