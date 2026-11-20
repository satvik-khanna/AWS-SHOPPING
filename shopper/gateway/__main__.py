"""Run the 1-URL multi-protocol commerce gateway (MCP + UCP + ACP + AI Discovery).

Usage:
    python3 -m shopper.gateway --host 127.0.0.1 --port 8080
"""
from __future__ import annotations

import argparse

from .server import make_server


def main() -> None:
    p = argparse.ArgumentParser(prog="shopper.gateway")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8080)
    a = p.parse_args()
    srv = make_server(host=a.host, port=a.port)
    addr, port = srv.server_address
    base = f"http://{addr}:{port}"
    print(f"AWS-SHOPPING Universal Gateway listening on {base}")
    print(f"  Human Link-in-Bio : {base}/s/tokyo-camera")
    print(f"  MCP (JSON-RPC)    : {base}/mcp")
    print(f"  UCP Manifest      : {base}/.well-known/ucp")
    print(f"  ACP Checkout      : {base}/acp/checkout_sessions")
    print(f"  AI Discovery      : {base}/llms.txt  |  {base}/.well-known/agent-commerce.json")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()


if __name__ == "__main__":
    main()
