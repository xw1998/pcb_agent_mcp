"""What is actually in the way, and whether anything can be done about it.

A router that answers "no_path" leaves the person with nothing to do. The useful
answer is a ledger of the objects that stop this pad, each tagged with the one
thing that decides whether the design can change: what it would take to move it.

The tag is a STRUCTURAL fact about the object, never a decision this module
makes on the caller's behalf:

    pad          movable      the part can be moved (and re-placed)
    track, via   reroutable   another net's copper; any router may reroute it
    zone, pour   fixed        a plane's shape is a design decision, not a detour
    keepout      fixed        a rule, not copper
    edge         fixed        the board outline
    graphic      fixed        copper graphics are drawn on purpose

`blockers()` probes the pad in eight directions with the SAME exact segment test
the router uses, groups the hits into corridors (one per direction sector) and
says which sectors are OPEN - because "the pad is boxed in on all sides" and "the
pad has three open sides and the search failed anyway" are different diagnoses
and lead to different fixes.

Nothing here guesses a distance: every number comes from `Grid.seg_blockers` /
`Grid.via_blockers`, which measure polygons.
"""
import math

from . import rules as R

# What it would take to get an object out of the way. See the module docstring:
# this is a property of the object's kind, not a judgement about the design.
MUTABILITY = {
    "pad": "movable",
    "track": "reroutable",
    "via": "reroutable",
    "zone": "fixed",
    "pour": "fixed",
    "keepout": "fixed",
    "edge": "fixed",
    "graphic": "fixed",
}

# The eight sectors a pad can try to leave through, named for a report a person
# reads. Index 0 is +x (east) and they run counter-clockwise.
SECTORS = ("E", "NE", "N", "NW", "W", "SW", "S", "SE")

def classify(item):
    """`(mutability, why)` for one blocking item."""
    kind = (item.get("kind") or "?")
    how = MUTABILITY.get(kind)
    if how is None:
        return "fixed", ("an object of kind %r this module does not know how to "
                         "move" % kind)
    if how == "movable":
        return how, "it is a pad: the part can be moved and re-placed"
    if how == "reroutable":
        return how, ("it is %s of another net: any router may reroute it" % kind)
    if kind in ("zone", "pour"):
        return how, "it is a pour: its shape is a design decision, not a detour"
    if kind == "keepout":
        return how, "it is a keepout: a rule, not copper"
    if kind == "edge":
        return how, "it is the board outline"
    return how, "copper graphics are drawn on purpose"

def _sector_of(dx, dy):
    if abs(dx) < 1e-12 and abs(dy) < 1e-12:
        return 0
    ang = math.degrees(math.atan2(dy, dx)) % 360.0
    return int(round(ang / 45.0)) % 8

