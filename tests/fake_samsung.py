"""A fake Samsung television: the HTTP API on one port and the remote channel (plain ws) on another."""
import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from websockets.asyncio.server import serve


class FakeTV:
    def __init__(self, token_auth=True, allow=True, issue_token="tok-123456"):
        self.apps = {"app.unwanted": {"running": False, "visible": False}, "app.wanted": {"running": False, "visible": False}}
        self.launched = []
        self.keys = []
        self.tokens_seen = []
        self.token_auth = token_auth
        self.allow = allow
        self.issue_token = issue_token
        tv = self

        class Api(BaseHTTPRequestHandler):
            def _send(self, code, body):
                data = json.dumps(body).encode()
                self.send_response(code)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                if self.path == "/api/v2/":
                    return self._send(200, {"device": {"name": "Fake TV", "wifiMac": "AA:BB:CC:DD:EE:FF",
                                                       "TokenAuthSupport": "true" if tv.token_auth else "false"}})
                app = self.path.rsplit("/", 1)[-1]
                if app in tv.apps:
                    return self._send(200, dict(tv.apps[app], id=app))
                return self._send(404, {})

            def do_POST(self):
                app = self.path.rsplit("/", 1)[-1]
                self.rfile.read(int(self.headers.get("content-length") or 0))
                if app not in tv.apps:
                    return self._send(404, {})
                for a in tv.apps.values():
                    a["visible"] = False
                tv.apps[app] = {"running": True, "visible": True}
                tv.launched.append(app)
                return self._send(200, {})

            def log_message(self, *_):
                pass

        self.http = ThreadingHTTPServer(("127.0.0.1", 0), Api)
        threading.Thread(target=self.http.serve_forever, daemon=True).start()
        self.api_port = self.http.server_address[1]

        self.loop = asyncio.new_event_loop()
        ready = threading.Event()

        async def handler(ws):
            q = parse_qs(urlparse(ws.request.path).query)
            token = (q.get("token") or [None])[0]
            tv.tokens_seen.append(token)
            if tv.token_auth and token != tv.issue_token:
                if not tv.allow:
                    await ws.send(json.dumps({"event": "ms.channel.unauthorized"}))
                    return
                await ws.send(json.dumps({"event": "ms.channel.connect", "data": {"token": tv.issue_token}}))
            else:
                await ws.send(json.dumps({"event": "ms.channel.connect", "data": {}}))
            async for msg in ws:
                d = json.loads(msg)
                tv.keys.append(d["params"]["DataOfCmd"])

        async def start():
            self.ws = await serve(handler, "127.0.0.1", 0)
            self.remote_port = self.ws.sockets[0].getsockname()[1]
            ready.set()
            await asyncio.Future()

        threading.Thread(target=self.loop.run_until_complete, args=(start(),), daemon=True).start()
        ready.wait(5)

    def device(self, name="tv", redirects=None):
        from couchctl.config import Device

        return Device(name=name, kind="samsung", host="127.0.0.1", api_port=self.api_port,
                      remote_scheme="ws", remote_port=self.remote_port, redirects=redirects or [])

    def close(self):
        self.http.shutdown()
        self.loop.call_soon_threadsafe(self.ws.close)
