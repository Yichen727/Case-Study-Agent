from io import BytesIO
from unittest.mock import Mock

import requests
import pytest

from app.db.repository import connect, init_db
from app.services import web_enrichment


class FakeResponse:
    status_code = 200
    headers = {}
    text = '''<html><body>
      <h1>EY: Sustainability People</h1>
      <p>We created a series of video profiles for EY.</p>
      <div>Client</div><div>EY</div>
      <div>Pillar</div><div>Short and Social</div>
      <div>Services</div><div>Concept. Shoot. Edit. Copywriting.</div>
    </body></html>'''

    def raise_for_status(self):
        return None

    def __enter__(self):
        self.raw = FakeRaw(self.text.encode())
        return self

    def __exit__(self, *args):
        self.raw.close()


class FakeRaw(BytesIO):
    def read1(self, size, decode_content=True):
        return self.read(size)


def test_fetch_case_page_extracts_client_services_and_text(monkeypatch):
    monkeypatch.setattr(web_enrichment.requests, "get", lambda *args, **kwargs: FakeResponse())
    result = web_enrichment.fetch_case_page("https://www.atomizedstudios.tv/work/example")
    assert result["client"] == "EY"
    assert result["services"] == "Concept. Shoot. Edit. Copywriting."
    assert "video profiles" in result["text"]


def test_fetch_case_page_rejects_non_approved_host(monkeypatch):
    def fail(*args, **kwargs):
        raise AssertionError("should not fetch non-approved host")
    monkeypatch.setattr(web_enrichment.requests, "get", fail)
    result = web_enrichment.fetch_case_page("https://example.com/case")
    assert result == {"text": None, "client": None, "services": None}


def test_redirect_cannot_leave_approved_hosts(monkeypatch):
    calls = []

    def redirect(url, **kwargs):
        calls.append(url)
        assert kwargs["allow_redirects"] is False
        response = FakeResponse()
        response.status_code = 302
        response.headers = {"Location": "http://127.0.0.1/private"}
        return response

    monkeypatch.setattr(web_enrichment.requests, "get", redirect)
    result = web_enrichment.fetch_case_page("https://www.atomizedstudios.tv/work/example")
    assert result["client"] is None
    assert len(calls) == 1


def test_redirect_within_approved_hosts_still_enriches(monkeypatch):
    calls = []

    def fetch(url, **kwargs):
        calls.append(url)
        response = FakeResponse()
        if len(calls) == 1:
            response.status_code = 301
            response.headers = {"Location": "https://www.atomizedstudios.tv/work/example"}
        return response

    monkeypatch.setattr(web_enrichment.requests, "get", fetch)
    result = web_enrichment.fetch_case_page("https://atomizedstudios.tv/work/example")
    assert result["client"] == "EY"
    assert len(calls) == 2


def test_persistent_cache_normalizes_urls_expires_and_allows_forced_refresh(tmp_path, monkeypatch):
    db = tmp_path / "cases.sqlite3"
    init_db(db)
    fetch = Mock(side_effect=lambda *args, **kwargs: FakeResponse())
    monkeypatch.setattr(web_enrichment.requests, "get", fetch)
    url = "https://WWW.ATOMIZEDSTUDIOS.TV:443/work/example#section"
    metrics = {}
    first = web_enrichment.fetch_case_page(url, cache_db=db, metrics=metrics)
    assert web_enrichment.fetch_case_page(url.replace("#section", "#other"), cache_db=db, metrics=metrics) == first
    assert fetch.call_count == 1 and metrics == {"http_requests": 1, "cache_hits": 1}
    with connect(db) as conn:
        rows = conn.execute("SELECT url,expires_at FROM web_cache").fetchall()
        assert len(rows) == 1 and rows[0]["url"] == "https://www.atomizedstudios.tv/work/example"
        assert rows[0]["expires_at"] > web_enrichment.time() + 86_000
        conn.execute("UPDATE web_cache SET expires_at=0")
    web_enrichment.fetch_case_page(url, cache_db=db)
    web_enrichment.fetch_case_page(url, cache_db=db, refresh=True)
    assert fetch.call_count == 3


def test_negative_cache_is_short_and_network_failure_is_nonfatal(tmp_path, monkeypatch):
    db = tmp_path / "cases.sqlite3"
    init_db(db)
    fetch = Mock(side_effect=requests.Timeout("sensitive diagnostic"))
    monkeypatch.setattr(web_enrichment.requests, "get", fetch)
    url = "https://www.atomizedstudios.tv/work/missing"
    assert web_enrichment.fetch_case_page(url, cache_db=db)["text"] is None
    assert web_enrichment.fetch_case_page(url, cache_db=db)["client"] is None
    assert fetch.call_count == 1
    with connect(db) as conn:
        expiry = conn.execute("SELECT expires_at FROM web_cache").fetchone()[0]
        assert 0 < expiry - web_enrichment.time() <= 60


def test_page_deadline_and_size_limit_stop_optional_enrichment(monkeypatch):
    fetch = Mock(side_effect=lambda *args, **kwargs: FakeResponse())
    monkeypatch.setattr(web_enrichment.requests, "get", fetch)
    url = "https://www.atomizedstudios.tv/work/example"
    metrics = {}
    assert web_enrichment.fetch_case_page(url, deadline=0, metrics=metrics)["text"] is None
    fetch.assert_not_called()
    assert metrics["skipped_budget"] == 1
    monkeypatch.setattr(web_enrichment, "MAX_RESPONSE_BYTES", 8)
    assert web_enrichment.fetch_case_page(url)["text"] is None


@pytest.mark.parametrize("url", ["http://atomizedstudios.tv/", "https://user:secret@atomizedstudios.tv/",
                                "https://atomizedstudios.tv:8080/", "https://atomizedstudios.tv.evil.test/",
                                "https://127.0.0.1/"])
def test_cache_and_fetch_reject_unapproved_addresses(monkeypatch, url):
    fetch = Mock()
    monkeypatch.setattr(web_enrichment.requests, "get", fetch)
    assert web_enrichment.fetch_case_page(url)["text"] is None
    fetch.assert_not_called()
