"""
Network constants for CAESAR Desktop Client.

All ports and magic protocol strings are defined here to avoid
magic numbers scattered across the codebase.
"""

# ─── UDP Ports ────────────────────────────────────────────────────────────────

# Audio stream (RTP/Opus)
AUDIO_PORT = 5000

# PTT control
PTT_PORT = 5001

# Server ping / monitoring
PING_PORT = 5002

# CAT bridge (FLRig)
CAT_PORT = 3001

# ─── Ping Protocol ─────────────────────────────────────────────────────────────

MAGIC_PING_REQUEST = b"PING_REQUEST"
MAGIC_PING_RESPONSE = b"PING_RESPONSE"