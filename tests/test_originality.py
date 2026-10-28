"""Originality workflow: one test per case in the spec, plus search hygiene and records."""
import json
import os
import subprocess
import sys
import zlib

import pytest

from lucid.budget import BudgetLedger
from lucid.originality import (LOCAL_PROVIDER, LocalCollectionProvider, OriginalityError, UnavailableProvider,
                               similarity)

LOCAL = LocalCollectionProvider()


@pytest.fixture
def O(L):
    return L.originality


def study(O, **kw):
    return O.create_study("Many clients overload a shared service after an outage", **kw)["id"]


def add(O, sid, title, mechanism, difference="", method="new_method", **kw):
    return O.add_candidate(sid, title, mechanism, difference, method, **kw)["id"]


class HostWeb:
    """Stands in for the host assistant's own web search: returns what it was given and counts queries."""
    external = True
    covers = ("papers", "products", "repos")

    def __init__(self, hits=None, name="host:web"):
        self.name = name
        self.hits = hits or []
        self.queries = []

    def search(self, q):
        self.queries.append(q)
        return list(self.hits)


# --------------------------------------------------------------------------- spec J cases
def test_renamed_invention_is_known(O):
    sid = study(O)
    cid = add(O, sid, "SetSieve", "Hash each item with several hash functions into a bit array and set those "
              "bits; to check if an item was seen before, look at those bits. Occasionally wrong (false positives).",
              "a new name", advantage="fast")
    a = O.check(cid, [LOCAL])
    assert a["status"] == "known"
    assert a["precedents"][0]["title"] == "Bloom filter"
    assert "Not new" in a["claim"]


def test_different_terminology_is_known(O):
    sid = study(O)
    cid = add(O, sid, "Patient callers", "When a call fails, try again after a pause that doubles each time, plus "
              "a random offset so clients do not line up.", "different words", advantage="less load")
    a = O.check(cid, [LOCAL])
    assert a["status"] == "known"
    assert a["basis"]["source"].startswith("IEEE 802.3")


def test_known_method_in_new_domain_is_adaptation(O):
    sid = study(O)
    cid = add(O, sid, "Valve retries", "When an irrigation valve command fails, resend it after an exponentially "
              "growing delay with random jitter.", "used on farm valves", "change_constraint",
              advantage="fewer dropped commands", domain="irrigation")
    a = O.check(cid, [LOCAL])
    assert a["status"] == "adaptation"
    assert "irrigation" in a["precedents"][0]["materially_different"]


def test_combination_without_improvement_is_flagged_and_gated(O):
    sid = study(O)
    cid = add(O, sid, "Sensor ring", "Place soil moisture sensors on a ring with consistent hashing and schedule "
              "watering when the soil is dry.", "combines two known things", "combine_fields",
              components=["consistent hashing ring", "soil moisture watering schedule"])
    c = O.candidate(cid)
    assert "unexplained_combination" in c["cosmetic_flags"]
    a = O.check(cid, [LOCAL])
    assert a["status"] == "adaptation"
    g = O.gate(sid)
    assert g["qualifying"] == [] and g["excluded"][0]["id"] == cid
    assert "None of the 1 candidates" in g["message"]


def test_duplicate_of_lucids_previous_work(O):
    s1 = study(O)
    old = add(O, s1, "Budget-aware decision timer", "While waiting for a human answer, hold part of the token "
              "budget for the default action and release the hold if the human answers first.", "x",
              advantage="never overspends")
    O.reject(old, "not useful then")
    s2 = study(O)
    again = add(O, s2, "Decision budget hold", "While waiting for a human answer, hold part of the token budget "
                "for the default action and release the hold if the human answers first.", "x", advantage="y")
    a = O.check(again, [LOCAL])
    assert a["status"] == "known" and a["basis"]["type"] == "internal_duplicate"
    assert a["basis"]["match"]["id"] == old and a["basis"]["match"]["rejected"] is True
    # Revisiting is allowed, but only with a reason, and the reason is kept.
    with pytest.raises(OriginalityError):
        add(O, s2, "Again", "same", "x", revisit_of=old)
    rev = add(O, s2, "Decision budget hold v2", "While waiting for a human answer, hold part of the token budget "
              "for the default action and release the hold if the human answers first.", "x", advantage="y",
              revisit_of=old, revisit_reason="budgets are now shared across workers, so holds matter more")
    r = O.internal_check(rev)
    assert r["duplicate_of"] is None and r["revisit"]["of"] == old


