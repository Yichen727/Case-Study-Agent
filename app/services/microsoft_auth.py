"""Desktop Microsoft sign-in; credentials and refresh cache stay in session memory."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
from threading import RLock

import msal
import requests

from app.ingestion.errors import IngestionError

SCOPES = ["https://graph.microsoft.com/Sites.Read.All"]


class MicrosoftAuth:
    def __init__(self, settings):
        self.settings = settings
        self._lock = RLock()
        self._pool = ThreadPoolExecutor(max_workers=1,thread_name_prefix="microsoft-signin")
        self._future = None
        self._cache = msal.SerializableTokenCache()
        self._app = None
        self._account = None
        self._closed = False
        self._owner = None
        self._name = None
        self._error = None

    @property
    def pending(self):
        return self._future is not None and not self._future.done()

    @property
    def owner(self):
        return self._owner if not self._closed else None

    @property
    def display_name(self):
        return self._name if self.owner else None

    @property
    def error_code(self):
        return self._error

    def start(self):
        if not self.settings.configured:
            raise IngestionError("AUTH_REQUIRED")
        if not self.pending and not self._closed:
            self._future = self._pool.submit(self._sign_in)

    def _sign_in(self):
        try:
            app = msal.PublicClientApplication(self.settings.client_id,
                authority=f"https://login.microsoftonline.com/{self.settings.tenant_id}",
                token_cache=self._cache,timeout=8,enable_pii_log=False)
            result = app.acquire_token_interactive(scopes=SCOPES,prompt="select_account",timeout=180)
            claims = result.get("id_token_claims") or {}
            accounts = app.get_accounts()
            if not result.get("access_token") or not accounts or claims.get("tid","").casefold()!=self.settings.tenant_id.casefold():
                raise IngestionError("AUTH_REQUIRED")
            with self._lock:
                if self._closed:
                    self._cache.deserialize("{}")
                    return
                self._app,self._account = app,accounts[0]
                identity = f"{claims['tid']}:{self._account['home_account_id']}"
                self._owner = "microsoft:"+hashlib.sha256(identity.encode()).hexdigest()
                self._name = claims.get("name") or self._account.get("username") or "Microsoft account"
        except requests.RequestException:
            self._error = "FETCH_FAILED"
            self._cache.deserialize("{}")
        except Exception:
            self._error = "AUTH_REQUIRED"
            self._cache.deserialize("{}")

    def token(self):
        with self._lock:
            if self._closed or not self._app or not self._account:
                raise IngestionError("AUTH_REQUIRED")
            app,account = self._app,self._account
        try:
            result = app.acquire_token_silent(SCOPES,account=account)
        except requests.RequestException:
            raise IngestionError("FETCH_FAILED") from None
        except Exception:
            raise IngestionError("AUTH_REQUIRED") from None
        with self._lock:
            if self._closed:
                self._cache.deserialize("{}")
                raise IngestionError("AUTH_REQUIRED")
            if not result or not result.get("access_token"):
                raise IngestionError("AUTH_REQUIRED")
            return result["access_token"]

    def check_active(self):
        if not self.owner:
            raise IngestionError("AUTH_REQUIRED")

    def close(self):
        with self._lock:
            self._closed = True
            self._owner,self._name,self._account,self._app = None,None,None,None
            self._cache.deserialize("{}")
        self._pool.shutdown(wait=False,cancel_futures=True)
