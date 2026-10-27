from app.services import web_enrichment


class FakeResponse:
    text = '''<html><body>
      <h1>EY: Sustainability People</h1>
      <p>We created a series of video profiles for EY.</p>
      <div>Client</div><div>EY</div>
      <div>Pillar</div><div>Short and Social</div>
      <div>Services</div><div>Concept. Shoot. Edit. Copywriting.</div>
    </body></html>'''

    def raise_for_status(self):
        return None


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
