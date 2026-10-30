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


def select_cases(state, case_ids):
    """Append a batch once, retaining existing choices and their custom order."""
    _store(state,dict.fromkeys((*selected_ids(state),*case_ids)))


def clear_selection(state):
    _store(state,())


def remove_cases(state, case_ids):
    removed = set(case_ids)
    _store(state,(case_id for case_id in selected_ids(state) if case_id not in removed))


def reorder_cases(state, order):
    order = tuple(order)
    current = selected_ids(state)
    if len(order)!=len(current) or set(order)!=set(current):
        raise ValueError("The new order must contain each selected case exactly once.")
    _store(state,order)


def export_order_snapshot(state):
    """A Generate click confirms and captures the current order exactly once."""
    current = selected_ids(state)
    if not current:
        raise ValueError("Select at least one case before generating a presentation.")
    state["confirmed_case_ids"] = current
    return current
