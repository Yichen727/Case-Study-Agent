import json
from threading import Event
from unittest.mock import Mock

import pytest

from app.config import MicrosoftSettings
from app.ingestion.errors import IngestionError
from app.services.microsoft_auth import MicrosoftAuth, SCOPES


def test_signin_is_background_readonly_session_scoped_and_disconnect_clears_tokens(monkeypatch):
    settings=MicrosoftSettings(tenant_id="11111111-1111-1111-1111-111111111111",client_id="22222222-2222-2222-2222-222222222222")
    client=Mock()
    client.get_accounts.return_value=[{"home_account_id":"oid.tenant","username":"tester@example.com"}]
    client.acquire_token_interactive.return_value={"access_token":"private-access-token","id_token_claims":{"tid":settings.tenant_id,"name":"Tester"}}
    client.acquire_token_silent.return_value={"access_token":"private-access-token"}
    constructor=Mock(return_value=client)
    monkeypatch.setattr("app.services.microsoft_auth.msal.PublicClientApplication",constructor)
    auth=MicrosoftAuth(settings)
    constructor.assert_not_called()
    auth.start();auth._future.result(timeout=5)
    assert auth.owner.startswith("microsoft:") and "tester" not in auth.owner
    assert auth.display_name=="Tester" and auth.token()=="private-access-token"
    assert SCOPES==["https://graph.microsoft.com/Sites.Read.All"]
    assert constructor.call_args.kwargs["token_cache"] is auth._cache
    assert client.acquire_token_interactive.call_args.kwargs["timeout"]==180
    auth.close()
    assert auth.owner is None and json.loads(auth._cache.serialize())=={}
    with pytest.raises(IngestionError) as error:
        auth.token()
    assert error.value.code=="AUTH_REQUIRED"


def test_disconnect_during_pending_signin_cannot_restore_late_credentials(monkeypatch):
    settings=MicrosoftSettings(tenant_id="11111111-1111-1111-1111-111111111111",client_id="22222222-2222-2222-2222-222222222222")
    entered,release=Event(),Event()
    client=Mock()
    def waiting(**kwargs):
        entered.set();assert release.wait(5)
        return {"access_token":"late-token","id_token_claims":{"tid":settings.tenant_id}}
    client.acquire_token_interactive.side_effect=waiting
    client.get_accounts.return_value=[{"home_account_id":"oid.tenant"}]
    monkeypatch.setattr("app.services.microsoft_auth.msal.PublicClientApplication",lambda *args,**kwargs:client)
    auth=MicrosoftAuth(settings)
    try:
        auth.start();assert entered.wait(3)
        auth.close();release.set();auth._future.result(timeout=5)
        assert auth.owner is None and json.loads(auth._cache.serialize())=={}
    finally:
        release.set();auth.close()