def test_close_earlier_record_blocks_a_claim_until_compared(O):
    s1 = study(O)
    old = add(O, s1, "Choir split", "During practice a singer hears only their own part in one ear and the full "
              "mix in the other ear.", "x", advantage="y")
    s2 = study(O, allowed_providers=[LOCAL_PROVIDER, "host:web"])
    cid = add(O, s2, "Rehearsal ears", "Singers hear their own part in one ear while the other ear gets the full "
              "choir mix.", "split by ear", "change_constraint", advantage="learn parts faster")
    r = O.internal_check(cid)
    assert r["duplicate_of"] is None and [m["id"] for m in r["possible_repeats"]] == [old]
    a = O.check(cid, [LOCAL])
    assert a["status"] == "unresolved" and any("may repeat earlier records" in x for x in a["reasons"])
    O.not_a_repeat(cid, old, "the earlier idea was for solo practice")
    assert not any("may repeat" in x for x in O.assess(cid)["reasons"])


def test_previous_memory_counts_as_known_to_user(O, L):
    L.memory.add("Our cache layer memoizes function results keyed by their arguments and reuses earlier results.",
                 kind="decision")
    sid = study(O)
    cid = add(O, sid, "Result reuse", "Memoize function results keyed by their arguments and reuse earlier "
              "results.", "x", advantage="y")
    r = O.internal_check(cid)
    assert r["duplicate_of"]["type"] == "memory"


def test_search_unavailable_is_not_absence(O):
    sid = study(O, allowed_providers=[LOCAL_PROVIDER, "host:web"])
    cid = add(O, sid, "Decision budget hold", "While a question waits for a human, reserve tokens for its default "
              "action and release them when the human answers first.", "ties the clock to the ledger",
              "change_constraint", advantage="never overspends while waiting")
    a = O.check(cid, [LOCAL, UnavailableProvider("host:web")])
    assert a["status"] == "unresolved"
    assert any("host:web" in r for r in a["reasons"])
    assert "not evidence of absence" in a["claim"]
    statuses = {s["status"] for s in O.searches(cid)}
    assert "unavailable" in statuses


def test_no_search_means_not_assessed(O):
    sid = study(O)
    cid = add(O, sid, "Decision budget hold", "While a question waits for a human, reserve tokens for its default "
              "action and release them when the human answers first.", "x", advantage="y")
    a = O.check(cid, [])
    assert a["status"] == "not_assessed" and a["scope_label"] == "new_to_user"
    g = O.gate(sid)
    assert g["qualifying"] == [] and "not assessed" in g["message"]


def test_plausible_but_invalid_fails_value(O):
    sid = study(O)
    cid = add(O, sid, "Infinite zip", "Compress a file, then compress the output again, repeating until it is one "
              "byte long; each pass gives a smaller encoding.", "repeat compression", "reverse_process",
              advantage="store anything in one byte")

    def shrinks_every_pass(c):
        data = os.urandom(4096)
        sizes = [len(data)]
        for _ in range(3):
            data = zlib.compress(data, 9)
            sizes.append(len(data))
        return all(b < a for a, b in zip(sizes, sizes[1:])), f"sizes per pass: {sizes}"

    v = O.assess_value(cid, {"repeated compression shrinks": shrinks_every_pass})
    assert v["status"] == "invalid"
    O.check(cid, [LOCAL])
    g = O.gate(sid)
    assert g["excluded"][0]["reason"] == "failed a correctness check"


