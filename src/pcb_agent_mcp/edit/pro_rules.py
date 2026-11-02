"""The `.kicad_pro` write path: net-class rules, and proof that nothing else moved.

Why this exists at all
    `set_netclass` (edit/ops.py) edits the `(net_class ...)` blocks **inside** the
    board file. That is the right place for a v5-era board and the wrong place for
    everything KiCad 7+ does: the application reads net classes from the PROJECT
    file and ignores the ones in the board. So a caller could "succeed" and still
    be routed/DRC'd against `Default`. The rule minimums the DRC actually applies
    live in the same project file (`board.design_settings.rules`), which is why the
    DRC diff has to read both halves against one `.kicad_pro` (plan._project_beside).

    Editing that JSON by hand is what this tool replaces: a hand edit is not
    reproducible and never passes a gate.

What the file looks like (measured, KiCad 10.0.7)
    2-space indent, **keys sorted alphabetically**, **non-ASCII escaped**
    (``"Référence"`` is stored as ``"R\\u00e9f\\u00e9rence"``), trailing newline.
    Round-tripping the real 16,523-byte project through
    ``json.dumps(indent=2, sort_keys=True, ensure_ascii=True) + "\\n"`` is
    byte-identical. With ``ensure_ascii=False`` it differs by exactly one line
    (the ``sort_field`` field), so the escaping is not cosmetic.

Refusing instead of reformatting
    If a project does NOT round-trip byte-for-byte, this module refuses to write
    (``PRO_FORMAT_UNKNOWN``) rather than re-serialising someone's project in a
    style KiCad did not choose. Rewriting 900 lines of hand-sorted JSON to change
    one clearance is a diff nobody can review, and KiCad has already been shown to
    reject files that merely *look* right.

The gates (a write has to survive all of them to stand)
1. ``format_roundtrip``  the untouched text re-serialises to itself, byte for byte
2. ``diff_is_intended``  the ONLY keys that differ are the ones the operations name
3. ``bytes_written``     the bytes on disk are the bytes that were planned
4. ``json_roundtrip``    re-reading the written file gives back the planned dict
5. ``readback``          `adapters.kicad_pcb.load_project_rules` - the same reader
                         the DRC uses - sees the new values and the new ownership

A post-write failure puts the original bytes back and says so.
"""
import copy
import json
import os
import tempfile

from ..adapters import kicad_pcb as _kpcb
from . import plan as _plan
from .fsutil import atomic_write as _atomic_write
from .fsutil import sha256 as _sha256

ProRulesError = _plan.EditError

# The one place the serialisation style is written down. Both halves of the
# round-trip gate use it, so "it round-trips" and "it was written in that style"
# cannot drift apart.
STYLE = {"indent": 2, "sort_keys": True, "ensure_ascii": True}


def dump(data):
    """The bytes KiCad itself would have written for this document."""
    return (json.dumps(data, **STYLE) + "\n").encode("utf-8")


# The knobs a caller may set, and the value every new class inherits when the
# caller does not name it. These are NOT defaults invented here: a new class is a
# copy of `Default`'s shape (see `_template`), so an unnamed knob keeps Default's
# number and the reply says which ones it inherited.
KNOBS = ("clearance", "track_width", "via_diameter", "via_drill",
         "diff_pair_gap", "diff_pair_width", "line_style", "priority")

# Spellings a caller may use for the same knob (the board-side op accepts these
# too; one vocabulary, so nobody has to remember which tool calls it what).
KNOB_ALIASES = {"trace_width": "track_width", "via_dia": "via_diameter",
                "via_drill_mm": "via_drill", "diff_gap": "diff_pair_gap"}

DEFAULT_CLASS = "Default"


