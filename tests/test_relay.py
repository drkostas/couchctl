"""The relay passes bytes both ways and opens its outgoing connection from the source address."""
from __future__ import annotations

import socket
import threading

from couchctl import relay


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _echo_server(seen: list):
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)

    def run():
        conn, peer = srv.accept()
        seen.append(peer[0])
        conn.sendall(conn.recv(1024).upper())
        conn.close()
        srv.close()

    threading.Thread(target=run, daemon=True).start()
    return srv.getsockname()[1]


def test_relays_both_ways_from_the_source_address():
    seen: list = []
    target = _echo_server(seen)
    port = _free_port()
    ready, stop, said = threading.Event(), threading.Event(), []
    t = threading.Thread(target=relay.serve, args=("127.0.0.1", port, "127.0.0.1", target),
                         kwargs={"ready": ready, "stop": stop, "say": said.append}, daemon=True)
    t.start()
    assert ready.wait(5)
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=5) as c:
            c.sendall(b"hello")
            assert c.recv(1024) == b"HELLO"
        assert seen == ["127.0.0.1"]
        assert said[0].startswith(f"relay 127.0.0.1:{port} -> 127.0.0.1:{target}")
    finally:
        stop.set()
        t.join(5)
    assert not t.is_alive()


def test_an_unreachable_target_closes_the_client_and_keeps_listening():
    port, dead = _free_port(), _free_port()
    ready, stop, said = threading.Event(), threading.Event(), []
    t = threading.Thread(target=relay.serve, args=("127.0.0.1", port, "127.0.0.1", dead),
                         kwargs={"ready": ready, "stop": stop, "say": said.append}, daemon=True)
    t.start()
    assert ready.wait(5)
    try:
        for _ in range(2):
            with socket.create_connection(("127.0.0.1", port), timeout=5) as c:
                assert c.recv(16) == b""
        assert sum("could not reach" in s for s in said) == 2
    finally:
        stop.set()
        t.join(5)