def test_passes_search_but_unresolved_until_inspected(O):
    sid = study(O, allowed_providers=[LOCAL_PROVIDER, "host:web"])
    cid = add(O, sid, "Decision budget hold", "While a question waits for a human, reserve tokens for its default "
              "action and release them when the human answers first.", "ties the decision clock to budget holds",
              "change_constraint", advantage="never overspends while waiting")
    web = HostWeb([{"ref": "https://example.org/hitl-timeouts", "title": "Human-in-the-loop timeouts",
                    "summary": "Agents wait for approval with a timeout, then apply a default action.",
                    "kind": "papers"}])
    a = O.check(cid, [LOCAL, web])
    assert a["status"] == "unresolved"
    assert any("not inspected" in r or "recognised concepts" in r for r in a["reasons"])
    # The host reads the closest source and records how it relates. Now a scoped claim is possible.
    src = [s for s in O.sources(cid) if s["provider"] == "host:web"][0]
    O.inspect(src["id"], relation="different", differences="no budget reservation while waiting")
    a = O.assess(cid)
    assert a["status"] == "potentially_original"
    assert a["scope_label"] == "potentially_new_more_broadly"
    assert "not proof that nobody has thought of it" in a["claim"]


def test_ai_washing_is_cosmetic(O):
    sid = study(O)
    cid = add(O, sid, "AI-powered Bloom filter", "An AI-powered bit array with several hash functions for set "
              "membership, with false positives.", "AI", advantage="smarter")
    assert "ai_washing" in O.candidate(cid)["cosmetic_flags"]
    assert O.check(cid, [LOCAL])["status"] == "known"


def test_siblings_must_differ(O):
    sid = study(O)
    add(O, sid, "A", "Retry failed calls after a delay that doubles each time with random jitter.", "x")
    b = O.candidate(add(O, sid, "B", "Retry failed calls after a delay that doubles each time, random jitter.", "y"))
    assert any(f.startswith("near_duplicate_of:") for f in b["cosmetic_flags"])


# --------------------------------------------------------------------------- search hygiene
def test_confidential_terms_never_leave(O):
    sid = study(O, allowed_providers=[LOCAL_PROVIDER, "host:web"], confidential_terms=["ProjectNimbus", "AcmeBank"])
    cid = add(O, sid, "Nimbus retry", "ProjectNimbus retries AcmeBank calls after a delay that doubles each time.",
              "x", components=["AcmeBank"])
    web = HostWeb()
    O.search(cid, [web])
    sent = " ".join(web.queries).lower()
    assert "nimbus" not in sent and "acmebank" not in sent
    assert "skipped_confidential" in {s["status"] for s in O.searches(cid)}  # the bare 'AcmeBank' component


def test_unauthorized_provider_is_not_called(O):
    sid = study(O)  # only the local collection is allowed
    cid = add(O, sid, "x", "Retry failed calls after a delay that doubles each time.", "x")
    web = HostWeb()
    O.search(cid, [web])
    assert web.queries == []
    assert [s["status"] for s in O.searches(cid) if s["provider"] == "host:web"] == ["not_authorized"]


def test_query_plan_covers_problem_mechanism_synonyms_components(O):
    sid = study(O)
    cid = add(O, sid, "x", "Retry failed calls after a delay that doubles each time.", "x",
              components=["retry", "delay"])
    kinds = {q["kind"] for q in O.plan_queries(cid)}
    assert {"problem", "mechanism", "synonym", "adjacent", "component", "combination"} <= kinds


def test_similarity_is_terminology_aware():
    a = "keep a stack of reversible commands so the user can roll back"
    b = "maintain a history so people can undo their last change"
    assert similarity(a, b).shared == ["undo"]


def test_sources_need_references_and_relation_needs_inspection(O):
    sid = study(O)
    cid = add(O, sid, "x", "Retry failed calls after a delay that doubles each time.", "x")
    with pytest.raises(OriginalityError):
        O.add_source(cid, "host:web", kind="papers", ref=" ", title="t", summary="s")
    with pytest.raises(OriginalityError):
        O.add_source(cid, "host:web", kind="papers", ref="r", title="t", summary="s", relation="different")


