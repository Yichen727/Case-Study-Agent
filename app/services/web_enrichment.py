"""Optional enrichment from approved Atomized Studios case-study pages."""
from __future__ import annotations

import re
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

ALLOWED_HOSTS = {"atomizedstudios.tv", "www.atomizedstudios.tv"}


def fetch_case_page(url: str) -> dict[str, str | None]:
    """Fetch readable text and Client/Services fields from an approved case page.

    This intentionally only fetches pages on Atomized Studios' own domain.
    Failures are non-fatal: the original PPTX content is still ingested.
    """
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in ALLOWED_HOSTS:
        return {"text": None, "client": None, "services": None}

    try:
        response = requests.get(
            url,
            timeout=8,
            headers={"User-Agent": "CaseStudyAgent/0.1 (internal case-study indexing)"},
        )
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        for node in soup(["script", "style", "noscript", "svg"]):
            node.decompose()
        lines = [re.sub(r"\s+", " ", line).strip() for line in soup.get_text("\n").splitlines()]
        lines = [line for line in lines if line]
        page_text = "\n".join(dict.fromkeys(lines))

        def value_after(label: str) -> str | None:
            for i, line in enumerate(lines):
                if line.strip().casefold() == label.casefold() and i + 1 < len(lines):
                    value = lines[i + 1].strip()
                    if value and value.casefold() not in {"client", "pillar", "services"}:
                        return value
            return None

        return {
            "text": page_text or None,
            "client": value_after("Client"),
            "services": value_after("Services"),
        }
    except (requests.RequestException, ValueError):
        return {"text": None, "client": None, "services": None}