# --------------------------------------------------------------- paths
def project_of(path):
    """The `.kicad_pro` addressed by `path` (a project path or the board beside it)."""
    if not path:
        raise ProRulesError("path is required", "ARG_MISSING")
    if path.lower().endswith(".kicad_pro"):
        return os.path.abspath(path), None
    base, _ext = os.path.splitext(path)
    pro = base + ".kicad_pro"
    if os.path.isfile(pro):
        return os.path.abspath(pro), None
    return os.path.abspath(pro), (
        "KiCad 7+ keeps net classes in the project file, not in the board, so there "
        "is nothing to write for %r: no %r exists beside it. Create the project in "
        "KiCad first (or pass the .kicad_pro itself); this server does not invent a "
        "project file" % (path, os.path.basename(pro)))


def _read_file(path):
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
    except OSError as exc:
        raise ProRulesError("cannot read %r: %s" % (path, exc), "FILE_NOT_FOUND")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ProRulesError(
            "%r is not UTF-8 (%s); KiCad writes UTF-8 projects, so this file is not "
            "one this server can edit safely" % (path, exc), "PRO_FORMAT_UNKNOWN")
    return raw, text


def _load(pro):
    raw, text = _read_file(pro)
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise ProRulesError("%r is not valid JSON: %s" % (pro, exc), "PRO_FORMAT_UNKNOWN")
    if not isinstance(data, dict):
        raise ProRulesError("%r does not hold a JSON object" % pro, "PRO_FORMAT_UNKNOWN")
    return raw, text, data


# --------------------------------------------------------------- net_settings
def _net_settings(data, create=False):
    ns = data.get("net_settings")
    if isinstance(ns, dict):
        return ns
    if create:
        ns = data["net_settings"] = {}
        return ns
    raise ProRulesError(
        "this project has no `net_settings` object, so it carries no net classes to "
        "edit; there is nothing to change here",
        "NO_NET_SETTINGS",
        hint="open and re-save the project once in KiCad (that writes net_settings), "
             "or pass a project that has net classes")


def _classes(ns):
    classes = ns.get("classes")
    if not isinstance(classes, list):
        raise ProRulesError(
            "`net_settings.classes` is not a list; refusing to rewrite a structure "
            "this server does not recognise", "PRO_FORMAT_UNKNOWN")
    return classes


def _class_index(classes, name):
    for i, cls in enumerate(classes):
        if isinstance(cls, dict) and (cls.get("name") or DEFAULT_CLASS) == name:
            return i
    return None


def _assignment_form(ns):
    """'map' (KiCad 7+: net -> class names) or 'in_class' (v6: class.nets)."""
    if isinstance(ns.get("netclass_assignments"), dict) or "netclass_assignments" in ns:
        return "map"
    for cls in ns.get("classes") or []:
        if isinstance(cls, dict) and "nets" in cls:
            return "in_class"
    return "map"


def _assigned_to(value):
    """`"Fanout"` and `["Fanout", "HV"]` mean the same thing; keep the shape."""
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple)):
        return [str(v) for v in value]
    return [str(value)]


def _pack_assignment(names):
    names = list(dict.fromkeys(names))
    if not names:
        return None
    if len(names) == 1:
        return names[0]
    return names


def ownership(ns):
    """net -> [class names], from whichever form this project uses."""
    out = {}
    if _assignment_form(ns) == "map":
        for net, value in (ns.get("netclass_assignments") or {}).items():
            out[net] = _assigned_to(value)
        return out
    for cls in ns.get("classes") or []:
        if not isinstance(cls, dict):
            continue
        name = cls.get("name") or DEFAULT_CLASS
        for net in cls.get("nets") or []:
            out.setdefault(str(net), []).append(name)
    return out


