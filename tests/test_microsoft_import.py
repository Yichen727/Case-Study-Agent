from io import BytesIO
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from zipfile import ZipFile, ZIP_DEFLATED

import pytest
import requests

from app.config import MicrosoftSettings
from app.db.repository import connect, init_db, list_cases, list_sources, update_case_metadata
from app.ingestion.errors import IngestionError
from app.services.microsoft_source import parse_cloud_link, MicrosoftSource, GRAPH
from app.services.task_service import TaskManager

FIXTURE = Path(__file__).resolve().parents[1] / "evaluation/fixtures/source_a.pptx"
LINK = "https://freud.sharepoint.com/:p:/r/sites/Atomized1/Shared%20Documents/Brand%20Assets/Atomized_Studios_template%202025.pptx?d=wa2844a8f571b4dae87d7693f93c46cff&csf=1&web=1&e=zViLGN"


class Auth:
    def __init__(self, owner="microsoft:test-user"):
        self.owner = owner
        self.active = True

    def check_active(self):
        if not self.active:
            raise IngestionError("AUTH_REQUIRED")

    def token(self):
        self.check_active()
        return "private-access-token"


def response(status=200, data=None, body=None, location=None):
    result = requests.Response()
    result.status_code = status
    result._content = json.dumps(data).encode() if data is not None else (body or b"")
    result.raw = BytesIO(result._content)
    if location:
        result.headers["Location"] = location
    if body is not None:
        result.headers["Content-Length"] = str(len(body))
    return result


@pytest.fixture
def graph(monkeypatch):
    settings = MicrosoftSettings()
    monkeypatch.setattr("app.config.get_microsoft_settings",lambda:settings)
    monkeypatch.setattr("app.services.ingest_service.fetch_case_page",lambda *args,**kwargs:{})
    fixture = SimpleNamespace(body=FIXTURE.read_bytes(),etag='"v1"',post_etag=None,status=200,
                              location="https://freud.sharepoint.com/download.aspx?tempauth=private-signed-token",sessions=[],
                              drive_web_url="https://freud.sharepoint.com/sites/Atomized1/Shared%20Documents")

    def get(url,**kwargs):
        if not url.startswith(GRAPH):
            return response(body=fixture.body)
        if fixture.status!=200:
            return response(fixture.status,data={"error":{"message":"Bearer private-access-token"}})
        if url.endswith("/content"):
            return response(302,location=fixture.location)
        if "/items/" in url:
            return response(data={"id":"file-1","eTag":fixture.post_etag or fixture.etag})
        if "/root:/" in url:
            return response(data={"id":"file-1","name":"Atomized_Studios_template 2025.pptx","file":{},
                                  "size":len(fixture.body),"eTag":fixture.etag})
        if "/drives?" in url:
            return response(data={"value":[{"id":"drive-1","webUrl":fixture.drive_web_url}]})
        return response(data={"id":"freud.sharepoint.com,site-1,web-1"})

    def session():
        value = SimpleNamespace(get=Mock(side_effect=get),close=Mock(),trust_env=True)
        fixture.sessions.append(value)
        return value

    monkeypatch.setattr("app.services.microsoft_source.requests.Session",session)
    return fixture


def test_user_office_link_and_business_onedrive_paths_are_parsed_without_fetching():
    link = parse_cloud_link(LINK,MicrosoftSettings())
    assert link.host=="freud.sharepoint.com" and link.site_path=="/sites/Atomized1"
    assert link.file_path=="/sites/Atomized1/Shared Documents/Brand Assets/Atomized_Studios_template 2025.pptx"
    assert link.source_type=="SHAREPOINT"
    drive = parse_cloud_link("https://freud-my.sharepoint.com/personal/user_freud_com/Documents/Deck.pptx",MicrosoftSettings())
    assert drive.source_type=="ONEDRIVE" and drive.site_path=="/personal/user_freud_com"


