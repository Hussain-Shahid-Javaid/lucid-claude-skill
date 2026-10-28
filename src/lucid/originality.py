"""Original idea generation and prior-art checking.

What this module does:
* Records an invention study: the target problem, the candidate ideas, every
  prior-art search attempt (including the ones that could not run), the sources
  found and whether each was inspected, independent role assessments, and a value test.
* Assigns each candidate an originality status from that record. The status is
  scoped to what was actually searched.

What it does not do:
* It does not prove an idea is new. "No match found" is never turned into
  "nobody has thought of this".
* Similarity scores come from a small concept thesaurus plus word overlap. They help
  find what to inspect. They are not judgments of novelty.
* Lucid cannot browse the web. External results come from the host assistant's own
  search tool and are logged here with `log_search` and `add_source`.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from importlib import resources
from typing import Any, Callable, Iterable, Protocol

from .db import Store
from .memory import Evidence, MemoryStore, Scope
from .redact import redact
from .util import dumps, loads, new_id, sha256

STATUSES = {
    "known": "Known or substantially equivalent to existing work",
    "adaptation": "Adaptation or incremental improvement of existing work",
    "potentially_original": "Potentially original within the documented search scope",
    "unresolved": "Originality unresolved",
    "not_assessed": "Not assessed",
}
SCOPE_LABELS = {
    "new_to_user": "not found in this user's Lucid records",
    "new_within_searched_collection": "no equivalent found in the collections that were searched",
    "potentially_new_more_broadly": "no equivalent found across a broad documented search",
}
METHODS = {"challenge_assumption", "combine_fields", "change_constraint", "reverse_process",
           "remove_dependency", "new_method"}
LEVELS = {"mechanism", "implementation", "application", "combination"}
QUERY_KINDS = ("problem", "mechanism", "synonym", "adjacent", "component", "combination")
REQUIRED_QUERY_KINDS = ("problem", "mechanism", "synonym")
SEARCH_STATUSES = {"ran", "unavailable", "not_authorized", "skipped_confidential"}
SOURCE_KINDS = {"papers", "docs", "products", "repos", "standards", "patents", "web"}
BROAD_KINDS = {"papers", "products", "repos"}   # all three must be covered for the broader label
RELATIONS = {"equivalent", "adaptation", "different", "unclear"}
ROLES = {"ideation", "prior_art", "skeptic"}

INTERNAL_PROVIDER = "lucid:internal"
LOCAL_PROVIDER = "local:reference"

# Thresholds. Tuned on the dev probes only (evals/originality_probes.jsonl, split=dev).
DUPLICATE_T = 0.72     # internal: at or above this, treat as the same idea as an earlier one
SIBLING_T = 0.72       # two candidates in one study this close are not substantively different
CLOSE_T = 0.30         # sources at or above this must be inspected before any originality claim
TOKEN_KNOWN_T = 0.60   # word overlap this high counts as equivalent even with few recognised concepts
SAME_CORE_TOKEN_T = 0.25  # identical concept sets also need this much word overlap to count as equivalent
CONTAINS_TOKEN_T = 0.20   # candidate holds a source's whole mechanism plus more: needs this much word overlap
INSPECT_TOP = 3
COSMETIC_CONCEPTS = {"ai"}

AGREEMENT_NOTE = ("Roles assessed independently. Agreement between them is not evidence; "
                  "the status comes from the recorded searches, sources and checks.")
NOT_PROOF = "This is not proof that nobody has thought of it."


class OriginalityError(ValueError):
    pass


class ProviderUnavailable(RuntimeError):
    pass


class Provider(Protocol):
    name: str
    external: bool          # True if queries leave this machine
    covers: tuple[str, ...]  # source kinds this provider searches

    def search(self, query: str) -> list[dict]: ...


# ------------------------------------------------------------------ text comparison
_STOP = {"the", "a", "an", "of", "to", "and", "or", "is", "are", "was", "be", "it", "that", "this", "in", "on",
         "for", "with", "by", "as", "at", "from", "so", "each", "its", "into", "when", "then", "than", "can",
         "use", "using", "uses", "used", "which", "we", "our", "you", "your", "they", "one", "only", "not",
         "no", "any", "all", "more", "less", "new", "way", "system", "method", "approach", "idea", "tool",
         "based", "after", "before", "same", "other", "every", "do", "does"}

_CONCEPTS: dict[str, list[str]] | None = None


def _concept_table() -> dict[str, list[str]]:
    global _CONCEPTS
    if _CONCEPTS is None:
        raw = (resources.files("lucid") / "data" / "concepts.json").read_text(encoding="utf-8")
        _CONCEPTS = json.loads(raw)
    return _CONCEPTS


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s\-]", " ", (text or "").lower())).strip()


def concepts(text: str) -> set[str]:
    """Concept ids whose phrases appear in the text (tolerates simple plural/past endings)."""
    t = " " + _norm(text) + " "
    found = set()
    for cid, phrases in _concept_table().items():
        for ph in phrases:
            # every word may carry a simple ending: "try again" also matches "trying again"
            p = r"[\s\-]+".join(re.escape(w) + r"(?:s|es|d|ed|ing)?" for w in _norm(ph).split())
            if re.search(rf"(?<![\w]){p}(?![\w])", t):
                found.add(cid)
                break
    return found


def _stem(w: str) -> str:
    for suf in ("ations", "ation", "ings", "ing", "ies", "ed", "es", "s"):
        if len(w) > len(suf) + 2 and w.endswith(suf):
            return w[: -len(suf)]
    return w


def tokens(text: str) -> set[str]:
    return {_stem(w) for w in re.findall(r"[a-z][a-z\-]+", (text or "").lower()) if w not in _STOP and len(w) > 2}


@dataclass
class Similarity:
    score: float
    cov_a: float          # share of A's concepts that B also has
    cov_b: float          # share of B's concepts that A also has
    shared: list[str]
    only_a: list[str]
    only_b: list[str]
    token_overlap: float

    def as_dict(self) -> dict:
        return {"score": round(self.score, 3), "cov_a": round(self.cov_a, 3), "cov_b": round(self.cov_b, 3),
                "shared": self.shared, "only_a": self.only_a, "only_b": self.only_b,
                "token_overlap": round(self.token_overlap, 3)}


def similarity(a: str, b: str) -> Similarity:
    """Retrieval aid only: concept overlap through the thesaurus, plus word overlap."""
    ca, cb = concepts(a), concepts(b)
    ta, tb = tokens(a), tokens(b)
    shared = ca & cb
    cov_a = len(shared) / len(ca) if ca else 0.0
    cov_b = len(shared) / len(cb) if cb else 0.0
    tj = len(ta & tb) / len(ta | tb) if (ta or tb) else 0.0
    if ca and cb:
        score = 0.45 * cov_a + 0.35 * cov_b + 0.20 * tj
    else:
        score = tj
    return Similarity(score, cov_a, cov_b, sorted(shared), sorted(ca - cb), sorted(cb - ca), tj)


def _candidate_text(c: dict) -> str:
    """What gets compared: the mechanism, its parts and the application domain.
    The title is excluded on purpose so a new name cannot make a known idea look new."""
    parts = [c.get("mechanism") or ""] + list(c.get("components") or []) + [c.get("domain") or ""]
    return " ".join(p for p in parts if p)


# ------------------------------------------------------------------ providers
class LocalCollectionProvider:
    """Searches a documented local reference collection (JSON lines: id, kind, ref, title, summary)."""

    external = False

    def __init__(self, path: str | None = None, name: str = LOCAL_PROVIDER, min_score: float = 0.15, top: int = 5):
        self.name = name
        self.min_score = min_score
        self.top = top
        if path:
            with open(path, encoding="utf-8") as fh:
                raw = fh.read()
        else:
            raw = (resources.files("lucid") / "data" / "originality_reference.jsonl").read_text(encoding="utf-8")
        self.docs = [json.loads(line) for line in raw.splitlines() if line.strip()]
        self.covers = tuple(sorted({d["kind"] for d in self.docs}))

    def search(self, query: str) -> list[dict]:
        scored = []
        for d in self.docs:
            sim = similarity(query, d["title"] + ". " + d["summary"])
            if sim.score >= self.min_score:
                scored.append((sim.score, d))
        scored.sort(key=lambda x: -x[0])
        return [dict(d, inspected=True) for _, d in scored[: self.top]]


class UnavailableProvider:
    """Stands in for a provider that cannot be reached (offline, no credentials)."""

    external = True

    def __init__(self, name: str, covers: Iterable[str] = ("web",), reason: str = "provider not reachable"):
        self.name = name
        self.covers = tuple(covers)
        self.reason = reason

    def search(self, query: str) -> list[dict]:
        raise ProviderUnavailable(self.reason)


# ------------------------------------------------------------------ main API
class Originality:
    def __init__(self, store: Store, memory: MemoryStore):
        self.s = store
        self.mem = memory

    # ---------------------------------------------------------------- A. target
    def create_study(self, problem: str, *, scope: Scope = Scope(), users: str = "", constraints: Iterable[str] = (),
                     resources_: Iterable[str] = (), existing_approaches: Iterable[str] = (), improvement: str = "",
                     level: str = "mechanism", confidential_terms: Iterable[str] = (),
                     allowed_providers: Iterable[str] = (LOCAL_PROVIDER,),
                     required_providers: Iterable[str] | None = None, ledger_id: str | None = None) -> dict:
        if not problem or not problem.strip():
            raise OriginalityError("describe the problem first")
        if level not in LEVELS:
            raise OriginalityError(f"level must be one of {sorted(LEVELS)}")
        allowed = list(dict.fromkeys(allowed_providers))
        required = list(required_providers) if required_providers is not None else list(allowed)
        if set(required) - set(allowed):
            raise OriginalityError("a required provider must also be allowed")
        target = {"problem": redact(problem.strip()), "users": users, "constraints": list(constraints),
                  "resources": list(resources_), "existing_approaches": list(existing_approaches),
                  "improvement": improvement, "level": level, "confidential_terms": list(confidential_terms),
                  "allowed_providers": allowed, "required_providers": required}
        sid = new_id("study")
        now = self.s.clock.iso()
        self.s.ex("INSERT INTO invention_studies (id, user_scope, project_scope, target, ledger_id, created_at,"
                  " updated_at) VALUES (?,?,?,?,?,?,?)", (sid, scope.user, scope.project, dumps(target), ledger_id,
                                                         now, now))
        self.s.log("user", "invent.study", "invention_study", sid, {"level": level})
        return self.study(sid)

    def study(self, sid: str) -> dict:
        r = self.s.one("SELECT * FROM invention_studies WHERE id=?", (sid,))
        if not r:
            raise KeyError(sid)
        d = dict(r)
        d["target"] = loads(d["target"], {})
        d["candidates"] = [x[0] for x in self.s.q(
            "SELECT id FROM invention_candidates WHERE study_id=? ORDER BY created_at, rowid", (sid,))]
        return d

    # ---------------------------------------------------------------- B. candidates
    def add_candidate(self, study_id: str, title: str, mechanism: str, difference: str, method: str, *,
                      advantage: str | None = None, components: Iterable[str] = (), domain: str | None = None,
                      revisit_of: str | None = None, revisit_reason: str | None = None) -> dict:
        st = self.study(study_id)
        if method not in METHODS:
            raise OriginalityError(f"method must be one of {sorted(METHODS)}")
        if revisit_of and not (revisit_reason and revisit_reason.strip()):
            raise OriginalityError("revisiting an earlier idea needs a reason: say what changed")
        comps = [c.strip() for c in components if c and c.strip()]
        cand: dict[str, Any] = {"title": title.strip(), "mechanism": (mechanism or "").strip(), "difference": (difference or "").strip(),
                "advantage": (advantage or "").strip(), "components": comps, "domain": domain, "method": method}
        flags = cosmetic_flags(cand)
        for other in st["candidates"]:
            o = self.candidate(other)
            sim = similarity(_candidate_text(cand), _candidate_text(o))
            if sim.score >= SIBLING_T:
                flags.append(f"near_duplicate_of:{other}")
        cid = new_id("cand")
        now = self.s.clock.iso()
        self.s.ex("INSERT INTO invention_candidates (id, study_id, title, mechanism, difference, method, advantage,"
                  " components, domain, revisit_of, revisit_reason, cosmetic_flags, created_at, updated_at)"
                  " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                  (cid, study_id, redact(cand["title"]), redact(cand["mechanism"]), redact(cand["difference"]),
                   method, redact(cand["advantage"]) or None, dumps(comps), domain, revisit_of, revisit_reason,
                   dumps(flags), now, now))
        self.s.log("lucid", "invent.candidate", "invention_candidate", cid, {"study": study_id, "flags": flags})
        return self.candidate(cid)

    def candidate(self, cid: str) -> dict:
        r = self.s.one("SELECT * FROM invention_candidates WHERE id=?", (cid,))
        if not r:
            raise KeyError(cid)
        d = dict(r)
        for k, dflt in (("components", []), ("cosmetic_flags", []), ("assessment", {}), ("value", {})):
            d[k] = loads(d[k], dflt)
        d["rejected"] = bool(d["rejected"])
        return d

    def reject(self, cid: str, reason: str) -> dict:
        c = self.candidate(cid)
        a = c["assessment"]
        a["rejected_reason"] = reason
        self.s.ex("UPDATE invention_candidates SET rejected=1, assessment=?, updated_at=? WHERE id=?",
                  (dumps(a), self.s.clock.iso(), cid))
        return self.candidate(cid)

    # ---------------------------------------------------------------- C. Lucid's own knowledge
    def internal_check(self, cid: str) -> dict:
        c = self.candidate(cid)
        st = self.study(c["study_id"])
        text = _candidate_text(c)
        exact = sha256(_norm(c["mechanism"]))
        matches = []
        rows = self.s.q(
            "SELECT c.*, s.created_at AS study_at FROM invention_candidates c JOIN invention_studies s"
            " ON s.id=c.study_id WHERE s.user_scope=? AND c.id<>? AND c.study_id<>?",
            (st["user_scope"], cid, c["study_id"]))
        for r in rows:
            o = self.candidate(r["id"])
            sim = similarity(text, _candidate_text(o))
            is_exact = sha256(_norm(o["mechanism"])) == exact
            if is_exact or sim.score >= CLOSE_T:
                matches.append({"type": "earlier_candidate", "id": o["id"], "study_id": o["study_id"],
                                "title": o["title"], "status": o["status"], "rejected": o["rejected"],
                                "exact": is_exact, "similarity": sim.as_dict(), "created_at": o["created_at"]})
        mems = self.s.q("SELECT id, kind, content, created_at FROM memories WHERE user_scope=? AND status='active'",
                        (st["user_scope"],))
        for m in mems:
            sim = similarity(text, m["content"])
            is_exact = sha256(_norm(m["content"])) == exact
            if is_exact or sim.score >= CLOSE_T:
                matches.append({"type": "memory", "id": m["id"], "kind": m["kind"], "title": m["content"][:80],
                                "exact": is_exact, "similarity": sim.as_dict(), "created_at": m["created_at"]})
        matches.sort(key=lambda x: (not x["exact"], -x["similarity"]["score"]))
        dup = next((m for m in matches if m["exact"] or m["similarity"]["score"] >= DUPLICATE_T), None)
        dismissed = (c["assessment"].get("internal") or {}).get("dismissed", {})
        result = {"checked": True, "matches": matches[:10], "note": "Similarity scores are retrieval aids, "
                  "not judgments of novelty.", "duplicate_of": None, "revisit": None, "possible_repeats": [],
                  "dismissed": dismissed}
        if dup:
            if c["revisit_of"] == dup["id"]:
                result["revisit"] = {"of": dup["id"], "what_changed": c["revisit_reason"]}
            else:
                result["duplicate_of"] = dup
        # Close but not certain: someone has to look before this can be presented as new.
        result["possible_repeats"] = [m for m in matches if m is not dup and m["id"] != c["revisit_of"]
                                      and m["id"] not in dismissed][:5]
        self.s.ex("INSERT INTO prior_art_searches (id, study_id, candidate_id, provider, query, query_kind, status,"
                  " result_count, note, searched_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                  (new_id("pas"), c["study_id"], cid, INTERNAL_PROVIDER, c["mechanism"][:200], "internal", "ran",
                   len(matches), "earlier candidates and memories in this user's scope", self.s.clock.iso()))
        a = c["assessment"]
        a["internal"] = result
        self._save_assessment(cid, a)
        return result

    def not_a_repeat(self, cid: str, match_id: str, reason: str) -> dict:
        """Record that someone compared the candidate with a close earlier record and found it different."""
        if not reason or not reason.strip():
            raise OriginalityError("say how it differs from the earlier record")
        c = self.candidate(cid)
        a = c["assessment"]
        internal = a.setdefault("internal", {"checked": False})
        internal.setdefault("dismissed", {})[match_id] = reason.strip()
        internal["possible_repeats"] = [m for m in internal.get("possible_repeats", []) if m["id"] != match_id]
        self._save_assessment(cid, a)
        return internal

    # ---------------------------------------------------------------- D. search
    def plan_queries(self, cid: str) -> list[dict]:
        c = self.candidate(cid)
        st = self.study(c["study_id"])
        t = st["target"]
        mech = c["mechanism"]
        cons = concepts(_candidate_text(c))
        table = _concept_table()
        qs = [("problem", t["problem"]), ("mechanism", mech)]
        if cons:
            syn = []
            for cid_ in sorted(cons):
                alts = [p for p in table[cid_] if _norm(p) not in _norm(mech)]
                syn.append(alts[0] if alts else table[cid_][0])
            qs.append(("synonym", " ".join(syn)))
        else:
            qs.append(("synonym", " ".join(sorted(tokens(mech)))))
        core = [x for x in sorted(cons) if x not in COSMETIC_CONCEPTS]
        if c.get("domain") or core:
            qs.append(("adjacent", " ".join(table[x][0] for x in core[:4]) + " in other industries"))
        for comp in c["components"]:
            qs.append(("component", comp))
        if len(c["components"]) >= 2:
            qs.append(("combination", " + ".join(c["components"])))
        terms = [x for x in t.get("confidential_terms", []) if x]
        out = []
        for kind, q in qs:
            ext = q
            for term in terms:
                ext = re.sub(re.escape(term), " ", ext, flags=re.I)
            ext = re.sub(r"\s+", " ", ext).strip()
            out.append({"kind": kind, "query": q, "external_query": ext,
                        "confidential_removed": ext != q.strip()})
        return out

    def search(self, cid: str, providers: Iterable[Provider]) -> dict:
        c = self.candidate(cid)
        st = self.study(c["study_id"])
        allowed = set(st["target"]["allowed_providers"])
        plan = self.plan_queries(cid)
        summary = {"ran": 0, "failed": 0, "not_authorized": 0, "skipped_confidential": 0, "sources_added": 0,
                   "provider_calls": 0}
        for p in providers:
            if p.name not in allowed:
                self.log_search(cid, p.name, "(not sent)", "mechanism", "not_authorized",
                                note="provider is not in this study's allowed list")
                summary["not_authorized"] += 1
                continue
            for q in plan:
                text = q["external_query"] if p.external else q["query"]
                if p.external and len(tokens(text)) < 2:
                    self.log_search(cid, p.name, "(withheld)", q["kind"], "skipped_confidential",
                                    note="query would have revealed confidential terms")
                    summary["skipped_confidential"] += 1
                    continue
                summary["provider_calls"] += 1
                try:
                    hits = p.search(text)
                except ProviderUnavailable as e:
                    self.log_search(cid, p.name, text, q["kind"], "unavailable", note=str(e))
                    summary["failed"] += 1
                    continue
                self.log_search(cid, p.name, text, q["kind"], "ran", result_count=len(hits),
                                covers=list(getattr(p, "covers", ())),
                                note="confidential terms removed" if (p.external and q["confidential_removed"]) else None)
                summary["ran"] += 1
                for h in hits:
                    if self._add_source_once(cid, p.name, h, found_by=q["kind"]):
                        summary["sources_added"] += 1
        return summary

    def log_search(self, cid: str, provider: str, query: str, kind: str, status: str, *, result_count: int = 0,
                   note: str | None = None, covers: Iterable[str] = ()) -> str:
        """Record a search attempt. Used directly by the host for its own web searches."""
        if status not in SEARCH_STATUSES:
            raise OriginalityError(f"status must be one of {sorted(SEARCH_STATUSES)}")
        if kind not in QUERY_KINDS:
            raise OriginalityError(f"query kind must be one of {list(QUERY_KINDS)}")
        bad = set(covers) - SOURCE_KINDS
        if bad:
            raise OriginalityError(f"unknown source kinds {sorted(bad)}")
        c = self.candidate(cid)
        sid = new_id("pas")
        self.s.ex("INSERT INTO prior_art_searches (id, study_id, candidate_id, provider, query, query_kind, status,"
                  " result_count, note, covers, searched_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                  (sid, c["study_id"], cid, provider, redact(query), kind, status, result_count, note,
                   dumps(sorted(set(covers))), self.s.clock.iso()))
        return sid

    def add_source(self, cid: str, provider: str, *, kind: str, ref: str, title: str, summary: str,
                   inspected: bool = False, relation: str | None = None, differences: str | None = None,
                   found_by: str | None = None) -> dict:
        """Record a source. `inspected=True` means someone actually read it, not just saw a search snippet."""
        if kind not in SOURCE_KINDS:
            raise OriginalityError(f"source kind must be one of {sorted(SOURCE_KINDS)}")
        if relation and relation not in RELATIONS:
            raise OriginalityError(f"relation must be one of {sorted(RELATIONS)}")
        if relation and not inspected:
            raise OriginalityError("only an inspected source can be given a relation")
        if not ref.strip():
            raise OriginalityError("a source needs a reference (URL, DOI or citation)")
        c = self.candidate(cid)
        sim = similarity(_candidate_text(c), title + ". " + summary)
        sid = new_id("paso")
        self.s.ex("INSERT INTO prior_art_sources (id, study_id, candidate_id, provider, kind, ref, title, summary,"
                  " inspected, similarity, found_by, relation, differences, added_at)"
                  " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                  (sid, c["study_id"], cid, provider, kind, ref.strip(), title, redact(summary), int(inspected),
                   round(sim.score, 4), found_by, relation, differences, self.s.clock.iso()))
        return self.source(sid)

    def _add_source_once(self, cid: str, provider: str, hit: dict, found_by: str) -> bool:
        if self.s.one("SELECT 1 FROM prior_art_sources WHERE candidate_id=? AND ref=?", (cid, hit["ref"])):
            return False
        kind = str(hit["kind"]) if hit.get("kind") in SOURCE_KINDS else "web"
        self.add_source(cid, provider, kind=kind, ref=hit["ref"], title=hit.get("title", ""),
                        summary=hit.get("summary", ""), inspected=bool(hit.get("inspected")), found_by=found_by)
        return True

    def inspect(self, source_id: str, *, relation: str | None = None, differences: str | None = None) -> dict:
        if relation and relation not in RELATIONS:
            raise OriginalityError(f"relation must be one of {sorted(RELATIONS)}")
        self.s.ex("UPDATE prior_art_sources SET inspected=1, relation=COALESCE(?, relation),"
                  " differences=COALESCE(?, differences) WHERE id=?", (relation, differences, source_id))
        return self.source(source_id)

    def source(self, sid: str) -> dict:
        r = self.s.one("SELECT * FROM prior_art_sources WHERE id=?", (sid,))
        if not r:
            raise KeyError(sid)
        d = dict(r)
        d["inspected"] = bool(d["inspected"])
        return d

    def sources(self, cid: str) -> list[dict]:
        return [self.source(r[0]) for r in self.s.q(
            "SELECT id FROM prior_art_sources WHERE candidate_id=? ORDER BY similarity DESC, added_at", (cid,))]

    def searches(self, cid: str) -> list[dict]:
        out = []
        for r in self.s.q("SELECT * FROM prior_art_searches WHERE candidate_id=? ORDER BY searched_at, rowid", (cid,)):
            d = dict(r)
            d["covers"] = loads(d["covers"], [])
            out.append(d)
        return out

    # ---------------------------------------------------------------- E. compare + status
    def assess(self, cid: str) -> dict:
        c = self.candidate(cid)
        st = self.study(c["study_id"])
        t = st["target"]
        a = c["assessment"]
        text = _candidate_text(c)
        cand_concepts = concepts(text)
        reasons: list[str] = []
        limitations: list[str] = []
        flags = list(c["cosmetic_flags"])

        internal = a.get("internal")
        searches = self.searches(cid)
        external = [s for s in searches if s["provider"] != INTERNAL_PROVIDER]
        ran = [s for s in external if s["status"] == "ran"]
        failed = [s for s in external if s["status"] in ("unavailable", "not_authorized", "skipped_confidential")]
        ran_providers = sorted({s["provider"] for s in ran})
        failed_required = sorted({s["provider"] for s in failed if s["provider"] in t["required_providers"]
                                  and s["status"] in ("unavailable", "not_authorized")})
        never_tried = sorted(set(t["required_providers"]) - {s["provider"] for s in external})
        kinds_ran = {s["query_kind"] for s in ran}
        covers = sorted({k for s in ran for k in s["covers"]})
        for s in failed:
            limitations.append(f"{s['provider']}: {s['status']}" + (f" ({s['note']})" if s["note"] else ""))
        limitations = sorted(set(limitations))

        srcs = self.sources(cid)
        comparisons = []
        for s in srcs:
            sim = similarity(text, s["title"] + ". " + s["summary"])
            comparisons.append({"source_id": s["id"], "ref": s["ref"], "title": s["title"], "kind": s["kind"],
                                "provider": s["provider"], "inspected": s["inspected"], "relation": s["relation"],
                                "similarity": sim.as_dict(), "_sim": sim, "summary": s["summary"],
                                "differences_noted": s["differences"]})
        comparisons.sort(key=lambda x: -x["similarity"]["score"])
        closest = comparisons[:INSPECT_TOP]

        status = None
        basis = None
        # 1. Lucid already proposed or recorded this.
        if internal and internal.get("duplicate_of"):
            d = internal["duplicate_of"]
            status = "known"
            basis = {"type": "internal_duplicate", "match": d}
            reasons.append(f"Same idea as {d['type'].replace('_', ' ')} {d['id']} already in Lucid's records.")
        # 2. An inspector marked a source as equivalent or an adaptation.
        if status is None:
            eq = [x for x in comparisons if x["inspected"] and x["relation"] == "equivalent"]
            ad = [x for x in comparisons if x["inspected"] and x["relation"] == "adaptation"]
            if eq:
                status, basis = "known", {"type": "inspected_equivalent", "source": eq[0]["ref"]}
                reasons.append(f"An inspected source describes the same mechanism: {eq[0]['title']}.")
            elif ad:
                status, basis = "adaptation", {"type": "inspected_adaptation", "source": ad[0]["ref"]}
                reasons.append(f"An inspected source covers the core of it: {ad[0]['title']}.")
        # 3. Computed comparison against inspected sources.
        if status is None:
            for x in comparisons:
                sim = x["_sim"]
                if x["relation"] == "different":
                    continue
                extra = set(sim.only_a)
                core_a = set(sim.shared) | (extra - COSMETIC_CONCEPTS)
                same_core = bool(core_a) and core_a == set(sim.shared) and not sim.only_b \
                    and sim.token_overlap >= SAME_CORE_TOKEN_T
                if (len(sim.shared) >= 2 and sim.cov_b >= 0.5 and extra <= COSMETIC_CONCEPTS) or same_core or \
                        sim.token_overlap >= TOKEN_KNOWN_T:
                    status, basis = "known", {"type": "computed_equivalent", "source": x["ref"]}
                    reasons.append(f"Mechanism matches {x['title']} with different wording.")
                    if extra & COSMETIC_CONCEPTS and "ai_washing" not in flags:
                        flags.append("ai_washing")
                    break
            if status is None:
                for x in comparisons:
                    sim = x["_sim"]
                    if x["relation"] == "different":
                        continue
                    # The whole known mechanism is inside the candidate, plus something more.
                    contains_known = bool(sim.shared) and not sim.only_b and bool(sim.only_a) \
                        and sim.token_overlap >= CONTAINS_TOKEN_T
                    if (len(sim.shared) >= 2 and sim.cov_a >= 0.5 and sim.cov_b >= 0.5) or contains_known:
                        status, basis = "adaptation", {"type": "computed_adaptation", "source": x["ref"],
                                                       "adds": sim.only_a}
                        reasons.append(f"Applies or extends {x['title']}; adds {', '.join(sim.only_a) or 'little'}.")
                        break
            if status is None:
                combo = _combination(cand_concepts, comparisons)
                if combo:
                    status, basis = "adaptation", {"type": "combination", "sources": [combo[0], combo[1]]}
                    reasons.append("Combines known pieces: " + " + ".join(combo[2]) + ".")
                    if not c["advantage"] and "unexplained_combination" not in flags:
                        flags.append("unexplained_combination")
        # 4. Nothing equivalent found: is the search good enough to say anything?
        if status is None:
            blockers = []
            if not external:
                blockers.append("no prior-art search was run")
            if external and not ran:
                blockers.append("every search attempt failed or was not allowed")
            if failed_required:
                blockers.append("required search did not run: " + ", ".join(failed_required))
            if never_tried and external:
                blockers.append("required provider never searched: " + ", ".join(never_tried))
            missing = [k for k in REQUIRED_QUERY_KINDS if k not in kinds_ran]
            if c["components"] and "component" not in kinds_ran:
                missing.append("component")
            if ran and missing:
                blockers.append("query kinds not covered: " + ", ".join(missing))
            uninspected = [x for x in closest if not x["inspected"] and x["similarity"]["score"] >= CLOSE_T]
            if uninspected:
                blockers.append("closest sources not inspected: " + ", ".join(x["ref"] for x in uninspected))
            if len(cand_concepts - COSMETIC_CONCEPTS) < 2 and not any(x["inspected"] and x["relation"] for x in closest):
                blockers.append("too few recognised concepts for a reliable automatic comparison; "
                                "an inspected comparison is needed")
            reps = (internal or {}).get("possible_repeats") or []
            if reps:
                blockers.append("may repeat earlier records: " + ", ".join(m["id"] for m in reps)
                                + " (compare them, then use revisit or not-a-repeat)")
            if not c["mechanism"] or "no_mechanism" in flags:
                blockers.append("no mechanism described")
            if not c["difference"]:
                blockers.append("no specific difference stated")
            if not c["advantage"]:
                blockers.append("no practical advantage stated")
            if flags:
                blockers.append("cosmetic-novelty flags: " + ", ".join(flags))
            if not external:
                status = "not_assessed"
                reasons.append("External originality was not assessed: no authorized search ran.")
            elif blockers:
                status = "unresolved"
                reasons.extend(blockers)
            else:
                status = "potentially_original"
                reasons.append("No equivalent found in the searched collections, and the closest sources "
                               "were inspected.")

        scope_label = None
        if status == "potentially_original":
            scope_label = ("potentially_new_more_broadly" if BROAD_KINDS <= set(covers) and len(ran_providers) >= 1
                           and any(p != LOCAL_PROVIDER for p in ran_providers) else "new_within_searched_collection")
        elif status in ("not_assessed", "unresolved") and internal and internal.get("checked") \
                and not internal.get("duplicate_of"):
            scope_label = "new_to_user"

        precedents = []
        for x in closest:
            sim = x["_sim"]
            precedents.append({
                "source_id": x["source_id"], "ref": x["ref"], "title": x["title"], "kind": x["kind"],
                "inspected": x["inspected"], "relation": x["relation"],
                "what_exists": x["summary"],
                "borrowed": sim.shared,
                "materially_different": sim.only_a + ([c["difference"]] if c["difference"] else []),
                "practical_advantage": c["advantage"] or None,
                "risky_similarities": sim.shared if sim.score >= CLOSE_T else [],
                "similarity": x["similarity"]["score"],
                "differences_noted": x["differences_noted"],
            })

        cost = {"queries_run": len(ran), "queries_failed": len(failed), "providers": ran_providers,
                "query_kinds": sorted(kinds_ran), "source_kinds_covered": covers, "sources_found": len(srcs),
                "sources_inspected": sum(1 for s in srcs if s["inspected"])}
        claim = claim_text(status, scope_label, ran_providers, len(ran), limitations, basis)
        a.update({"status": status, "status_text": STATUSES[status], "scope_label": scope_label,
                  "reasons": reasons, "limitations": limitations, "precedents": precedents, "basis": basis,
                  "coverage": cost, "claim": claim, "assessed_at": self.s.clock.iso(),
                  "note": "Similarity scores are retrieval aids, not judgments of novelty."})
        self.s.ex("UPDATE invention_candidates SET status=?, scope_label=?, assessment=?, cosmetic_flags=?,"
                  " updated_at=? WHERE id=?", (status, scope_label, dumps(a), dumps(flags), self.s.clock.iso(), cid))
        self.s.log("lucid", "invent.assess", "invention_candidate", cid, {"status": status})
        return a

    def check(self, cid: str, providers: Iterable[Provider] = ()) -> dict:
        """Internal check, then search, then assess."""
        self.internal_check(cid)
        provs = list(providers)
        if provs:
            self.search(cid, provs)
        return self.assess(cid)

    # ---------------------------------------------------------------- G. value
    def assess_value(self, cid: str, checks: dict[str, Callable[[dict], tuple[bool, str]]] | None = None, *,
                     feasibility: str | None = None, usefulness: str | None = None, resources_: str | None = None,
                     advantage_evidence: str | None = None, results: dict[str, tuple[bool, str]] | None = None) -> dict:
        """Value is tested separately from novelty. A failed correctness check makes the idea invalid,
        however original it looks."""
        c = self.candidate(cid)
        outcomes: dict[str, dict] = {}
        for name, fn in (checks or {}).items():
            try:
                ok, ev = fn(c)
            except Exception as e:  # a crashing check is a failed check, not a pass
                ok, ev = False, f"check raised {type(e).__name__}: {e}"
            outcomes[name] = {"passed": bool(ok), "evidence": redact(str(ev))[:1500]}
        for name, (ok, ev) in (results or {}).items():
            outcomes[name] = {"passed": bool(ok), "evidence": redact(str(ev))[:1500]}
        if any(not o["passed"] for o in outcomes.values()):
            vstatus = "invalid"
        elif outcomes:
            vstatus = "supported"
        else:
            vstatus = "untested"
        value = {"status": vstatus, "checks": outcomes, "feasibility": feasibility, "usefulness": usefulness,
                 "resources": resources_, "advantage_evidence": advantage_evidence,
                 "note": "Value was tested separately from originality."}
        self.s.ex("UPDATE invention_candidates SET value=?, updated_at=? WHERE id=?",
                  (dumps(value), self.s.clock.iso(), cid))
        return value

    # ---------------------------------------------------------------- H. independent roles
    def record_assessment(self, cid: str, role: str, verdict: str, rationale: str, *, saw_others: bool = False) -> str:
        if role not in ROLES:
            raise OriginalityError(f"role must be one of {sorted(ROLES)}")
        aid = new_id("ia")
        self.s.ex("INSERT INTO independent_assessments (id, candidate_id, role, verdict, rationale, saw_others,"
                  " created_at) VALUES (?,?,?,?,?,?,?)",
                  (aid, cid, role, verdict, redact(rationale), int(saw_others), self.s.clock.iso()))
        return aid

    def assessments(self, cid: str) -> dict:
        rows = [dict(r) for r in self.s.q(
            "SELECT role, verdict, rationale, saw_others, created_at FROM independent_assessments"
            " WHERE candidate_id=? ORDER BY created_at, rowid", (cid,))]
        for r in rows:
            r["saw_others"] = bool(r["saw_others"])
        independent = [r for r in rows if not r["saw_others"]]
        verdicts = {r["verdict"] for r in independent}
        return {"items": rows, "independent_count": len(independent),
                "roles_agree": len(verdicts) == 1 if independent else None, "note": AGREEMENT_NOTE}

    def run_workers(self, study_id: str, ideate: Callable[[dict], list[dict]], *,
                    providers: Iterable[Provider] = (), skeptic: Callable[[dict], tuple[str, str]] | None = None,
                    ledger_id: str | None = None, concurrency: int = 3, estimate: tuple[int, int] = (800, 400)) -> dict:
        """Ideation, then prior-art research and a skeptical review in parallel.

        Prior-art and skeptic depend only on ideation, so neither sees the other's verdict.
        All workers share one budget ledger (if given) through the Coordinator."""
        from .budget import Usage
        from .orchestrator import Coordinator, FunctionRunner, WorkerResult

        provs = list(providers)
        st = self.study(study_id)
        ledger_id = ledger_id or st["ledger_id"]
        coord = Coordinator(self.s, concurrency=concurrency)
        t_idea = coord.spawn(None, "invent:ideation", objective=st["target"]["problem"],
                             acceptance=["candidates have a mechanism and a specific difference"], ledger_id=ledger_id)
        t_prior = coord.spawn(None, "invent:prior_art", objective="search existing work for each candidate",
                              depends_on=[t_idea["id"]], acceptance=["every search attempt logged"],
                              ledger_id=ledger_id)
        t_skep = coord.spawn(None, "invent:skeptic", objective="look for reasons each candidate is weak or invalid",
                             depends_on=[t_idea["id"]], acceptance=["every candidate reviewed"], ledger_id=ledger_id)

        def run_ideation(ctx):
            made = []
            for spec in ideate(st["target"]):
                spec = dict(spec)
                cand = self.add_candidate(study_id, spec.pop("title"), spec.pop("mechanism"), spec.pop("difference"),
                                          spec.pop("method"), **spec)
                self.record_assessment(cand["id"], "ideation", "proposed",
                                       f"method={cand['method']}; flags={cand['cosmetic_flags']}")
                made.append(cand["id"])
            return WorkerResult(output=made, usage=Usage(input_tokens=0, output_tokens=0), acceptance={"ac1": (bool(made), f"{len(made)} candidates")})

        def run_prior(ctx):
            ids = ctx.dependency_results[t_idea["id"]] or []
            out = {}
            for cid in ids:
                ctx.token.checkpoint()
                self.internal_check(cid)
                self.search(cid, provs)
                verdict = self._evidence_verdict(cid)
                self.record_assessment(cid, "prior_art", verdict["status"], "; ".join(verdict["reasons"]) or "-")
                out[cid] = verdict["status"]
            return WorkerResult(output=out, usage=Usage(input_tokens=0, output_tokens=0),
                                acceptance={"ac1": (True, f"{len(ids)} candidates searched")})

        def run_skeptic(ctx):
            ids = ctx.dependency_results[t_idea["id"]] or []
            out = {}
            for cid in ids:
                ctx.token.checkpoint()
                c = self.candidate(cid)
                verdict, why = (skeptic or default_skeptic)(c)
                self.record_assessment(cid, "skeptic", verdict, why)
                out[cid] = verdict
            return WorkerResult(output=out, usage=Usage(input_tokens=0, output_tokens=0),
                                acceptance={"ac1": (True, f"{len(ids)} candidates reviewed")})

        # Deterministic workers make no model calls, so they report zero usage. A host or API worker
        # would report its real usage; the reservation still caps what it may spend.
        runners: dict[str, Any] = {t_idea["id"]: FunctionRunner(run_ideation, estimate),
                   t_prior["id"]: FunctionRunner(run_prior, estimate),
                   t_skep["id"]: FunctionRunner(run_skeptic, estimate)}
        res = coord.run([t_idea["id"], t_prior["id"], t_skep["id"]], runners, ledger_id=ledger_id)
        ids = res["results"].get(t_idea["id"]) or []
        final = {}
        for cid in ids:
            final[cid] = self.assess(cid)["status"]
        return {"statuses": res["statuses"], "candidates": ids, "final": final,
                "assessments": {cid: self.assessments(cid) for cid in ids}, "note": AGREEMENT_NOTE}

    def _evidence_verdict(self, cid: str) -> dict:
        """The prior-art role's own reading of its searches (does not look at other roles)."""
        a = self.assess(cid)
        return {"status": a["status"], "reasons": a["reasons"]}

    def worker_plan(self, study_id: str) -> dict:
        """Delegation plan for host-native subagents (e.g. Claude Code's Task tool)."""
        st = self.study(study_id)
        base = "lucid invent"
        return {
            "study": study_id, "problem": st["target"]["problem"], "note": AGREEMENT_NOTE,
            "workers": [
                {"role": "ideation", "brief": "Propose substantively different candidates. Each needs a mechanism, "
                 "a specific difference and the method used (" + ", ".join(sorted(METHODS)) + ").",
                 "report_back": f"{base} candidate {study_id} --title ... --mechanism ... --difference ... --method ..."},
                {"role": "prior_art", "brief": "Search papers, docs, products, repos and standards by problem, "
                 "mechanism, synonyms, adjacent fields and components. Log every search, including failures. "
                 "Inspect the closest sources before judging. Do not see the skeptic's notes.",
                 "report_back": f"{base} search-log / add-source / assess --role prior_art"},
                {"role": "skeptic", "brief": "Look for reasons each candidate is cosmetic, infeasible or wrong. "
                 "Run checks where possible. Do not see the prior-art notes.",
                 "report_back": f"{base} value <cand> --check NAME=CMD ; {base} assess <cand> --role skeptic"},
            ],
            "budget": {"ledger_id": st["ledger_id"], "rule": "all workers share this ledger and its limits"},
        }

    # ---------------------------------------------------------------- F. gate
    def gate(self, study_id: str, *, original_only: bool = True) -> dict:
        st = self.study(study_id)
        qualifying, incremental, excluded = [], [], []
        any_external = False
        for cid in st["candidates"]:
            c = self.candidate(cid)
            status = c["status"]
            any_external |= bool([s for s in self.searches(cid) if s["provider"] != INTERNAL_PROVIDER])
            flags = [f for f in c["cosmetic_flags"]]
            if c["rejected"]:
                excluded.append({"id": cid, "title": c["title"], "reason": "rejected earlier"})
            elif c["value"].get("status") == "invalid":
                excluded.append({"id": cid, "title": c["title"], "reason": "failed a correctness check"})
            elif flags:
                excluded.append({"id": cid, "title": c["title"], "reason": "cosmetic novelty: " + ", ".join(flags)})
            elif status == "known":
                excluded.append({"id": cid, "title": c["title"], "reason": "known or substantially equivalent"})
            elif status == "adaptation":
                incremental.append({"id": cid, "title": c["title"], "status": status})
            elif status == "potentially_original":
                qualifying.append({"id": cid, "title": c["title"], "scope_label": c["scope_label"],
                                   "claim": c["assessment"].get("claim")})
            else:
                excluded.append({"id": cid, "title": c["title"],
                                 "reason": STATUSES[status].lower() + (": " + "; ".join(c["assessment"].get("reasons", [])[:2])
                                                                      if c["assessment"].get("reasons") else "")})
        if not any_external:
            message = ("External originality was not assessed: no authorized search ran. "
                       "None of these ideas can be called original yet.")
        elif qualifying:
            message = (f"{len(qualifying)} of {len(st['candidates'])} candidates look original within the "
                       f"searched scope. {NOT_PROOF}")
        else:
            message = f"None of the {len(st['candidates'])} candidates qualified as original within the searched scope."
        return {"original_only": original_only, "qualifying": qualifying,
                "incremental": [] if original_only else incremental,
                "incremental_held_back": incremental if original_only else [],
                "excluded": excluded, "message": message}

    # ---------------------------------------------------------------- I. invention record
    def record(self, cid: str) -> dict:
        c = self.candidate(cid)
        st = self.study(c["study_id"])
        a = c["assessment"]
        verified_refs = [{"ref": s["ref"], "title": s["title"], "kind": s["kind"], "provider": s["provider"],
                          "relation": s["relation"]} for s in self.sources(cid) if s["inspected"]]
        prec = [p for p in a.get("precedents", []) if p["inspected"]]
        unc = list(a.get("limitations", []))
        if c["status"] in ("unresolved", "not_assessed"):
            unc += a.get("reasons", [])
        nxt = _next_experiment(c)
        return {
            "id": cid, "study_id": c["study_id"], "title": c["title"], "problem": st["target"]["problem"],
            "originality_level": st["target"]["level"], "mechanism": c["mechanism"], "method": c["method"],
            "closest_existing_work": prec, "verified_references": verified_refs,
            "material_differences": c["difference"], "advantage": c["advantage"],
            "status": c["status"], "status_text": STATUSES[c["status"]], "scope_label": c["scope_label"],
            "claim": a.get("claim") or claim_text(c["status"], c["scope_label"], [], 0, [], None),
            "feasibility_evidence": c["value"] or {"status": "untested"},
            "uncertainties": sorted(set(unc)), "next_experiment": nxt,
            "search_log": [{k: s[k] for k in ("provider", "query_kind", "query", "status", "result_count", "note",
                                               "searched_at")} for s in self.searches(cid)],
            "independent_assessments": self.assessments(cid),
            "cosmetic_flags": c["cosmetic_flags"], "revisit_of": c["revisit_of"],
            "revisit_reason": c["revisit_reason"],
        }

    def render_record(self, cid: str) -> str:
        r = self.record(cid)
        L = [f"# {r['title']}", "", f"**Status:** {r['status_text']}", "", r["claim"], "",
             f"**Problem.** {r['problem']}", "", f"**Mechanism.** {r['mechanism']}", "",
             f"**What is different.** {r['material_differences'] or '(not stated)'}", "",
             f"**Advantage.** {r['advantage'] or '(not stated)'}", ""]
        L.append("## Closest existing work")
        if r["closest_existing_work"]:
            for p in r["closest_existing_work"]:
                L.append(f"- {p['title']} ({p['ref']}). Shares: {', '.join(p['borrowed']) or 'nothing recognised'}. "
                         f"Differs in: {', '.join(p['materially_different']) or 'nothing stated'}.")
        else:
            L.append("- None inspected.")
        L += ["", "## Searches"]
        for s in r["search_log"]:
            L.append(f"- {s['searched_at'][:10]} {s['provider']} [{s['query_kind']}] {s['status']}: {s['query'][:90]}"
                     + (f" ({s['note']})" if s["note"] else ""))
        v = r["feasibility_evidence"]
        L += ["", "## Does it work?", f"- Value status: {v.get('status', 'untested')}"]
        for name, o in (v.get("checks") or {}).items():
            L.append(f"- {name}: {'passed' if o['passed'] else 'failed'} ({o['evidence'][:120]})")
        if r["uncertainties"]:
            L += ["", "## Open questions"] + [f"- {u}" for u in r["uncertainties"]]
        L += ["", "## Next experiment", r["next_experiment"], ""]
        return "\n".join(L)

    def save_record(self, cid: str, *, scope: Scope | None = None) -> dict:
        """Store the record as an 'invention' memory with provenance links to its inspected sources."""
        c = self.candidate(cid)
        st = self.study(c["study_id"])
        sc = scope or Scope(st["user_scope"], st["project_scope"])
        r = self.record(cid)
        ev = []
        for ref in r["verified_references"]:
            sid = self.mem.add_source("prior_art", scope=sc, uri=ref["ref"], title=ref["title"],
                                      provider=ref["provider"], metadata={"kind": ref["kind"],
                                                                         "relation": ref["relation"]})
            ev.append(Evidence(source_id=sid, relation="supports"))
        content = (f"Invention record: {c['title']}. Mechanism: {c['mechanism']} "
                   f"Status: {r['status_text']}. {r['claim']}")
        return self.mem.add(content, kind="invention", origin="lucid_summary", scope=sc, key=f"invention:{cid}",
                            rationale="originality check record", evidence=ev, tags=["invention", c["status"]],
                            allow_resurrect=False, actor="lucid")

    def _save_assessment(self, cid: str, a: dict) -> None:
        self.s.ex("UPDATE invention_candidates SET assessment=?, updated_at=? WHERE id=?",
                  (dumps(a), self.s.clock.iso(), cid))


