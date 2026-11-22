from __future__ import annotations

import socket

from fritz_lan_dns.portscan import scan_port, scan_ports


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def test_scan_port_detects_open_socket() -> None:
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    port = int(server.getsockname()[1])
    try:
        assert scan_port("127.0.0.1", port=port, timeout=1.0) is True
        assert scan_ports(["127.0.0.1"], port=port, timeout=1.0) == {
            "127.0.0.1": True
        }
    finally:
        server.close()


def test_scan_port_closed_socket() -> None:
    port = _free_port()
    assert scan_port("127.0.0.1", port=port, timeout=0.2) is False


def test_scan_ports_handles_empty_and_duplicates() -> None:
    assert scan_ports([]) == {}
    assert scan_ports(["", None]) == {}  # type: ignore[list-item]
    port = _free_port()
    results = scan_ports(
        ["127.0.0.1", "127.0.0.1"], port=port, timeout=0.2, workers=2
    )
    assert results == {"127.0.0.1": False}