def _set_ownership(ns, net, names):
    if _assignment_form(ns) == "map":
        bag = ns.setdefault("netclass_assignments", {})
        if not isinstance(bag, dict):
            raise ProRulesError("`netclass_assignments` is not an object; refusing to "
                                "rewrite it", "PRO_FORMAT_UNKNOWN")
        packed = _pack_assignment(names)
        if packed is None:
            bag.pop(net, None)
        else:
            bag[net] = packed
        return
    # v6 form: membership lives in the class. Remove first, then add.
    for cls in ns.get("classes") or []:
        if not isinstance(cls, dict):
            continue
        nets = [n for n in (cls.get("nets") or []) if str(n) != net]
        if nets:
            cls["nets"] = nets
        elif "nets" in cls:
            cls["nets"] = []
    for cls in ns.get("classes") or []:
        if isinstance(cls, dict) and (cls.get("name") or DEFAULT_CLASS) in names:
            bag = cls.setdefault("nets", [])
            if net not in bag:
                bag.append(net)


# --------------------------------------------------------------- diff bookkeeping
def _flatten(node, prefix, out):
    if isinstance(node, dict):
        if not node:
            out[prefix] = "{}"
        for key, value in node.items():
            _flatten(value, prefix + "/" + str(key), out)
    elif isinstance(node, list):
        if not node:
            out[prefix] = "[]"
        for i, value in enumerate(node):
            _flatten(value, prefix + "/" + str(i), out)
    else:
        out[prefix] = json.dumps(node, sort_keys=True, ensure_ascii=True)


def _flatten_classes(classes, out):
    """Key classes by NAME, not by list index.

    A dotted index path would call adding `HV` at position 2 a change to every
    later class; keyed by name, "which class changed" is the answer.
    """
    for cls in classes:
        if not isinstance(cls, dict):
            continue
        name = cls.get("name") or DEFAULT_CLASS
        _flatten(cls, "/net_settings/classes/" + name, out)


def fingerprint(data):
    """A flat `path -> value` map of the whole document, classes keyed by name."""
    out = {}
    ns = data.get("net_settings")
    if isinstance(ns, dict):
        classes = ns.get("classes")
        if isinstance(classes, list):
            _flatten_classes(classes, out)
        for key, value in ns.items():
            if key != "classes":
                _flatten(value, "/net_settings/" + str(key), out)
        rest = {k: v for k, v in data.items() if k != "net_settings"}
    else:
        rest = data
    _flatten(rest, "", out)
    return out


def changed_keys(before, after):
    keys = set(before) | set(after)
    return sorted(k for k in keys if before.get(k) != after.get(k))


def _allowed_prefixes(declared):
    return tuple(sorted(set(declared)))


def _covered(key, prefixes):
    for p in prefixes:
        if key == p or key.startswith(p + "/"):
            return True
    return False


# --------------------------------------------------------------- read
def read(path):
    """What net classes this project declares, and whether it is safe to write."""
    pro, why = project_of(path)
    if why:
        raise ProRulesError(why, "FILE_NOT_FOUND",
                            hint="this server never creates a project file")
    raw, text, data = _load(pro)
    ns = _net_settings(data)
    classes = _classes(ns)
    form = _assignment_form(ns)
    own = ownership(ns)
    roundtrip = dump(data) == raw
    rows = []
    for cls in classes:
        if not isinstance(cls, dict):
            continue
        rows.append({
            "name": cls.get("name") or DEFAULT_CLASS,
            "clearance": cls.get("clearance"),
            "track_width": cls.get("track_width"),
            "via_diameter": cls.get("via_diameter"),
            "via_drill": cls.get("via_drill"),
            "diff_pair_gap": cls.get("diff_pair_gap"),
            "diff_pair_width": cls.get("diff_pair_width"),
            "nets": sum(1 for names in own.values()
                        if (cls.get("name") or DEFAULT_CLASS) in names),
        })
    return {
        "status": "ok",
        "project": pro,
        "bytes": len(raw),
        "sha256": _sha256(raw),
        "net_classes": rows,
        "net_count": len(own),
        "assignment_form": form,
        "assignment_form_means": (
            "net -> class names live in `net_settings.netclass_assignments` (KiCad 7+)"
            if form == "map" else
            "net membership lives inside each class (`nets`) - the v6 layout"),
        "netclass_patterns": len(ns.get("netclass_patterns") or []),
        "patterns_are_read_only_here": (
            "wildcard patterns (`netclass_patterns`) are reported but not written: a "
            "pattern this tool half-edited would change which class an entire net name "
            "family resolves to"),
        "writable": roundtrip,
        "style_roundtrip": roundtrip,
        "why_not_writable": None if roundtrip else (
            "this file does not re-serialise byte-for-byte in KiCad's own style "
            "(indent=2, sorted keys, escaped non-ASCII), so writing it would reformat "
            "the whole project. Refusing is the honest answer"),
        "write_op_note": ("the rules a DRC applies alongside these classes live in "
                          "`board.design_settings.rules`; this tool does not touch them"),
    }


