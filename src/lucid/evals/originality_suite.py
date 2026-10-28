"""Offline evaluation of the originality checker against a documented reference collection.

Probes are hand-written candidate ideas with a recorded ground truth relative to the
bundled collection (src/lucid/data/originality_reference.jsonl):
  known       substantially the same as one entry
  adaptation  an entry's mechanism applied elsewhere or combined
  absent      no equivalent entry in the collection (says nothing about the wider world)

Conditions:
  title_match    flags a probe as known only if its title names a collection entry
  keyword        flags as known if word overlap with any entry is at least 0.3
  lucid          the full workflow with the local collection as the only search provider

Each probe runs in a fresh database so earlier probes cannot leak in as "previous work".
"""
from __future__ import annotations

import json
import time

from ..core import Lucid
from ..originality import LocalCollectionProvider, similarity, tokens

PRESENTED_AS_NEW = {"potentially_original"}


def load(path: str, split: str) -> list[dict]:
    with open(path, encoding="utf-8") as fh:
        return [p for p in (json.loads(x) for x in fh if x.strip()) if p["split"] == split]


def _title_match(p: dict, docs: list[dict]) -> tuple[str, str | None]:
    for d in docs:
        if d["title"].lower() in (p["title"] + " " + p["mechanism"]).lower():
            return "known", d["id"]
    return "potentially_original", None


def _keyword(p: dict, docs: list[dict]) -> tuple[str, str | None]:
    best, best_id = 0.0, None
    a = tokens(p["mechanism"])
    for d in docs:
        b = tokens(d["summary"])
        j = len(a & b) / len(a | b) if a | b else 0.0
        if j > best:
            best, best_id = j, d["id"]
    return ("known", best_id) if best >= 0.3 else ("potentially_original", None)


def _lucid(p: dict, provider: LocalCollectionProvider, ref_to_id: dict) -> dict:
    L = Lucid(":memory:")
    try:
        O = L.originality
        sid = O.create_study("probe: " + p["id"])["id"]
        c = O.add_candidate(sid, p["title"], p["mechanism"], p["difference"], p["method"],
                            advantage=p["advantage"] or None, components=p["components"], domain=p["domain"])
        a = O.check(c["id"], [provider])
        basis = a.get("basis") or {}
        src = basis.get("source") or (basis.get("sources") or [None])[0]
        top = a["precedents"][0] if a["precedents"] else None
        gate = O.gate(sid)
        return {"status": a["status"], "cited": ref_to_id.get(src), "top_diff": top["materially_different"] if top else [],
                "queries": a["coverage"]["queries_run"], "inspected": a["coverage"]["sources_inspected"],
                "kinds": a["coverage"]["query_kinds"], "flags": O.candidate(c["id"])["cosmetic_flags"],
                "kept": bool(gate["qualifying"]) or a["status"] in ("unresolved", "not_assessed")}
    finally:
        L.close()


def run(probes_path: str, dup_path: str, split: str) -> dict:
    t0 = time.time()
    probes = load(probes_path, split)
    provider = LocalCollectionProvider()
    docs = provider.docs
    ref_to_id = {d["ref"]: d["id"] for d in docs}
    rows = []
    for p in probes:
        tm, tm_src = _title_match(p, docs)
        kw, kw_src = _keyword(p, docs)
        lu = _lucid(p, provider, ref_to_id)
        rows.append({"id": p["id"], "truth": p["truth"], "truth_source": p["truth_source"],
                     "expect_difference": p["expect_difference"],
                     "title_match": {"status": tm, "cited": tm_src},
                     "keyword": {"status": kw, "cited": kw_src}, "lucid": lu})

    def metrics(cond: str) -> dict:
        known = [r for r in rows if r["truth"] == "known"]
        prior = [r for r in rows if r["truth"] in ("known", "adaptation")]
        absent = [r for r in rows if r["truth"] == "absent"]
        detected = [r for r in known if r[cond]["status"] == "known"]
        false_new = [r for r in prior if r[cond]["status"] in PRESENTED_AS_NEW]
        flagged_prior = [r for r in prior if r[cond]["status"] in ("known", "adaptation")]
        cited_ok = [r for r in flagged_prior if r[cond]["cited"] == r["truth_source"]]
        m = {
            "known_detected": f"{len(detected)}/{len(known)}",
            "known_detection_rate": round(len(detected) / max(1, len(known)), 3),
            "false_originality_claims": f"{len(false_new)}/{len(prior)}",
            "false_originality_rate": round(len(false_new) / max(1, len(prior)), 3),
            "citation_accuracy": f"{len(cited_ok)}/{len(flagged_prior)}",
        }
        if cond == "lucid":
            adapt = [r for r in rows if r["truth"] == "adaptation" and r["expect_difference"]]
            good_diff = [r for r in adapt if r["expect_difference"] in r["lucid"]["top_diff"]]
            kept = [r for r in absent if r["lucid"]["kept"]]
            wrongly_dropped = [r["id"] for r in absent if not r["lucid"]["kept"]]
            m.update({
                "difference_named": f"{len(good_diff)}/{len(adapt)}",
                "absent_ideas_kept_for_review": f"{len(kept)}/{len(absent)}",
                "absent_ideas_wrongly_dropped": wrongly_dropped,
                "absent_ideas_claimed_original": sum(1 for r in absent if r["lucid"]["status"] == "potentially_original"),
                "mean_queries_per_candidate": round(sum(r["lucid"]["queries"] for r in rows) / max(1, len(rows)), 2),
                "mean_sources_inspected": round(sum(r["lucid"]["inspected"] for r in rows) / max(1, len(rows)), 2),
                "query_kinds_seen": sorted({k for r in rows for k in r["lucid"]["kinds"]}),
            })
        else:
            kept = [r for r in absent if r[cond]["status"] not in ("known", "adaptation")]
            m["absent_ideas_kept_for_review"] = f"{len(kept)}/{len(absent)}"
        return m

    dup = []
    with open(dup_path, encoding="utf-8") as fh:
        pairs = [x for x in (json.loads(l) for l in fh if l.strip()) if x["split"] == split]
    for d in pairs:
        L = Lucid(":memory:")
        try:
            O = L.originality
            s1 = O.create_study("dup probe")["id"]
            O.add_candidate(s1, "earlier", d["earlier"], "x", "new_method", advantage="y")
            s2 = O.create_study("dup probe")["id"]
            c2 = O.add_candidate(s2, "later", d["later"], "x", "new_method", advantage="y")
            r = O.internal_check(c2["id"])
            dup.append({"truth": d["duplicate"], "caught": r["duplicate_of"] is not None,
                        "flagged_for_review": bool(r["possible_repeats"]),
                        "score": round(similarity(d["later"], d["earlier"]).score, 3)})
        finally:
            L.close()
    dups = [x for x in dup if x["truth"]]
    nondups = [x for x in dup if not x["truth"]]
    return {
        "split": split, "n_probes": len(rows),
        "truth_counts": {t: sum(1 for r in rows if r["truth"] == t) for t in ("known", "adaptation", "absent")},
        "conditions": {c: metrics(c) for c in ("title_match", "keyword", "lucid")},
        "internal_duplicates": {"caught": f"{sum(x['caught'] for x in dups)}/{len(dups)}",
                                "caught_or_flagged": f"{sum(x['caught'] or x['flagged_for_review'] for x in dups)}/{len(dups)}",
                                "false_duplicates": f"{sum(x['caught'] for x in nondups)}/{len(nondups)}",
                                "unrelated_flagged": f"{sum(x['flagged_for_review'] for x in nondups)}/{len(nondups)}",
                                "pairs": dup},
        "rows": rows, "wall_s": round(time.time() - t0, 2),
        "collection": {"entries": len(docs), "kinds": sorted({d["kind"] for d in docs}),
                       "path": "src/lucid/data/originality_reference.jsonl"},
    }


