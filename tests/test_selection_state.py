import pytest

from app.ui.selection_state import (
    confirm_order, export_order_snapshot, remove_cases, reorder_cases, select_case, selected_ids,
)
from app.ui.selection_panel import selection_labels


def test_selection_labels_accept_previous_lightweight_rows_without_guessing_status():
    case = {"case_id":"source-a:256","title":"Same case","source_deck":"source.pptx","slide_numbers":"1"}
    labels = selection_labels([case])
    assert labels[case["case_id"]] == "Same case\nsource.pptx · slide 1"
    for stored,display in (("CONFIRMED","Confirmed"),("NEEDS_REVIEW","Needs Review")):
        assert selection_labels([dict(case,review_status=stored)])[case["case_id"]].endswith("\n"+display)


def test_ordered_independent_slides_confirmation_and_export_snapshot():
    state = {}
    for case_id in ("source-a:256","source-b:256","source-a:257","source-a:256"):
        select_case(state,case_id,True)
    assert selected_ids(state) == ("source-a:256","source-b:256","source-a:257")
    with pytest.raises(ValueError,match="Confirm"):
        export_order_snapshot(state)
    confirm_order(state)
    snapshot = export_order_snapshot(state)
    select_case(state,"source-a:256",True)
    assert export_order_snapshot(state) == snapshot  # A no-op does not invalidate confirmation.
    reorder_cases(state,reversed(snapshot))
    assert state["confirmed_case_ids"] is None
    with pytest.raises(ValueError,match="Confirm"):
        export_order_snapshot(state)
    confirm_order(state)
    remove_cases(state,["source-b:256"])
    assert state["confirmed_case_ids"] is None
    assert selected_ids(state)==("source-a:257","source-a:256")
    assert snapshot==("source-a:256","source-b:256","source-a:257")
    select_case(state,"source-b:256",True)
    assert selected_ids(state)[-1]=="source-b:256"
    select_case(state,"source-a:256",False)
    confirm_order(state)
    select_case(state,"source-c:256",True)
    assert state["confirmed_case_ids"] is None


def test_invalid_drag_payload_cannot_drop_or_duplicate_cases():
    state = {}
    for case_id in ("a","b"):
        select_case(state,case_id,True)
    for invalid in (("a",),("a","a"),("a","c")):
        with pytest.raises(ValueError):
            reorder_cases(state,invalid)
        assert selected_ids(state)==("a","b")
    remove_cases(state,["a","b"])
    with pytest.raises(ValueError):
        confirm_order(state)
