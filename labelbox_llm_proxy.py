#!/usr/bin/env python3
"""Forward LLM requests to Labelbox while adding project attribution.

Harbor's OpenHands adapter can configure an API key and base URL, but it cannot
add Labelbox's required ``x-labelbox-context`` header.  This small local proxy
adds that one header and otherwise forwards the request and response unchanged.
It never logs request headers or bodies.
"""

from __future__ import annotations

import argparse
import http.client
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


UPSTREAM_HOST = "litellm.labelbox.com"
HOP_BY_HOP = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
}


class ProxyHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    project_id = ""

    def log_message(self, fmt, *args):
        # Method/path/status are useful; secrets and payloads are never printed.
        super().log_message(fmt, *args)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        headers = {
            key: value
            for key, value in self.headers.items()
            if key.lower() not in HOP_BY_HOP and key.lower() != "host"
        }
        headers["Host"] = UPSTREAM_HOST
        headers["x-labelbox-context"] = json.dumps(
            {"project_id": self.project_id}
        )

        connection = http.client.HTTPSConnection(UPSTREAM_HOST, timeout=3600)
        try:
            connection.request("POST", self.path, body=body, headers=headers)
            response = connection.getresponse()
            payload = response.read()
            self.send_response(response.status, response.reason)
            for key, value in response.getheaders():
                if key.lower() not in HOP_BY_HOP and key.lower() != "content-length":
                    self.send_header(key, value)
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(payload)
        finally:
            connection.close()
            self.close_connection = True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8019)
    parser.add_argument("--project-id", required=True)
    args = parser.parse_args()

    ProxyHandler.project_id = args.project_id
    server = ThreadingHTTPServer((args.host, args.port), ProxyHandler)
    print(
        f"Labelbox LLM proxy listening on {args.host}:{args.port}",
        flush=True,
    )
    server.serve_forever()


if __name__ == "__main__":
    main()
