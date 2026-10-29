"""Explicit Microsoft sign-in and background link import for the local operator."""
import streamlit as st

from app.config import get_microsoft_settings
from app.ingestion.errors import IngestionError
from app.services.microsoft_auth import MicrosoftAuth
from app.services.task_service import TaskQueueFull


def cloud_auth():
    return st.session_state.get("_microsoft_auth")


def render_microsoft_import(manager):
    initial = cloud_auth()
    polling = bool(initial and initial.pending)

    @st.fragment(run_every=1 if polling else None)
    def panel():
        settings = get_microsoft_settings()
        auth = cloud_auth()
        disconnect = False
        if not settings.configured:
            st.info("Microsoft sign-in needs company app setup. See README → Microsoft setup. Local upload is still available.")
        if auth and auth.owner:
            st.caption("Signed in: "+auth.display_name)
            disconnect = st.button("Disconnect Microsoft")
        elif auth and auth.pending:
            st.info("Complete Microsoft sign-in in the browser window. You can keep using the demo.")
        elif auth and auth.error_code:
            st.info("Microsoft sign-in was not completed. Try again, or ask your IT administrator to approve this app.")
        label = "Reconnect Microsoft" if auth and auth.owner else "Sign in with Microsoft"
        sign_in = st.button(label,disabled=not settings.configured or bool(auth and auth.pending))
        with st.form("microsoft_import"):
            url = st.text_area("Presentation link",height=110,max_chars=4096,
                               placeholder="https://freud.sharepoint.com/:p:/r/sites/.../presentation.pptx")
            submitted = st.form_submit_button("Import presentation",width="stretch")
        st.caption("Use the full PowerPoint URL containing its file path. Your cloud file is only read; the original is not changed.")
        # Render the form before rerunning so sign-in does not clear its URL draft.
        if disconnect:
            auth.close()
            st.rerun()
        if sign_in:
            if auth:
                auth.close()
            auth = MicrosoftAuth(settings)
            st.session_state["_microsoft_auth"] = auth
            auth.start()
            st.rerun()
        if polling and auth and not auth.pending:
            st.rerun()
        if submitted:
            try:
                manager.submit_cloud_import(url,cloud_auth())
                st.session_state["notice"] = "Presentation queued for import. You can keep working while it processes."
                st.rerun()
            except IngestionError as exc:
                st.error(exc.user_message("SHAREPOINT"))
            except TaskQueueFull as exc:
                st.warning(str(exc))
    panel()
