#!/usr/bin/env python3
"""Hackathon agent chat relay (stdlib only).

Server (one person runs it, others reach it via LAN IP or ngrok):
  python3 relay.py serve --port 8787 --token SECRET

Client (every agent / participant):
  export RELAY_URL=http://HOST:8787 RELAY_TOKEN=SECRET RELAY_NAME=alice
  python3 relay.py agents
  python3 relay.py send bob "hello"          # direct message
  python3 relay.py send all "hello everyone" # broadcast
  python3 relay.py inbox                     # show new messages for RELAY_NAME
  python3 relay.py inbox --all               # show full history for RELAY_NAME
  python3 relay.py listen                    # poll and print new messages forever
"""
import argparse
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

LOG_PATH = os.environ.get("RELAY_LOG", "relay_messages.jsonl")
_lock = threading.Lock()


def _load():
    if not os.path.exists(LOG_PATH):
        return []
    with open(LOG_PATH, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


# ---------------- server ----------------

def make_handler(token):
    class H(BaseHTTPRequestHandler):
        def _send(self, code, obj):
            body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _auth(self):
            if token and self.headers.get("Authorization") != f"Bearer {token}":
                self._send(401, {"error": "bad token"})
                return False
            return True

        def do_GET(self):
            if not self._auth():
                return
            u = urllib.parse.urlparse(self.path)
            q = urllib.parse.parse_qs(u.query)
            if u.path == "/agents":
                with _lock:
                    names = sorted({m["from"] for m in _load()} | {m["to"] for m in _load() if m["to"] != "all"})
                return self._send(200, {"agents": names})
            if u.path == "/inbox":
                name = q.get("name", [""])[0]
                since = int(q.get("since", ["0"])[0])
                with _lock:
                    msgs = [m for m in _load() if m["id"] > since and (m["to"] == name or m["to"] == "all")]
                return self._send(200, {"messages": msgs})
            self._send(404, {"error": "not found"})

        def do_POST(self):
            if not self._auth():
                return
            if urllib.parse.urlparse(self.path).path != "/send":
                return self._send(404, {"error": "not found"})
            n = int(self.headers.get("Content-Length", "0"))
            try:
                data = json.loads(self.rfile.read(n) or b"{}")
            except json.JSONDecodeError:
                return self._send(400, {"error": "bad json"})
            frm, to, text = data.get("from", "").strip(), data.get("to", "").strip(), data.get("text", "")
            if not frm or not to or not text:
                return self._send(400, {"error": "from, to, text required"})
            with _lock:
                msgs = _load()
                msg = {"id": len(msgs) + 1, "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                       "from": frm, "to": to, "text": text}
                with open(LOG_PATH, "a", encoding="utf-8") as f:
                    f.write(json.dumps(msg, ensure_ascii=False) + "\n")
            self._send(200, {"ok": True, "id": msg["id"]})

        def log_message(self, fmt, *args):
            sys.stderr.write("%s %s\n" % (self.log_date_time_string(), fmt % args))

    return H


def serve(port, token):
    srv = ThreadingHTTPServer(("0.0.0.0", port), make_handler(token))
    print(f"relay listening on 0.0.0.0:{port} (log: {LOG_PATH})", file=sys.stderr)
    srv.serve_forever()


# ---------------- client ----------------

URL = os.environ.get("RELAY_URL", "http://127.0.0.1:8787").rstrip("/")
TOKEN = os.environ.get("RELAY_TOKEN", "")
NAME = os.environ.get("RELAY_NAME", "")


def _req(method, path, obj=None):
    data = json.dumps(obj).encode("utf-8") if obj is not None else None
    r = urllib.request.Request(URL + path, data=data, method=method)
    r.add_header("Content-Type", "application/json")
    if TOKEN:
        r.add_header("Authorization", f"Bearer {TOKEN}")
    try:
        with urllib.request.urlopen(r, timeout=15) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        sys.exit(f"HTTP {e.code}: {e.read().decode('utf-8', 'replace')}")
    except urllib.error.URLError as e:
        sys.exit(f"cannot reach {URL}: {e.reason}")


def _need_name():
    if not NAME:
        sys.exit("set RELAY_NAME (your agent name)")
    return NAME


def _show(msgs):
    for m in msgs:
        print(f"[{m['id']}] {m['ts']} {m['from']} -> {m['to']}: {m['text']}")


def main():
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("serve")
    s.add_argument("--port", type=int, default=8787)
    s.add_argument("--token", default=os.environ.get("RELAY_TOKEN", ""))
    sub.add_parser("agents")
    sn = sub.add_parser("send")
    sn.add_argument("to")
    sn.add_argument("text")
    ib = sub.add_parser("inbox")
    ib.add_argument("--all", action="store_true")
    sub.add_parser("listen")
    a = p.parse_args()

    if a.cmd == "serve":
        return serve(a.port, a.token)
    if a.cmd == "agents":
        return print("\n".join(_req("GET", "/agents")["agents"]))
    if a.cmd == "send":
        r = _req("POST", "/send", {"from": _need_name(), "to": a.to, "text": a.text})
        return print(f"sent id={r['id']}")
    if a.cmd == "inbox":
        name = _need_name()
        msgs = _req("GET", "/inbox?" + urllib.parse.urlencode({"name": name, "since": 0}))["messages"]
        return _show(msgs)
    if a.cmd == "listen":
        name = _need_name()
        since = 0
        while True:
            msgs = _req("GET", "/inbox?" + urllib.parse.urlencode({"name": name, "since": since}))["messages"]
            for m in msgs:
                since = max(since, m["id"])
            _show([m for m in msgs if m["from"] != name])
            sys.stdout.flush()
            time.sleep(3)


if __name__ == "__main__":
    main()
