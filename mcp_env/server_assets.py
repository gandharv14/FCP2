"""Static assets for the MCP sidecar: the standalone server and its Dockerfile.

The server follows the Harbor MCP-server-task pattern (FastMCP over
streamable HTTP on port 8000) and exposes one generic tool surface across all
domains: ``list_sources``, ``search_documents``, ``fetch_document``,
``list_datasets``, ``query_records``. Tool names and descriptions carry no
information about which records are supported answers.
"""

SERVER_PY = '''\
"""Generated mock research MCP server. Serves ./runtime; contains no answers
beyond the data records themselves."""

from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Any

from fastmcp import FastMCP

TOKEN_RE = re.compile(r"[a-z0-9.]+")
RUNTIME = Path(__file__).resolve().parent / "runtime"


def tokenize(value: str) -> list[str]:
    return TOKEN_RE.findall(str(value).casefold())


def load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()]


def matches(actual: Any, expected: str) -> bool:
    left, right = " ".join(tokenize(str(actual))), " ".join(tokenize(expected))
    return left == right or right in left


class Store:
    def __init__(self, runtime: Path):
        self.config = load(runtime / "server.json")
        self.sources = load(runtime / "sources.json")
        self.datasets = load(runtime / "datasets.json")
        self.documents = jsonl(runtime / "documents.jsonl")
        self.records = jsonl(runtime / "records.jsonl")
        self.documents_by_id = {row["id"]: row for row in self.documents}
        self.dataset_ids = {row["id"] for row in self.datasets}

    def list_sources(self, kind: str = "", limit: int = 20) -> list[dict[str, Any]]:
        rows = [row for row in self.sources
                if not kind or matches(row.get("kind", ""), kind)]
        return rows[: max(1, min(limit, 100))]

    def list_datasets(self, source_id: str = "", limit: int = 20) -> list[dict[str, Any]]:
        rows = [row for row in self.datasets
                if not source_id or row["source_id"] == source_id]
        return rows[: max(1, min(limit, 100))]

    def search(self, query: str, source_id: str = "", document_kind: str = "",
               limit: int = 10) -> list[dict[str, Any]]:
        terms = tokenize(query)
        ranked = []
        for doc in self.documents:
            if source_id and doc["source_id"] != source_id:
                continue
            if document_kind and doc["kind"] != document_kind:
                continue
            title, content = set(tokenize(doc["title"])), set(tokenize(doc["content"]))
            score = (sum(4 for term in terms if term in title)
                     + sum(1 for term in terms if term in content))
            if score:
                score -= math.log2(max(2, len(content))) / 12
                ranked.append((score, doc))
        ranked.sort(key=lambda item: (-item[0], item[1]["id"]))
        return [
            {
                "document_id": doc["id"], "source_id": doc["source_id"],
                "title": doc["title"], "kind": doc["kind"],
                "published_at": doc["published_at"],
                "snippet": doc["content"][:300], "score": round(score, 4),
            }
            for score, doc in ranked[: max(1, min(limit, 50))]
        ]

    def fetch(self, document_id: str) -> dict[str, Any]:
        if document_id not in self.documents_by_id:
            raise KeyError("Unknown document_id: %s" % document_id)
        return self.documents_by_id[document_id]

    def query(self, dataset_id: str, limit: int = 20, **filters: str) -> dict[str, Any]:
        if dataset_id not in self.dataset_ids:
            raise KeyError("Unknown dataset_id: %s" % dataset_id)
        rows = [row for row in self.records if row["dataset_id"] == dataset_id]
        for field, expected in filters.items():
            if not expected:
                continue
            if field == "metric":
                rows = [row for row in rows
                        if matches(row[field], expected)
                        or any(matches(alias, expected)
                               for alias in row.get("metric_aliases", []))]
            else:
                rows = [row for row in rows if matches(row.get(field), expected)]
        capped = rows[: max(1, min(limit, 100))]
        return {"rows": capped, "returned": len(capped), "total_matches": len(rows)}


def build_server(runtime: Path = RUNTIME) -> FastMCP:
    store = Store(runtime)
    server = FastMCP(store.config["name"], instructions=store.config["instructions"])

    @server.tool()
    def list_sources(kind: str = "", limit: int = 20) -> list[dict]:
        """List available source collections; optionally filter by source kind."""
        return store.list_sources(kind, limit)

    @server.tool()
    def search_documents(query: str, source_id: str = "", document_kind: str = "",
                         limit: int = 10) -> list[dict]:
        """Search document snippets. Fetch a document before relying on a reported value."""
        return store.search(query, source_id, document_kind, limit)

    @server.tool()
    def fetch_document(document_id: str) -> dict:
        """Fetch one complete source document by exact document_id."""
        return store.fetch(document_id)

    @server.tool()
    def list_datasets(source_id: str = "", limit: int = 20) -> list[dict]:
        """List structured datasets and their filter dimensions."""
        return store.list_datasets(source_id, limit)

    @server.tool()
    def query_records(dataset_id: str, entity: str = "", metric: str = "",
                      period: str = "", scenario: str = "", basis: str = "",
                      unit: str = "", status: str = "", limit: int = 20) -> dict:
        """Filter records explicitly. Empty dimensions may produce multiple conflicting values."""
        return store.query(dataset_id, limit, entity=entity, metric=metric,
                           period=period, scenario=scenario, basis=basis,
                           unit=unit, status=status)

    return server


if __name__ == "__main__":
    build_server().run(transport="streamable-http", host="0.0.0.0", port=8000)
'''

SIDECAR_DOCKERFILE = """\
FROM python:3.12-slim

RUN pip install --no-cache-dir "fastmcp>=2.0"

WORKDIR /srv
COPY server.py /srv/server.py
COPY runtime /srv/runtime

EXPOSE 8000
CMD ["python", "server.py"]
"""

COMPOSE_YAML = """\
services:
  main:
    depends_on:
      mcp-server:
        condition: service_healthy

  mcp-server:
    build:
      context: ./mcp-server
    expose:
      - "8000"
    healthcheck:
      test: ["CMD", "python", "-c", "import socket; s=socket.create_connection(('localhost',8000),timeout=2); s.close()"]
      interval: 2s
      timeout: 5s
      retries: 15
      start_period: 5s
"""
