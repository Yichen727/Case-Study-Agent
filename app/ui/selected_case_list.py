"""One small Streamlit component for draggable cases with an inline remove button."""
from streamlit.components.v2 import component


selected_case_list = component(
    "selected_case_list",
    html='<ol class="selected-case-list" aria-label="Selected cases in slide order"></ol>',
    css="""
    .selected-case-list { list-style: none; padding: 0; margin: 0; max-height: 480px;
        overflow-y: auto; counter-reset: slide; font-family: var(--st-font); color: var(--st-text-color); }
    .selected-case-card { position: relative; margin: 0 0 8px; padding: 12px 48px 12px 14px;
        border: 1px solid var(--st-border-color, #ddd); border-radius: 8px;
        background: var(--st-background-color, white); cursor: grab; }
    .selected-case-label { white-space: pre-line; line-height: 1.5; }
    .selected-case-label::before { counter-increment: slide; content: counter(slide) ". "; font-weight: bold; }
    .selected-case-card.dragging { opacity: 0.45; }
    .selected-case-card.drop-target { border-color: var(--st-primary-color, #ff006e); }
    .remove-case { position: absolute; top: 6px; right: 6px; width: 28px; height: 28px;
        border: 0; border-radius: 5px; background: transparent; color: inherit;
        font-size: 22px; line-height: 1; cursor: pointer; }
    .remove-case:hover, .remove-case:focus-visible { color: var(--st-primary-color, #ff006e);
        background: var(--st-secondary-background-color, #f6f6f6); }
    """,
    js="""
    export default function({parentElement, data, setTriggerValue}) {
        const list = parentElement.querySelector('.selected-case-list');
        list.replaceChildren();
        let dragged = null;
        for (const item of data.cases) {
            const card = document.createElement('li');
            card.className = 'selected-case-card';
            card.dataset.caseId = item.id;
            card.draggable = true;
            const label = document.createElement('div');
            label.className = 'selected-case-label';
            label.textContent = item.label;
            const close = document.createElement('button');
            close.className = 'remove-case';
            close.type = 'button';
            close.textContent = '\u00d7';
            close.title = 'Remove case';
            close.setAttribute('aria-label', 'Remove case: ' + item.label.replace(/\\n/g, ' · '));
            close.onpointerdown = event => event.stopPropagation();
            close.ondragstart = event => event.preventDefault();
            close.onclick = event => {
                event.stopPropagation();
                close.disabled = true;
                setTriggerValue('action', {type: 'remove', case_id: item.id});
            };
            card.append(label, close);
            card.ondragstart = event => {
                if (event.target.closest('button')) { event.preventDefault(); return; }
                dragged = card;
                event.dataTransfer.effectAllowed = 'move';
                event.dataTransfer.setData('text/plain', item.id);
                card.classList.add('dragging');
            };
            card.ondragover = event => {
                if (dragged && dragged !== card) {
                    event.preventDefault();
                    event.dataTransfer.dropEffect = 'move';
                    card.classList.add('drop-target');
                }
            };
            card.ondragleave = () => card.classList.remove('drop-target');
            card.ondrop = event => {
                event.preventDefault();
                if (!dragged || dragged === card) return;
                const cards = Array.from(list.children);
                list.insertBefore(dragged, cards.indexOf(dragged) < cards.indexOf(card) ? card.nextSibling : card);
                setTriggerValue('action', {type: 'reorder', case_ids: Array.from(list.children, row => row.dataset.caseId)});
            };
            card.ondragend = () => {
                dragged = null;
                for (const row of list.children) row.classList.remove('dragging', 'drop-target');
            };
            list.appendChild(card);
        }
    }
    """,
)