# ------------------------------------------------------------------ helpers
_AI_ONLY = re.compile(r"\b(ai[- ]powered|ai|artificial intelligence|smart|intelligent|machine learning|ml)\b", re.I)
_MODEL_WORDS = re.compile(r"\b(train|trained|predict|prediction|classif|model of|learns? from|features?|labels?|"
                          r"regression|neural|forecast)\w*", re.I)


def cosmetic_flags(c: dict) -> list[str]:
    """Signs of cosmetic novelty that can be detected from the candidate alone."""
    flags = []
    mech = c.get("mechanism") or ""
    if len(tokens(mech)) < 4:
        flags.append("no_mechanism")
    if not (c.get("difference") or "").strip():
        flags.append("no_difference")
    if _AI_ONLY.search(mech + " " + (c.get("title") or "")) and not _MODEL_WORDS.search(mech):
        flags.append("ai_washing")
    if c.get("method") == "combine_fields" and len(c.get("components") or []) >= 2 and \
            not (c.get("advantage") or "").strip():
        flags.append("unexplained_combination")
    return flags


def default_skeptic(c: dict) -> tuple[str, str]:
    flags = c["cosmetic_flags"]
    if flags:
        return "reject", "cosmetic novelty: " + ", ".join(flags)
    if not c["advantage"]:
        return "weak", "no practical advantage stated"
    if c["value"].get("status") == "invalid":
        return "reject", "failed a correctness check"
    if c["value"].get("status") != "supported":
        return "untested", "no feasibility or correctness evidence yet"
    return "keep", "mechanism, difference and advantage stated; checks passed"


