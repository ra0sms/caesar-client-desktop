# Kept for backwards compatibility — delegates to audio.backend
from audio.backend import get_input_devices, get_output_devices

__all__ = ["get_input_devices", "get_output_devices"]
