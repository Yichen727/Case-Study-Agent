"""Evidence checks and explanations; vector similarity never proves a hard fact."""
import json
import re
import unicodedata
from functools import lru_cache

from app.models import missing_value, REQUIRED_CASE_FIELDS
from app.slide_text import case_text

CLASSIFIER_VERSION = "match-evidence-v5-clean-evidence"


@lru_cache(maxsize=8192)
def normal(text):
    text = unicodedata.normalize("NFKC",text or "").casefold()
    return re.sub(r"\s+"," ",re.sub(r"[\u2010-\u2015-]"," ",text)).strip()


@lru_cache(maxsize=1024)
def term_pattern(term):
    return re.compile(r"(?<!\w)"+re.escape(normal(term))+r"(?!\w)")


@lru_cache(maxsize=1024)
def evidence_pattern(term):
    value = normal(term)
    plural = r"s?" if value.isalpha() and len(value)>=4 and not value.endswith("s") else ""
    return re.compile(r"(?<!\w)"+re.escape(value)+plural+r"(?!\w)")


def contains(text, term):
    term = normal(term)
    return bool(term and term_pattern(term).search(normal(case_text(text))))


@lru_cache(maxsize=2048)
def sentences(text):
    return tuple(part.strip() for part in re.split(r"(?<=[.!?])\s+|\n+",case_text(text)) if part.strip())


@lru_cache(maxsize=2048)
def words(text):
    return frozenset(re.findall(r"[^\W_]+",normal(text)))


def quote_for(text, terms, *, negated=False):
    return _quote_for(text,tuple(terms),negated)


@lru_cache(maxsize=8192)
def _quote_for(text, terms, negated):
    for sentence in sentences(text):
        value = normal(sentence)
        for term in terms:
            match = evidence_pattern(term).search(value) if term else None
            position = match.start() if match else None
            if position is None:
                atomic_words = words(term) - {"a","an","the","and","or","of","for","to","with","in"}
                if not 2<=len(atomic_words)<=4 or not atomic_words.issubset(words(sentence)):
                    continue
                position = min(term_pattern(word).search(value).start() for word in atomic_words)
            is_negated = bool(re.search(r"\b(?:no|not|without|never)(?:\s+\w+){0,3}\s*$",value[:position]))
            if is_negated == negated:
                return sentence[:420]
    return None


def trusted(metadata):
    return bool(metadata and (metadata["verification_status"] == "VERIFIED" or
                metadata["source_kind"] in {"SLIDE_EXTRACTED","LINK_EXTRACTED"}))


def client_quote(original, terms):
    for sentence in sentences(original):
        value = normal(sentence)
        for term in terms:
            pattern = r"(?<!\w)"+re.escape(normal(term))+r"\s+(?:[\w']+\s+){0,4}(?:wanted|asked|needed|commissioned|tasked|invited|briefed)\b"
            if re.search(pattern,value):
                return sentence[:420]
    return None


METRICS = re.compile(r"\b(?:views?|viewers?|reach|impressions?|engagement rate|conversion(?: rate)?|positive sentiment|clicks?|downloads?|signups?)\b",re.I)
NUMBER = re.compile(r"\b(\d[\d,]*(?:\.\d+)?)\s*(million|billion|thousand|%|m\b|k\b)?",re.I)


def quantified_quote(impact, terms, minimum):
    metric_terms = [term for term in terms if METRICS.search(term)]
    for sentence in sentences(impact):
        if re.search(r"\b(?:TBC|target|aim|planned|projected|forecast|expected|objective)\b",sentence,re.I):
            continue
        if metric_terms and not any(contains(sentence,term) for term in metric_terms):
            continue
        metrics = list(METRICS.finditer(sentence))
        for number in NUMBER.finditer(sentence):
            if not metrics or min(abs(number.start()-metric.start()) for metric in metrics)>75:
                continue
            value = float(number[1].replace(",",""))
            unit = (number[2] or "").casefold()
            if not unit and 1900 <= value <= 2100:
                continue
            value *= {"million":1e6,"m":1e6,"billion":1e9,"thousand":1e3,"k":1e3}.get(unit,1)
            if minimum is None or value >= minimum:
                return sentence[:420]
    return None