def render(r: dict) -> list[str]:
    L = [f"## Originality checking (n={r['n_probes']} probes: {r['truth_counts']['known']} known, "
         f"{r['truth_counts']['adaptation']} adaptations, {r['truth_counts']['absent']} absent from the collection)", "",
         f"Reference collection: {r['collection']['entries']} documented entries ({', '.join(r['collection']['kinds'])}). "
         "Ground truth is relative to this collection only. \"Absent\" means not in the collection, not new to the world.", "",
         "| condition | known equivalents detected | false originality claims | citation accuracy | absent ideas kept for review |",
         "|---|---|---|---|---|"]
    names = {"title_match": "title match", "keyword": "keyword overlap", "lucid": "Lucid (local collection)"}
    for c, m in r["conditions"].items():
        L.append(f"| {names[c]} | {m['known_detected']} | {m['false_originality_claims']} | {m['citation_accuracy']} | "
                 f"{m['absent_ideas_kept_for_review']} |")
    lm = r["conditions"]["lucid"]
    dd = r["internal_duplicates"]
    missed = [x["id"] for x in r["rows"] if x["truth"] == "known" and x["lucid"]["status"] != "known"]
    L += ["",
          f"- Adaptations where Lucid named the expected difference: {lm['difference_named']}.",
          f"- Absent ideas Lucid claimed as original: {lm['absent_ideas_claimed_original']} "
          "(with only the local collection searched and no inspection by the host, Lucid leaves them unresolved).",
          f"- Search cost: {lm['mean_queries_per_candidate']} queries and {lm['mean_sources_inspected']} inspected "
          f"sources per candidate; query kinds used: {', '.join(lm['query_kinds_seen'])}.",
          f"- Rephrased earlier proposals: {dd['caught']} identified as repeats automatically, {dd['caught_or_flagged']} "
          f"identified or flagged for a person to compare. Unrelated pairs: {dd['false_duplicates']} wrongly called "
          f"repeats, {dd['unrelated_flagged']} flagged for comparison.",
          f"- Known equivalents Lucid missed: {', '.join(missed) if missed else 'none'}.", "",
          _interpret(r), "",
          "**Limits.** The probes, the collection and the concept thesaurus were written by the same developer, "
          "so this measures the mechanism, not real-world prior-art search. The set is small; treat the counts as "
          "counts, not rates. A real originality check needs the host's own web searches, logged with "
          "`lucid invent search-log` and `add-source`, and the closest sources read in full. This workflow is not "
          "a guarantee that an idea is new.", ""]
    return L


def _interpret(r: dict) -> str:
    lu, kw = r["conditions"]["lucid"], r["conditions"]["keyword"]
    return (f"**Interpretation.** On this set Lucid made {lu['false_originality_claims']} false originality claims "
            f"against {kw['false_originality_claims']} for keyword overlap, and detected {lu['known_detected']} known "
            f"equivalents against {kw['known_detected']}. When the comparison is weak it says \"unresolved\" instead "
            "of guessing, which avoids false claims but leaves more known ideas for a person to check.")