@pytest.mark.parametrize("url,code",[
    ("not a link","INVALID_URL"),
    ("http://freud.sharepoint.com/sites/X/file.pptx","INVALID_URL"),
    ("https://127.0.0.1/private.pptx","UNSUPPORTED_SOURCE"),
    ("https://freud.sharepoint.com.evil.test/sites/X/file.pptx","UNSUPPORTED_SOURCE"),
    ("https://freud.sharepoint.com:4443/sites/X/file.pptx","INVALID_URL"),
    ("https://user:secret@freud.sharepoint.com/sites/X/file.pptx","INVALID_URL"),
    ("https://freud.sharepoint.com/sites/X/%2e%2e/file.pptx","INVALID_URL"),
    ("https://freud.sharepoint.com/:p:/s/X/opaque-share-code","INVALID_URL"),
    ("https://freud.sharepoint.com/sites/X/file.pptx?access_token=secret","INVALID_URL"),
    ("https://freud.sharepoint.com/sites/X/file.pptx#access_token=secret","INVALID_URL"),
])
def test_untrusted_urls_stop_before_auth_or_network(tmp_path,graph,url,code):
    manager = TaskManager(tmp_path / "cases.sqlite3")
    try:
        with pytest.raises(IngestionError) as error:
            manager.submit_cloud_import(url,Auth())
        assert error.value.code==code and not graph.sessions
        assert manager.list_tasks(None)==[] and list_cases(manager.db_path)==[]
    finally:
        manager.shutdown()


def test_authorized_cloud_download_provenance_duplicates_and_version_isolation(tmp_path,graph):
    db = tmp_path / "cases.sqlite3"
    auth = Auth()
    manager = TaskManager(db)
    try:
        with pytest.raises(IngestionError,match="Sign in"):
            manager.submit_cloud_import(LINK,None)
        first = manager.wait(manager.submit_cloud_import(LINK,auth),owner=auth.owner)
        assert first["status"]=="SUCCEEDED"
        source = list_sources(db)[0]
        assert source["source_type"]=="SHAREPOINT" and source["source_file_id"]=="drive-1:file-1"
        assert source["source_version"]=='"v1"' and source["original_source_url"]==LINK
        assert Path(source["source_snapshot_reference"]).read_bytes()==graph.body
        case_id = list_cases(db)[0]["case_id"]
        update_case_metadata(db,case_id,{"client":"Retained correction"})
        repeated = manager.wait(manager.submit_cloud_import(LINK,auth),owner=auth.owner)
        assert repeated["status"]=="SUCCEEDED" and json.loads(repeated["result_json"])["reused"]
        assert len(list_cases(db))==2 and len(list_sources(db))==1
        assert next(case for case in list_cases(db) if case["case_id"]==case_id)["client"]=="Retained correction"
        graph.etag='"v2"'
        assert manager.wait(manager.submit_cloud_import(LINK,auth),owner=auth.owner)["status"]=="SUCCEEDED"
        assert len(list_cases(db))==4 and len(list_sources(db))==2
        assert len({row["source_snapshot_reference"] for row in list_sources(db)})==1
        assert not manager._cloud_credentials
        for session in graph.sessions:
            assert session.trust_env is False
            for call in session.get.call_args_list:
                url=call.args[0]
                if url.startswith(GRAPH):
                    assert call.kwargs["headers"]["Authorization"]=="Bearer private-access-token"
                else:
                    assert not call.kwargs["headers"]
                assert call.kwargs["allow_redirects"] is False
        with connect(db) as conn:
            stored=" ".join(str(tuple(row)) for row in conn.execute("SELECT * FROM tasks"))
        assert "private-access-token" not in stored and "private-signed-token" not in stored
        assert manager.get_task(first["task_id"],"microsoft:another-user") is None
    finally:
        manager.shutdown()


@pytest.mark.parametrize("status,code",[(401,"AUTH_REQUIRED"),(403,"ACCESS_DENIED"),(404,"FILE_NOT_FOUND"),(410,"FILE_NOT_FOUND"),(429,"FETCH_FAILED"),(500,"FETCH_FAILED")])
def test_graph_errors_are_distinct_and_never_create_cases(tmp_path,graph,status,code):
    graph.status=status
    auth=Auth();manager=TaskManager(tmp_path / "cases.sqlite3")
    try:
        task=manager.wait(manager.submit_cloud_import(LINK,auth),owner=auth.owner)
        assert task["error_code"]==code and task["status"]=="FAILED"
        assert list_cases(manager.db_path)==[] and list_sources(manager.db_path)==[]
        assert "private-access-token" not in json.dumps(task)
    finally:
        manager.shutdown()