def check_requirement(case, metadata, requirement):
    kind,terms = requirement.kind,requirement.terms
    raw = case["original_text"]
    status,quote,source = "UNKNOWN",None,"Original slide"
    if kind == "services":
        quote = quote_for(raw,terms)
        if quote:
            status = "MET"
        elif quote_for(raw,terms,negated=True):
            status,quote = "NOT_MET",quote_for(raw,terms,negated=True)
        elif trusted(metadata.get(kind)):
            quote = quote_for(case.get(kind),terms)
            if quote:
                status,source = "MET","Verified or extracted services"
        # A short services list is not an exhaustive declaration of everything
        # the team did. An omitted service is UNKNOWN, not a contradiction.
    elif kind in {"client","pillar"}:
        value = case_text(case.get(kind))
        if not missing_value(value) and trusted(metadata.get(kind)):
            status = "MET" if any(contains(value,term) for term in terms) else "NOT_MET"
            quote,source = value,"Verified or extracted " + kind
        elif kind == "client":
            quote = client_quote(raw,terms)
            if quote:
                status = "MET"
            elif not missing_value(value) and client_quote(raw,[value]):
                status,quote = "NOT_MET",client_quote(raw,[value])
        else:
            quote = quote_for(raw,terms)
            if quote:
                status = "MET"
    elif kind == "link_type":
        types = {item.get("link_type") for item in json.loads(case["source_link_labels_json"])}
        status = "MET" if any(term.upper().replace(" ","_") in types for term in terms) else "NOT_MET"
        quote,source = ", ".join(sorted(types)) or "No linked action","Original slide links"
    elif kind == "source":
        status = "MET" if any(contains(case["source_deck"],term) for term in terms) else "NOT_MET"
        quote,source = case["source_deck"],"Source deck"
    elif kind == "quantified_outcomes":
        impact = case.get("impact") if trusted(metadata.get("impact")) else ""
        quote = quantified_quote(impact,terms,requirement.minimum)
        if quote:
            status,source = "MET","Original or verified Impact"
    else:
        patterns = {
            "ai_production":["generative AI","AI generated","using AI","AI voice dubbing","synthetic clone","AI powered solution"],
            "interactive":["interactive","touch sensitive","motion sensitive","role play"],
            "no_additional_filming":["no need for additional filming","without repeat filming","without repeated shoots",
                                     "no cameras","no live action","every scene was generated using AI"],
        }
        evidence_terms = patterns.get(kind,terms)
        if kind == "ai_production":
            generic_words = {"ai","gen","generative","generated","led","driven","production","produced",
                             "in","using","used","content","workflow","workflows","tools","actual",
                             "make","made","making","create","created","creating","generate","generating",
                             "generation","artificial","intelligence"}
            specific = [term for term in terms if not words(term).issubset(generic_words)]
            if specific:
                evidence_terms = specific
        quote = quote_for(raw,evidence_terms)
        if quote:
            status = "MET"
        elif quote_for(raw,evidence_terms,negated=True):
            status,quote = "NOT_MET",quote_for(raw,evidence_terms,negated=True)
        elif kind == "content":
            # Only explicitly verified human metadata may supplement original text.
            for name,row in metadata.items():
                if name not in {"title","pillar","client","services","brief","creative","impact"}:
                    continue
                excerpt = quote_for(row.get("value"),terms) if trusted(row) else None
                if excerpt:
                    status,quote,source = "MET",excerpt,"Verified or extracted " + name
                    break
    return {"requirement":requirement.description,"status":status,"quote":quote,"source":source}


def mandatory_checks(case, metadata, intent):
    if intent is None:
        return []
    atomic = [check_requirement(case,metadata,req) for req in intent.mandatory]
    groups = intent.mandatory_groups or [[i] for i in range(len(atomic))]
    checks = []
    for group in groups:
        alternatives = [atomic[i] for i in group]
        # Three-valued OR: one supported alternative suffices; only all proven
        # contradictions fail. Missing evidence remains UNKNOWN.
        chosen = min(alternatives,key=lambda check:{"MET":0,"UNKNOWN":1,"NOT_MET":2}[check["status"]])
        check = dict(chosen)
        if len(group)>1:
            check["requirement"] = "Any of: " + "; ".join(item["requirement"] for item in alternatives)
            check["alternatives"] = alternatives
        checks.append(check)
    return checks


