from __future__ import annotations

import socket
from concurrent.futures import ThreadPoolExecutor
from typing import Iterable

HTTP_PORT = 80


def scan_port(ip: str, port: int = HTTP_PORT, timeout: float = 1.0) -> bool:
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            return True
    except OSError:
        return False


def scan_ports(
    ips: Iterable[str],
    *,
    port: int = HTTP_PORT,
    timeout: float = 1.0,
    workers: int = 16,
) -> dict[str, bool]:
    targets = sorted({ip for ip in ips if ip})
    if not targets:
        return {}
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        results = list(pool.map(lambda ip: scan_port(ip, port, timeout), targets))
    return dict(zip(targets, results))