# --------------------------------------------------------------------------- workers, records
def test_parallel_roles_share_budget_and_stay_independent(O, L):
    led = BudgetLedger(L.store).create("invent", token_limit=10_000, mode="hard")
    sid = study(O, ledger_id=led)

    def ideate(target):
        return [
            {"title": "Patient callers", "mechanism": "When a call fails, try again after a pause that doubles each "
             "time, plus a random offset.", "difference": "x", "method": "new_method", "advantage": "less load"},
            {"title": "Decision budget hold", "mechanism": "While a question waits for a human, reserve tokens for "
             "its default action and release them when the human answers first.", "difference": "y",
             "method": "change_constraint", "advantage": "never overspends"},
        ]

    out = O.run_workers(sid, ideate, providers=[LOCAL])
    assert set(out["statuses"].values()) == {"completed"}
    assert len(out["candidates"]) == 2
    purposes = sorted(e["purpose"] for e in BudgetLedger(L.store).entries(led))
    assert purposes == ["worker:invent:ideation", "worker:invent:prior_art", "worker:invent:skeptic"]
    assert BudgetLedger(L.store).status(led)["unknown_held_tokens"] == 0  # deterministic workers report usage
    for cid in out["candidates"]:
        a = O.assessments(cid)
        roles = [x["role"] for x in a["items"]]
        assert sorted(roles) == ["ideation", "prior_art", "skeptic"]
        assert all(not x["saw_others"] for x in a["items"])
        assert "not evidence" in a["note"]


def test_parallel_roles_stop_when_budget_too_small(O, L):
    led = BudgetLedger(L.store).create("tiny", token_limit=500, mode="hard")
    sid = study(O, ledger_id=led)
    out = O.run_workers(sid, lambda t: [], providers=[LOCAL])
    assert "budget_exhausted" in out["statuses"].values()


def test_invention_record_with_provenance(O, L):
    sid = study(O)
    cid = add(O, sid, "SetSieve", "Hash each item with several hash functions into a bit array; to check if it was "
              "seen before, look at the bits. Occasionally wrong (false positives).", "new name", advantage="fast")
    O.check(cid, [LOCAL])
    r = O.record(cid)
    assert r["status"] == "known"
    refs = {x["ref"] for x in r["verified_references"]}
    collection = {d["ref"] for d in LOCAL.docs}
    assert refs and refs <= collection  # citations come only from sources that were actually recorded
    md = O.render_record(cid)
    assert "Closest existing work" in md and "Bloom filter" in md
    m = O.save_record(cid)
    assert m["kind"] == "invention" and m["origin"] == "lucid_summary"
    assert m["evidence"] and all(e["source_id"] for e in m["evidence"])
    # The saved record is now part of Lucid's knowledge: proposing it again is caught.
    s2 = study(O)
    again = add(O, s2, "Bit sieve", "Hash each item with several hash functions into a bit array; to check if it "
                "was seen before, look at the bits. Occasionally wrong (false positives).", "x", advantage="y")
    assert O.internal_check(again)["duplicate_of"] is not None


def test_handoff_category_and_memory_kind(L):
    from lucid.handoff import CATEGORIES
    from lucid.memory import KINDS
    assert "invention" in KINDS and "invention" in CATEGORIES


def test_cli_flow(tmp_path):
    env = dict(os.environ, LUCID_HOME=str(tmp_path), PYTHONPATH=os.path.join(os.path.dirname(__file__), "..", "src"))

    def run(*a):
        p = subprocess.run([sys.executable, "-m", "lucid.cli", "--json", "invent", *a], capture_output=True,
                           text=True, env=env, timeout=60)
        assert p.returncode == 0, p.stderr
        return json.loads(p.stdout)

    sid = run("target", "Clients retry at once after an outage")["id"]
    cid = run("candidate", sid, "--title", "Patient callers", "--mechanism", "When a call fails, try again after a "
              "pause that doubles each time, plus a random offset.", "--difference", "x", "--method", "new_method",
              "--advantage", "less load")["id"]
    res = run("check", cid)
    assert res["status"] == "known"
    ok = run("value", cid, "--check", f"runs={sys.executable} -c pass")
    assert ok["status"] == "supported"
    bad = run("value", cid, "--check", f"fails={sys.executable} -c 'raise SystemExit(1)'")
    assert bad["status"] == "invalid"
    g = run("gate", sid)
    assert g["qualifying"] == []