# --------------------------------------------------------------- operations
def _number(value, knob, netclass, allow_zero=False, positive=False):
    try:
        num = float(value)
    except (TypeError, ValueError):
        raise ProRulesError("%s=%r is not a number" % (knob, value), "KNOB_NOT_A_NUMBER")
    if num != num or num in (float("inf"), float("-inf")):
        raise ProRulesError("%s=%r is not a finite number" % (knob, value),
                            "KNOB_NOT_A_NUMBER")
    if positive and num <= 0:
        raise ProRulesError("%s must be > 0 (got %r)" % (knob, value),
                            "KNOB_OUT_OF_RANGE")
    if not allow_zero and num < 0:
        raise ProRulesError("%s must be >= 0 (got %r)" % (knob, value),
                            "KNOB_OUT_OF_RANGE")
    return int(num) if knob in ("line_style", "priority") else num


def _canon_knobs(spec):
    """The knob keys of `spec` in canonical spelling, aliases resolved."""
    out = {}
    for key, value in spec.items():
        if key in ("op", "name", "create"):
            continue
        knob = KNOB_ALIASES.get(key, key)
        if knob not in KNOBS:
            raise ProRulesError(
                "unknown knob %r for a net class; this tool writes %s (aliases: %s)"
                % (key, ", ".join(KNOBS), ", ".join(sorted(KNOB_ALIASES))),
                "UNKNOWN_KNOB")
        if value is not None:
            out[knob] = value
    return out


def _knobs(spec, netclass):
    out = {}
    for knob, value in _canon_knobs(spec).items():
        out[knob] = _number(value, knob, netclass,
                            allow_zero=knob in ("line_style", "priority"),
                            positive=knob not in ("line_style", "priority"))
    if not out:
        raise ProRulesError("set_netclass on %r names no knob to change" % netclass,
                            "NOTHING_TO_DO")
    return out


def _template(ns, name):
    """A new class is a copy of `Default`'s shape, with the caller's name."""
    classes = _classes(ns)
    idx = _class_index(classes, DEFAULT_CLASS)
    if idx is None:
        raise ProRulesError(
            "there is no `Default` class in this project, so there is no shape to "
            "copy for a new class; this server does not invent a class definition",
            "NO_DEFAULT_CLASS")
    shape = copy.deepcopy(classes[idx])
    shape["name"] = name
    return shape


def _op_set_netclass(ns, spec, report):
    name = spec.get("name")
    if not name:
        raise ProRulesError("set_netclass needs `name`", "ARG_MISSING")
    name = str(name)
    classes = _classes(ns)
    knobs = _knobs({k: v for k, v in spec.items() if k not in ("op", "name")}, name)
    idx = _class_index(classes, name)
    created = False
    if idx is None:
        if spec.get("create") is False:
            raise ProRulesError(
                "no net class named %r; create=false forbids adding one" % name,
                "NO_SUCH_CLASS")
        cls = _template(ns, name)
        classes.append(cls)
        idx = len(classes) - 1
        created = True
    cls = classes[idx]
    before = {k: cls.get(k) for k in knobs}
    for knob, value in knobs.items():
        cls[knob] = value
    after = {k: cls.get(k) for k in knobs}
    report.append({
        "op": "set_netclass",
        "name": name,
        "created": created,
        "created_from": DEFAULT_CLASS if created else None,
        "shape_copied_from": DEFAULT_CLASS if created else None,
        "inherited": sorted(k for k in KNOBS if k not in knobs),
        "changed": {k: {"before": before[k], "after": after[k]}
                    for k in knobs if before[k] != after[k]},
        "unchanged": sorted(k for k in knobs if before[k] == after[k]),
        "note": ("a new class copies `Default`'s whole record and then applies the "
                 "knobs above, so every field the caller did not name keeps Default's "
                 "value - including the ones this tool never writes"
                 if created else None),
    })
    return ["/net_settings/classes/" + name]


