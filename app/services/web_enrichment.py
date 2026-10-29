"""Bounded public-page enrichment with persistent caching."""
from __future__ import annotations

import json
import re
from time import monotonic, time
from urllib.parse import urljoin, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup
from urllib3.exceptions import HTTPError

from app.db.repository import connect

ALLOWED_HOSTS = {"atomizedstudios.tv", "www.atomizedstudios.tv"}
PAGE_BUDGET_SECONDS = 8
MAX_RESPONSE_BYTES = 1024 * 1024
POSITIVE_TTL_SECONDS = 24 * 60 * 60
NEGATIVE_TTL_SECONDS = 60


def normalize_url(url: str) -> str | None:
    try:
        parsed = urlparse(url)
        if (parsed.scheme != "https" or parsed.hostname not in ALLOWED_HOSTS
                or parsed.port not in (None, 443) or parsed.username or parsed.password):
            return None
        return urlunparse(("https", parsed.hostname, parsed.path or "/", parsed.params, parsed.query, ""))
    except ValueError:
        return None


def _allowed_url(url: str) -> bool:
    return normalize_url(url) is not None


def _empty():
    return {"text": None, "client": None, "services": None}


def _parse_page(content: bytes) -> dict:
    soup = BeautifulSoup(content, "html.parser")
    for node in soup(["script", "style", "noscript", "svg"]):
        node.decompose()
    lines = [re.sub(r"\s+", " ", line).strip() for line in soup.get_text("\n").splitlines()]
    lines = [line for line in lines if line]

    def value_after(label):
        for index, line in enumerate(lines[:-1]):
            if line.casefold() == label.casefold():
                value = lines[index + 1]
                if value.casefold() not in {"client", "pillar", "services"}:
                    return value
        return None

    return {"text": "\n".join(dict.fromkeys(lines))[:100_000] or None,
            "client": value_after("Client"), "services": value_after("Services")}


def fetch_case_page(
    url: str, *, cache_db=None, refresh: bool = False,
    deadline: float | None = None, metrics: dict | None = None,
) -> dict:
    normalized = normalize_url(url)
    if normalized is None:
        return _empty()
    metrics = metrics if metrics is not None else {}
    if cache_db is not None and not refresh:
        with connect(cache_db) as conn:
            row = conn.execute("SELECT payload_json FROM web_cache WHERE url=? AND expires_at>?",
                               (normalized, time())).fetchone()
        if row:
            metrics["cache_hits"] = metrics.get("cache_hits", 0) + 1
            return json.loads(row[0])
    end = min(deadline if deadline is not None else float("inf"), monotonic() + PAGE_BUDGET_SECONDS)
    if end <= monotonic():
        metrics["skipped_budget"] = metrics.get("skipped_budget", 0) + 1
        return _empty()
    result = _empty()
    visited = []
    current = normalized
    try:
        for _ in range(4):
            current = normalize_url(current)
            remaining = end - monotonic()
            if current is None or remaining <= 0:
                break
            visited.append(current)
            metrics["http_requests"] = metrics.get("http_requests", 0) + 1
            # read1 returns available bytes rather than waiting for a full chunk;
            # the deadline is checked between socket reads (each capped at 2s).
            with requests.get(current, timeout=(min(2, remaining), min(2, remaining)),
                              stream=True, allow_redirects=False,
                              headers={"User-Agent": "CaseStudyAgent/0.1 (internal case-study indexing)"}) as response:
                if response.status_code in {301, 302, 303, 307, 308}:
                    location = response.headers.get("Location")
                    if not location:
                        break
                    current = urljoin(current, location)
                    continue
                response.raise_for_status()
                content = bytearray()
                while monotonic() < end:
                    chunk = response.raw.read1(8192, decode_content=True)
                    if not chunk:
                        result = _parse_page(bytes(content))
                        break
                    content.extend(chunk)
                    if len(content) > MAX_RESPONSE_BYTES:
                        break
                break
    except (requests.RequestException, HTTPError, OSError, ValueError):
        result = _empty()
    if cache_db is not None and visited:
        ttl = POSITIVE_TTL_SECONDS if result["text"] else NEGATIVE_TTL_SECONDS
        payload = json.dumps(result)
        with connect(cache_db) as conn:
            conn.executemany(
                """INSERT INTO web_cache(url,payload_json,expires_at) VALUES (?,?,?)
                   ON CONFLICT(url) DO UPDATE SET payload_json=excluded.payload_json,expires_at=excluded.expires_at""",
                [(address, payload, time() + ttl) for address in dict.fromkeys([normalized, *visited])])
    return result