def _combination(cand: set[str], comparisons: list[dict]) -> tuple[str, str, list[str]] | None:
    core = cand - COSMETIC_CONCEPTS
    if len(core) < 3:
        return None
    for i, x in enumerate(comparisons):
        for y in comparisons[i + 1:]:
            if x["relation"] == "different" or y["relation"] == "different":
                continue
            sx = set(x["_sim"].shared)
            sy = set(y["_sim"].shared)
            if len(sx - sy) >= 1 and len(sy - sx) >= 1 and len((sx | sy) & core) / len(core) >= 0.8:
                return x["ref"], y["ref"], [x["title"], y["title"]]
    return None


def claim_text(status: str, scope_label: str | None, providers: list[str], n_queries: int,
               limitations: list[str], basis: dict | None) -> str:
    where = ", ".join(providers) if providers else "no external source"
    lim = (" Limits: " + "; ".join(limitations) + ".") if limitations else ""
    if status == "known":
        if basis and basis.get("type") == "internal_duplicate":
            return "Not new: Lucid already has this idea on record, so it is not presented as new."
        return "Not new: existing work describes substantially the same mechanism."
    if status == "adaptation":
        return "Builds on existing work: the core mechanism is known; the change is an adaptation or increment."
    if status == "potentially_original":
        label = SCOPE_LABELS.get(scope_label or "", "")
        return (f"Potentially original within the documented search scope ({label}): searched {where} with "
                f"{n_queries} queries. {NOT_PROOF}{lim}")
    if status == "unresolved":
        return f"Originality unresolved: the search could not settle it.{lim} A failed search is not evidence of absence."
    return "Originality not assessed: no authorized search ran, so no claim about novelty is made."


def _next_experiment(c: dict) -> str:
    v = c["value"]
    if c["status"] in ("not_assessed", "unresolved"):
        return "Run the missing prior-art searches (see open questions) and inspect the closest sources."
    if v.get("status") == "invalid":
        return "Fix or drop the idea: it failed a correctness check."
    if v.get("status") != "supported":
        return "Build the smallest prototype that tests the stated advantage against the closest existing method."
    return "Measure the advantage against the closest existing method on a held-out workload."
