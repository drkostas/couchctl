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


def serve(source: str, listen_port: int, target: str, target_port: int, *,
          ready: threading.Event | None = None, stop: threading.Event | None = None,
          say=print) -> None:
    """Relay 127.0.0.1:`listen_port` to `target`:`target_port`, each outgoing connection opened from
    `source`. Runs until `stop` is set (checked once a second) or forever. A connection the target
    refuses closes the client's connection and the relay keeps listening."""
    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", listen_port))
    srv.listen(8)
    srv.settimeout(1.0)
    say(f"relay 127.0.0.1:{listen_port} -> {target}:{target_port} from {source}")
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
                out.bind((source, 0))
                out.connect((target, target_port))
            except OSError as e:
                say(f"could not reach {target}:{target_port} from {source}: {e}")
                for s in (client, out):
                    s.close()
                continue
            client.settimeout(None)
            threading.Thread(target=_pipe, args=(client, out), daemon=True).start()
            threading.Thread(target=_pipe, args=(out, client), daemon=True).start()
    finally:
        srv.close()
