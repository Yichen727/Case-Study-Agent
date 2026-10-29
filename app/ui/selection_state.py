"""Ordered case selection, independent of widgets, searches, and page lifetime."""


def selected_ids(state):
    return tuple(state.get("selected_case_ids",()))


def _store(state, order):
    order = tuple(order)
    if order != selected_ids(state):
        state["selected_case_ids"] = order
        state["confirmed_case_ids"] = None
        state["selection_revision"] = state.get("selection_revision",0)+1


def select_case(state, case_id, selected):
    current = selected_ids(state)
    if selected and case_id not in current:
        _store(state,(*current,case_id))
    elif not selected:
        remove_cases(state,[case_id])


def remove_cases(state, case_ids):
    removed = set(case_ids)
    _store(state,(case_id for case_id in selected_ids(state) if case_id not in removed))


def reorder_cases(state, order):
    order = tuple(order)
    current = selected_ids(state)
    if len(order)!=len(current) or set(order)!=set(current):
        raise ValueError("The new order must contain each selected case exactly once.")
    _store(state,order)


def confirm_order(state):
    current = selected_ids(state)
    if not current:
        raise ValueError("Select at least one case before confirming the order.")
    state["confirmed_case_ids"] = current


def export_order_snapshot(state):
    """Phase 7 captures this immutable order when submitting an export task."""
    current = selected_ids(state)
    confirmed = state.get("confirmed_case_ids")
    if not current or confirmed is None or tuple(confirmed)!=current:
        raise ValueError("Confirm the current selection and order before exporting.")
    return tuple(confirmed)