def _op_remove_netclass(ns, spec, report):
    name = spec.get("name")
    if not name:
        raise ProRulesError("remove_netclass needs `name`", "ARG_MISSING")
    name = str(name)
    if name == DEFAULT_CLASS:
        raise ProRulesError(
            "`Default` is the fallback class for every unassigned net; removing it "
            "leaves the project without a fallback and KiCad will not open it. Refused",
            "CANNOT_REMOVE_DEFAULT",
            hint="to retire a class, move its nets somewhere else with assign_nets "
                 "first, then remove it")
    classes = _classes(ns)
    idx = _class_index(classes, name)
    if idx is None:
        raise ProRulesError("no net class named %r" % name, "NO_SUCH_CLASS")
    own = ownership(ns)
    users = sorted(net for net, names in own.items() if name in names)
    patterns = [p for p in (ns.get("netclass_patterns") or [])
                if isinstance(p, dict) and p.get("netclass") == name]
    if users or patterns:
        target = spec.get("reassign_to")
        if not target:
            raise ProRulesError(
                "%d net(s) and %d pattern(s) still point at %r; removing it would "
                "leave dangling references" % (len(users), len(patterns), name),
                "CLASS_STILL_IN_USE",
                hint=("pass reassign_to=\"Default\" to move those nets first "
                      "(patterns are not rewritten by this tool, so a class named by "
                      "a pattern cannot be removed)"),)
        if patterns:
            raise ProRulesError(
                "%d netclass_pattern(s) name %r and this tool does not rewrite "
                "patterns; a dangling pattern is a rule the application silently "
                "drops" % (len(patterns), name), "PATTERN_REFERENCES_CLASS")
        if _class_index(classes, str(target)) is None:
            raise ProRulesError("reassign_to=%r is not a class in this project"
                                % target, "NO_SUCH_CLASS")
        for net in users:
            names = [n for n in own[net] if n != name] or [str(target)]
            _set_ownership(ns, net, names)
    removed = classes.pop(idx)
    report.append({
        "op": "remove_netclass",
        "name": name,
        "removed_record": {k: removed.get(k) for k in ("clearance", "track_width",
                                                       "via_diameter", "via_drill")},
        "nets_moved": users,
        "reassigned_to": spec.get("reassign_to") if users else None,
    })
    keys = ["/net_settings/classes/" + name]
    if users:
        keys.append("/net_settings/netclass_assignments")
        if _assignment_form(ns) == "in_class":
            keys.append("/net_settings/classes")
    return keys


def _op_assign_nets(ns, spec, report):
    target = spec.get("netclass") or spec.get("class")
    if not target:
        raise ProRulesError("assign_nets needs `netclass`", "ARG_MISSING")
    target = str(target)
    if _class_index(_classes(ns), target) is None:
        raise ProRulesError(
            "no net class named %r; assign_nets does not create classes (that would "
            "decide its knobs for you)" % target, "NO_SUCH_CLASS",
            hint="call set_netclass with name=%r first" % target)
    nets = _nets(spec)
    own = ownership(ns)
    moved = {}
    for net in nets:
        before = own.get(net) or []
        after = [n for n in before if n != target] + [target]
        _set_ownership(ns, net, after)
        moved[net] = {"before": before, "after": after}
    report.append({
        "op": "assign_nets",
        "netclass": target,
        "nets": nets,
        "changed": {n: v for n, v in moved.items() if v["before"] != v["after"]},
        "already_there": sorted(n for n, v in moved.items()
                                if v["before"] == v["after"]),
        "ownership_is_a_set": ("a net may belong to several classes; the last one that "
                               "sets a value wins for that value, which is KiCad's own "
                               "resolution order, not this tool's"),
    })
    return ["/net_settings/netclass_assignments"]
