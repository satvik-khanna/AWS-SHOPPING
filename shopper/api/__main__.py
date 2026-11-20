"""python3 -m shopper.api [--host 127.0.0.1] [--port 8000]"""
from __future__ import annotations

import argparse

from .server import make_server


def main() -> None:
    p = argparse.ArgumentParser(prog="shopper.api")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--quiet", action="store_true")
    a = p.parse_args()
    srv = make_server(a.host, a.port, quiet=a.quiet)
    print(f"shopper API on http://{a.host}:{srv.server_address[1]}  (GET /health)", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()


if __name__ == "__main__":
    main()