@pytest.mark.parametrize("location",["http://127.0.0.1/file","https://evil.test/file","https://freud.sharepoint.com.evil.test/file","https://freud.sharepoint.com:8443/file"])
def test_download_redirects_cannot_reach_external_or_internal_targets(tmp_path,graph,location):
    graph.location=location
    auth=Auth();manager=TaskManager(tmp_path / "cases.sqlite3")
    try:
        task=manager.wait(manager.submit_cloud_import(LINK,auth),owner=auth.owner)
        assert task["error_code"]=="FETCH_FAILED" and not list_cases(manager.db_path)
        assert all(call.args[0].startswith(GRAPH) for call in graph.sessions[0].get.call_args_list)
    finally:
        manager.shutdown()


@pytest.mark.parametrize("kind,code",[("html","INVALID_FILE"),("broken","PARSE_FAILED"),("changed","FETCH_FAILED")])
def test_actual_download_validation_and_version_consistency(tmp_path,graph,kind,code):
    if kind=="html":
        graph.body=b"<html>Sign in</html>"
    elif kind=="broken":
        data=BytesIO()
        with ZipFile(FIXTURE) as source,ZipFile(data,"w",ZIP_DEFLATED) as target:
            for item in source.infolist():
                if item.filename!="ppt/slides/slide1.xml":
                    target.writestr(item,source.read(item.filename))
        graph.body=data.getvalue()
    else:
        graph.post_etag='"changed-during-download"'
    auth=Auth();manager=TaskManager(tmp_path / "cases.sqlite3")
    try:
        task=manager.wait(manager.submit_cloud_import(LINK,auth),owner=auth.owner)
        assert task["error_code"]==code and list_sources(manager.db_path)==[] and list_cases(manager.db_path)==[]
        assert not list((tmp_path / "incoming").glob("*.part"))
    finally:
        manager.shutdown()


def test_business_onedrive_uses_the_same_authorized_pipeline(tmp_path,graph):
    graph.drive_web_url="https://freud-my.sharepoint.com/personal/user_freud_com/Documents"
    graph.location="https://download.files.1drv.com/file?temporary=private-signed-token"
    link=graph.drive_web_url+"/Deck.pptx"
    auth=Auth();manager=TaskManager(tmp_path / "cases.sqlite3")
    try:
        assert manager.wait(manager.submit_cloud_import(link,auth),owner=auth.owner)["status"]=="SUCCEEDED"
        assert list_sources(manager.db_path)[0]["source_type"]=="ONEDRIVE"
        assert len(list_cases(manager.db_path))==2
    finally:
        manager.shutdown()


def test_retry_after_commit_keeps_original_cloud_version_and_requires_reconnection(tmp_path,graph,monkeypatch):
    def failed_enrichment(*args,**kwargs):
        raise RuntimeError("private diagnostic")
    monkeypatch.setattr("app.services.ingest_service.enrich_source",failed_enrichment)
    auth=Auth();manager=TaskManager(tmp_path / "cases.sqlite3")
    try:
        task_id=manager.submit_cloud_import(LINK,auth)
        assert manager.wait(task_id,owner=auth.owner)["status"]=="FAILED"
        assert len(list_cases(manager.db_path))==2
        source=list_sources(manager.db_path)[0]
        with pytest.raises(IngestionError):
            manager.retry(task_id,auth.owner)
        assert manager.retry(task_id,"microsoft:another-user",cloud_auth=Auth("microsoft:another-user")) is False
        graph.etag='"new-cloud-version"'
        assert manager.retry(task_id,auth.owner,cloud_auth=auth)
        assert manager.wait(task_id,owner=auth.owner)["status"]=="SUCCEEDED"
        assert len(list_cases(manager.db_path))==2 and list_sources(manager.db_path)[0]["source_version"]==source["source_version"]
        requests_made=[call.args[0] for session in graph.sessions for call in session.get.call_args_list]
        assert sum(url.endswith("/content") for url in requests_made)==1
    finally:
        manager.shutdown()