def _nets(spec):
    nets = spec.get("nets")
    if isinstance(nets, str):
        nets = [nets]
    if not nets:
        raise ProRulesError("this operation needs `nets` (a list of net names)",
                            "ARG_MISSING")
    out = []
    for net in nets:
        net = str(net).strip()
        if not net:
            raise ProRulesError("an empty net name is not a net", "ARG_INVALID")
        out.append(net)
    return list(dict.fromkeys(out))


def _op_unassign_nets(ns, spec, report):
    nets = _nets(spec)
    only = spec.get("netclass") or spec.get("class")
    own = ownership(ns)
    changed = {}
    untouched = {}
    for net in nets:
        before = own.get(net) or []
        if only:
            after = [n for n in before if n != str(only)]
        else:
            after = []
        if after == before:
            untouched[net] = before
            continue
        _set_ownership(ns, net, after)
        changed[net] = {"before": before, "after": after}
    report.append({
        "op": "unassign_nets",
        "netclass": only or None,
        "changed": changed,
        "nothing_to_do": sorted(untouched),
        "note": ("a net with no class at all falls back to Default, whose values are "
                 "unchanged by this operation" if not only else
                 "only the named class was removed from those nets"),
    })
    keys = ["/net_settings/netclass_assignments"]
    if _assignment_form(ns) == "in_class":
        keys = ["/net_settings/classes"]
    return keys


OPERATIONS = {
    "set_netclass": _op_set_netclass,
    "remove_netclass": _op_remove_netclass,
    "assign_nets": _op_assign_nets,
    "unassign_nets": _op_unassign_nets,
}


def describe():
    """The operation catalogue, for the tool layer to advertise."""
    return {
        "ops": [
            {"op": "set_netclass",
             "args": {"name": "class name (created if absent, as a copy of Default)",
                      ", ".join(KNOBS): "numbers in mm (line_style/priority ints)",
                      "create": "false refuses to add a missing class"},
             "writes": "net_settings.classes[<name>]"},
            {"op": "remove_netclass",
             "args": {"name": "class name (Default is refused)",
                      "reassign_to": "move its nets to this class first"},
             "writes": "net_settings.classes, netclass_assignments"},
            {"op": "assign_nets",
             "args": {"nets": "[net names]", "netclass": "existing class"},
             "writes": "net_settings.netclass_assignments"},
            {"op": "unassign_nets",
             "args": {"nets": "[net names]", "netclass": "optional: only this one"},
             "writes": "net_settings.netclass_assignments"},
        ],
        "knob_aliases": KNOB_ALIASES,
        "not_written": ["netclass_patterns (wildcards)", "board.design_settings.rules",
                        "everything outside net_settings"],
        "format": STYLE,
    }


# --------------------------------------------------------------- gates
def _readback_ownership(rules):
    """net -> [class names] from the loaded project, whatever form it stores."""
    own = {}
    for net, value in (rules.get("netclass_assignments") or {}).items():
        own[str(net)] = _assigned_to(value)
    for name, cls in (rules.get("net_classes") or {}).items():
        for net in cls.get("nets") or []:
            own.setdefault(str(net), []).append(name)
    return own


