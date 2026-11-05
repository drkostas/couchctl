"""A TCP relay that connects from a chosen source address, for sdb to a set that trusts one host.

A Samsung set in developer mode accepts sdb only from the one "Host PC IP" entered on it. A machine
whose address on the network changed (another network, a new lease, a second interface) is then
refused, and changing the set's setting means walking to it with the remote. When the machine still
holds the trusted address (on another interface, or as an alias), this relay listens on the loopback
and opens each outgoing connection from that address, so `sdb connect 127.0.0.1:<port>` reaches the
set as the host it trusts.

    couchctl relay --from 192.0.2.243 --listen 26102 192.0.2.40:26101
    sdb connect 127.0.0.1:26102

It listens on 127.0.0.1 only, so nothing else on the network can use it, and runs until stopped.

Some firmware also refuses a direct `sdb connect` from the right address and accepts the same
connection through a relay. With no `--from` the connections start from this machine's usual address
for the set. `couchctl sideload` starts such a relay by itself when a direct connection fails but the
set's sdb port answers (`start` below), and stops it when the install is over.
"""
from __future__ import annotations

import socket
import threading


def _pipe(a: socket.socket, b: socket.socket) -> None:
    try:
        while data := a.recv(65536):
            b.sendall(data)
    except OSError:
        pass
    finally:
        for s in (a, b):
            try:
                s.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass


def _listen(port: int) -> socket.socket:
    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", port))
    srv.listen(8)
    srv.settimeout(1.0)
    return srv


def serve(source: str | None, listen_port: int, target: str, target_port: int, *,
          ready: threading.Event | None = None, stop: threading.Event | None = None,
          say=print) -> None:
    """Relay 127.0.0.1:`listen_port` to `target`:`target_port`, each outgoing connection opened from
    `source` (this machine's usual address for the target when None). Runs until `stop` is set
    (checked once a second) or forever. A connection the target refuses closes the client's
    connection and the relay keeps listening."""
    _run(_listen(listen_port), source, target, target_port, ready=ready, stop=stop, say=say)


class Running:
    """A relay started by `start`: where to connect, and how to stop it."""

    def __init__(self, port: int, thread: threading.Thread, stop: threading.Event):
        self.port, self._thread, self._stop = port, thread, stop

    @property
    def address(self) -> str:
        return f"127.0.0.1:{self.port}"

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(5)


def start(target: str, target_port: int, *, source: str | None = None, say=lambda s: None) -> Running:
    """Start a relay to `target`:`target_port` in a thread, on a free loopback port, and return once it
    listens. Call `stop()` on the result when done."""
    srv = _listen(0)
    port = srv.getsockname()[1]
    ready, stop = threading.Event(), threading.Event()
    thread = threading.Thread(target=_run, args=(srv, source, target, target_port),
                              kwargs={"ready": ready, "stop": stop, "say": say}, daemon=True)
    thread.start()
    ready.wait(5)
    return Running(port, thread, stop)


def _run(srv: socket.socket, source: str | None, target: str, target_port: int, *,
         ready: threading.Event | None, stop: threading.Event | None, say) -> None:
    port = srv.getsockname()[1]
    say(f"relay 127.0.0.1:{port} -> {target}:{target_port}" + (f" from {source}" if source else ""))
    if ready:
        ready.set()
    try:
        while not (stop and stop.is_set()):
            try:
                client, _ = srv.accept()
            except socket.timeout:
                continue
            out = socket.socket()
            try:
                if source:
                    out.bind((source, 0))
                out.connect((target, target_port))
            except OSError as e:
                say(f"could not reach {target}:{target_port}" + (f" from {source}" if source else "") + f": {e}")
                for s in (client, out):
                    s.close()
                continue
            client.settimeout(None)
            threading.Thread(target=_pipe, args=(client, out), daemon=True).start()
            threading.Thread(target=_pipe, args=(out, client), daemon=True).start()
    finally:
        srv.close()