def blockers(grid, net, at, layer, width=None, radius_mm=None, directions=8,
             max_items=24):
    """The ledger for one pad (or point) on one layer.

    `radius_mm` is how far the probe reaches; the default is one pad-and-a-bit
    (clearance + half the track + 0.4 mm), which is exactly the room a pad needs
    to get its first track out.
    """
    x0, y0 = float(at[0]), float(at[1])
    width = float(width if width is not None
                  else R.for_net(grid.bundle, net)["track_mm"])
    spec = R.for_net(grid.bundle, net)
    if radius_mm is None:
        radius_mm = float(grid.clr) + width / 2.0 + 0.4
    radius = float(radius_mm)

    found = {}
    open_sectors = set(range(len(SECTORS)))
    enough = max(1, int(directions))
    for k in range(enough):
        ang = 2.0 * math.pi * k / enough
        ux, uy = math.cos(ang), math.sin(ang)
        ex, ey = x0 + ux * radius, y0 + uy * radius
        idx = _sector_of(ux, uy)
        sector = SECTORS[idx]
        hits = grid.seg_blockers((x0, y0), (ex, ey), layer, net, width)
        if hits:
            open_sectors.discard(idx)
        for hit in hits:
            key = (hit.get("kind"), hit.get("reference"), hit.get("net"),
                   hit.get("layer"), hit.get("reason"))
            row = found.get(key)
            short = hit.get("short_by_mm")
            if row is None:
                how, why = classify(hit)
                row = {"kind": hit.get("kind"), "net": hit.get("net"),
                       "reference": hit.get("reference"),
                       "layer": hit.get("layer") or layer,
                       "reason": hit.get("reason"),
                       "mutability": how, "why": why,
                       "needed_mm": hit.get("needed_mm"),
                       "short_by_mm": short,
                       "sectors": []}
                found[key] = row
            elif short is not None and (row.get("short_by_mm") is None
                                        or short > row["short_by_mm"]):
                row["short_by_mm"] = short
                row["needed_mm"] = hit.get("needed_mm")
            if sector not in row["sectors"]:
                row["sectors"].append(sector)

    rows = sorted(found.values(),
                  key=lambda r: (-(r.get("short_by_mm") or 0.0), r["kind"] or ""))
    rows = rows[:int(max_items)]

    # A via in the pad is the third way out (it needs a filled via from the fab,
    # which is a manufacturing decision, so it is reported and not assumed).
    via_hits = grid.via_blockers(x0, y0, net)

    corridors = []
    for name in SECTORS:
        inner = [r for r in rows if name in r["sectors"]]
        corridors.append({
            "sector": name,
            "open": SECTORS.index(name) in open_sectors,
            "blocked_by": [{"kind": r["kind"], "net": r["net"],
                            "reference": r["reference"],
                            "mutability": r["mutability"],
                            "short_by_mm": r["short_by_mm"]} for r in inner],
        })

    fixed = [r for r in rows if r["mutability"] == "fixed"]
    movable = [r for r in rows if r["mutability"] == "movable"]
    reroutable = [r for r in rows if r["mutability"] == "reroutable"]
    any_open = any(c["open"] for c in corridors)

    if not rows:
        diagnosis = ("no other-net copper within %.3f mm of this pad on %s, so "
                     "nothing here is what stopped the route" % (radius, layer))
    elif any_open:
        diagnosis = ("%d of %d directions are clear, so this pad is NOT boxed in: "
                     "the failure is upstream (a channel further along the net, or "
                     "the search budget), not at this pad"
                     % (sum(1 for c in corridors if c["open"]), len(corridors)))
    else:
        bits = []
        if movable:
            bits.append("%d pad(s) of other parts could move" % len(movable))
        if reroutable:
            bits.append("%d track/via of other nets could be rerouted"
                        % len(reroutable))
        if fixed:
            bits.append("%d object(s) cannot move at all (%s)"
                        % (len(fixed),
                           ", ".join(sorted(set(r["kind"] or "?" for r in fixed)))))
        diagnosis = ("the pad is boxed in on every probed side: " + "; ".join(bits)
                     if bits else "the pad is boxed in on every probed side")

    return {"net": net, "at": [round(x0, 4), round(y0, 4)], "layer": layer,
            "probe_radius_mm": round(radius, 4),
            "blockers": rows,
            "corridors": corridors,
            "open_sectors": [SECTORS[i] for i in sorted(open_sectors)],
            "via_in_pad": {"fits": not via_hits, "blockers": via_hits[:4]},
            "counts": {"total": len(rows), "movable": len(movable),
                       "reroutable": len(reroutable), "fixed": len(fixed)},
            "diagnosis": diagnosis,
            "mutability_rule": ("movable = a pad, the part can be moved; "
                                "reroutable = another net's track or via; fixed = "
                                "a pour, a keepout, the board outline or copper "
                                "graphics. It is a property of the kind, not a "
                                "decision made here."),
            # What this probe leaned on without the file saying it. The clearance
            # test here is the same one the router applies, so an assumed edge
            # clearance can decide a corridor just as it decides a track.
            "assumptions": ["%s = %s (%s)" % (a.get("fact"), a.get("value"),
                                              a.get("why") or "assumed")
                            for a in (grid.bundle.get("assumed") or [])]}