def _readback_check(pro, plan_specs):
    """Re-read the written project with the SAME reader the DRC uses."""
    rules = _kpcb.load_project_rules(pro)
    problems = []
    classes = rules.get("net_classes") or {}
    own = _readback_ownership(rules)
    for spec in plan_specs:
        op = spec.get("op")
        if op == "set_netclass":
            name = str(spec.get("name"))
            got = classes.get(name)
            if got is None:
                problems.append("class %r is not in the written file" % name)
                continue
            for key, want in _canon_knobs(spec).items():
                have = got.get(key)
                if have is None or abs(float(have) - float(want)) > 1e-9:
                    problems.append("class %r %s reads back %r, planned %r"
                                    % (name, key, have, want))
        elif op == "remove_netclass":
            name = str(spec.get("name"))
            if name in classes:
                problems.append("class %r still present after removal" % name)
        elif op == "assign_nets":
            target = str(spec.get("netclass") or spec.get("class"))
            for net in _nets(spec):
                if target not in own.get(net, []):
                    problems.append("net %r reads back owned by %r, planned %r"
                                    % (net, own.get(net), target))
        elif op == "unassign_nets":
            only = spec.get("netclass") or spec.get("class")
            for net in _nets(spec):
                now = own.get(net, [])
                if only and str(only) in now:
                    problems.append("net %r still owned by %r" % (net, only))
                if not only and now:
                    problems.append("net %r still owned by %r" % (net, now))
    return {
        "reader": "adapters.kicad_pcb.load_project_rules",
        "ok": not problems,
        "problems": problems,
        "classes_read": len(classes),
        "nets_with_a_class": len(own),
        "assignment_form_read": ("map" if rules.get("netclass_assignments")
                                 else "none"),
    }


