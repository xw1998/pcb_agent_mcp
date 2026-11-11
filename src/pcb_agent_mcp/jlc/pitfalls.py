"""The field notes that came with the workflow, queryable by area / keyword.

19 entries, each with symptom -> countermeasure -> evidence, kept verbatim so the wording of
a real failure is not lost in paraphrase.
"""
from . import spec


def entries(area=None, query=None):
    data = spec.load("known-pitfalls.json")
    rows = list(data["pitfalls"])
    if area:
        rows = [r for r in rows if r.get("area") == area]
    if query:
        needle = query.lower()
        rows = [r for r in rows
                if needle in " ".join(str(r.get(k, "")) for k in
                                      ("id", "area", "symptom", "countermeasure", "evidence")).lower()]
    return rows


def areas():
    seen = []
    for row in spec.load("known-pitfalls.json")["pitfalls"]:
        if row.get("area") not in seen:
            seen.append(row.get("area"))
    return seen