def classify(case, metadata, intent, semantic, lexical_coverage, keyword_terms):
    mandatory = mandatory_checks(case,metadata,intent)
    preferred = [check_requirement(case,metadata,req) for req in intent.preferred] if intent else []
    excluded = []
    for req in intent.excluded if intent else []:
        check = check_requirement(case,metadata,req)
        check["status"] = {"MET":"NOT_MET","NOT_MET":"MET","UNKNOWN":"UNKNOWN"}[check["status"]]
        excluded.append(check)
    checks = [*mandatory,*excluded]
    failures = [item for item in checks if item["status"] == "NOT_MET"]
    unknown = [item for item in checks if item["status"] == "UNKNOWN"]
    if intent:
        unknown.extend({"requirement":condition,"status":"UNKNOWN","quote":None,"source":"Request interpretation"} for condition in intent.unresolved_mandatory)
    topic_quote = quote_for(case["original_text"],keyword_terms)
    # Direct evidence for every requested hard condition outweighs a wording-based
    # similarity score. Unknown/excluded conditions still block High Match below.
    evidenced = bool(mandatory) and all(item["status"] == "MET" for item in mandatory)
    strong = evidenced or (semantic is not None and semantic>=0.52) or lexical_coverage>=0.60
    # One evidenced AND branch is a related example even with low similarity.
    # Topic checks below still prevent generic assets from proving an industry.
    related = any(item["status"] == "MET" for item in mandatory) or (semantic is not None and semantic>=0.40) or lexical_coverage>=0.10
    if failures or not related:
        level = "Not a Match"
        reason = "Conflicts with: " + "; ".join(item["requirement"] for item in failures) if failures else "Insufficient relevance to the requested theme."
    elif unknown or not strong or intent is None:
        level = "Partial Match"
        reason = "Not evidenced: " + "; ".join(item["requirement"] for item in unknown) if unknown else "Related work; some requested details or preferences remain unconfirmed."
    else:
        level = "High Match"
        reason = "The original evidence supports the requested theme and mandatory conditions."
    if level == "Partial Match":
        groups = (intent.mandatory_groups or [[i] for i in range(len(mandatory))]) if intent else []
        context_checks = [check for group,check in zip(groups,mandatory) if any(
            intent.mandatory[i].role == "context" or intent.mandatory[i].kind in {"client","pillar","source"}
            for i in group)]
        # Context comes from the interpreted request, not a vocabulary of output
        # names. Animation, websites and new deliverables share the same logic.
        supported = any(check["status"] == "MET" for check in (context_checks or mandatory))
        semantic_alternative = bool(context_checks) and semantic is not None and semantic>=0.52
        if mandatory and not supported and not semantic_alternative:
            level, reason = "Not a Match", "Insufficient evidence of relevance to the requested topic."
    evidence = [{"source":item["source"],"quote":item["quote"]} for item in [*mandatory,*preferred,*excluded] if item["quote"]]
    if topic_quote:
        evidence.append({"source":"Original slide","quote":topic_quote})
    unique = list({item["quote"]:item for item in evidence}.values())[:4]
    if level == "High Match" and not unique:
        level,reason = "Partial Match","Related metadata, but no original supporting excerpt is available."
    uncertainty = [item["requirement"] for item in unknown]
    missing = [name for name in REQUIRED_CASE_FIELDS if missing_value(case.get(name))]
    if missing:
        uncertainty.append("Missing metadata: " + ", ".join(missing))
    if case.get("unverified_ai_fields"):
        uncertainty.append("AI-inferred metadata: " + case["unverified_ai_fields"] + "; not used alone to prove mandatory conditions.")
    return {"match_level":level,"match_reason":reason,"evidence":unique,"condition_checks":checks,
            "preferred_checks":preferred,"uncertainties":uncertainty}
