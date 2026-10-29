from __future__ import annotations

from app.db.repository import CASE_SELECT, connect


def search_cases(db_path: str, query: str, limit: int = 10) -> list[dict]:
    """
    Simple MVP keyword search using LIKE.
    Replace with SQLite FTS5 after validating tokenisation and query behaviour.
    This deliberately does not pretend to be semantic search.
    """
    query = query.strip()
    if not query:
        return []

    pattern = f"%{query}%"
    with connect(db_path) as conn:
        rows = conn.execute(
            CASE_SELECT + """
            WHERE c.review_status IN ('CONFIRMED', 'NEEDS_REVIEW')
              AND (
                  c.title LIKE ? COLLATE NOCASE OR
                  COALESCE(c.pillar, '') LIKE ? COLLATE NOCASE OR
                  COALESCE(c.client, '') LIKE ? COLLATE NOCASE OR
                  COALESCE(c.services, '') LIKE ? COLLATE NOCASE OR
                  COALESCE(c.brief, '') LIKE ? COLLATE NOCASE OR
                  COALESCE(c.creative, '') LIKE ? COLLATE NOCASE OR
                  COALESCE(c.impact, '') LIKE ? COLLATE NOCASE OR
                  COALESCE(c.linked_page_text, '') LIKE ? COLLATE NOCASE
              )
            GROUP BY c.case_id
            ORDER BY
              CASE WHEN c.title LIKE ? COLLATE NOCASE THEN 0 ELSE 1 END,
              c.title COLLATE NOCASE, c.source_id, s.slide_number
            LIMIT ?
            """,
            (pattern, pattern, pattern, pattern, pattern, pattern, pattern, pattern, pattern, limit),
        ).fetchall()
        return [dict(row) for row in rows]