def test_cloud_export_keeps_imported_slides_after_remote_file_changes(tmp_path,graph):
    from lxml import etree as ET
    from pptx import Presentation
    auth=Auth();manager=TaskManager(tmp_path / "cases.sqlite3")
    try:
        assert manager.wait(manager.submit_cloud_import(LINK,auth),owner=auth.owner)["status"]=="SUCCEEDED"
        cases=sorted(list_cases(manager.db_path),key=lambda case:int(case["slide_numbers"]),reverse=True)
        request_count=sum(session.get.call_count for session in graph.sessions)
        graph.etag='"changed-remotely"'
        graph.body=(FIXTURE.parent / "source_b.pptx").read_bytes()
        task=manager.wait(manager.submit_export([case["case_id"] for case in cases]))
        assert task["status"]=="SUCCEEDED"
        output=Presentation(json.loads(task["result_json"])["output_path"])
        original=Presentation(FIXTURE)
        assert len(output.slides)==len(cases)
        for slide,case in zip(output.slides,cases):
            assert ET.tostring(slide._element)==ET.tostring(original.slides[int(case["slide_numbers"])-1]._element)
        assert sum(session.get.call_count for session in graph.sessions)==request_count
    finally:
        manager.shutdown()


def test_cloud_sidebar_signin_import_disconnect_and_local_fallback(tmp_path,graph,monkeypatch):
    from streamlit.testing.v1 import AppTest
    from app.services.task_service import get_task_manager
    root=Path(__file__).resolve().parents[1]
    db=tmp_path / "cases.sqlite3"
    settings=MicrosoftSettings(tenant_id="11111111-1111-1111-1111-111111111111",client_id="22222222-2222-2222-2222-222222222222")
    monkeypatch.setattr("app.config.DB_PATH",db)
    monkeypatch.setattr("app.ui.microsoft_panel.get_microsoft_settings",lambda:settings)
    monkeypatch.setattr("app.config.get_microsoft_settings",lambda:settings)
    class UIAuth(Auth):
        pending=False
        error_code=None
        display_name="Test account"
        def __init__(self, settings):
            super().__init__(None)
        def start(self):
            self.owner="microsoft:ui-user"
        def close(self):
            self.owner=None;self.active=False
    monkeypatch.setattr("app.ui.microsoft_panel.MicrosoftAuth",UIAuth)
    app=AppTest.from_file(str(root / "app/ui/streamlit_app.py")).run(timeout=30)
    app.radio[0].set_value("OneDrive / SharePoint").run()
    assert not app.exception
    next(field for field in app.text_area if field.label=="Presentation link").set_value(LINK)
    next(button for button in app.button if button.label=="Import presentation").click().run()
    assert any("Sign in with Microsoft" in error.value for error in app.error)
    next(button for button in app.button if button.label=="Sign in with Microsoft").click().run()
    assert not app.exception and any("Test account" in item.value for item in app.caption)
    next(button for button in app.button if button.label=="Import presentation").click().run()
    manager=get_task_manager(db)
    try:
        assert manager.list_tasks("microsoft:ui-user"), [error.value for error in app.error]
        task=manager.list_tasks("microsoft:ui-user")[0]
        assert manager.wait(task["task_id"],owner=task["owner"])["status"]=="SUCCEEDED"
        app.run()
        assert not app.exception and len(list_cases(db))==2
        calls=sum(session.get.call_count for session in graph.sessions)
        app.run();app.run()
        assert sum(session.get.call_count for session in graph.sessions)==calls
        next(button for button in app.button if button.label=="Disconnect Microsoft").click().run()
        assert not app.exception
        app.radio[0].set_value("Local upload").run()
        assert not app.exception and len(app.get("file_uploader"))==1
    finally:
        manager.shutdown()
