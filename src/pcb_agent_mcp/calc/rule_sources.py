"""Where a layout rule's claim actually comes from.

The point of this module is the one question that 0.1.17 could not answer:
"show me the sentence that says this". Before 0.1.17 every layout rule carried a
`source` string naming one of this repository's own skill documents. That is not
a source; it is a memory of having once thought the rule was obvious. An
inductor's body being off-limits to signal copper was exactly such a rule, and
it was not enforced as P1 until the sentence was found in writing.

So: a rule is *cited* only when data/layout-rule-sources.json holds an entry for
it whose quotes are non-empty and whose every `src` resolves in that file's own
`sources` table. Everything else is *uncited* and must be reported as such -
never upgraded to "documented" because the rule sounds standard.

This module does not judge whether the rule is right. It reports what a reader
could check, and refuses to answer when the file is missing rather than
returning an empty dictionary that would read as "nothing is cited".

Nothing here is a route, and nothing here fills a number.
"""

import io
import json
import os

DATA_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))))),
    "data", "layout-rule-sources.json",
)

# Agreement labels that say something about the strength of the claim. A source
# that only says "45 degrees OR an arc" is weaker than three independent vendors
# saying the same thing, and the level a rule gets should follow that, not the
# level the author wanted.
AGREEMENT_RANK = {
    "three_independent_sources_agree": 3,
    "sources_conflict_resolved_从严": 2,
    "derived_from_magnetic_keepout": 2,
    "single_source_plus_user_instruction": 1,
    "single_source": 1,
}


def _load():
    """Return (ledger, error). error is a sentence, never a silent None."""
    if not os.path.exists(DATA_PATH):
        return None, "ledger file missing: " + DATA_PATH
    try:
        with io.open(DATA_PATH, encoding="utf-8") as f:
            ledger = json.load(f)
    except Exception as e:
        return None, "ledger unreadable: %s (%s)" % (DATA_PATH, e)
    if not isinstance(ledger, dict) or "entries" not in ledger:
        return None, "ledger has no entries: " + DATA_PATH
    return ledger, None


def _validate(ledger):
    """Return (ok_entries, problems). A quote with an unknown src is a problem."""
    sources = ledger.get("sources") or {}
    ok, problems = {}, []
    for e in ledger.get("entries") or []:
        rid = e.get("rule_id")
        if not rid:
            problems.append("entry without rule_id: %r" % (e,))
            continue
        quotes = e.get("quotes") or []
        if not quotes:
            problems.append("%s: no quotes" % rid)
            continue
        bad = [q.get("src") for q in quotes if q.get("src") not in sources]
        if bad:
            problems.append("%s: quote cites unknown source(s) %s" % (rid, bad))
            continue
        empty = [q.get("src") for q in quotes if not (q.get("quote") or "").strip()]
        if empty:
            problems.append("%s: empty quote from %s" % (rid, empty))
            continue
        ok[rid] = e
    return ok, problems


def ledger():
    """The whole ledger plus the parsed entries, or (None, error)."""
    raw, err = _load()
    if err:
        return None, err
    entries, problems = _validate(raw)
    return {
        "sources": raw.get("sources") or {},
        "entries": entries,
        "problems": problems,
        "schema_version": raw.get("schema_version"),
        "path": DATA_PATH,
    }, None


def citation(rule_id):
    """The entry for one rule, or (None, reason). Never a guessed citation."""
    led, err = ledger()
    if err:
        return None, err
    e = led["entries"].get(rule_id)
    if e is None:
        return None, ("%s is not in the ledger: it is UNCITED. Do not describe it "
                      "as documented until a sentence is added to %s"
                      % (rule_id, DATA_PATH))
    return e, None


def source_line(rule_id):
    """A compact one-line citation suitable for a rule's `source` field, or None.

    Format: '<first three words of each title> [T1,T2,T2]'
    Keeping it short is deliberate: the full quote lives in the ledger, and a
    source field that repeats paragraphs is a source field nobody reads.
    """
    e, why = citation(rule_id)
    if e is None:
        return None, why
    led, err = ledger()
    tiers, names = [], []
    for q in e["quotes"]:
        s = led["sources"][q["src"]]
        tiers.append(s.get("tier", "?"))
        if s.get("title") not in names:
            names.append(s.get("title") or q["src"])
    short = []
    for t in names:
        words = str(t).split()
        # the vendor name is usually the first word; keep it plus the doc type
        short.append(" ".join(words[:4]))
    return "%s [%s]" % ("; ".join(short), ",".join(sorted(set(tiers)))), None


def coverage(rule_ids):
    """Report which of the given rule ids are cited and which are not.

    Returns dict with counts, the two sets, and the ledger's own problems. The
    caller is expected to print all of it - a coverage number that hides the
    uncited list is the failure mode this module exists to prevent.
    """
    led, err = ledger()
    if err:
        return None, err
    cited, uncited = [], []
    for rid in rule_ids:
        (cited if rid in led["entries"] else uncited).append(rid)
    return {
        "cited": cited,
        "uncited": uncited,
        "n_cited": len(cited),
        "n_uncited": len(uncited),
        "n_rules": len(cited) + len(uncited),
        "problems": led["problems"],
        "sources": sorted((led["sources"] or {}).keys()),
        "path": led["path"],
    }, None


if __name__ == "__main__":
    import sys

    try:
        from pcb_agent_mcp.calc.rules import LAYOUT_RULES
    except Exception as e:  # pragma: no cover - the tool path sets sys.path
        print("cannot import rules: %s" % e)
        sys.exit(2)
    rep, err = coverage([r["id"] for r in LAYOUT_RULES])
    if err:
        print("REFUSED: %s" % err)
        sys.exit(2)
    print("ledger: %s" % rep["path"])
    print("sources: %s" % ", ".join(rep["sources"]))
    print("cited  %d/%d" % (rep["n_cited"], rep["n_rules"]))
    for rid in rep["cited"]:
        line, _ = source_line(rid)
        print("  + %-34s %s" % (rid, line))
    print("uncited %d/%d" % (rep["n_uncited"], rep["n_rules"]))
    for rid in rep["uncited"]:
        print("  - %s" % rid)
    if rep["problems"]:
        print("ledger problems:")
        for p in rep["problems"]:
            print("  ! %s" % p)
