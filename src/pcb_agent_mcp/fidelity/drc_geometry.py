"""Turn KiCad's violation DESCRIPTION into the two numbers a caller needs.

Why this module exists
----------------------
`kicad-cli pcb drc --format json` (measured on 10.0.7) gives a violation four
things: `type`, `severity`, `description`, `items[].pos`. It does NOT give the
clearance it wanted or the clearance it measured - the only place those live is
the human-readable `description` string, which is LOCALISED. A caller who wants
"how much is this short" therefore had to write its own parser (the reference
session did exactly that, in `measure_gap.py`), which is a fine outcome for one
caller and a bad one for the product.

What is parsed, and how sure we are
-----------------------------------
The description is a template: OPTIONAL context, then the CONSTRAINT value with
its unit, then the MEASURED value with its unit. Verbatim samples:

    (zh) 间距违规 (填充区 间距 0.2500 mm; 实际 0.2005 mm)
    (zh) 钻孔太靠近其他孔 (电路板配置约束 最小 0.2500 mm; 实际 0.0000 mm)
    (zh) 文字高度超出范围 (最小高度 0.8000 mm; 实际 0.7000 mm)
    (en) Clearance violation (Zone clearance 0.25mm; actual 0.2005mm)
    (en) Board edge clearance violation (board setup constraints edge 0.5mm;
         actual 0.2mm)

So: every number is followed by `mm`; the measured one is the first number after
the word "actual" / "实际". When that word is absent, the order is assumed to be
constraint-then-actual and the answer says so (`order` == "assumed") - a number
labelled assumed is still worth more than no number, but it is not presented as
measured.

The answer also says which way the shortfall goes (`shortfall_mm` = constraint
- actual, positive when the measured value violates the minimum) and never
invents a number: a rule whose text carries none gets `geometry: null` plus the
reason.
"""
import re

# A number with an explicit mm unit. KiCad always prints the unit, which is what
# makes this safer than "grab every number": a description can also name a
# netclass ('Default'), a layer or a uuid fragment, and none of those carry mm.
_NUMBER_MM = re.compile(r"(-?\d+(?:\.\d+)?)\s*mm", re.IGNORECASE)

# The word that marks the boundary: everything numeric after it is the measured
# value, everything before it is the constraint.
_ACTUAL_WORDS = ("实际", "actual", "measured", "实测")


def _numbers_mm(text):
    out = []
    for m in _NUMBER_MM.finditer(text or ""):
        out.append((float(m.group(1)), m.start()))
    return out


def _actual_index(text):
    low = (text or "").lower()
    best = None
    for word in _ACTUAL_WORDS:
        idx = low.find(word.lower())
        if idx >= 0 and (best is None or idx < best):
            best = idx
    return best


def parse(description):
    """(constraint_mm, actual_mm, order, numbers_mm) - any of them may be None."""
    text = description or ""
    nums = _numbers_mm(text)
    numbers_mm = [v for v, _ in nums]
    if not nums:
        return None, None, None, numbers_mm
    split = _actual_index(text)
    if split is not None:
        before = [v for v, pos in nums if pos < split]
        after = [v for v, pos in nums if pos > split]
        if before and after:
            return before[-1], after[0], "from_keyword", numbers_mm
        return None, None, None, numbers_mm
    if len(nums) >= 2:
        return nums[0][0], nums[1][0], "assumed", numbers_mm
    return None, None, None, numbers_mm


def annotate(violations):
    """Add `geometry` (or an explanation of its absence) to every violation.

    Mutates and returns the list; `geometry` is additive, nothing existing is
    renamed, so a caller written against the old shape keeps working.
    """
    for v in violations or []:
        constraint, actual, order, numbers = parse(v.get("description"))
        v["numbers_mm"] = numbers
        if constraint is None or actual is None:
            v["geometry"] = None
            v["geometry_absent_because"] = (
                "this rule's description carries no pair of millimetre values "
                "(KiCad states some rules - keepout, courtyard, unmatched - in "
                "words only); the position in `items[]` is all the geometry the "
                "engine publishes")
            continue
        v["geometry"] = {
            "constraint_mm": constraint,
            "actual_mm": actual,
            "shortfall_mm": round(constraint - actual, 6),
            "ratio": (round(actual / constraint, 6) if constraint else None),
            "order": order,
            "source": "parsed from KiCad's localised `description` text "
                      "(the only field that carries these values)",
        }
        if order == "assumed":
            v["geometry"]["caveat"] = (
                "KiCad printed no 'actual' marker in this description, so the "
                "first number is taken as the constraint and the second as the "
                "measurement. Read `numbers_mm` yourself if the order matters")
    return violations


def summarise(violations, top=10):
    """Worst offenders by shortfall, for a crash-free headline."""
    scored = [v for v in (violations or []) if v.get("geometry")]
    scored.sort(key=lambda v: -abs(v["geometry"].get("shortfall_mm") or 0.0))
    out = []
    for v in scored[:top]:
        g = v["geometry"]
        item = v.get("items") or []
        out.append({
            "type": v.get("type"),
            "severity": v.get("severity"),
            "constraint_mm": g["constraint_mm"],
            "actual_mm": g["actual_mm"],
            "shortfall_mm": g["shortfall_mm"],
            "description": v.get("description"),
            "at": [i.get("pos") for i in item[:2]],
        })
    return out
