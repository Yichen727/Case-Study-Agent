"""FTS5 + cached semantic retrieval and evidence-based match classes."""
from concurrent.futures import ThreadPoolExecutor
import re

from app.db.repository import CASE_SELECT, connect
from app.search.classification import classify, contains
from app.search.embedding_service import SearchServiceError, index_status, query_vector, semantic_scores
from app.search.intent import SearchIntent, interpret_query, normalize_query, MAX_QUERY_BYTES

STOPWORDS = set("a an the and or of to for in on with from by as at is are be been this that these those it its their our we i me my us find show looking need want case cases study studies example examples content work project projects please would should must include including have has also only can could do not rather than which when where how such all both each more most some any into across through using used use relevant original material evidence match matches result results".split())


class SearchResults(list):
    def __init__(self, items=(), *, mode="keyword", notices=(), intent=None, index=None,
                 intent_cache_hit=False, query_cache_hit=False):
        super().__init__(items)
        self.mode = mode
        self.notices = list(notices)
        self.intent = intent.model_dump() if intent else None
        self.index = index or {}
        self.intent_cache_hit = intent_cache_hit
        self.query_cache_hit = query_cache_hit


def keyword_terms(text):
    return list(dict.fromkeys(word.casefold() for word in re.findall(r"[^\W_]+",text,flags=re.UNICODE)
                             if len(word)>=2 and word.casefold() not in STOPWORDS))[:64]


def lexical_candidates(db_path, terms, limit=60):
    if not terms:
        return []
    expression = " OR ".join('"'+term+'"' for term in terms)
    with connect(db_path) as conn:
        return [row[0] for row in conn.execute(
            """SELECT case_id FROM cases_fts WHERE cases_fts MATCH ?
               ORDER BY bm25(cases_fts,0,5,2,3,2,1) LIMIT ?""",(expression,limit))]


def _safe_future(future):
    try:
        return future.result(),None
    except SearchServiceError as exc:
        return None,exc.code


def search_cases(db_path, query, limit=10, *, mandatory_conditions=(), preferred_conditions=()):
    """Search the shared library; explicit conditions supplement the parsed brief.

    The optional conditions are validated Requirement objects. Existing UI calls
    omit them and retain the same interpretation, retrieval and classification.
    """
    query = query.strip()
    if not query:
        return SearchResults()
    if len(query.encode("utf-8"))>MAX_QUERY_BYTES:
        raise ValueError("Please shorten the search request to within 6,000 UTF-8 bytes.")
    query = normalize_query(query)
    if not query:
        return SearchResults()
    readiness = index_status(db_path)
    if not readiness["total"]:
        return SearchResults(notices=["Import a presentation before searching."],index=readiness)
    with ThreadPoolExecutor(max_workers=2,thread_name_prefix="case-query") as pool:
        parsed = pool.submit(interpret_query,db_path,query)
        embedded = pool.submit(query_vector,db_path,query) if readiness["ready"] else None
        interpreted,intent_error = _safe_future(parsed)
        vector_result,vector_error = _safe_future(embedded) if embedded else (None,"INDEX_NOT_READY")
    intent,intent_hit = interpreted if interpreted else (None,False)
    notices = []
    if intent_error:
        notices.append("AI request interpretation is unavailable. Requirements remain unconfirmed; results use keyword matching.")
    supplied = [*mandatory_conditions,*preferred_conditions]
    if supplied:
        if intent is None:
            # Explicit conditions can still be checked, but must never imply that
            # other requirements in an uninterpreted brief have been satisfied.
            intent = SearchIntent(summary=query[:350],keywords=keyword_terms(query)[:24],
                                  mandatory=[],preferred=[],excluded=[],
                                  unresolved_mandatory=["Free-text requirements could not be interpreted."])
        intent = intent.model_copy(update={
            "mandatory":[*intent.mandatory,*mandatory_conditions],
            "preferred":[*intent.preferred,*preferred_conditions],
        })
    scores = semantic_scores(db_path,vector_result[0]) if vector_result and intent and not intent_error else {}
    if not scores:
        notices.append("Keyword-only search: semantic retrieval is unavailable or the index is not ready.")
    elif readiness["pending"]:
        notices.append("Some case vectors are updating; keyword retrieval still includes those cases.")
    terms = keyword_terms(" ".join(intent.keywords)) if intent else keyword_terms(query)
    if supplied:
        condition_text = " ".join(req.description + " " + " ".join(req.terms) for req in supplied)
        terms = keyword_terms(" ".join(terms) + " " + condition_text)
    if not terms:
        terms = keyword_terms(query)
    lexical = lexical_candidates(db_path,terms)
    semantic = sorted(scores,key=scores.get,reverse=True)[:60]
    candidates = list(dict.fromkeys([*semantic,*lexical]))[:100]
    if not candidates:
        return SearchResults(mode="hybrid" if scores else "keyword",notices=notices,intent=intent,index=readiness,
                             intent_cache_hit=intent_hit,query_cache_hit=vector_result[1] if vector_result else False)
    marks = ",".join("?" for _ in candidates)
    with connect(db_path) as conn:
        rows = conn.execute(CASE_SELECT.replace("SELECT c.*,","SELECT c.*,d.original_text,d.document_text,")+
                            f" JOIN search_documents d ON d.case_id=c.case_id WHERE c.case_id IN ({marks}) "
                            "AND c.review_status IN ('CONFIRMED','NEEDS_REVIEW') GROUP BY c.case_id",candidates).fetchall()
        metadata_rows = conn.execute(f"""SELECT * FROM field_metadata WHERE case_id IN ({marks})
                                        AND field_name IN ('title','pillar','client','services','brief','creative','impact')""",candidates).fetchall()
    metadata = {case_id:{} for case_id in candidates}
    for row in metadata_rows:
        metadata[row["case_id"]][row["field_name"]] = dict(row)
    lex_rank = {case_id:rank for rank,case_id in enumerate(lexical,1)}
    sem_rank = {case_id:rank for rank,case_id in enumerate(semantic,1)}
    results = []
    for row in rows:
        case = dict(row)
        case_id = case["case_id"]
        coverage = sum(contains(case["document_text"],term) for term in terms)/max(1,len(terms))
        case.update(classify(case,metadata[case_id],intent,scores.get(case_id),coverage,terms))
        case["_rank"] = (2/(60+sem_rank[case_id]) if case_id in sem_rank else 0) + (1/(60+lex_rank[case_id]) if case_id in lex_rank else 0)
        results.append(case)
    order = {"High Match":0,"Partial Match":1,"Not a Match":2}
    results.sort(key=lambda case:(order[case["match_level"]],-case["_rank"],case["title"],case["case_id"]))
    return SearchResults(results[:max(1,min(limit,100))],mode="hybrid" if scores else "keyword",notices=notices,
                         intent=intent,index=readiness,intent_cache_hit=intent_hit,
                         query_cache_hit=vector_result[1] if vector_result else False)