# --------------------------------------------------------------- apply
def apply(path, operations, dry_run=False):
    """Write net-class rules into the project, through the five gates."""
    if not operations:
        raise ProRulesError("no operations: nothing to change", "NOTHING_TO_DO")
    pro, why = project_of(path)
    if why:
        raise ProRulesError(why, "FILE_NOT_FOUND",
                            hint="this server never creates a project file")
    raw, text, data = _load(pro)

    # gate 0 (pre-write): the untouched text must be in KiCad's own style. If it
    # is not, a rewrite would reformat a file nobody asked us to reformat.
    if dump(data) != raw:
        raise ProRulesError(
            "%r does not re-serialise byte-for-byte in KiCad's own project style, so "
            "writing it would reformat the whole file (%d bytes). Nothing was "
            "written; `board_rules` reports the same check as style_roundtrip=false"
            % (pro, len(raw)),
            "PRO_FORMAT_UNKNOWN",
            hint=("open and re-save the project in KiCad once (that normalises the "
                  "style), then retry. Re-serialising it here would silently reorder "
                  "and re-escape 900 lines that KiCad wrote"))
    ns = _net_settings(data)
    before_fp = fingerprint(data)
    spec_list = []
    report = []
    allowed = ["/net_settings/meta"]
    for i, spec in enumerate(operations):
        if not isinstance(spec, dict):
            raise ProRulesError("operation %d is not an object" % i, "ARG_INVALID")
        op = spec.get("op")
        fn = OPERATIONS.get(op)
        if fn is None:
            raise ProRulesError(
                "unknown op %r; this tool writes %s" % (op, ", ".join(sorted(OPERATIONS))),
                "UNKNOWN_OP", hint="board_rules_write describes them")
        allowed.extend(fn(ns, spec, report))
        spec_list.append(spec)

    planned = dump(data)
    after_fp = fingerprint(data)
    diff = changed_keys(before_fp, after_fp)
    stray = [k for k in diff if not _covered(k, _allowed_prefixes(allowed))]
    if stray:
        raise ProRulesError(
            "the planned document changes %d key(s) no operation named (%s); refusing "
            "to write a change nobody asked for"
            % (len(stray), ", ".join(stray[:6]) + (" ..." if len(stray) > 6 else "")),
            "DIFF_NOT_INTENDED")

    out = {
        "status": "ok",
        "project": pro,
        "dry_run": bool(dry_run),
        "bytes_before": len(raw),
        "bytes_after": len(planned),
        "sha256_before": _sha256(raw),
        "sha256_after": _sha256(planned),
        "bytes_changed": planned != raw,
        "operations": report,
        "changed_keys": diff,
        "changed_key_count": len(diff),
        "gates": {
            "format_roundtrip": {"ok": True,
                                 "style": STYLE,
                                 "means": "the file re-serialises to itself before any "
                                          "change, so the diff below is the only diff"},
            "diff_is_intended": {"ok": True, "changed": len(diff), "stray": []},
        },
    }

    if planned == raw:
        out["written"] = False
        out["would_write"] = False
        out["gates"]["bytes_written"] = {"ok": True, "written": False,
                                         "why": "the plan equals the file on disk; "
                                                "nothing was written"}
        out["gates"]["json_roundtrip"] = {"ok": True, "written": False}
        out["gates"]["readback"] = {"ok": True, "written": False,
                                    "why": "the file on disk already says this"}
        out["message"] = ("no operation changed anything: the values you asked for are "
                          "already in the project")
        return out

    checkpoint = None
    target = pro
    temp = None
    if dry_run:
        fd, temp = tempfile.mkstemp(prefix=".pcbagent-pro-", suffix=".kicad_pro",
                                    dir=os.path.dirname(pro) or ".")
        os.close(fd)
        target = temp
    else:
        checkpoint = _plan.save_checkpoint(pro, raw, "before net-class write")

    try:
        _atomic_write(target, planned)
        on_disk = _read_file(target)[0]
        out["gates"]["bytes_written"] = {
            "ok": on_disk == planned,
            "bytes": len(on_disk),
            "sha256": _sha256(on_disk),
            "measured_on": "the written file" if not dry_run
                           else "a temporary copy (this call wrote nothing)",
        }
        try:
            back = json.loads(on_disk.decode("utf-8"))
            json_ok = back == data
            json_err = None
        except ValueError as exc:            # pragma: no cover - would mean corrupt
            back = None
            json_ok = False
            json_err = str(exc)
        out["gates"]["json_roundtrip"] = {"ok": json_ok, "error": json_err}
        out["gates"]["readback"] = _readback_check(
            target, [dict(s) for s in spec_list])

        blocking = [name for name, gate in out["gates"].items() if not gate.get("ok")]
        out["blocking"] = blocking
        if blocking:
            if not dry_run:
                _atomic_write(pro, raw)
                out["restored"] = True
            out["status"] = "error"
            out["reason_code"] = "GATE_BLOCKED"
            out["message"] = ("%d gate(s) failed after writing (%s); the original "
                              "bytes were put back"
                              % (len(blocking), ", ".join(blocking)))
            out["hint"] = ("this is a defect in this tool, not in your project: the "
                           "file on disk is what it was before the call")
            return out
    finally:
        if temp and os.path.exists(temp):
            try:
                os.remove(temp)
            except OSError:
                pass

    ns_after = _net_settings(data)
    classes_after = _classes(ns_after)
    own_after = ownership(ns_after)
    out["written"] = not dry_run
    out["would_write"] = True
    out["checkpoint"] = checkpoint
    out["file_unchanged"] = bool(dry_run)
    out["net_classes"] = [
        {"name": c.get("name") or DEFAULT_CLASS,
         "clearance": c.get("clearance"),
         "track_width": c.get("track_width"),
         "via_diameter": c.get("via_diameter"),
         "via_drill": c.get("via_drill"),
         "nets": sum(1 for names in own_after.values()
                     if (c.get("name") or DEFAULT_CLASS) in names)}
        for c in classes_after if isinstance(c, dict)]
    out["assignments"] = {net: names for net, names in sorted(own_after.items())}
    out["takes_effect"] = (
        "KiCad reads these from the project file, so the next DRC / route / fill in "
        "the application uses them without reopening the board (a stale in-app DRC "
        "panel may need a refresh)")
    if dry_run:
        out["message"] = ("DRY RUN: every gate ran on a temporary copy; the project on "
                          "disk is unchanged")
    else:
        out["message"] = ("net classes written and re-read through "
                          "load_project_rules (the same reader the DRC uses)")
    return out
