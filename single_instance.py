import socket
from pathlib import Path
from typing import Iterable, List, Optional

from PyQt5.QtCore import QLockFile

from config import CONFIG_DIR
from network.constants import AUDIO_PORT, PING_PORT

LOCK_FILE: Path = CONFIG_DIR / "instance.lock"

# UDP ports this client listens on; a second client can't share them
# (audio would go to only one of them, the ping responder fails to bind).
CLIENT_UDP_PORTS = (AUDIO_PORT, PING_PORT)


def acquire_instance_lock(path: Path = LOCK_FILE) -> Optional[QLockFile]:
    """Take the single-instance lock; None if another client holds it.

    The returned lock must be kept alive for the lifetime of the app.
    A lock left behind by a crashed client is detected (its PID is gone)
    and taken over.
    """
    lock = QLockFile(str(path))
    lock.setStaleLockTime(0)  # stale only when the owner process is gone
    if not lock.tryLock(100):
        return None
    return lock


def busy_udp_ports(ports: Iterable[int] = CLIENT_UDP_PORTS) -> List[int]:
    """Ports from ``ports`` that some other process already listens on.

    Catches clients that predate the lock file (v1.2.0 and older) and
    other programs using the same ports.
    """
    busy: List[int] = []
    for port in ports:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.bind(("0.0.0.0", port))
        except OSError:
            busy.append(port)
        finally:
            sock.close()
    return busy
