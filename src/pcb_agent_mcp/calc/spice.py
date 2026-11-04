"""A SPICE-subset circuit simulator in pure stdlib.

Why this exists: an AI that can compute a track width but cannot answer "does
this divider bias the transistor" is only half a tool. ngspice is the reference
implementation of the language and the semantics here follow its manual
(modified nodal analysis, companion models, Newton with gmin) - but no ngspice
code is copied: this module is written against the published algorithms, and the
parts that are NOT implemented are listed in NOT_IMPLEMENTED so a caller can
never mistake a subset for the whole language.

What is solved
--------------
* `.op`   - DC operating point (Newton-Raphson, gmin stepping, source stepping)
* `.tran` - transient, backward Euler companion models (unconditionally stable,
            first order, which is why the timestep is the user's choice and is
            reported back rather than hidden)
* `.ac`   - small-signal AC sweep, complex MNA linearised at the DC operating
            point (this is exactly what SPICE does, so a nonlinear device's AC
            answer is only as good as its bias)

Devices: R, C, L, V, I (DC / PULSE / SIN / PWL / AC), E (VCVS), G (VCCS),
D (Shockley), Q (Ebers-Moll injection), M (Shichman-Hodges level 1),
J (JFET level 1: the same square law as M without the 1/2 factor, three
terminals, AREA as the only geometry knob), F/H/K (controlled sources and
mutual inductance), S/W (switches), T (lossless transmission line: a pair of
wires at DC, exact Y parameters in AC, the method of characteristics in
transient), O/U (distributed lines as lumped ladders).

Hierarchy: `.subckt` / `.ends` with X instances, numeric parameters and `{...}`
arithmetic on them. Instances are FLATTENED before the solver sees them, and the
internal nodes keep the `<instance>.<local>` names ngspice uses in its rawfile,
so `cross_check=True` compares the same node on both engines instead of quietly
finding nothing in common.

Why a dense solver: a PCB-level circuit is 10..300 nodes. Dense LU with partial
pivoting is O(n^3) but branch-count independent and has no fill-in heuristics to
get wrong; at 300 nodes it is ~10 ms. Sparse storage would be the wrong trade
here - it is what you do when the answer is "the board", not "the circuit".

Honesty rules kept from the rest of this server:
* a non-converged solve is an ERROR, never a plausible-looking number;
* every element's own voltage/current/power is reported so the answer can be
  sanity-checked without re-deriving it;
* `.model` parameters that were defaulted are listed in `defaulted_models`.
"""
import ast
import cmath
import math
import re

from .result import ok, err

# kT/q at 300.15 K (27 C). SPICE's default TEMP is 27 C; this module does not
# implement .temp, so the number is stated rather than configurable.
VT = 0.025852
TEMP_C = 27.0

GROUND_NAMES = ("0", "gnd", "ground", "gnd!", "vss")

# Everything the language has that this module refuses by name instead of
# silently ignoring (a silently ignored card is how a netlist "runs" and lies).
# Cards the language has that are refused BY NAME (a silently ignored card is how
# a netlist "runs" and lies). `A` left this table in 0.0.39: its two purely
# algebraic code models (gain, limit) are now solved here, and every other code
# model is still refused by name inside _parse_xspice - at the model, which is
# where the refusal belongs, because an `A` card without its model says nothing.
NOT_IMPLEMENTED_CARDS = {}

SUFFIX = {
    "t": 1e12, "g": 1e9, "meg": 1e6, "k": 1e3,
    "m": 1e-3, "u": 1e-6, "n": 1e-9, "p": 1e-12, "f": 1e-15,
}

DEFAULT_MAX_ITER = 200

# Sections in the lumped ladder a lossy line (O) or a distributed RC line (U) is
# turned into. This is a MODEL parameter, not a numerical tolerance: it is the
# only thing standing between the card and a plausible-looking answer, so it is
# reported with every answer (elements[].segments / segments_from) and defaulted
# out loud in defaulted_model_params rather than silently.
DEFAULT_LUMP = 20


def _is_at_temp(IS, temp_c, tnom_c=27.0, n=1.0, xti=3.0, eg=1.11):
    """SPICE's saturation-current temperature scaling.

    IS(T) = IS * (T/Tnom)^(XTI/N) * exp((T/Tnom - 1) * EG / (N * Vt(T)))

    This is not a refinement, it is the difference between a right and a wrong
    sign. With IS held constant and only Vt rising, a hotter junction produces a
    LARGER forward drop - the opposite of what silicon does (a small-signal
    diode at 5 mA reads 0.69 V at 27 C and about 0.58 V at 100 C, while the
    constant-IS version claims 0.86 V). A "plausible" number with the wrong sign
    is exactly the failure mode this module refuses.
    """
    T = 273.15 + temp_c
    Tn = 273.15 + tnom_c
    if abs(T - Tn) < 1e-9:
        return IS
    vt = 0.025852 * T / 300.15
    ratio = T / Tn
    return IS * ratio ** (xti / n) * math.exp((ratio - 1.0) * eg / (n * vt))


def _limit_junction(v, vt, IS):
    """SPICE's pnjlim, in the form that matters for Newton: limit the voltage at
    which the exponential is EVALUATED, not the answer.

    Why: exp() saturates (it has to, or it overflows), and a saturated
    exponential has the wrong slope - Newton then walks toward the answer one
    thermal voltage per iteration (0.0259 V), which is exactly the crawl that
    ends with "converged only with a large gmin". The standard fix is to
    evaluate the junction at a critical voltage plus a logarithmic continuation,
    which keeps the slope sane while never leaving the physical regime.
    """
    vcrit = vt * math.log(vt / (math.sqrt(2.0) * IS))
    if v <= vcrit:
        return v
    return vcrit + vt * math.log(1.0 + (v - vcrit) / vt)


# --------------------------------------------------------------- value parsing

_NUMERIC_TAIL = re.compile(r"^[+-]?(\d+(\.\d*)?|\.\d+)(e[+-]?\d+)?[a-z]*$")


def _looks_numeric(text):
    """True only if _value() would read the WHOLE token as a number.

    _value() stops at the first character that cannot be part of a number, so
    "1k*2" silently becomes 1000. For an element value that leniency has always
    been there; in a .param or a {…} slot the same string is arithmetic the user
    meant (#1: 2 kohm), so it is refused instead of guessed at.
    """
    return _NUMERIC_TAIL.match(str(text).strip().lower()) is not None


def _value(text):
    """SPICE number: 1k, 4.7u, 2.2meg, 100n, 1e3, 0.5."""
    s = str(text).strip().lower()
    if s == "":
        raise ValueError("empty value")
    num = []
    i = 0
    while i < len(s) and (s[i].isdigit() or s[i] in "+-.e"):
        # a '-' or '+' is only part of the number at the start or right after e
        if s[i] in "+-" and i > 0 and s[i - 1] != "e":
            break
        num.append(s[i])
        i += 1
    head = "".join(num)
    tail = s[i:]
    if head in ("", "+", "-", "."):
        raise ValueError("not a number: %r" % text)
    try:
        base = float(head)
    except ValueError:
        raise ValueError("not a number: %r" % text)
    mult = 1.0
    for key in ("meg", "t", "g", "k", "m", "u", "n", "p", "f"):
        if tail.startswith(key):
            mult = SUFFIX[key]
            break
    return base * mult


def _node(name):
    # None is already the global ground; str(None) would be the node named "None",
    # which is exactly the kind of quiet wrong answer this module refuses to make
    if name is None:
        return None
    n = str(name).strip()
    return None if n.lower() in GROUND_NAMES else n


# --------------------------------------------------------------- netlist parse

def parse_netlist(text):
    """Parse the subset. Returns a spec dict; raises ValueError on anything it
    does not implement, with the line number, rather than skipping it."""
    spec = {"title": "", "elements": [], "models": {}, "analyses": [],
            "options": {}, "subckts": {}, "params": {}, "warnings": []}
    lines = str(text or "").splitlines()
    sub = None
    pending_params = []
    for lineno, raw in enumerate(lines, 1):
        line = raw.split(";")[0].split("$")[0].strip()
        if line.startswith("*") or line == "":
            if lineno == 1 and line.startswith("*") and not spec["title"]:
                spec["title"] = line[1:].strip()
            continue
        low = line.lower()
        if sub is not None:
            # inside a .subckt body: only elements and .model are meaningful, and
            # ".ends" is checked BEFORE ".end" (which would otherwise match it)
            if low.startswith(".ends"):
                if sub["name"] in spec["subckts"]:
                    raise ValueError("line %d: .subckt %r is already defined on "
                                     "line %d"
                                     % (lineno, sub["name"],
                                        spec["subckts"][sub["name"]]["lineno"]))
                spec["subckts"][sub["name"]] = sub
                sub = None
                continue
            if low.startswith(".subckt"):
                raise ValueError("line %d: a .subckt inside another .subckt is not "
                                 "implemented here (ngspice has no nested "
                                 "definitions either); move it to the top level"
                                 % lineno)
            if low.startswith(".param"):
                _parse_param_card(line, lineno, sub["locals"])
                continue
            if low.startswith(".model"):
                _parse_model(sub, line, lineno)
                continue
            if low == ".end":
                raise ValueError("line %d: .end reached while .subckt %r is still "
                                 "open (a missing .ends, not a stray directive)"
                                 % (lineno, sub["name"]))
            if low.startswith("."):
                raise ValueError("line %d: directive %r inside .subckt %r is not "
                                 "implemented here"
                                 % (lineno, line.split()[0], sub["name"]))
            sub["elements"].append(_parse_element(sub, line, lineno))
            continue
        if low.startswith(".ends"):
            raise ValueError("line %d: .ends without a .subckt" % lineno)
        if low.startswith(".subckt"):
            sub = _parse_subckt(line, lineno)
            continue
        if low.startswith(".end"):
            break
        if low.startswith(".param"):
            _parse_param_card(line, lineno, pending_params)
            continue
        if low.startswith(".model"):
            _parse_model(spec, line, lineno)
            continue
        if low.startswith(".op"):
            spec["analyses"].append({"kind": "op"})
            continue
        if low.startswith(".tran"):
            parts = line.split()
            if len(parts) < 3:
                raise ValueError("line %d: .tran needs tstep tstop" % lineno)
            rec = {"kind": "tran", "tstep": _value(parts[1]),
                   "tstop": _value(parts[2]), "uic": "uic" in low}
            for p in parts[3:]:
                if p.lower().startswith("tmax="):
                    rec["tmax"] = _value(p.split("=", 1)[1])
            spec["analyses"].append(rec)
            continue
        if low.startswith(".ac"):
            parts = line.split()
            if len(parts) < 5:
                raise ValueError("line %d: .ac needs dec n fstart fstop" % lineno)
            spec["analyses"].append({"kind": "ac", "sweep": parts[1].lower(),
                                     "n": int(float(parts[2])),
                                     "fstart": _value(parts[3]),
                                     "fstop": _value(parts[4])})
            continue
        if low.startswith(".noise"):
            # .noise V(out) <input source> dec|oct|lin n fstart fstop
            # SPICE's own signature; the sweep belongs to the noise card, so
            # this analysis does not need a .ac card to borrow a sweep from.
            parts = line.split()
            if len(parts) < 7:
                raise ValueError("line %d: .noise needs V(out) <input source> "
                                 "dec|oct|lin n fstart fstop (got %d tokens)"
                                 % (lineno, len(parts)))
            m = re.match(r"^\s*[vV]\(\s*([^,()]+?)\s*(?:,\s*([^()]+?)\s*)?\)\s*$",
                         parts[1])
            if m is None:
                raise ValueError("line %d: .noise output must be written V(node) or "
                                 "V(node1,node2), not %r" % (lineno, parts[1]))
            pos = _node(m.group(1))
            neg = _node(m.group(2)) if m.group(2) is not None else None
            if pos is None and neg is None:
                raise ValueError("line %d: .noise %s refers only to ground, so the "
                                 "output is zero by construction"
                                 % (lineno, parts[1]))
            if parts[3].lower() not in ("dec", "oct", "lin"):
                raise ValueError("line %d: .noise sweep must be dec, oct or lin, "
                                 "not %r" % (lineno, parts[3]))
            spec["analyses"].append({"kind": "noise", "out": (pos, neg),
                                     "out_text": parts[1], "src": parts[2],
                                     "sweep": parts[3].lower(),
                                     "n": int(float(parts[4])),
                                     "fstart": _value(parts[5]),
                                     "fstop": _value(parts[6])})
            continue
        if low.startswith(".temp"):
            # `.temp 25 100` is a SWEEP, not a list of suggestions. It used to
            # be read as "take the first and drop the rest", which is a deck
            # that runs and answers a question nobody asked.
            vals = [_value(tok) for tok in line.split()[1:]]
            if not vals:
                raise ValueError("line %d: .temp needs at least one temperature"
                                 % lineno)
            spec["options"]["temp_c"] = vals[0]
            spec["options"]["temp_list"] = vals
            continue
        if low.startswith("."):
            raise ValueError("line %d: unsupported directive %r"
                             % (lineno, line.split()[0]))
        el = _parse_element(spec, line, lineno)
        spec["elements"].append(el)
    if sub is not None:
        raise ValueError(".subckt %r is never closed by .ends" % sub["name"])
    # .param may be written after the element that uses it, and a parameter may
    # refer to another one: resolve the whole set at the end, then flatten.
    spec["params"] = _resolve_params(pending_params, {})
    # Subcircuit instances become plain elements here, before anything downstream
    # (model reference check, node table, solver) looks at the element list.
    _expand_instances(spec)
    if not spec["analyses"]:
        spec["analyses"].append({"kind": "op"})
    # .model cards may come after the instances that use them, so the reference
    # check is deferred to the end (a netlist is not read in order)
    for el in spec["elements"]:
        if el["type"] not in ("D", "Q", "M", "J", "S", "W"):
            continue
        model = el["model"]
        if model not in spec["models"]:
            raise ValueError("line %d: %s references .model %r which is not defined"
                             % (el.get("lineno", 0), el["name"], model))
        kind = spec["models"][model]["type"]
        want = {"D": ("D",), "Q": ("NPN", "PNP"), "M": ("NMOS", "PMOS"),
                "J": ("NJF", "PJF"),
                "S": ("SW",), "W": ("CSW",)}[el["type"]]
        if kind not in want:
            raise ValueError("line %d: %s needs a %s model, %r is %s"
                             % (el.get("lineno", 0), el["name"],
                                "/".join(want), model, kind))
    # Last step, once models are known and subcircuits are flattened: a line
    # becomes a ladder of plain elements and nothing downstream sees it again.
    _expand_lossy_lines(spec)
    return spec


# ------------------------------------------------------------ lossy lines (O/U)
# An O card (LTRA) and a U card (URC) are the two DISTRIBUTED cards in this
# subset. Neither has a single MNA row: the card carries per-length parameters
# and a length, not a component value. So both are turned, once the whole file is
# in hand, into an N-section ladder of ordinary R/L/C - at which point the node
# table, the solver and the report need to know nothing about them.
#
# That is a MODEL CHOICE and it is stated rather than hidden: the section count
# (lump=, default DEFAULT_LUMP) is the model's only knob, the ladder is what the
# answer is the answer TO, and no r/l/g/c here is frequency dependent. What the
# lumped model does NOT contain -skin effect, dielectric loss, dispersion of a
# real trace- is refused in words in NOT_COMPUTED instead of being approximated
# silently.

_LTRA_KEYS = ("R", "L", "G", "C", "LEN", "LUMP")
_URC_KEYS = ("R", "RPERL", "C", "CPERL", "LEN", "LUMP")

_LTRA_REFUSED = {
    "Z0": "a lossless line's characteristic impedance. That is the T card "
          "(Tname n1 n2 n3 n4 Z0=... TD=...), which is solved exactly; an O model "
          "takes r/l/g/c/len and derives Z0 = sqrt(l/c) from them",
    "TD": "a lossless line's delay. Give r/l/g/c/len here (the delay is then "
          "sqrt(l*c)*len), or use the T card for an ideal line",
    "REL": "a truncation-error tolerance of a variable-step engine; this solver is "
           "fixed-step, so the tolerance could not be honoured",
    "ABS": "a truncation-error tolerance of a variable-step engine; this solver is "
           "fixed-step, so the tolerance could not be honoured",
}

_URC_REFUSED = {
    "ISPERL": "a per-length saturation current, i.e. a nonlinear (diode-like) RC "
              "line; only the linear distributed RC is implemented",
    "RSPERL": "a per-length series resistance of the saturation path; no nonlinear "
              "RC line is implemented",
    "FMAX": "the frequency at which the URC stops behaving like a distributed line; "
            "the ladder here has no such cutoff - its own dispersion IS the model",
    "REL": "a truncation-error tolerance of a variable-step engine; this solver is "
           "fixed-step",
    "ABS": "a truncation-error tolerance of a variable-step engine; this solver is "
           "fixed-step",
}

def _lossy_model(spec, el):
    """Resolve and validate an O/U card's .model. Returns a dict of lengths."""
    where = "line %d: %s" % (el.get("lineno") or 0, el["name"])
    name = el["model"]
    models = spec["models"]
    if name not in models:
        raise ValueError("%s references .model %r which is not defined"
                         % (where, name))
    m = models[name]
    kind = (m.get("type") or "").upper()
    params = m["params"]
    if el["type"] == "O":
        if kind != "LTRA":
            raise ValueError("%s needs an LTRA model, and %r is %s. An LTRA model "
                             "carries r/l/g/c/len: Oname n1 n2 n3 n4 model"
                             % (where, name, kind or "untyped"))
        allowed, refused = _LTRA_KEYS, _LTRA_REFUSED
    else:
        if kind != "URC":
            raise ValueError("%s needs a URC model, and %r is %s. A URC model "
                             "carries r (or rperl), c (or cperl) and len: Uname n1 "
                             "n2 n3 model" % (where, name, kind or "untyped"))
        allowed, refused = _URC_KEYS, _URC_REFUSED
    for k in params:
        if k in allowed:
            continue
        if k in refused:
            raise ValueError("%s: %s= in .model %r is %s; it is refused by name "
                             "instead of being ignored (an ignored parameter is a "
                             "netlist that runs and lies)" % (where, k, name, refused[k]))
        raise ValueError("%s: .model %r has %s=, which is not a parameter of a %s "
                         "model here (implemented: %s)"
                         % (where, name, k, kind, ", ".join(allowed)))
    if "LEN" not in params:
        raise ValueError("%s: .model %r has no LEN= (the line's length). ngspice "
                         "refuses an LTRA without a length, and defaulting it to 1 "
                         "would scale every total the caller sees by a factor "
                         "nobody stated" % (where, name))
    length = params["LEN"]
    if length <= 0:
        raise ValueError("%s: .model %r has LEN=%g; a line's length must be positive"
                         % (where, name, length))
    if el["type"] == "O":
        r = params.get("R", 0.0)
        l = params.get("L", 0.0)
        g = params.get("G", 0.0)
        c = params.get("C", 0.0)
        if not (r or l or g or c):
            raise ValueError("%s: .model %r gives no R, L, G or C, so the card is a "
                             "chain of nothing: delete it and join the nodes"
                             % (where, name))
    else:
        if "R" in params and "RPERL" in params:
            raise ValueError("%s: .model %r gives both R= and RPERL= (the same "
                             "per-length resistance under two names); give one"
                             % (where, name))
        if "C" in params and "CPERL" in params:
            raise ValueError("%s: .model %r gives both C= and CPERL= (the same "
                             "per-length capacitance under two names); give one"
                             % (where, name))
        r = params.get("R", params.get("RPERL"))
        c = params.get("C", params.get("CPERL"))
        l = g = 0.0
        if not r:
            raise ValueError("%s: .model %r has no R= (or RPERL=); a uniform RC line "
                             "with no series resistance is a short" % (where, name))
        if not c:
            raise ValueError("%s: .model %r has no C= (or CPERL=); without the "
                             "distributed capacitance the card is a resistor, so "
                             "delete it and use an R" % (where, name))
    for key, val in (("R", r), ("L", l), ("G", g), ("C", c)):
        if val < 0:
            raise ValueError("%s: .model %r has %s=%g; a per-length parameter cannot "
                             "be negative" % (where, name, key, val))
    lump = params.get("LUMP")
    if lump is not None and (lump != int(lump) or lump < 1):
        raise ValueError("%s: .model %r has LUMP=%g; the number of ladder sections "
                         "must be a whole number of at least 1" % (where, name, lump))
    return {"length": length, "r": r, "l": l, "g": g, "c": c,
            "lump": int(lump) if lump is not None else None}

def _ladder(el, spec, taken, lossy):
    """One O/U card -> its sections, as ordinary R/L/C elements."""
    name = el["name"]
    where = "line %d: %s" % (el.get("lineno") or 0, name)
    v = _lossy_model(spec, el)
    base = name.lower()
    segs = v["lump"] or DEFAULT_LUMP
    from_lump = ("lump=%d" % v["lump"] if v["lump"]
                 else "default %d (lump= not given)" % DEFAULT_LUMP)
    if el["type"] == "O":
        n1, ref, far = el["nodes"][0], el["nodes"][1], el["nodes"][2]
        ports = ((n1, ref), (far, ref))
    else:
        n1, far, ref = el["nodes"][0], el["nodes"][1], el["nodes"][2]
        ports = ((n1, ref), (far, ref))
    r_s = v["r"] * v["length"] / segs
    l_s = v["l"] * v["length"] / segs
    g_s = v["g"] * v["length"] / segs
    c_s = v["c"] * v["length"] / segs
    node_names = ["%s#n%d" % (base, k) for k in range(1, segs)]
    mid_names = ["%s#a%d" % (base, k) for k in range(1, segs + 1)]
    elem_names = []
    for k in range(1, segs + 1):
        elem_names.extend(["%s#r%d" % (base, k), "%s#l%d" % (base, k),
                           "%s#g%d" % (base, k), "%s#c%d" % (base, k)])
    for nm in node_names + mid_names + elem_names:
        if nm.lower() in taken:
            raise ValueError("%s: the ladder needs the internal name %r, which the "
                             "netlist already uses; rename the line so the expanded "
                             "nodes cannot collide with a real node" % (where, nm))
        taken.add(nm.lower())
    sig = [n1] + node_names + [far]
    elems = []
    first = None
    for k in range(1, segs + 1):
        a, bb = sig[k - 1], sig[k]
        if r_s > 0 and l_s > 0:
            mid = mid_names[k - 1]
            er = {"type": "R", "name": "%s#r%d" % (base, k), "nodes": (a, mid),
                  "value": r_s, "_internal": True}
            el_ = {"type": "L", "name": "%s#l%d" % (base, k), "nodes": (mid, bb),
                   "value": l_s, "ic": None, "_internal": True}
            elems.extend([er, el_])
            if first is None:
                first = {"kind": "R", "a": a, "b": mid, "value": r_s}
        elif l_s > 0:
            el_ = {"type": "L", "name": "%s#l%d" % (base, k), "nodes": (a, bb),
                   "value": l_s, "ic": None, "_internal": True}
            elems.append(el_)
            if first is None:
                first = {"kind": "L", "name": el_["name"]}
        elif r_s > 0:
            er = {"type": "R", "name": "%s#r%d" % (base, k), "nodes": (a, bb),
                  "value": r_s, "_internal": True}
            elems.append(er)
            if first is None:
                first = {"kind": "R", "a": a, "b": bb, "value": r_s}
        if g_s > 0:
            elems.append({"type": "R", "name": "%s#g%d" % (base, k), "nodes": (bb, ref),
                          "value": 1.0 / g_s, "_internal": True})
        if c_s > 0:
            elems.append({"type": "C", "name": "%s#c%d" % (base, k), "nodes": (bb, ref),
                          "value": c_s, "ic": None, "_internal": True})
    def sig(x):
        return float("%.12g" % x)

    per_length = {"r_ohm_per_m": sig(v["r"]), "l_h_per_m": sig(v["l"]),
                  "g_s_per_m": sig(v["g"]), "c_f_per_m": sig(v["c"])}
    rec = {"name": name, "type": el["type"], "model": el["model"],
           "nodes": el["nodes"], "ports": ports,
           "segments": segs, "segments_from": from_lump,
           "length_m": v["length"], "per_length": per_length,
           "totals": {"series_r_ohm": sig(v["r"] * v["length"]),
                      "series_l_h": sig(v["l"] * v["length"]),
                      "shunt_g_s": sig(v["g"] * v["length"]),
                      "shunt_c_f": sig(v["c"] * v["length"])},
           "first": first}
    lossy.append(rec)
    return elems

def _expand_lossy_lines(spec):
    """Replace every O/U card with its ladder, after models and .subckt are settled."""
    if not any(el["type"] in ("O", "U") for el in spec["elements"]):
        return
    taken = set()
    for el in spec["elements"]:
        taken.add(el["name"].lower())
        for n in el["nodes"]:
            if n is not None:
                taken.add(n.lower())
    out = []
    lossy = spec.get("lossy") or []
    for el in spec["elements"]:
        if el["type"] not in ("O", "U"):
            out.append(el)
            continue
        out.extend(_ladder(el, spec, taken, lossy))
    spec["elements"] = out
    spec["lossy"] = lossy

# ------------------------------------------------------------------ subcircuits

SUBCKT_MAX_DEPTH = 32

# Only these five operators exist. Anything else is refused by name, because a
# half-implemented expression language produces plausible wrong answers, which is
# the one thing this module is built not to do.
_EXPR_OPS = {ast.Add: lambda a, b: a + b,
             ast.Sub: lambda a, b: a - b,
             ast.Mult: lambda a, b: a * b,
             ast.Div: lambda a, b: a / b,
             ast.Pow: lambda a, b: a ** b}


# SPICE's statistical values look like a number to a caller and are not one:
# `R1 1 0 1k mc(1)` used to parse as 1000 ohm and come back as a perfectly good
# deterministic answer to a question nobody asked. They are refused by name.
_STAT_FUNCS = ("mc(", "unif(", "aunif(", "gauss(", "agauss(", "limit(")

def _value_or_expr(tok, lineno):
    """A number, or a ``{...}`` expression kept for later.

    A subcircuit's parameters are not in scope at its definition, so the value
    has no answer yet; it becomes one when an instance supplies (or defaults)
    them.

    A Monte-Carlo / statistical value (``mc(1)``, ``agauss(1, .1, 1)``) is
    refused here by name rather than read as its nominal number: one run of a
    distribution is not the distribution, and the deck carries neither the
    sample count nor the seed that would make the answer mean anything.
    """
    t = str(tok).strip()
    if len(t) > 2 and t.startswith("{") and t.endswith("}"):
        return ("expr", t[1:-1].strip())
    if _looks_numeric(t):
        return _value(t)
    low = t.lower()
    for fn in _STAT_FUNCS:
        if low.startswith(fn) or (" " + fn) in (" " + low):
            raise ValueError(
                "line %d: %r is a Monte-Carlo / statistical value, which this "
                "subset does not run: the deck names neither the sample count nor "
                "the seed, so reading it as its nominal number would be a "
                "confident answer to a different question. Give the nominal value "
                "and run the deck once per sample yourself (or use "
                "engine='ngspice' with a .control loop)" % (lineno, t))
    raise ValueError("line %d: %r is neither a number nor a {…} expression (write "
                     "{%s} if it is arithmetic)" % (lineno, t, t))


class _MissingParam(ValueError):
    """A name used in a {…} expression that is not in scope, with the name kept.

    Two very different situations produce this: a typo, and a circular .param
    definition where every name in the cycle is still waiting on the others.
    _resolve_params tells them apart by asking whether the missing name is one of
    the names it is still holding.
    """

    def __init__(self, message, name):
        ValueError.__init__(self, message)
        self.name = name


def _eval_expr(text, env, where):
    """Evaluate ``{...}`` with a whitelisted AST, never with eval()."""
    try:
        tree = ast.parse(text, mode="eval")
    except SyntaxError:
        raise ValueError("%s: %r is not a valid expression" % (where, text))

    def walk(node):
        if isinstance(node, ast.Expression):
            return walk(node.body)
        if isinstance(node, ast.Constant):
            v = node.value
            if isinstance(v, bool) or not isinstance(v, (int, float)):
                raise ValueError("%s: only numbers are allowed in %r"
                                 % (where, text))
            return float(v)
        if isinstance(node, ast.Name):
            key = node.id.lower()
            if key not in env:
                raise _MissingParam(
                    "%s: %r is not a parameter in scope here (%s)"
                    % (where, node.id,
                       ", ".join(sorted(env)) if env else "none"), key)
            return env[key]
        if isinstance(node, ast.UnaryOp) and isinstance(node.op,
                                                       (ast.UAdd, ast.USub)):
            v = walk(node.operand)
            return v if isinstance(node.op, ast.UAdd) else -v
        if isinstance(node, ast.BinOp) and type(node.op) in _EXPR_OPS:
            a, b = walk(node.left), walk(node.right)
            if isinstance(node.op, ast.Div) and b == 0.0:
                raise ValueError("%s: division by zero in %r" % (where, text))
            if isinstance(node.op, ast.Pow) and abs(b) > 64:
                raise ValueError("%s: exponent %g is out of range in %r"
                                 % (where, b, text))
            try:
                return _EXPR_OPS[type(node.op)](a, b)
            except OverflowError:
                raise ValueError("%s: %r overflows" % (where, text))
        raise ValueError("%s: %r uses %s, which is not implemented here (only "
                         "+ - * / ** on numbers and parameters)"
                         % (where, text, type(node).__name__))

    out = walk(tree)
    if not isinstance(out, float) or out != out or out in (float("inf"),
                                                           float("-inf")):
        raise ValueError("%s: %r does not evaluate to a finite number"
                         % (where, text))
    return out


def _resolve_value(val, env, where):
    """A number in scope: either already numeric, or ``{...}`` evaluated here."""
    if isinstance(val, tuple):
        return _eval_expr(val[1], env, where)
    return float(val)


_PARAM_ASSIGN = re.compile(r"([A-Za-z_][A-Za-z_0-9]*)\s*=\s*(\{[^}]*\}|[^\s,]+)")


def _parse_param_card(line, lineno, into):
    """.param a=1k b={a/2} ... -> appended to *into* as (name, value, lineno).

    Values are numbers or {…}. ngspice also accepts a bare expression ("1k*2"),
    this build asks for the braces instead of growing a second grammar: one
    parser for both .subckt parameters and .param cards, one set of error
    messages. The entries are resolved later (see _resolve_params), because a
    parameter may refer to one written after it.
    """
    text = line.strip()[len(".param"):].strip()
    if not text:
        raise ValueError("line %d: .param needs at least one name=value" % lineno)
    pos, got = 0, 0
    while pos < len(text):
        m = _PARAM_ASSIGN.match(text, pos)
        if m is None or m.start() != pos:
            raise ValueError("line %d: cannot read .param from %r onward (expected "
                             "name=value)" % (lineno, text[pos:]))
        key = m.group(1).strip().lower()
        raw = m.group(2).strip()
        if raw.startswith("{"):
            if not raw.endswith("}"):
                raise ValueError("line %d: .param %s=%s: the {…} expression is "
                                 "not closed" % (lineno, key, raw))
            into.append((key, ("expr", raw[1:-1].strip()), lineno))
        elif _looks_numeric(raw):
            into.append((key, _value(raw), lineno))
        else:
            raise ValueError("line %d: .param %s=%s: %r is neither a number nor "
                             "a {…} expression (write {%s} if it is arithmetic)"
                             % (lineno, key, raw, raw, raw))
        pos = m.end()
        while pos < len(text) and text[pos] in " \t,":
            pos += 1
        got += 1
    if not got:
        raise ValueError("line %d: .param needs at least one name=value" % lineno)


def _resolve_params(pending, base_env, where="param"):
    """Resolve (name, value, lineno) triples, in dependency order.

    Sources are not read in order, so a parameter may use one written later.
    Resolution therefore runs in rounds until nothing moves; when a round makes
    no progress at all, the remaining names are a circular definition and the
    first one is reported with the reason it failed.
    """
    env = dict(base_env)
    todo = list(pending)
    while todo:
        stuck = []
        for name, val, lineno in todo:
            try:
                env[name] = _resolve_value(val, env, "line %d: %s %s"
                                           % (lineno, where, name))
            except ValueError:
                stuck.append((name, val, lineno))
        if len(stuck) == len(todo):
            waiting = set(n for n, _v, _l in stuck)
            name, val, lineno = stuck[0]
            try:
                _resolve_value(val, env, "line %d: %s %s" % (lineno, where, name))
            except _MissingParam as exc:
                if exc.name in waiting:
                    raise ValueError(
                        "line %d: %s %r refers to %r, which is itself unresolved "
                        "(still waiting: %s) - a circular definition cannot be "
                        "resolved"
                        % (lineno, where, name, exc.name,
                           ", ".join(sorted(waiting))))
                raise
            except ValueError:
                raise
        todo = stuck
    return env


def _parse_subckt(line, lineno):
    """.subckt NAME p1 p2 ... [PARAMS:] k=v ...

    Pins come first and parameters after: a pin name containing "=" is not a pin,
    and accepting a parameter before the pins would make "which token is a node"
    depend on luck.
    """
    parts = line.split()
    if len(parts) < 2:
        raise ValueError("line %d: .subckt needs a name" % lineno)
    name = parts[1].strip().lower()
    pins = []
    params = {}
    for tok in parts[2:]:
        low = tok.lower()
        if low in ("params:", "params"):
            continue
        if "=" in tok:
            key, val = tok.split("=", 1)
            params[key.strip().lower()] = _value_or_expr(val.strip(), lineno)
            continue
        if params:
            raise ValueError("line %d: pin %r comes after a parameter in .subckt "
                             "%r; pins must come first" % (lineno, tok, name))
        pins.append(tok)
    return {"name": name, "pins": pins, "params": params, "elements": [],
            "models": {}, "locals": [], "lineno": lineno}


def _parse_instance(parts, name, lineno):
    """X<name> n1 n2 ... <subckt> [param=value ...]

    The subcircuit name is the LAST token that is not a parameter. That is the
    only place SPICE puts it; picking the token that happens to match a known
    subcircuit name would turn a typo into a silently different circuit.
    """
    overrides = {}
    tail = []
    for tok in parts[1:]:
        if tok.lower() in ("params:", "params"):
            continue
        if "=" in tok:
            key, val = tok.split("=", 1)
            overrides[key.strip().lower()] = _value_or_expr(val.strip(), lineno)
            continue
        tail.append(tok)
    if len(tail) < 2:
        raise ValueError("line %d: %s needs at least one node and a subcircuit "
                         "name" % (lineno, name))
    return {"type": "X", "name": name, "lineno": lineno,
            "nodes": tuple(tail[:-1]), "sub": tail[-1].strip().lower(),
            "overrides": overrides}


def _resolve_element(el, env, where):
    """Replace every {…} left on this element by its number, in place.

    Element values, gains, a source's dc / ac amplitude+phase and every PULSE /
    SIN / PWL argument go through the same reader, so "which slot accepts a
    parameter" does not have to be remembered card by card.
    """
    for key in ("value", "gain", "dc"):
        if isinstance(el.get(key), tuple):
            el[key] = _resolve_value(el[key], env, "%s (%s)" % (where, key))
    ac = el.get("ac")
    if isinstance(ac, list):
        el["ac"] = [_resolve_value(x, env, "%s (ac)" % where) for x in ac]
    wave = el.get("wave")
    if isinstance(wave, dict):
        wave["args"] = [_resolve_value(x, env, "%s (%s args)" % (where, wave["kind"]))
                        for x in wave["args"]]
    if el.get("ir") is not None:
        # a name inside a B expression is a parameter like any other; folding it
        # into a number now is what makes "not in scope" a build error rather
        # than a convergence failure
        el["ir"] = _b_fold(el["ir"], env, "%s (%s=)" % (where, el["text"]))


def _expand_instances(spec):
    """Flatten every X instance into plain elements.

    Subcircuits are the one construct that cannot be handled where it is read (a
    definition may come after its use, and X bodies are definitions themselves),
    so this runs once the whole file is in hand.

    Internal nodes are named ``<instance path>.<local name>`` in lower case, which
    is also how ngspice names them in its rawfile: that is what makes
    ``cross_check=True`` compare the same node on both engines instead of finding
    nothing in common and calling it a day. Element names get the same prefix, so
    two instances of one subcircuit cannot collide.
    """
    global_env = spec.get("params") or {}
    out = []
    for el in spec["elements"]:
        if el["type"] != "X":
            out.append(el)
            continue
        out.extend(_instantiate(spec, el, el["name"].lower(), global_env, (), 0))
    spec["elements"] = out
    for el in spec["elements"]:
        _resolve_element(el, global_env,
                         "line %d: %s" % (el.get("lineno", 0), el["name"]))


def _instantiate(spec, el, path, caller_env, stack, depth):
    """One instance -> its subcircuit's elements, renamed and parameterised."""
    where = "line %d" % (el.get("lineno") or 0)
    sub = spec["subckts"].get(el["sub"])
    if sub is None:
        raise ValueError("%s: %s instantiates subcircuit %r, which is not defined "
                         "(defined: %s)"
                         % (where, el["name"], el["sub"],
                            ", ".join(sorted(spec["subckts"])) or "none"))
    if el["sub"] in stack:
        raise ValueError("%s: subcircuit %r instantiates itself (%s)"
                         % (where, el["sub"],
                            " -> ".join(list(stack) + [el["sub"]])))
    if depth >= SUBCKT_MAX_DEPTH:
        raise ValueError("%s: subcircuit %r nests deeper than %d levels"
                         % (where, el["sub"], SUBCKT_MAX_DEPTH))
    if len(el["nodes"]) != len(sub["pins"]):
        raise ValueError("%s: %s gives %d node(s) to subcircuit %r, which has %d "
                         "pin(s) (%s)"
                         % (where, el["name"], len(el["nodes"]), el["sub"],
                            len(sub["pins"]), " ".join(sub["pins"]) or "none"))
    # a body may read a GLOBAL .param; local names shadow it
    env = dict(spec.get("params") or {})
    for key, val in sub["params"].items():
        env[key] = _resolve_value(val, caller_env,
                                  "%s (.subckt %s default %s)"
                                  % (where, el["sub"], key))
    for key, val in el["overrides"].items():
        if key not in sub["params"]:
            local_names = sorted(n for n, _v, _l in (sub.get("locals") or []))
            declared = sorted(sub["params"]) + local_names
            raise ValueError("%s: %s sets parameter %r, which .subckt %r does not "
                             "declare (%s)"
                             % (where, el["name"], key, el["sub"],
                                ", ".join(declared) or "none"))
        env[key] = _resolve_value(val, caller_env,
                                  "%s (%s %s)" % (where, el["name"], key))
    if sub.get("locals"):
        env.update(_resolve_params(sub["locals"], env,
                                   ".subckt %s .param" % el["sub"]))
    pin_map = {}
    for pin, actual in zip(sub["pins"], el["nodes"]):
        # a pin tied to a ground name collapses to the one global ground, exactly
        # like a top-level node called 0 does
        pin_map[pin.strip().lower()] = _node(actual)

    def mapped(node):
        if node is None:
            return None
        low = str(node).lower()
        if low in pin_map:
            return pin_map[low]
        return "%s.%s" % (path, low)

    body_names = set(b["name"].lower() for b in sub["elements"])

    def scoped_ref(nm):
        """An element name inside this body -> its prefixed name.

        A name that the body does not define is left alone on purpose: it may be
        a top-level element, and that reference is checked (and refused with its
        own message) when the whole deck is in hand.
        """
        low = str(nm).lower()
        return "%s.%s" % (path, low) if low in body_names else nm

    out = []
    for body in sub["elements"]:
        copy = dict(body)
        # lower case throughout: internal node names are what ngspice prints in
        # its rawfile (x9.x1.m), and a name that differs only by case compares as
        # "no node in common" instead of a mismatch
        copy["name"] = "%s.%s" % (path, body["name"].lower())
        copy["nodes"] = tuple(mapped(n) for n in body["nodes"])
        if body.get("inputs"):
            copy["inputs"] = tuple(mapped(n) for n in body["inputs"])
        if body.get("ctrl"):
            copy["ctrl"] = tuple(mapped(n) for n in body["ctrl"])
        if copy["type"] in ("F", "H", "W"):
            copy["ctrl_name"] = scoped_ref(copy["ctrl_name"])
        elif copy["type"] == "K":
            copy["l1"] = scoped_ref(copy["l1"])
            copy["l2"] = scoped_ref(copy["l2"])
        if copy["type"] == "X":
            copy["overrides"] = dict(
                (k, _resolve_value(v, env, "%s (%s %s)"
                                   % (where, copy["name"], k)))
                for k, v in body["overrides"].items())
            out.extend(_instantiate(spec, copy, copy["name"], env,
                                    tuple(stack) + (el["sub"],), depth + 1))
            continue
        if copy["type"] in ("BV", "BI"):
            copy["ir"] = _b_remap(copy["ir"], mapped)
            copy["deps"] = tuple(mapped(n) for n in copy["deps"])
        _resolve_element(copy, env, "%s (%s)" % (where, copy["name"]))
        if "model" in copy:
            if copy["model"] in sub["models"]:
                full = "%s.%s" % (path, copy["model"])
                if full not in spec["models"]:
                    spec["models"][full] = sub["models"][copy["model"]]
                copy["model"] = full
            # a model name that is not defined inside the subcircuit is a GLOBAL
            # .model: left alone on purpose, and the deferred reference check
            # still has to find it at the top level
        out.append(copy)
    return out


def _split_params(text):
    """Split a .model body on whitespace that is OUTSIDE brackets and quotes.

    An XSPICE vector parameter is written `in_gain=[2 3]`, and a plain
    ``str.split()`` cuts that into ``in_gain=[2`` and ``3]``. Measured on
    ngspice-47, whose own MIF reader then answers "unrecognized parameter (3]) -
    ignored" and quietly keeps the default for every port - a wrong number with
    no error, which is exactly what this module is not allowed to produce.
    """
    out, cur, depth, quote = [], [], 0, None
    for ch in text:
        if quote is not None:
            cur.append(ch)
            if ch == quote:
                quote = None
            continue
        if ch in "'\"":
            quote = ch
            cur.append(ch)
            continue
        if ch in "[(":
            depth += 1
        elif ch in "])":
            depth = max(0, depth - 1)
        if ch.isspace() and depth == 0:
            if cur:
                out.append("".join(cur))
                cur = []
            continue
        cur.append(ch)
    if cur:
        out.append("".join(cur))
    return out


def _param_value(text):
    """A scalar SPICE number, or a `[a b c]` vector kept as a list."""
    t = str(text).strip()
    if t.startswith("[") and t.endswith("]"):
        inner = t[1:-1].replace(",", " ")
        vals = [_value(tok) for tok in inner.split()]
        if not vals:
            raise ValueError("a vector parameter written as [] names no value")
        return vals
    return _value(t)


def _parse_model(spec, line, lineno):
    # .model NAME TYPE(p=v p=v ...)
    body = line[len(".model"):].strip()
    # the parenthesis is glued to the TYPE ("dmod D(IS=...)"), not to the name,
    # so the head must be split on whitespace rather than on "("
    if "(" in body:
        head = body[:body.index("(")].split()
        inner = body[body.index("(") + 1:body.rindex(")")] if ")" in body else ""
    else:
        head = body.split()
        inner = ""
    if len(head) < 2:
        raise ValueError("line %d: .model needs NAME TYPE(...)" % lineno)
    name = head[0].strip().lower()
    kind = head[1].strip().upper()
    bits = [kind] + _split_params(inner)
    params = {}
    for b in bits[1:]:
        if "=" not in b:
            continue
        k, v = b.split("=", 1)
        params[k.strip().upper()] = _param_value(v)
    spec["models"][name] = {"type": kind, "params": params}


def _parse_tline(parts, name, lineno):
    """`Tname n1 n2 n3 n4 Z0=<ohm> TD=<s>` (or F=/NL=, which is the same delay).

    Port 1 is (n1, n2) and port 2 is (n3, n4), which is ngspice's order and the
    reason the four nodes are read positionally instead of by keyword: the card
    carries no other structure to read them from.

    Z0 is REQUIRED here, not defaulted to 50 ohm: ngspice-47 itself refuses the
    card without it ("transmission line z0 must be given", measured), and a
    silently assumed 50 ohm would silently set every reflection coefficient in
    the deck. TD is required for the same reason - ngspice defaults a missing TD
    to 0, which quietly turns the line into a wire.
    """
    if len(parts) < 5:
        raise ValueError("line %d: %s needs four nodes: Tname n1 n2 n3 n4 "
                         "Z0=<ohm> TD=<s> (port 1 is n1,n2 and port 2 is n3,n4); "
                         "got %r" % (lineno, name, " ".join(parts[1:])))
    if any("=" in p for p in parts[1:5]):
        raise ValueError("line %d: %s needs four node names before any "
                         "parameter: Tname n1 n2 n3 n4 Z0=... TD=...; got %r"
                         % (lineno, name, " ".join(parts[1:5])))
    nodes = tuple(_node(parts[k]) for k in (1, 2, 3, 4))
    params = {}
    for p in parts[5:]:
        if "=" not in p:
            raise ValueError("line %d: %s: %r is not a parameter; a lossless line "
                             "takes Z0=<ohm> and TD=<s> (or F=<Hz> NL=<n>)"
                             % (lineno, name, p))
        key, _, val = p.partition("=")
        key = key.strip().lower()
        if key not in ("z0", "td", "f", "nl"):
            raise ValueError("line %d: %s: %s= is not implemented here; this card "
                             "takes Z0, TD, F and NL. IC= (the line's initial "
                             "voltages/currents), REL= and ABS= (truncation-error "
                             "tolerances this fixed-step solver cannot honour) are "
                             "refused by name instead of being ignored"
                             % (lineno, name, key))
        try:
            params[key] = _value(val.strip())
        except ValueError as exc:
            raise ValueError("line %d: %s: %s= %s" % (lineno, name, key, exc))
    if all(n is None for n in nodes):
        # checked before the port-to-port case below, which a four-way ground
        # card also satisfies: "both ports on ground" says what is wrong with
        # the deck, "connects a port to itself" would send the reader looking
        # for a duplicated node name that is not there
        raise ValueError("line %d: %s has both ports on ground, so the card "
                         "constrains nothing and leaves the solver with a free "
                         "unknown" % (lineno, name))
    if nodes[0] == nodes[2] and nodes[1] == nodes[3]:
        raise ValueError("line %d: %s connects a port to itself (n1==n3 and "
                         "n2==n4); the DC constraint would read 0 = 0, so the "
                         "circuit has no solution. A line joins two different "
                         "port pairs" % (lineno, name))
    if "z0" not in params:
        raise ValueError("line %d: %s needs Z0= (the characteristic impedance); "
                         "assuming 50 ohm here would silently set every "
                         "reflection coefficient in the deck" % (lineno, name))
    z0 = params["z0"]
    if z0 <= 0:
        raise ValueError("line %d: %s has Z0=%g; a characteristic impedance must "
                         "be positive" % (lineno, name, z0))
    if "td" in params and "f" in params:
        raise ValueError("line %d: %s gives both TD= and F=; give one of them "
                         "(TD=<seconds>, or F=<Hz> with NL=<normalised length>, "
                         "which is TD = 1/(F*NL))" % (lineno, name))
    if "nl" in params and "f" not in params:
        raise ValueError("line %d: %s gives NL= without F=; NL is the normalised "
                         "length AT F, so it only means something together with "
                         "F=<Hz>" % (lineno, name))
    if "f" in params:
        freq = params["f"]
        nl = params.get("nl", 1.0)
        if freq <= 0 or nl <= 0:
            raise ValueError("line %d: %s has F=%g NL=%g; both must be positive"
                             % (lineno, name, freq, nl))
        td = 1.0 / (freq * nl)
    else:
        td = params.get("td")
    if td is None:
        raise ValueError("line %d: %s has no delay: give TD=<seconds> (or F=<Hz> "
                         "with NL=<n>). ngspice silently defaults a missing TD to "
                         "0, which turns the line into a wire - a netlist that "
                         "loses this number is a netlist whose delay nobody knows"
                         % (lineno, name))
    if td <= 0:
        raise ValueError("line %d: %s has TD=%g; a zero-delay line is a wire, so "
                         "delete the card and join the nodes"
                         % (lineno, name, td))
    return {"type": "T", "name": name, "lineno": lineno, "nodes": nodes,
            "z0": z0, "td": td,
            "delay_from": ("F=%g NL=%g" % (params["f"], params.get("nl", 1.0))
                           if "f" in params else "TD=%g" % td)}


_LUMP_HINT = (
    " Put lump=<sections> in the .model instead of on the card: an unknown token "
    "on an O/U card is FATAL to ngspice (measured: \"Error on line 4 or its "
    "substitute\"), while an unknown .model parameter is only a warning, so the "
    "same deck still runs when it is delegated to the engine")

def _parse_lossy_line(parts, name, lineno):
    """`Oname n1 n2 n3 n4 model [lump=N]` - a lossy line (LTRA model).

    Four nodes, positionally: port 1 is (n1, n2) and port 2 is (n3, n4), the same
    order the T card uses. The per-length r/l/g/c and the length come from the
    .model, which is why a bare O card cannot be solved: a line with no
    per-length parameters is a wire with extra steps.

    The two RETURN nodes (n2 and n4) must be the same node. The ladder built for
    this card puts the whole series r+jw*l in the signal conductor and treats the
    return as one ideal node - that is what a single-ended trace is drawn as. Two
    different returns would make the return a second conductor with its own
    delay, which this ladder cannot represent; saying so is better than quietly
    halving the series resistance.
    """
    if len(parts) < 6:
        raise ValueError("line %d: %s needs four nodes and a model: Oname n1 n2 n3 "
                         "n4 model [lump=N] (port 1 is n1,n2 and port 2 is n3,n4); "
                         "got %r" % (lineno, name, " ".join(parts[1:])))
    if any("=" in p for p in parts[1:5]):
        raise ValueError("line %d: %s needs four node names before any parameter: "
                         "Oname n1 n2 n3 n4 model [lump=N]; got %r"
                         % (lineno, name, " ".join(parts[1:5])))
    nodes = tuple(_node(parts[k]) for k in (1, 2, 3, 4))
    model = parts[5].strip().lower()
    if "=" in model:
        raise ValueError("line %d: %s: the model name comes before any parameter, "
                         "and %r is not a name" % (lineno, name, parts[5]))
    if len(parts) > 6:
        raise ValueError("line %d: %s has trailing %r. An O card is four nodes and "
                         "a model; the r/l/g/c, len and lump= all live in the .model "
                         "that is named there.%s"
                         % (lineno, name, " ".join(parts[6:]), _LUMP_HINT))
    if all(n is None for n in nodes):
        raise ValueError("line %d: %s has every terminal on ground, so the card "
                         "constrains nothing" % (lineno, name))
    if nodes[0] == nodes[2] and nodes[1] == nodes[3]:
        raise ValueError("line %d: %s connects a port to itself (n1==n3 and "
                         "n2==n4): the line would be its own load" % (lineno, name))
    for k, (a, c) in enumerate(((nodes[0], nodes[1]), (nodes[2], nodes[3])), 1):
        if a == c:
            raise ValueError("line %d: %s has port %d shorted (both of its terminals "
                             "are node %r), so that port carries no voltage"
                             % (lineno, name, k, a))
    if nodes[1] != nodes[3]:
        raise ValueError("line %d: %s has two different return nodes (%r and %r). "
                         "The lumped ladder here puts the whole series r+jw*l in the "
                         "signal conductor and treats the return as ONE ideal node, "
                         "which is how a single-ended trace is drawn; two returns "
                         "would make the return a second conductor with its own "
                         "delay. Tie them to the same node, or use engine='ngspice' "
                         "for the distributed model"
                         % (lineno, name, nodes[1], nodes[3]))
    return {"type": "O", "name": name, "lineno": lineno, "nodes": nodes,
            "model": model}

def _parse_urc(parts, name, lineno):
    """`Uname n1 n2 n3 model [lump=N]` - a uniform distributed RC line.

    Three nodes: n1 is the input, n2 the output, and n3 the node the distributed
    capacitance returns to (SPICE's URC order; normally ground). r and c are per
    unit length, so the card needs a model with them and a length.
    """
    if len(parts) < 5:
        raise ValueError("line %d: %s needs three nodes and a model: Uname n1 n2 n3 "
                         "model [lump=N] (n3 is the node the line's capacitance "
                         "returns to); got %r" % (lineno, name, " ".join(parts[1:])))
    if any("=" in p for p in parts[1:4]):
        raise ValueError("line %d: %s needs three node names before any parameter: "
                         "Uname n1 n2 n3 model [lump=N]; got %r"
                         % (lineno, name, " ".join(parts[1:4])))
    nodes = tuple(_node(parts[k]) for k in (1, 2, 3))
    model = parts[4].strip().lower()
    if "=" in model:
        raise ValueError("line %d: %s: the model name comes before any parameter, "
                         "and %r is not a name" % (lineno, name, parts[4]))
    if len(parts) > 5:
        raise ValueError("line %d: %s has trailing %r. A U card is three nodes and a "
                         "model; r, c, len and lump= all live in the .model that is "
                         "named there.%s" % (lineno, name, " ".join(parts[5:]),
                                             _LUMP_HINT))
    if nodes[0] == nodes[1]:
        raise ValueError("line %d: %s has both signal terminals on node %r, so there "
                         "is nothing for the line to drive"
                         % (lineno, name, nodes[0]))
    return {"type": "U", "name": name, "lineno": lineno, "nodes": nodes,
            "model": model}

# XSPICE `A` cards take TWO nodes, not three: `A1 in out model`. That is not a
# simplification, it is measured on ngspice-47 - the three-node form
# (`A1 1 0 10 g1`) is rejected by the engine as an "incomplete or empty netlist",
# and the two-node form drives v(out) = 2*v(in) for gain(gain=2).
#
# A block with several inputs writes them as an ARRAY CONNECTION:
#
#   A1 [1 2] 10 s1
#   .model s1 summer(in_gain=[2 3] out_gain=1 out_offset=0)
#
# The `%v(1) %v(2)` spelling is NOT accepted by the engine for that (measured:
# "incomplete or empty netlist"), so it is not accepted here either.
#
# Four code models are algebraic - no state, no sample clock - so they are solved
# here instead of delegated. Every number below was measured on the bundled
# ngspice-47 first, then written down:
#
#   gain   (1 input) :  out = gain * (in + in_offset)
#   limit  (1 input) :  the same, then clamped to
#                       [out_lower_limit, out_upper_limit]
#   summer (n inputs):  out = out_offset + out_gain * SUM_i  in_gain_i*(v_i + in_offset_i)
#   mult   (n inputs):  out = out_offset + out_gain * PROD_i in_gain_i*(v_i + in_offset_i)
#
# The multi-input models take `in_gain`/`in_offset` VECTORS, not the scalar
# `gain`/`in_offset` of gain()/limit(): `summer(gain=[2 3])` makes the engine
# answer "MIF: unrecognized parameter (gain) - ignored" and fall back to
# in_gain=1 on every port (3 V + 4 V settles at 7.0 V, not 18.0 V). A vector must
# also match the port count exactly - a 2-port block with in_gain=[2 3 4] is
# refused by the engine outright - so the length is checked here instead of
# being padded.
#
# Measurements behind the formulas:
#   gain(gain=2 in_offset=1) at 2 V                -> 6.0 V   (offset INSIDE gain)
#   summer() at 3 V, 4 V                           -> 7.0 V   (in_gain defaults to 1)
#   summer(in_gain=[2 3])                          -> 18.0 V
#   summer(in_gain=[2 3] in_offset=[1 1])          -> 23.0 V  (2*4 + 3*5)
#   summer(... out_gain=2 out_offset=7)            -> 53.0 V  (2*23 + 7)
#   mult() at 3 V, 4 V                             -> 12.0 V
#   mult() at 3 V, 4 V, 5 V                        -> 60.0 V  (n inputs, not two)
#   mult(in_offset=[0 0 2]) at 3 V, 4 V, 5 V       -> 84.0 V  (3*4*(5+2))
#   mult(in_gain=[2 3] in_offset=[1 1] out_gain=2 out_offset=7) -> 247.0 V
#   summer/mult with ONE input node                -> engine error, so >= 2 ports
#   summer(in_gain=2) (scalar)                     -> "Array parameter expected"
#
# limit_range (XSPICE's polynomial corner smoothing) is refused by name rather
# than approximated: with limit_range=0.5 an input of 0.9 V settles at 0.82 V
# instead of 0.9 V (measured), and a hard clamp would report the wrong number
# exactly where someone cared about the corner. `fraction` is refused the same
# way. Both bounds must be given: the engine itself rejects `limit(gain=1
# out_lower_limit=-1)` as incomplete, so there is no default to inherit.
_XSPICE_KEYS = {
    # kind -> (minimum input ports, every .model parameter it may carry)
    "GAIN": (1, ("GAIN", "IN_OFFSET")),
    "LIMIT": (1, ("GAIN", "IN_OFFSET", "OUT_LOWER_LIMIT", "OUT_UPPER_LIMIT")),
    "SUMMER": (2, ("IN_GAIN", "IN_OFFSET", "OUT_GAIN", "OUT_OFFSET")),
    "MULT": (2, ("IN_GAIN", "IN_OFFSET", "OUT_GAIN", "OUT_OFFSET")),
}


def _parse_xspice(parts, name, lineno):
    toks = list(parts[1:])
    if not toks:
        raise ValueError("line %d: %s needs an input node, an output node and the "
                         "name of a code model: Aname in out model" % (lineno, name))
    if toks[0].startswith("["):
        # array connection: A1 [1 2 3] out model
        ins, i, closed = [], 0, False
        while True:
            tok = toks[i]
            if i == 0:
                tok = tok[1:]
            if tok.endswith("]"):
                tok = tok[:-1]
                closed = True
            for p in tok.split():
                if "=" in p or p.startswith("%"):
                    raise ValueError(
                        "line %d: %s: %r is not a node. The engine does not accept "
                        "%%v() references in an array connection (measured: "
                        "incomplete or empty netlist); write A1 [1 2] out model"
                        % (lineno, name, p))
                ins.append(_node(p))
            if closed:
                i += 1
                break
            i += 1
            if i >= len(toks):
                raise ValueError("line %d: %s: the array connection is never closed. "
                                 "All input nodes go inside ONE pair of square "
                                 "brackets: A1 [1 2] out model" % (lineno, name))
        rest = toks[i:]
    else:
        if "=" in toks[0] or toks[0].startswith("%"):
            raise ValueError("line %d: %s: %r is not a node" % (lineno, name, toks[0]))
        ins = [_node(toks[0])]
        rest = toks[1:]
    if not ins:
        raise ValueError("line %d: %s: the array connection names no input node"
                         % (lineno, name))
    if len(rest) != 2:
        raise ValueError("line %d: %s has %r after its input node(s). An A card is "
                         "exactly: input node(s), output node, model - every "
                         "parameter of the block lives in the .model it names"
                         % (lineno, name, " ".join(rest) or "nothing"))
    out_node, model = rest[0], rest[1]
    if "=" in out_node or out_node.startswith("%"):
        raise ValueError("line %d: %s: %r is not a node" % (lineno, name, out_node))
    if out_node in ins:
        raise ValueError("line %d: %s has the output on the same node %r as an "
                         "input, so the block drives its own input"
                         % (lineno, name, out_node))
    return {"type": "A", "name": name, "lineno": lineno,
            "nodes": (ins[0], _node(out_node)),
            "inputs": tuple(ins), "model": model}


def _xspice_model(spec, el):
    """Resolve an A card's .model into numbers, or refuse it by name."""
    where = "line %d: %s" % (el.get("lineno") or 0, el["name"])
    name = el["model"]
    models = spec["models"]
    if name not in models:
        raise ValueError("%s references .model %r which is not defined"
                         % (where, name))
    m = models[name]
    kind = (m.get("type") or "").upper()
    params = m["params"]
    if kind not in _XSPICE_KEYS:
        raise ValueError("%s names an XSPICE code model of type %r, and only gain, "
                         "limit, summer and mult are solved here. Those four are "
                         "pure algebra (no state, no sample clock); every other "
                         "analogue code model carries state or a sample clock "
                         "(integrator, ddt, s_xfer, ...) or divides and accumulates "
                         "(divider, ...), which is a different solver. Use "
                         "engine=\"ngspice\" for those"
                         % (where, kind or "untyped"))
    min_ports, allowed = _XSPICE_KEYS[kind]
    for k in params:
        if k not in allowed:
            raise ValueError("%s: %s= in .model %r is refused by name, not ignored. "
                             "limit_range= changes the corner (measured: 0.9 V in "
                             "with limit_range=0.5 settles at 0.82 V, not 0.9 V) and "
                             "fraction= changes where the polynomial starts; "
                             "approximating either would report a number right where "
                             "the caller is asking about that corner. A %s block "
                             "takes %s and nothing else"
                             % (where, k.lower(), name, kind,
                                ", ".join(a.lower() + "=" for a in allowed)))
    inputs = el.get("inputs") or (el["nodes"][0],)
    n = len(inputs)
    if n < min_ports:
        raise ValueError("%s: %s has %d input node(s) and needs at least %d. The "
                         "engine itself refuses a single-input summer/mult (measured: "
                         "Error on line 4), so the minimum is not extra caution here"
                         % (where, name, n, min_ports))
    defaulted = []
    if kind in ("GAIN", "LIMIT"):
        gain = params.get("GAIN", 1.0)
        if isinstance(gain, list):
            raise ValueError("%s: gain= in .model %r is a single number; the vector "
                             "parameter of a multi-input block is in_gain="
                             % (where, name))
        if "GAIN" not in params:
            defaulted.append("gain=1")
        off = params.get("IN_OFFSET", 0.0)
        if isinstance(off, list):
            raise ValueError("%s: in_offset= in .model %r is a single number: a "
                             "%s block has one port" % (where, name, kind))
        if "IN_OFFSET" not in params:
            defaulted.append("in_offset=0")
        in_gain, in_offset = [float(gain)], [float(off)]
        out_gain, out_off = 1.0, 0.0
    else:
        in_gain, in_offset, out_gain, out_off = [], [], 1.0, 0.0
        for key, dflt in (("IN_GAIN", 1.0), ("IN_OFFSET", 0.0)):
            vec = params.get(key)
            if vec is None:
                defaulted.append("%s=1 (x%d)" % (key.lower(), n)
                                 if key == "IN_GAIN" else
                                 "%s=0 (x%d)" % (key.lower(), n))
                vec = [dflt] * n
            elif not isinstance(vec, list):
                raise ValueError("%s: %s= in .model %r is an ARRAY parameter "
                                 "(measured: the engine answers \"Array parameter "
                                 "expected - No array delimiter found\" for a scalar). "
                                 "Write %s=[%s]"
                                 % (where, key.lower(), name, key.lower(),
                                    " ".join(repr(float(dflt))[:3] for _ in range(n))))
            elif len(vec) != n:
                raise ValueError("%s: %s= in .model %r has %d value(s) for %d input "
                                 "node(s). The engine refuses that mismatch outright "
                                 "(measured: Error on line 4), so the lengths are "
                                 "checked here rather than padded"
                                 % (where, key.lower(), name, len(vec), n))
            [in_gain, in_offset][0 if key == "IN_GAIN" else 1].extend(
                float(x) for x in vec)
        for key, dflt in (("OUT_GAIN", 1.0), ("OUT_OFFSET", 0.0)):
            val = params.get(key, dflt)
            if isinstance(val, list):
                raise ValueError("%s: %s= in .model %r is a single number"
                                 % (where, key.lower(), name))
            if key not in params:
                defaulted.append("%s=%g" % (key.lower(), dflt))
            if key == "OUT_GAIN":
                out_gain = float(val)
            else:
                out_off = float(val)
    lo = hi = None
    if kind == "LIMIT":
        for key in ("OUT_LOWER_LIMIT", "OUT_UPPER_LIMIT"):
            if key not in params:
                raise ValueError("%s: .model %r is a limit block without %s. Both "
                                 "bounds are REQUIRED here because the engine itself "
                                 "refuses limit(gain=1 out_lower_limit=-1) as an "
                                 "incomplete netlist - there is no default to "
                                 "inherit, and a made-up one would silently pick "
                                 "which side of the clip the answer falls on"
                                 % (where, name, key.lower() + "="))
        lo = float(params["OUT_LOWER_LIMIT"])
        hi = float(params["OUT_UPPER_LIMIT"])
        if lo > hi:
            raise ValueError("%s: .model %r has out_lower_limit=%g above "
                             "out_upper_limit=%g" % (where, name, lo, hi))
    el["kind"] = kind
    el["in_gain"] = in_gain
    el["in_offset"] = in_offset
    el["out_gain"] = out_gain
    el["out_offset"] = out_off
    el["lo"] = lo
    el["hi"] = hi
    el["defaulted"] = defaulted


def _xspice_eval(el, vins):
    """(f(vins), [df/dv_i]) at the CURRENT iterate.

    The clamp is examined at the current iterate, which is what makes a clipped
    block Newton-convergent: inside the band the slope is the gain, outside it the
    output is a constant and every slope is 0. A mult block is bilinear, so its
    rows are the true partials and Newton reduces to a couple of steps.
    """
    shifted = [el["in_gain"][i] * (vins[i] + el["in_offset"][i])
               for i in range(len(vins))]
    if el["kind"] == "MULT":
        raw = el["out_gain"]
        for s_ in shifted:
            raw *= s_
        raw += el["out_offset"]
        slopes = []
        for i in range(len(vins)):
            p = el["out_gain"] * el["in_gain"][i]
            for j, s_ in enumerate(shifted):
                if j != i:
                    p *= s_
            slopes.append(p)
        return raw, slopes
    raw = el["out_offset"] + el["out_gain"] * sum(shifted)
    if el["lo"] is not None:
        if raw < el["lo"]:
            return el["lo"], [0.0] * len(vins)
        if raw > el["hi"]:
            return el["hi"], [0.0] * len(vins)
    return raw, [el["out_gain"] * g for g in el["in_gain"]]


def _xspice_ac_slopes(el, vins):
    """Small-signal slopes of an A block at the bias point (the `.ac` linearisation).

    This is `_xspice_eval` plus one convention that had to be MEASURED rather than
    guessed: a `limit` block whose bias sits EXACTLY on a bound reports **half**
    the inside slope in ngspice-47 (0.5 V in, gain=2, hi=1 => 1.0; gain=4 at the
    same tie => 2.0; one ulp either side gives the full slope or zero). Reproduced
    here so a cross-check against the engine is exact instead of differing on a
    measure-zero tie. A mult block needs no special case: its partials are already
    the bias-point values of the other inputs.
    """
    f0, slopes = _xspice_eval(el, vins)
    if el["kind"] == "LIMIT" and el["lo"] is not None:
        if f0 == el["lo"] or f0 == el["hi"]:
            return f0, [s * 0.5 for s in slopes]
    return f0, slopes

def _parse_element(spec, line, lineno):
    parts = line.split()
    name = parts[0]
    letter = name[0].upper()
    if letter in NOT_IMPLEMENTED_CARDS:
        raise ValueError("line %d: %s (%s) is not implemented here: %s"
                         % (lineno, name, letter, NOT_IMPLEMENTED_CARDS[letter]))
    if letter == "A":
        return _parse_xspice(parts, name, lineno)
    low = [p.lower() for p in parts]

    def val(idx, what):
        if len(parts) <= idx:
            raise ValueError("line %d: %s needs a %s" % (lineno, name, what))
        return parts[idx]

    def _tail(allow_ic):
        """Refuse anything after the value that this subset does not read.

        `R1 1 0 1k mc(1)` is the shape that hurt: the value parses, the trailing
        token is dropped, and the deck answers at 1 k as if the Monte-Carlo call
        had never been written - a confident answer to a different question. A
        SPICE R/C/L card carries exactly one value plus (C, L) an optional ic=,
        so every other trailing token is named instead of ignored.
        """
        for p in low[4:]:
            if allow_ic and p.startswith("ic="):
                continue
            raise ValueError(
                "line %d: %s has trailing %r after its value. %s %s card carries one "
                "value%s; this subset reads nothing else there, so the token would "
                "be silently dropped and the answer computed as if it had never "
                "been written%s"
                % (lineno, name, p, "An" if letter in "AEFILMNORSX" else "A",
                   letter, " and an optional ic=" if allow_ic
                   else "", " (a mc(...)/agauss(...)/unif(...) is a Monte-Carlo "
                             "call: it wants a distribution and a run count the "
                             "deck does not carry)" if "(" in p else ""))

    if letter == "R":
        _tail(False)
        return {"type": "R", "name": name, "nodes": (_node(parts[1]), _node(parts[2])),
                "value": _value_or_expr(val(3, "resistance"), lineno)}
    if letter == "C":
        el = {"type": "C", "name": name, "nodes": (_node(parts[1]), _node(parts[2])),
              "value": _value_or_expr(val(3, "capacitance"), lineno), "ic": None}
        _tail(True)
        for p in low[4:]:
            if p.startswith("ic="):
                el["ic"] = _value(p.split("=", 1)[1])
        return el
    if letter == "L":
        el = {"type": "L", "name": name, "nodes": (_node(parts[1]), _node(parts[2])),
              "value": _value_or_expr(val(3, "inductance"), lineno), "ic": None}
        _tail(True)
        for p in low[4:]:
            if p.startswith("ic="):
                el["ic"] = _value(p.split("=", 1)[1])
        return el
    if letter in ("V", "I"):
        return _parse_source(parts, low, name, letter, lineno)
    if letter == "B":
        return _parse_b_source(parts, line, name, lineno)
    if letter == "T":
        return _parse_tline(parts, name, lineno)
    if letter == "O":
        return _parse_lossy_line(parts, name, lineno)
    if letter == "U":
        return _parse_urc(parts, name, lineno)
    if letter == "X":
        return _parse_instance(parts, name, lineno)
    if letter in ("E", "G"):
        if len(parts) > 6:
            raise ValueError("line %d: %s has trailing %r. An %s card here is "
                             "exactly five fields: n+ n- nc+ nc- gain (its value is "
                             "a constant, not a table), and anything after the gain "
                             "would be silently dropped"
                             % (lineno, name, " ".join(parts[6:]), letter))
        return {"type": letter, "name": name,
                "nodes": (_node(parts[1]), _node(parts[2])),
                "ctrl": (_node(parts[3]), _node(parts[4])),
                "value": _value_or_expr(val(5, "gain"), lineno)}
    if letter in ("F", "H"):
        # F (CCCS) and H (CCVS) are controlled by the CURRENT through another
        # element, which SPICE spells by naming that element - so the third
        # token is a NAME, not a pair of control nodes. Reading it positionally
        # (as E/G's four-node form) would turn "V1" into a node called V1 and
        # solve a deck whose control term silently reads 0 V.
        if len(parts) < 5:
            raise ValueError("line %d: %s needs two nodes, the NAME of the element "
                             "whose current controls it, and a gain: %sname n+ n- "
                             "vname gain; got %r"
                             % (lineno, name, letter, " ".join(parts[1:])))
        if len(parts) > 5:
            raise ValueError("line %d: %s has trailing %r. A %s card is exactly "
                             "four fields: n+ n- vname gain"
                             % (lineno, name, " ".join(parts[5:]), letter))
        ctrl = parts[3]
        if "=" in ctrl or _looks_numeric(ctrl):
            raise ValueError("line %d: %s: the controlling element comes before the "
                             "gain and %r is not an element name. The controlling "
                             "element is the one whose CURRENT is read (its + to - "
                             "branch current), so it has to be named, not measured"
                             % (lineno, name, ctrl))
        return {"type": letter, "name": name, "lineno": lineno,
                "nodes": (_node(parts[1]), _node(parts[2])),
                "ctrl_name": ctrl, "value": _value_or_expr(parts[4], lineno)}
    if letter == "K":
        # K is not a device with terminals: it is a statement about two existing
        # inductors, so it has no nodes at all and owns no row of its own.
        if len(parts) != 4:
            raise ValueError("line %d: %s: a K card is exactly `Kname L1 L2 k` "
                             "(the two inductors it couples and the coefficient), "
                             "got %r" % (lineno, name, " ".join(parts[1:])))
        return {"type": "K", "name": name, "lineno": lineno, "nodes": (),
                "l1": parts[1], "l2": parts[2], "value": _value_or_expr(parts[3], lineno)}
    if letter in ("S", "W"):
        # S: Sname n+ n- nc+ nc- model   - the control is a VOLTAGE between two
        #                                 nodes, so the card has four terminals
        # W: Wname n+ n- vname model     - the control is the CURRENT through a
        #                                 named element, spelled as a name
        # Neither owns a branch unknown: a switch is a conductance that changes
        # between two values, not a source.
        need = 6 if letter == "S" else 5
        shape = ("Sname n+ n- nc+ nc- model [ON|OFF]" if letter == "S"
                 else "Wname n+ n- vname model [ON|OFF]")
        if len(parts) < need:
            raise ValueError("line %d: %s: a %s card is `%s`; got %r"
                             % (lineno, name, letter, shape,
                                " ".join(parts[1:])))
        el = {"type": letter, "name": name, "lineno": lineno,
              "nodes": (_node(parts[1]), _node(parts[2]))}
        if letter == "S":
            el["ctrl"] = (_node(parts[3]), _node(parts[4]))
            el["model"] = parts[5].lower()
            tail = low[6:]
        else:
            ctrl = parts[3]
            if "=" in ctrl or _looks_numeric(ctrl):
                raise ValueError("line %d: %s: the controlling element comes "
                                 "before the model name and %r is not an element "
                                 "name. A W card is switched by the CURRENT "
                                 "through another card, so that card is named"
                                 % (lineno, name, ctrl))
            el["ctrl_name"] = ctrl
            el["model"] = parts[4].lower()
            tail = low[5:]
        el["_init"] = None
        for tok in tail:
            if tok == "on":
                el["_init"] = True
            elif tok == "off":
                el["_init"] = False
            else:
                raise ValueError("line %d: %s: %r is not a field of a %s card "
                                 "(only an initial state, ON or OFF, may follow "
                                 "the model name)" % (lineno, name, tok, letter))
        return el
    if letter == "J":
        # J: Jname nd ng ns model [AREA=<x>] [OFF]
        # THREE terminals, not four: a JFET's gate is a junction to the channel,
        # and level-1 SPICE has no separate bulk terminal. It is not a mistake to
        # read the third node as the drain and the fourth as a model name - the
        # card has no fourth node at all.
        if len(parts) < 5:
            raise ValueError("line %d: %s: a J card is `Jname nd ng ns model "
                             "[AREA=<x>]`; got %r"
                             % (lineno, name, " ".join(parts[1:])))
        if "=" in parts[4]:
            raise ValueError("line %d: %s: the model name comes before any "
                             "parameter and %r is not a model name; a J card has "
                             "three nodes (drain, gate, source), then the model"
                             % (lineno, name, parts[4]))
        el = {"type": "J", "name": name, "lineno": lineno,
              "nodes": (_node(parts[1]), _node(parts[2]), _node(parts[3])),
              "model": parts[4].lower(), "_area": None, "_off": False}
        for tok in parts[5:]:
            if "=" not in tok:
                if tok.lower() == "off":
                    # an initial guess, not a state: the operating point is
                    # solved, so OFF cannot change a converged answer (measured:
                    # ngspice returns the same current with and without it)
                    el["_off"] = True
                    continue
                raise ValueError("line %d: %s: %r is not a field of a J card "
                                 "(only AREA=<x> and the OFF flag may follow the "
                                 "model name)" % (lineno, name, tok))
            key, raw = tok.split("=", 1)
            if key.strip().lower() != "area":
                raise ValueError("line %d: %s: %s= is not a J card field here "
                                 "(implemented: AREA=, plus the OFF flag). W= and "
                                 "L= are refused by ngspice itself on a J card - a "
                                 "junction FET's geometry scales through AREA"
                                 % (lineno, name, key.strip()))
            area = _value(raw)
            if area <= 0.0:
                raise ValueError("line %d: %s has AREA=%g; an area factor scales "
                                 "the transconductance and cannot be zero or "
                                 "negative" % (lineno, name, area))
            el["_area"] = area
        return el
    if letter == "D":
        model = val(3, "model name").lower()
        return {"type": "D", "name": name, "lineno": lineno,
                "nodes": (_node(parts[1]), _node(parts[2])), "model": model}
    if letter == "Q":
        model = val(4, "model name").lower()
        return {"type": "Q", "name": name, "lineno": lineno,
                "nodes": (_node(parts[1]), _node(parts[2]), _node(parts[3])),
                "model": model}
    if letter == "M":
        model = val(5, "model name").lower()
        el = {"type": "M", "name": name, "lineno": lineno,
              "nodes": (_node(parts[1]), _node(parts[2]), _node(parts[3]),
                        _node(parts[4])), "model": model}
        geometry = {}
        ignored = []
        for p in low[6:]:
            if "=" not in p:
                raise ValueError("line %d: %s: %r is not a name=value parameter"
                                 % (lineno, name, p))
            key, raw = p.split("=", 1)
            if key in ("w", "l", "m"):
                geometry[key] = _value_or_expr(raw, lineno)
            else:
                ignored.append(key)
        if ("w" in geometry) != ("l" in geometry):
            raise ValueError("line %d: %s gives only %s=, so the channel's width "
                             "over length is undefined; give both W= and L="
                             % (lineno, name, "W" if "w" in geometry else "L"))
        if geometry:
            w = geometry["w"]
            l_ = geometry["l"]
            if w <= 0 or l_ <= 0:
                raise ValueError("line %d: %s has W=%g L=%g; a channel dimension "
                                 "cannot be zero or negative"
                                 % (lineno, name, w, l_))
            if "m" in geometry and geometry["m"] <= 0:
                raise ValueError("line %d: %s has M=%g; a device multiplicity "
                                 "cannot be zero or negative"
                                 % (lineno, name, geometry["m"]))
            el["wl"] = (w / l_) * geometry.get("m", 1.0)
            el["geometry"] = {"w_m": w, "l_m": l_,
                              "m": geometry.get("m", 1.0), "w_over_l": w / l_}
        if ignored:
            # Junction areas only feed junction capacitance and leakage scaling,
            # neither of which this subset models. Saying so is the difference
            # between a documented limit and a silent "we used what we liked".
            el["_ignored_params"] = ignored
        return el
    raise ValueError("line %d: unknown element type %r (element %s)"
                     % (lineno, letter, name))


def _need_model(spec, model, want, lineno, name):
    if model not in spec["models"]:
        raise ValueError("line %d: %s references .model %r which is not defined"
                         % (lineno, name, model))


def _parse_source(parts, low, name, letter, lineno):
    nodes = (_node(parts[1]), _node(parts[2]))
    dc = 0.0
    wave = None
    ac = None
    i = 3
    while i < len(parts):
        p = low[i]
        if p.startswith("dc="):
            dc = _value_or_expr(p.split("=", 1)[1], lineno)
            i += 1
        elif p == "dc":
            if i + 1 >= len(parts):
                raise ValueError("line %d: %s: 'dc' needs a value" % (lineno, name))
            dc = _value_or_expr(parts[i + 1], lineno)
            i += 2
        elif p == "ac":
            mag = (_value_or_expr(parts[i + 1], lineno) if i + 1 < len(parts)
                   else 1.0)
            phase = (_value_or_expr(parts[i + 2], lineno) if i + 2 < len(parts)
                     else 0.0)
            ac = [mag, phase]
            i += 3
        elif p.split("(")[0] in ("pulse", "sin", "pwl"):
            # the parenthesis may be glued to the keyword ("pulse(0"), so the
            # function has to be recognised on the token prefix and the body
            # collected until the brackets balance
            kind = p.split("(")[0]
            depth = 0
            buf = []
            j = i
            while j < len(parts):
                tok = parts[j]
                depth += tok.count("(") - tok.count(")")
                buf.append(tok)
                j += 1
                if depth <= 0 and ")" in tok:
                    break
            body = " ".join(buf)
            body = body[body.index("(") + 1:body.rindex(")")] if "(" in body else ""
            nums = [_value_or_expr(x, lineno)
                    for x in body.replace(",", " ").split()
                    if x.strip() not in ("", ")")]
            wave = {"kind": kind, "args": nums}
            i = j
        else:
            # a bare number is the DC value
            dc = _value_or_expr(parts[i], lineno)
            i += 1
    return {"type": letter, "name": name, "nodes": nodes, "dc": dc,
            "wave": wave, "ac": ac}



# ------------------------------------------------------------------ B sources
# A behavioural source is the one card where the netlist carries a *program*. It
# is compiled to a small tree when the card is read - so a syntax error is a
# syntax error, reported once, with the line number - and evaluated while Newton
# iterates. Everything the tree can say is listed in _B_FUNCS; nothing else is
# guessed at.

_B_FUNCS = {
    "abs": (abs, 1, 1), "sqrt": (math.sqrt, 1, 1), "exp": (math.exp, 1, 1),
    "ln": (math.log, 1, 1), "log": (math.log, 1, 1), "log10": (math.log10, 1, 1),
    "sin": (math.sin, 1, 1), "cos": (math.cos, 1, 1), "tan": (math.tan, 1, 1),
    "asin": (math.asin, 1, 1), "acos": (math.acos, 1, 1), "atan": (math.atan, 1, 1),
    "atan2": (math.atan2, 2, 2),
    "sinh": (math.sinh, 1, 1), "cosh": (math.cosh, 1, 1), "tanh": (math.tanh, 1, 1),
    "floor": (math.floor, 1, 1), "ceil": (math.ceil, 1, 1),
    "int": (math.trunc, 1, 1), "pow": (pow, 2, 2),
    "min": (min, 1, None), "max": (max, 1, None),
    "uramp": (lambda a: a if a > 0.0 else 0.0, 1, 1),
    "u": (lambda a: 1.0 if a > 0.0 else 0.0, 1, 1),
    "sgn": (lambda a: 1.0 if a > 0.0 else (-1.0 if a < 0.0 else 0.0), 1, 1),
}

# A caller who writes one of these should read why it is not there, not "unknown
# function": the two are different kinds of answer.
_B_REFUSED_FUNCS = {
    "laplace": "a Laplace transfer function is a function of the complex frequency, "
               "and this module only linearises B sources about the DC point",
    "white": "a noise source is a stochastic quantity; nothing here does noise",
    "limit": "the pnjlim-style limiter is not exposed; write if(cond, a, b) instead",
}

# "1k*2" is a number expression here (there is no second slot to be ambiguous
# with), so engineering suffixes are translated rather than refused.
_B_NUM_SUFFIX = re.compile(
    r"(?<![A-Za-z0-9_.])(\d+(?:\.\d*)?|\.\d+)(meg|t|g|k|m|u|n|p|f)(?![A-Za-z0-9_])")

# the V= / I= marker after the two nodes; the expression may follow with or
# without spaces around the "=" ("V=5", "V = 5")
_B_MARKER = re.compile(r"(?<![A-Za-z0-9_.])([VvIi])\s*=")

# V(n) / V(a,b) / I(...) - the argument is taken from the TEXT, because V(1) has
# a number as its argument, V(n-1) is a subtraction as far as Python is
# concerned, and V(in) has a Python keyword as its argument. All three are node
# names here, so the call is replaced by a placeholder before parsing.
_B_NODE_CALL = re.compile(r"(?<![A-Za-z0-9_.])([VvIi])\s*\(\s*([^()]*?)\s*\)")

def _b_split_top(text, want, start=0):
    """Index of `want` at bracket depth 0, or None."""
    depth = 0
    for i in range(start, len(text)):
        ch = text[i]
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif ch == want and depth == 0:
            return i
    return None

def _b_ternary(text, where):
    """``cond ? a : b`` -> ``(a if (cond) else b)``, innermost-first by recursion.

    The same shape ngspice's B sources use (`? :`). Python has the ternary with
    the condition in the middle, so this is a reordering and not a new operator -
    but it is the one spelling that let the answer be compared with the reference
    engine on a real conditional deck.
    """
    q = _b_split_top(text, "?")
    if q is None:
        return text
    colon = _b_split_top(text, ":", q + 1)
    if colon is None:
        raise ValueError("%s: %r has a '?' with no matching ':'" % (where, text))
    cond = _b_ternary(text[:q], where)
    yes = _b_ternary(text[q + 1:colon], where)
    no = _b_ternary(text[colon + 1:], where)
    return "((%s) if (%s) else (%s))" % (yes, cond, no)

def _b_translate(text, where):
    """SPICE spelling -> something the Python AST parser can read.

    Returns (python_source, node_pairs). Only spelling changes: && || ! ^ <> and
    if(...) are renames, 1k/2.2u become numbers, `{...}` becomes `(...)`, `? :`
    is reordered, and every V(...) becomes a placeholder bound to a node pair.
    """
    refs = []

    def grab(m):
        kind = m.group(1).lower()
        args = [a.strip() for a in m.group(2).split(",") if a.strip() != ""]
        if kind == "i":
            raise ValueError("%s: %r: I(...) is not implemented here (the current "
                             "through another element is not a variable of this "
                             "solver; write the expression in node voltages)"
                             % (where, text))
        if len(args) == 1:
            pair = (args[0].lower(), None)
        elif len(args) == 2:
            pair = (args[0].lower(), args[1].lower())
        else:
            raise ValueError("%s: %r: V() takes one or two nodes, got %d"
                             % (where, text, len(args)))
        refs.append(pair)
        return "_vbnode%d_" % (len(refs) - 1)

    t = _B_NODE_CALL.sub(grab, text)
    t = t.replace("&&", " and ").replace("||", " or ")
    t = t.replace("<>", " != ")
    t = t.replace("^", "**")
    t = re.sub(r"!(?!=)", " not ", t)
    # if(...) first: a user-written call is a rename. Only then is ``? :``
    # expanded, because its output contains Python's own ``if`` keyword and
    # would otherwise be renamed to ``_if_`` and rejected by the parser.
    t = re.sub(r"\bif(?=\s*\()", "_if_", t)
    t = _b_ternary(t, where)

    def num(m):
        return "(%s*%r)" % (m.group(1), SUFFIX[m.group(2)])
    t = _B_NUM_SUFFIX.sub(num, t)
    # "{k}*V(2)" is how a parameter is written in some decks; the braces only
    # group here (a parameter is a Name either way), so dropping them is a
    # translation and not a reinterpretation
    t = t.replace("{", "(").replace("}", ")")
    return t, refs

def _b_walk(node, text, refs, where):
    """One AST node -> one tuple of the expression tree."""
    if isinstance(node, ast.Constant):
        v = node.value
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            raise ValueError("%s: only numbers may appear in %r" % (where, text))
        return ("num", float(v))
    if isinstance(node, ast.Name):
        key = node.id.lower()
        if key == "time":
            return ("time",)
        if key == "pi":
            return ("num", math.pi)
        if key.startswith("_vbnode"):
            pair = refs[int(key[len("_vbnode"):-1])]
            return ("v", _node(pair[0]), _node(pair[1]))
        return ("param", key)
    if isinstance(node, ast.UnaryOp):
        if isinstance(node.op, ast.USub):
            return ("un", "-", _b_walk(node.operand, text, refs, where))
        if isinstance(node.op, ast.UAdd):
            return _b_walk(node.operand, text, refs, where)
        if isinstance(node.op, ast.Not):
            return ("not", _b_walk(node.operand, text, refs, where))
        raise ValueError("%s: %r: %s is not implemented here"
                         % (where, text, type(node.op).__name__))
    if isinstance(node, ast.BoolOp):
        seq = [_b_walk(v, text, refs, where) for v in node.values]
        tag = "and" if isinstance(node.op, ast.And) else "or"
        acc = seq[0]
        for nxt in seq[1:]:
            acc = (tag, acc, nxt)
        return acc
    if isinstance(node, ast.BinOp):
        tag = {ast.Add: "+", ast.Sub: "-", ast.Mult: "*", ast.Div: "/",
               ast.Pow: "**"}.get(type(node.op))
        if tag is None:
            raise ValueError("%s: %r uses %s; only + - * / ** are implemented"
                             % (where, text, type(node.op).__name__))
        return ("bin", tag, _b_walk(node.left, text, refs, where),
                _b_walk(node.right, text, refs, where))
    if isinstance(node, ast.Compare):
        left = _b_walk(node.left, text, refs, where)
        tests = []
        for op, comp in zip(node.ops, node.comparators):
            tag = {ast.Lt: "<", ast.LtE: "<=", ast.Gt: ">", ast.GtE: ">=",
                   ast.Eq: "==", ast.NotEq: "!="}.get(type(op))
            if tag is None:
                raise ValueError("%s: %r uses %s, which is not a comparison here"
                                 % (where, text, type(op).__name__))
            right = _b_walk(comp, text, refs, where)
            tests.append(("cmp", tag, left, right))
            left = right
        acc = tests[0]
        for nxt in tests[1:]:
            acc = ("and", acc, nxt)
        return acc
    if isinstance(node, ast.IfExp):
        return ("if", _b_walk(node.test, text, refs, where),
                _b_walk(node.body, text, refs, where),
                _b_walk(node.orelse, text, refs, where))
    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name):
            raise ValueError("%s: %r calls something that is not a function name"
                             % (where, text))
        fname = node.func.id.lower()
        if node.keywords:
            raise ValueError("%s: %r: keyword arguments are not implemented"
                             % (where, text))
        if fname == "_if_":
            if len(node.args) != 3:
                raise ValueError("%s: %r: if() takes three arguments "
                                 "(condition, then, else), got %d"
                                 % (where, text, len(node.args)))
            return ("if", _b_walk(node.args[0], text, refs, where),
                    _b_walk(node.args[1], text, refs, where),
                    _b_walk(node.args[2], text, refs, where))
        if fname in _B_REFUSED_FUNCS:
            raise ValueError("%s: %r: %s() is not implemented here: %s"
                             % (where, text, fname, _B_REFUSED_FUNCS[fname]))
        if fname not in _B_FUNCS:
            raise ValueError("%s: %r: function %r is not implemented here "
                             "(implemented: %s)"
                             % (where, text, node.func.id,
                                ", ".join(sorted(list(_B_FUNCS) + ["if", "V"]))))
        lo, hi = _B_FUNCS[fname][1], _B_FUNCS[fname][2]
        if len(node.args) < lo or (hi is not None and len(node.args) > hi):
            raise ValueError("%s: %r: %s() takes %s argument(s), got %d"
                             % (where, text, fname,
                                ("%d" % lo) if lo == hi else
                                ("at least %d" % lo if hi is None else
                                 "%d..%d" % (lo, hi)), len(node.args)))
        return ("call", fname,
                [_b_walk(a, text, refs, where) for a in node.args])
    raise ValueError("%s: %r uses %s, which is not part of an expression here"
                     % (where, text, type(node).__name__))

def _compile_b_expr(text, where):
    """Expression text -> tree. Raises ValueError with the line and the name."""
    src, refs = _b_translate(text, where)
    try:
        tree = ast.parse(src, mode="eval")
    except SyntaxError as exc:
        raise ValueError("%s: cannot read the expression %r (%s)"
                         % (where, text, exc.msg or "syntax error"))
    return _b_walk(tree.body, text, refs, where)

def _b_nodes(ir, out=None):
    """The nodes an expression reads, in first-appearance order.

    Ground is left out: it is not a variable, so it needs no derivative column.
    """
    if out is None:
        out = []
    op = ir[0]
    if op == "v":
        for n in (ir[1], ir[2]):
            if n is not None and n not in out:
                out.append(n)
    elif op == "call":
        for a in ir[2]:
            _b_nodes(a, out)
    elif op == "un":
        _b_nodes(ir[2], out)
    elif op == "not":
        _b_nodes(ir[1], out)
    elif op in ("bin", "cmp"):
        _b_nodes(ir[2], out)
        _b_nodes(ir[3], out)
    elif op in ("and", "or"):
        _b_nodes(ir[1], out)
        _b_nodes(ir[2], out)
    elif op == "if":
        _b_nodes(ir[1], out)
        _b_nodes(ir[2], out)
        _b_nodes(ir[3], out)
    return out

def _b_remap(ir, mapped):
    """Rename the nodes of an expression, for an instance of a subcircuit."""
    op = ir[0]
    if op == "v":
        return ("v", mapped(ir[1]), mapped(ir[2]))
    if op == "call":
        return ("call", ir[1], [_b_remap(a, mapped) for a in ir[2]])
    if op == "un":
        return ("un", ir[1], _b_remap(ir[2], mapped))
    if op == "not":
        return ("not", _b_remap(ir[1], mapped))
    if op in ("bin", "cmp"):
        return (op, ir[1], _b_remap(ir[2], mapped), _b_remap(ir[3], mapped))
    if op in ("and", "or"):
        return (op, _b_remap(ir[1], mapped), _b_remap(ir[2], mapped))
    if op == "if":
        return ("if", _b_remap(ir[1], mapped), _b_remap(ir[2], mapped),
                _b_remap(ir[3], mapped))
    return ir

def _b_fold(ir, env, where):
    """Replace every parameter by its number, at build time.

    A name that is not in scope is an error here - once, with the line - instead
    of on the first Newton iteration, where it would read as "did not converge".
    """
    op = ir[0]
    if op in ("num", "time", "v"):
        return ir
    if op == "param":
        key = ir[1]
        if key not in env:
            raise ValueError("%s: %r is not a parameter in scope here (%s)"
                             % (where, key,
                                ", ".join(sorted(env)) if env else "none"))
        return ("num", float(env[key]))
    if op == "call":
        return ("call", ir[1], [_b_fold(a, env, where) for a in ir[2]])
    if op == "un":
        return ("un", ir[1], _b_fold(ir[2], env, where))
    if op == "not":
        return ("not", _b_fold(ir[1], env, where))
    if op in ("bin", "cmp"):
        return (op, ir[1], _b_fold(ir[2], env, where), _b_fold(ir[3], env, where))
    if op in ("and", "or"):
        return (op, _b_fold(ir[1], env, where), _b_fold(ir[2], env, where))
    if op == "if":
        return ("if", _b_fold(ir[1], env, where), _b_fold(ir[2], env, where),
                _b_fold(ir[3], env, where))
    raise ValueError("%s: internal: unknown expression node %r" % (where, op))

def _b_run(ir, vals, t):
    op = ir[0]
    if op == "num":
        return ir[1]
    if op == "time":
        return t
    if op == "v":
        return vals.get(ir[1], 0.0) - vals.get(ir[2], 0.0)
    if op == "call":
        name = ir[1]
        args = [_b_run(a, vals, t) for a in ir[2]]
        try:
            return float(_B_FUNCS[name][0](*args))
        except (ValueError, ZeroDivisionError) as exc:
            raise ValueError("%s(%s) has no value here (%s)"
                             % (name, ", ".join("%g" % a for a in args), exc))
    if op == "un":
        v = _b_run(ir[2], vals, t)
        return -v if ir[1] == "-" else v
    if op == "not":
        return 0.0 if _b_run(ir[1], vals, t) else 1.0
    if op == "bin":
        a = _b_run(ir[2], vals, t)
        b = _b_run(ir[3], vals, t)
        o = ir[1]
        if o == "+":
            return a + b
        if o == "-":
            return a - b
        if o == "*":
            return a * b
        if o == "/":
            if b == 0.0:
                raise ValueError("division by zero")
            return a / b
        if o == "**":
            try:
                return a ** b
            except OverflowError:
                raise ValueError("%g ** %g overflows" % (a, b))
        raise ValueError("unknown operator %r" % (o,))
    if op == "cmp":
        a = _b_run(ir[2], vals, t)
        b = _b_run(ir[3], vals, t)
        o = ir[1]
        hit = {"<": a < b, "<=": a <= b, ">": a > b, ">=": a >= b,
               "==": a == b, "!=": a != b}[o]
        return 1.0 if hit else 0.0
    if op == "and":
        return 1.0 if (_b_run(ir[1], vals, t) and _b_run(ir[2], vals, t)) else 0.0
    if op == "or":
        return 1.0 if (_b_run(ir[1], vals, t) or _b_run(ir[2], vals, t)) else 0.0
    if op == "if":
        return _b_run(ir[2] if _b_run(ir[1], vals, t) else ir[3], vals, t)
    raise ValueError("unknown expression node %r" % (op,))

def _b_eval(ir, vals, t, where):
    """Value of the expression at the node voltages in `vals`.

    The "expression: " prefix is what tells a caller (and _simulate_builtin) that
    this is not a convergence failure: the netlist is fine, the expression has no
    value at a voltage the solver had to try.
    """
    try:
        value = _b_run(ir, vals, t)
    except ValueError as exc:
        raise ValueError("expression: %s: %s" % (where, exc))
    value = float(value)
    if value != value or value in (float("inf"), float("-inf")):
        raise ValueError("expression: %s: the value is not a finite number" % where)
    return value

def _parse_b_source(parts, line, name, lineno):
    """B<name> n+ n- V=<expr>  |  I=<expr>"""
    if len(parts) < 4:
        raise ValueError("line %d: %s needs two nodes and V=<expr> or I=<expr>"
                         % (lineno, name))
    nodes = (_node(parts[1]), _node(parts[2]))
    body = " ".join(parts[1:])
    m = _B_MARKER.search(body)
    if m is None:
        raise ValueError("line %d: %s has no V=<expr> or I=<expr> after the two "
                         "nodes" % (lineno, name))
    kind = m.group(1).lower()
    rest = body[m.end():]
    # a B source is ONE equation; "V=2 I=3" has no meaning, and the parser would
    # otherwise hand "2 I=3" to the expression compiler and report a syntax error
    # about a deck that is actually ambiguous
    second = _B_MARKER.search(rest)
    if second is not None:
        raise ValueError("line %d: %s gives both V= and I=: a behaviour source is one "
                         "equation, so drop one of them"
                         % (lineno, name))
    expr = rest.strip()
    if not expr:
        raise ValueError("line %d: %s: %s= needs an expression"
                         % (lineno, name, m.group(1)))
    where = "line %d: %s" % (lineno, name)
    ir = _compile_b_expr(expr, where)
    return {"type": "BV" if kind == "v" else "BI", "name": name,
            "lineno": lineno, "nodes": nodes, "text": expr, "ir": ir,
            "deps": tuple(_b_nodes(ir)), "where": where}

# ------------------------------------------------------------------ source form

def _source_value(el, t):
    """Instantaneous value of an independent source."""
    w = el.get("wave")
    if w is None:
        return el.get("dc") or 0.0
    a = w["args"]
    kind = w["kind"]
    if kind == "pulse":
        # PULSE(v1 v2 td tr tf pw per)
        v1 = a[0] if len(a) > 0 else 0.0
        v2 = a[1] if len(a) > 1 else 0.0
        td = a[2] if len(a) > 2 else 0.0
        tr = a[3] if len(a) > 3 else 0.0
        tf = a[4] if len(a) > 4 else tr
        # PW omitted means "the pulse lasts for the rest of the run" in
        # SPICE (ngspice defaults PW to tstop, measured on PULSE(0 3 0 1p)).
        # Defaulting it to 0 instead made the source a zero-width spike that
        # then sat at v1 for the whole transient - a plausible flat answer that
        # was simply not the deck's waveform. The stop time is attached to the
        # element by the transient driver (a DC run has no stop time, and there
        # the width cannot matter: t = 0 is inside the rise).
        pw = a[5] if len(a) > 5 else (el.get("_tstop") or 0.0)
        per = a[6] if len(a) > 6 else 0.0
        if t < td:
            return v1
        tt = t - td
        if per > 0:
            tt = math.fmod(tt, per)
        if tr > 0 and tt < tr:
            return v1 + (v2 - v1) * tt / tr
        if tt < tr + pw:
            return v2
        if tf > 0 and tt < tr + pw + tf:
            return v2 + (v1 - v2) * (tt - tr - pw) / tf
        return v1
    if kind == "sin":
        # SIN(vo va freq td theta phase)
        vo = a[0] if len(a) > 0 else 0.0
        va = a[1] if len(a) > 1 else 0.0
        f = a[2] if len(a) > 2 else 0.0
        td = a[3] if len(a) > 3 else 0.0
        theta = a[4] if len(a) > 4 else 0.0
        phase = a[5] if len(a) > 5 else 0.0
        if t < td:
            return vo + va * math.sin(math.radians(phase))
        ts = t - td
        damp = math.exp(-ts * theta) if theta else 1.0
        return vo + va * damp * math.sin(2.0 * math.pi * f * ts + math.radians(phase))
    if kind == "pwl":
        # PWL(t1 v1 t2 v2 ...)
        pts = list(zip(a[0::2], a[1::2]))
        if not pts:
            return 0.0
        if t <= pts[0][0]:
            return pts[0][1]
        for (t0, v0), (t1, v1) in zip(pts, pts[1:]):
            if t <= t1:
                if t1 == t0:
                    return v1
                return v0 + (v1 - v0) * (t - t0) / (t1 - t0)
        return pts[-1][1]
    return 0.0


# ------------------------------------------------------------------ linear algebra

def _solve_dense(A, b):
    """Gauss-Jordan-free LU with partial pivoting. Returns x or raises."""
    n = len(b)
    M = [row[:] + [b[i]] for i, row in enumerate(A)]
    for col in range(n):
        piv = max(range(col, n), key=lambda r: abs(M[r][col]))
        if abs(M[piv][col]) < 1e-300:
            raise ZeroDivisionError("singular matrix at column %d" % col)
        if piv != col:
            M[col], M[piv] = M[piv], M[col]
        pv = M[col][col]
        for r in range(col + 1, n):
            f = M[r][col] / pv
            if f == 0:
                continue
            row_r = M[r]
            row_c = M[col]
            for c in range(col, n + 1):
                row_r[c] -= f * row_c[c]
    x = [0j] * n
    for r in range(n - 1, -1, -1):
        s = M[r][n]
        for c in range(r + 1, n):
            s -= M[r][c] * x[c]
        x[r] = s / M[r][r]
    return x


# --------------------------------------------------------- lossless line history
# The delayed values a lossless line needs live in a table of (t, v1, i1, v2, i2)
# once per step. Two things keep it honest:
#   * before the first entry (t < TD) nothing has arrived yet, so the answer is
#     the t=0 state - which is the DC operating point, where v1 = v2;
#   * between two entries the value is INTERPOLATED, because a delay that is not
#     a whole number of steps still has to land somewhere. A delay SHORTER than
#     one step cannot be interpolated at all (the value would be in the future),
#     so the transient driver rounds it up to one step and says so.

def _tline_history(el, tq):
    """(v1, i1, v2, i2) that left the far end at time `tq`."""
    hist = el["_hist"]
    if tq <= hist[0][0]:
        return hist[0][1], hist[0][2], hist[0][3], hist[0][4]
    if tq >= hist[-1][0]:
        # the driver refuses TD < h, so this is only the endpoint of an exact
        # grid match; keeping the last value is also what a query between the last
        # two steps would get, and extrapolating a wave would invent a front that
        # has not been solved for
        return hist[-1][1], hist[-1][2], hist[-1][3], hist[-1][4]
    lo, hi = 0, len(hist) - 1
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if hist[mid][0] <= tq:
            lo = mid
        else:
            hi = mid
    t0, t1 = hist[lo][0], hist[hi][0]
    f = (tq - t0) / (t1 - t0) if t1 > t0 else 1.0
    a, b = hist[lo], hist[hi]
    return tuple(a[k + 1] + (b[k + 1] - a[k + 1]) * f for k in range(4))


def _tline_ports(circ, el, x, t):
    """Port voltages and currents of a lossless line in the solution `x`.

    The currents are NOT read out of the branch unknown (in transient that row is
    parked at zero): they come out of the same companion model the matrix was
    built from, so a reported current cannot disagree with the solved voltages.
    """
    n1, n2, n3, n4 = el["nodes"]

    def vv(n):
        idx = circ.i(n)
        return x[idx - 1].real if idx else 0.0

    v1 = vv(n1) - vv(n2)
    v2 = vv(n3) - vv(n4)
    g = 1.0 / el["z0"]
    v1h, i1h, v2h, i2h = _tline_history(el, t - el["td"])
    return (v1, g * v1 - (g * v2h + i2h), v2, g * v2 - (g * v1h + i1h))


# ------------------------------------------------------------------ MNA assembly

class _Circuit(object):
    def __init__(self, spec):
        self.spec = spec
        self.temp_c = spec["options"].get("temp_c", TEMP_C)
        self.vt = 0.025852 * (273.15 + self.temp_c) / 300.15
        self.elements = spec["elements"]
        self.models = spec["models"]
        # An A card is only meaningful together with its .model, so the model is
        # resolved here (after every card has been read, so the .model may come
        # after the block) and its numbers are copied onto the element.
        for el in self.elements:
            if el["type"] == "A":
                _xspice_model(spec, el)
        # node table
        nodes = []
        for el in self.elements:
            for n in (list(el["nodes"]) + list(el.get("inputs") or ())
                      + list(el.get("ctrl") or ())):
                if n is not None and n not in nodes:
                    nodes.append(n)
        self.node_list = nodes
        self.has_ground = any(
            n is None for el in self.elements
            for n in (list(el["nodes"]) + list(el.get("inputs") or ())
                      + list(el.get("ctrl") or ())))
        self.node_idx = {n: i + 1 for i, n in enumerate(nodes)}
        for el in self.elements:
            for dep in el.get("deps") or ():
                if dep not in self.node_idx:
                    # a node nothing connects reads as 0 V, so a typo in V(...)
                    # would become a silent constant instead of an error
                    raise ValueError("line %s: %s reads node %r, which no other "
                                     "card connects; a node that does not exist "
                                     "would read as 0 V"
                                     % (el.get("lineno") or "?", el["name"], dep))
        self.n = len(nodes)
        branches = [el for el in self.elements
                    if el["type"] in ("V", "L", "E", "H", "BV", "A")]
        self.branch_idx = {}
        for k, el in enumerate(branches):
            self.branch_idx[el["name"]] = self.n + 1 + k
        # A lossless line adds ONE unknown, not two: at DC it is a pair of
        # wires, and "v1 = v2" is a single constraint whose current flows in
        # at one port and out of the other (i2 = -i1), so the second current
        # is not independent and must not get a row of its own - two rows
        # make the matrix singular the moment both ports return to ground,
        # which is how a single-ended line is normally drawn.
        extra = 0
        for el in self.elements:
            if el["type"] == "T":
                el["_row"] = self.n + 1 + len(branches) + extra
                self.branch_idx[el["name"] + "#i1"] = el["_row"]
                extra += 1
        self.size = self.n + len(branches) + extra
        # F/H (current-controlled sources) and K (coupled inductors) point at
        # other elements BY NAME. Resolving them here means a typo fails at build
        # time with the card's line number; deferring it to assembly would let a
        # deck solve with the control term silently missing, which is exactly the
        # class of answer this module refuses to give.
        self.by_name = {}
        for el in self.elements:
            self.by_name.setdefault(el["name"].lower(), el)
        self.branch_name = {}
        for el in branches:
            self.branch_name[el["name"].lower()] = el["name"]
        self.couplings = []
        for el in self.elements:
            T = el["type"]
            if T in ("F", "H"):
                key = str(el["ctrl_name"]).lower()
                where = "line %s" % (el.get("lineno") or "?")
                if key not in self.by_name:
                    raise ValueError("%s: %s is controlled by the current through "
                                     "%r, which no card in this deck defines"
                                     % (where, el["name"], el["ctrl_name"]))
                if key not in self.branch_name:
                    raise ValueError("%s: %s is controlled by %r, a %s card with no "
                                     "branch current. Only a source or an inductor "
                                     "(V, E, H, L, BV) carries a current that can be "
                                     "named"
                                     % (where, el["name"], el["ctrl_name"],
                                        self.by_name[key]["type"]))
                el["_ctrl_row"] = self.branch_idx[self.branch_name[key]]
                el["_ctrl_resolved"] = self.by_name[key]["name"]
            elif T == "K":
                where = "line %s" % (el.get("lineno") or "?")
                left = self.by_name.get(str(el["l1"]).lower())
                right = self.by_name.get(str(el["l2"]).lower())
                if left is None or right is None:
                    missing = el["l1"] if left is None else el["l2"]
                    raise ValueError("%s: %s couples %r and %r, and %r is not an "
                                     "element of this deck"
                                     % (where, el["name"], el["l1"], el["l2"],
                                        missing))
                if left["type"] != "L" or right["type"] != "L":
                    bad = left if left["type"] != "L" else right
                    raise ValueError("%s: %s couples %r and %r, but %r is a %s card. "
                                     "Mutual inductance is defined between two "
                                     "inductors; coupling anything else is not a "
                                     "smaller model, it is a different one"
                                     % (where, el["name"], el["l1"], el["l2"],
                                        bad["name"], bad["type"]))
                if left is right:
                    raise ValueError("%s: %s couples %r to itself"
                                     % (where, el["name"], left["name"]))
                k = float(el["value"])
                if abs(k) > 1.0 + 1e-12:
                    raise ValueError("%s: %s: coupling coefficient %g is outside "
                                     "[-1, 1]. |k| > 1 would put M^2 above L1*L2, "
                                     "i.e. a mutual flux larger than both of the "
                                     "fluxes that produce it" % (where, el["name"], k))
                if left["value"] <= 0.0 or right["value"] <= 0.0:
                    raise ValueError("%s: %s: an inductance of %g H cannot be "
                                     "coupled (M = k*sqrt(L1*L2) needs both positive)"
                                     % (where, el["name"],
                                        left["value"] if left["value"] <= 0.0
                                        else right["value"]))
                el["_m"] = k * math.sqrt(left["value"] * right["value"])
                el["_l1"] = left
                el["_l2"] = right
                self.couplings.append(el)
        # the parser's own "this was read but is not modelled" notes come first,
        # so a deck-level surprise and a solve-time surprise land in one list
        self.disabled = list(spec.get("warnings") or [])
        _over = spec.get("options", {}).get("temp_c_overrode")
        if _over:
            self.disabled.append(
                "the caller's temp_c argument overrode a .temp card that named %s; "
                "only the caller's one temperature was solved (drop the argument "
                "to get the card's own sweep, one independent solve per entry)"
                % (list(_over),))
        self.defaulted = {}       # model params that fell back to a default
        self.lossy = spec.get("lossy") or []   # O/U cards, for the report only
        # Switches (S/W) are piecewise-linear: which of TWO resistances is in
        # circuit depends on a state, so the answer is only as good as the state
        # it settled in. Models, parameters and a W card's control row are
        # resolved here, where a bad card still knows its own line number.
        self.switches = []
        for el in self.elements:
            if el["type"] in ("S", "W"):
                self._resolve_switch(el)
                self.switches.append(el)
        # JFETs: level-1 (Shichman-Hodges). Resolved here for the same reason the
        # switches are - a model parameter this subset does not stamp has to fail
        # on the card's own line number, not turn into a number nobody chose.
        self.jfets = []
        for el in self.elements:
            if el["type"] == "J":
                self._resolve_jfet(el)
                self.jfets.append(el)
        # Nodes that exist only inside a ladder: reported nowhere, because they
        # are not the caller's nodes (the O/U card is, with its section count).
        ext, inn = set(), set()
        for el in self.elements:
            for n in list(el["nodes"]) + list(el.get("inputs") or ()):
                if n is None:
                    continue
                (inn if el.get("_internal") else ext).add(n)
        self.internal_nodes = inn - ext
        # ... except a line's own terminals: a far end with nothing hung on it is
        # connected only by ladder sections, and it is still the caller's node
        for rec in self.lossy:
            for pair in rec["ports"]:
                for n in pair:
                    self.internal_nodes.discard(n)
        for rec in self.lossy:
            if "default" in rec["segments_from"]:
                # a defaulted section count is a defaulted model, and it is the
                # one number that decides how good the ladder is: said out loud
                self.defaulted["%s.lump" % rec["name"]] = rec["segments"]

    # -- helpers
    def is_linear(self):
        # a constant B source is an ordinary source: calling it nonlinear would
        # add damping to a circuit that solves exactly in one step
        return not any(el["type"] in ("D", "Q", "M", "J", "S", "W")
                       or (el["type"] in ("BV", "BI") and el["deps"])
                       # a mult block is bilinear: its small-signal gain is the
                       # other input's value, so it is not one-step solvable and
                       # wants the voltage limiter that is_linear() switches off
                       or (el["type"] == "A" and el.get("kind") == "MULT")
                       for el in self.elements)

    def i(self, name):
        return self.node_idx.get(name, 0) if name is not None else 0

    def _mat(self):
        return [[0j] * self.size for _ in range(self.size)], [0j] * self.size

    def _add(self, A, r, c, v):
        if r and c:
            A[r - 1][c - 1] += v

    def _rhs(self, b, r, v):
        if r:
            b[r - 1] += v

    def _conductance(self, A, na, nb, g):
        a, bb = self.i(na), self.i(nb)
        self._add(A, a, a, g)
        self._add(A, bb, bb, g)
        self._add(A, a, bb, -g)
        self._add(A, bb, a, -g)

    def _current(self, b, na, nb, amps):
        """Current `amps` flowing from na to nb inside the source."""
        self._rhs(b, self.i(na), -amps)
        self._rhs(b, self.i(nb), amps)

    # -- switches (S/W)
    def _resolve_switch(self, el):
        where = "line %s" % (el.get("lineno") or "?")
        kind, th_key, hys_key = {"S": ("SW", "VT", "VH"),
                                 "W": ("CSW", "IT", "IH")}[el["type"]]
        model = self.models.get(el["model"])
        if model is None:
            raise ValueError("%s: %s references .model %r which is not defined"
                             % (where, el["name"], el["model"]))
        if model["type"] != kind:
            raise ValueError("%s: %s needs a %s model and %r is %s"
                             % (where, el["name"], kind, el["model"],
                                model["type"]))
        params = dict(model["params"])
        allowed = (th_key, hys_key, "RON", "ROFF")
        unknown = sorted(k for k in params if k not in allowed)
        if unknown:
            raise ValueError("%s: .model %r has %s=, which is not a parameter of a "
                             "%s switch here (implemented: %s)"
                             % (where, el["model"], ", ".join(unknown), kind,
                                ", ".join(allowed)))
        defaults = {th_key: 0.0, hys_key: 0.0, "RON": 1.0, "ROFF": 1e6}
        vals = {}
        for key, dflt in defaults.items():
            if key in params:
                vals[key] = float(params[key])
            else:
                vals[key] = dflt
                # a defaulted switch resistance is exactly the kind of number
                # that turns into a plausible wrong curve, so it is said out
                # loud instead of being assumed away
                self.defaulted["%s.%s" % (el["model"], key.lower())] = dflt
        if vals["RON"] <= 0.0:
            raise ValueError("%s: .model %r has RON=%g. A closed switch of zero "
                             "resistance is an ideal short, which this solver "
                             "cannot stamp as a conductance; give RON > 0"
                             % (where, el["model"], vals["RON"]))
        if vals["ROFF"] <= 0.0:
            raise ValueError("%s: .model %r has ROFF=%g; an open switch is a "
                             "positive (large) resistance, not a zero or negative "
                             "one" % (where, el["model"], vals["ROFF"]))
        if vals["ROFF"] < vals["RON"]:
            raise ValueError("%s: .model %r has ROFF=%g below RON=%g, so the "
                             "switch conducts BETTER when open: the two states are "
                             "not the two states of a switch"
                             % (where, el["model"], vals["ROFF"], vals["RON"]))
        if vals[hys_key] < 0.0:
            raise ValueError("%s: .model %r has %s=%g; a hysteresis width cannot be "
                             "negative (0 means none)"
                             % (where, el["model"], hys_key, vals[hys_key]))
        el["_ron"] = vals["RON"]
        el["_roff"] = vals["ROFF"]
        el["_th"] = vals[th_key]
        el["_hys"] = vals[hys_key]
        el["_model_params"] = vals
        el["_on"] = True if el.get("_init") else False
        if el["type"] == "W":
            key = str(el["ctrl_name"]).lower()
            if key not in self.by_name:
                raise ValueError("%s: %s is switched by the current through %r, "
                                 "which no card in this deck defines"
                                 % (where, el["name"], el["ctrl_name"]))
            if key not in self.branch_name:
                raise ValueError("%s: %s is switched by %r, a %s card with no "
                                 "branch current. Only a source or an inductor "
                                 "(V, E, H, L, BV) carries a current that can be "
                                 "named"
                                 % (where, el["name"], el["ctrl_name"],
                                    self.by_name[key]["type"]))
            el["_ctrl_row"] = self.branch_idx[self.branch_name[key]]
            el["_ctrl_resolved"] = self.by_name[key]["name"]

    # -- JFETs (J)
    _JFET_IMPL = ("VTO", "BETA", "LAMBDA", "AREA")

    _JFET_REFUSED = {
        "RD": "a drain series resistance. It is an INTERNAL node between the drain "
              "terminal and the channel - it shifts the operating point and the "
              "transconductance - so it is not a number that can be added to the "
              "answer afterwards (measured: RD=100 with a 0.2 V drain moves the "
              "current by 14%)",
        "RS": "a source series resistance, an internal node for the same reason as "
              "RD= (measured: RS=100 with a 0.2 V drain moves the current by 16%)",
        "B": "the doping-tail parameter of the level-1 model. It is not the "
             "Shichman-Hodges square law this subset stamps: B=2 reads 1.5x and "
             "B=0.5 reads 0.75x the current (measured), i.e. a different equation",
        "IS": "a gate-junction saturation current, i.e. a gate-channel diode. This "
              "subset stamps no gate junction at all (the gate draws no current, "
              "which is exact below ~0.5 V of forward gate bias and wrong above it "
              "- the element report says when a deck crosses that line)",
        "CGS": "a gate-source capacitance. AC analysis here has no device "
               "capacitances - only C cards charge - so a nonzero CGS= would be "
               "missing from every AC answer with nothing on the page to say so",
        "CGD": "a gate-drain capacitance (same reason as CGS=)",
        "PB": "a gate-junction potential barrier; there is no gate junction here "
              "(see IS=)",
        "MJ": "a gate-junction grading coefficient; there is no gate junction here "
              "(see IS=)",
        "FC": "a gate-junction forward-bias coefficient; there is no gate junction "
              "here (see IS=)",
        "TCV": "a threshold temperature coefficient; this subset solves at the one "
               "temperature the deck states (.temp / temp_c) and does not scale VTO "
               "with it",
        "BEX": "a transconductance temperature exponent; this subset solves at the "
               "one temperature the deck states and does not scale BETA with it",
        "BETA1": "a second transconductance parameter of the level-2 JFET model; "
                 "only the level-1 square law is implemented",
        "LAMBDA1": "a second channel-length modulation parameter of the level-2 "
                   "model; only level 1 is implemented",
    }

    def _resolve_jfet(self, el):
        where = "line %s" % (el.get("lineno") or "?")
        model = self.models.get(el["model"])
        if model is None:
            raise ValueError("%s: %s references .model %r which is not defined"
                             % (where, el["name"], el["model"]))
        kind = model["type"]
        if kind not in ("NJF", "PJF"):
            raise ValueError("%s: %s needs an NJF or PJF model and %r is %s"
                             % (where, el["name"], el["model"], kind))
        params = dict(model["params"])
        for k in sorted(params):
            if k in self._JFET_IMPL:
                continue
            if k in ("RD", "RS") and float(params[k]) == 0.0:
                continue          # zero is the engine's own default: no internal node
            if k == "B" and float(params[k]) == 1.0:
                continue          # 1.0 is the engine's default: the square law itself
            if k in ("KF", "AF"):
                # Not refused here any more: the honest place to refuse a flicker
                # coefficient is the .noise analysis that would otherwise report
                # thermal+shot for a deck that asked for 1/f (see _noise_sources).
                # At .op/.ac/.tran it changes nothing, exactly as in SPICE.
                continue
            why = self._JFET_REFUSED.get(k)
            if why is not None:
                raise ValueError("%s: .model %r: %s= is %s; it is refused by name "
                                 "instead of being ignored (an ignored parameter is "
                                 "a netlist that runs and lies)"
                                 % (where, el["model"], k, why))
            raise ValueError("%s: .model %r has %s=, which is not a parameter of a "
                             "%s JFET here (implemented: %s)"
                             % (where, el["model"], k, kind,
                                ", ".join(self._JFET_IMPL)))
        defaults = {"VTO": -2.0, "BETA": 1e-4, "LAMBDA": 0.0, "AREA": 1.0}
        vals = {}
        for key, dflt in defaults.items():
            if key == "AREA":
                # the card's AREA= wins over the model's (measured: model AREA=2
                # with card AREA=3 gives 3x, not 6x - the card REPLACES it)
                if el.get("_area") is not None:
                    vals[key] = float(el["_area"])
                    continue
            if key in params:
                vals[key] = float(params[key])
            else:
                vals[key] = dflt
                # a defaulted threshold or transconductance is exactly the kind of
                # number that turns into a plausible wrong curve against a real
                # part's model card, so it is said out loud
                self.defaulted["%s.%s" % (el["model"], key.lower())] = dflt
        if vals["BETA"] <= 0.0:
            raise ValueError("%s: .model %r has BETA=%g; a transconductance "
                             "parameter cannot be zero or negative (it scales Id in "
                             "both the linear and the saturation region)"
                             % (where, el["model"], vals["BETA"]))
        if vals["LAMBDA"] < 0.0:
            raise ValueError("%s: .model %r has LAMBDA=%g; a negative channel-length "
                             "modulation would make the saturation current FALL as "
                             "Vds rises" % (where, el["model"], vals["LAMBDA"]))
        if vals["AREA"] <= 0.0:
            raise ValueError("%s: %r gives AREA=%g; an area factor scales the "
                             "transconductance and cannot be zero or negative"
                             % (where, el["model"], vals["AREA"]))
        vals["POLARITY"] = kind
        vals["BETA_eff"] = vals["BETA"] * vals["AREA"]
        el["_jf"] = vals

    def _switch_control(self, el, x):
        """What the switch compares against its threshold."""
        if el["type"] == "S":
            a, b = el["ctrl"]
            va = x[self.i(a) - 1] if self.i(a) else 0j
            vb = x[self.i(b) - 1] if self.i(b) else 0j
            return (va - vb).real
        return x[el["_ctrl_row"] - 1].real

    def switch_wanted(self, el, value):
        """The state the control quantity asks for, given the state it is in.

        The hysteresis width is VT -/+ VH - the FULL VH on each side, not half of
        it - and it applies in a DC solve as well as in a transient. Both of
        those are ngspice's own rules (sw.c: off->on when the control exceeds
        VT+VH, on->off when it falls below VT-VH, from an initial state that is
        open unless the card says otherwise), and both were MEASURED against the
        engine here rather than assumed:

        * with VT=1 VH=0.5 the engine is still open at a control of 1.5 V and
          closed at 1.6 V - no +/-VH/2 rule reproduces that;
        * with VT=1 VH=0.5 at DC and a control of 1.0 V the engine is OPEN, so
          "at DC there is no memory, compare instantly" is not the engine's
          behaviour either. It starts open and needs the band crossed.

        Without a width the engine closes AT the threshold (control exactly
        1.0 V with VT=1 closes it), so that boundary is inclusive.
        """
        th, width = el["_th"], el["_hys"]
        if width <= 0.0:
            return value >= th
        if el["_on"]:
            return value >= th - width
        return value > th + width

    def switch_conductance(self, el):
        return 1.0 / (el["_ron"] if el["_on"] else el["_roff"])

    # -- device models
    def diode_current(self, el, v):
        p = self._diode_params(el)
        x = v / (p["N"] * self.vt)
        x = min(x, 40.0)
        return p["IS"] * (math.exp(x) - 1.0) + p["RS_GMIN"] * v, p

    def _diode_params(self, el):
        m = self.models.get(el["model"], {"params": {}})
        pr = m["params"]
        IS = pr.get("IS", 1e-14)
        N = pr.get("N", 1.0)
        TNOM = pr.get("TNOM", TEMP_C)
        XTI = pr.get("XTI", 3.0)
        EG = pr.get("EG", 1.11)
        return {"IS": _is_at_temp(IS, self.temp_c, TNOM, N, XTI, EG),
                "IS_nominal": IS, "N": N, "RS_GMIN": 0.0,
                "TNOM": TNOM, "XTI": XTI, "EG": EG}

    def _bjt_currents(self, el, vb, vc, ve):
        m = self.models.get(el["model"], {})
        pr = m["params"]
        npn = m.get("type") == "NPN"
        IS = _is_at_temp(pr.get("IS", 1e-16), self.temp_c,
                         pr.get("TNOM", TEMP_C), pr.get("NF", 1.0) or 1.0,
                         pr.get("XTI", 3.0), pr.get("EG", 1.11))
        BF = pr.get("BF", 100.0)
        BR = pr.get("BR", 1.0)
        NF = pr.get("NF", 1.0)
        NR = pr.get("NR", 1.0)
        s = 1.0 if npn else -1.0
        vbe = _limit_junction(s * (vb - ve), self.vt, IS)
        vbc = _limit_junction(s * (vb - vc), self.vt, IS)
        xf = min(vbe / (NF * self.vt), 40.0)
        xr = min(vbc / (NR * self.vt), 40.0)
        If = IS * (math.exp(xf) - 1.0)
        Ir = IS * (math.exp(xr) - 1.0)
        alphaF = BF / (BF + 1.0)
        alphaR = BR / (BR + 1.0)
        ic = s * (alphaF * If - Ir)
        ib = s * ((1.0 - alphaF) * If + (1.0 - alphaR) * Ir)
        ie = -(ic + ib)
        return ic, ib, ie

    def _mos_currents(self, el, vd, vg, vs, vb):
        m = self.models.get(el["model"], {})
        pr = m["params"]
        nmos = m.get("type") == "NMOS"
        s = 1.0 if nmos else -1.0
        vgs = s * (vg - vs)
        vds = s * (vd - vs)
        vbs = s * (vb - vs)
        VTO = pr.get("VTO", 0.0 if nmos else -0.0)
        # KP alone is "the whole transconductance parameter" only when the card
        # states no geometry; with W= and L= the level-1 form is KP*(W/L)*m, and
        # ignoring it made every real deck's device 10x or 100x too weak with
        # nothing on the page to say so.
        KP = pr.get("KP", 2e-5) * el.get("wl", 1.0)
        LAMBDA = pr.get("LAMBDA", 0.0)
        GAMMA = pr.get("GAMMA", 0.0)
        PHI = pr.get("PHI", 0.6)
        vth = VTO
        if GAMMA and PHI > 0:
            arg = max(0.0, 2.0 * PHI - vbs)
            base = math.sqrt(2.0 * PHI)
            vth = VTO + GAMMA * (math.sqrt(arg) - base)
        vov = vgs - vth
        if vov <= 0:
            ids = 0.0
        elif vds < vov:
            ids = KP * (vov * vds - 0.5 * vds * vds) * (1.0 + LAMBDA * vds)
        else:
            ids = 0.5 * KP * vov * vov * (1.0 + LAMBDA * vds)
        ids *= s            # back to the polarity of the real terminals
        return ids, 0.0, -ids, 0.0

    def _jfet_currents(self, el, vd, vg, vs):
        """Level-1 (Shichman-Hodges) drain current: (id, ig, is).

        The two forms are MEASURED against ngspice-47, not copied from a
        textbook, and they are not the MOSFET's:

            saturation (Vds >= Vov):  Id = BETA*(Vov)^2*(1 + LAMBDA*Vds)
            linear     (Vds <  Vov):  Id = BETA*(2*Vov*Vds - Vds^2)*(1 + LAMBDA*Vds)

        i.e. twice the MOSFET's saturation current for the same BETA and no
        factor of 1/2 anywhere: BETA=1m with Vgs-Vto=1 reads 1 mA, not 0.5 mA
        (measured, docs/42). The two forms meet exactly at Vds = Vov, so the
        region boundary needs no smoothing term.

        The gate draws nothing: this subset stamps no gate-channel junction, and
        the element report raises `gate_junction_forward_v` when a deck actually
        forward-biases that junction (measured effect: nil below ~0.5 V, 1 mA at
        5 V on a 36 mA device - above that the engine itself starts to struggle).
        """
        jf = el["_jf"]
        s = 1.0 if jf["POLARITY"] == "NJF" else -1.0
        vgs = s * (vg - vs)
        vds = s * (vd - vs)
        vov = vgs - jf["VTO"]
        if vov <= 0.0:
            idr = 0.0
        elif vds < vov:
            idr = (jf["BETA_eff"] * (2.0 * vov * vds - vds * vds)
                   * (1.0 + jf["LAMBDA"] * vds))
        else:
            idr = (jf["BETA_eff"] * vov * vov
                   * (1.0 + jf["LAMBDA"] * vds))
        ids = s * idr
        return ids, 0.0, -ids

    # -- assembly
    def assemble(self, x_prev, t=None, mode="dc", gmin=1e-12, caps=None):
        """Build A, b. `x_prev` is the previous solution (for Newton and for the
        companion models). `caps` holds per-capacitor companion conductances."""
        A, b = self._mat()
        for el in self.elements:
            T = el["type"]
            if T == "K":
                # K couples two BRANCH ROWS and has no terminals at all; it is
                # stamped after this loop, where both rows are known to exist
                continue
            na, nb = el["nodes"][0], el["nodes"][1]
            if T == "R":
                self._conductance(A, na, nb, 1.0 / el["value"])
            elif T == "C":
                if mode == "ac":
                    self._conductance(A, na, nb, 1j * 2.0 * math.pi * el["_f"] * el["value"])
                elif mode == "tran":
                    g = el["value"] / el["_h"]
                    self._conductance(A, na, nb, g)
                    vprev = el["_vprev"]
                    self._current(b, na, nb, -g * vprev)
            elif T == "L":
                row = self.branch_idx[el["name"]]
                a, bb = self.i(na), self.i(nb)
                self._add(A, row, a, 1.0)
                self._add(A, row, bb, -1.0)
                self._add(A, a, row, 1.0)
                self._add(A, bb, row, -1.0)
                if mode == "ac":
                    self._add(A, row, row, -1j * 2.0 * math.pi * el["_f"] * el["value"])
                elif mode == "tran":
                    rl = el["value"] / el["_h"]
                    self._add(A, row, row, -rl)
                    self._rhs(b, row, -rl * el["_iprev"])
            elif T == "V":
                row = self.branch_idx[el["name"]]
                a, bb = self.i(na), self.i(nb)
                self._add(A, row, a, 1.0)
                self._add(A, row, bb, -1.0)
                self._add(A, a, row, 1.0)
                self._add(A, bb, row, -1.0)
                if mode == "ac":
                    # SPICE's own rule: the AC keyword is what makes a source an
                    # excitation. A source written without it (V1 1 0 3) has an AC
                    # magnitude of ZERO, not 1 - "3" is its DC value. Inventing a
                    # 1 V excitation here would produce a full-looking AC answer
                    # for a circuit that is not being driven, and ngspice-47
                    # returns 0 for exactly this deck (measured).
                    mag, phase = el.get("ac") or (0.0, 0.0)
                    self._rhs(b, row, cmath.rect(mag, math.radians(phase)))
                else:
                    self._rhs(b, row, _source_value(el, t if t is not None else 0.0))
            elif T == "I":
                if mode == "ac":
                    # see the V branch: no AC keyword means no excitation
                    mag, phase = el.get("ac") or (0.0, 0.0)
                    self._current(b, na, nb, -cmath.rect(mag, math.radians(phase)))
                else:
                    self._current(b, na, nb, -_source_value(el, t if t is not None else 0.0))
            elif T == "E":
                row = self.branch_idx[el["name"]]
                a, bb = self.i(na), self.i(nb)
                cp, cm = self.i(el["ctrl"][0]), self.i(el["ctrl"][1])
                self._add(A, row, a, 1.0)
                self._add(A, row, bb, -1.0)
                self._add(A, row, cp, -el["value"])
                self._add(A, row, cm, el["value"])
                self._add(A, a, row, 1.0)
                self._add(A, bb, row, -1.0)
            elif T == "A":
                # out = f(inputs) with the output referenced to ground, so the row
                # is v(out) - f(...) = 0 and needs its own branch current like V/E.
                # Every input voltage enters the row with -df/dv_i, linearised at
                # the previous iterate: a clamp is piecewise linear (slope gain
                # inside the band, 0 outside) and a mult is bilinear (the true
                # partials), so Newton lands in one or two steps and an answer that
                # sits on a bound is a real answer, not a stalled iterate.
                row = self.branch_idx[el["name"]]
                nb_ = self.i(nb)
                ins_ = [self.i(n) for n in el["inputs"]]
                if mode == "ac":
                    # A clip and a multiplier are linearised AT THE BIAS POINT: the
                    # sweep hands this function the DC operating point as x_prev, so
                    # the slopes are the bias-point ones (gain inside the band and 0
                    # outside it for a limit; the other inputs' bias values for a
                    # mult). Measured against ngspice-47 before it was written: 2.0
                    # inside the band and 0.0 clipped for a limit, 4.0 for
                    # mult(A=3 V, B=4 V) driven on A. Without a bias point there is
                    # nothing to linearise, and that stays an error rather than a
                    # made-up gain.
                    if x_prev is None:
                        raise ValueError(
                            "%s: a %s block in an .ac sweep needs a DC operating "
                            "point to be linearised at, and none was passed in"
                            % (el["name"], el["kind"].lower()))
                    vins0 = [x_prev[n - 1].real if n else 0.0 for n in ins_]
                    f0, slopes = _xspice_ac_slopes(el, vins0)
                    self._add(A, row, nb_, 1.0)
                    for k_, n_in in enumerate(ins_):
                        self._add(A, row, n_in, -slopes[k_])
                else:
                    vins0 = [x_prev[n - 1].real if n else 0.0 for n in ins_]
                    f0, slopes = _xspice_eval(el, vins0)
                    self._add(A, row, nb_, 1.0)
                    rhs0 = f0
                    for k_, n_in in enumerate(ins_):
                        self._add(A, row, n_in, -slopes[k_])
                        rhs0 -= slopes[k_] * vins0[k_]
                    self._rhs(b, row, rhs0)
                self._add(A, nb_, row, 1.0)
            elif T == "G":
                cp, cm = self.i(el["ctrl"][0]), self.i(el["ctrl"][1])
                a, bb = self.i(na), self.i(nb)
                g = el["value"]
                self._add(A, a, cp, g)
                self._add(A, a, cm, -g)
                self._add(A, bb, cp, -g)
                self._add(A, bb, cm, g)
            elif T == "F":
                # the controlling quantity is a BRANCH UNKNOWN, not a node
                # voltage: the gain lands on an existing column instead of a new
                # row, so F adds no unknown of its own
                a, bb = self.i(na), self.i(nb)
                col = el["_ctrl_row"]
                g = el["value"]
                self._add(A, a, col, g)
                self._add(A, bb, col, -g)
            elif T == "H":
                # H is a voltage source whose value is a current: it needs its own
                # branch row, and that row carries -gain on the control column
                row = self.branch_idx[el["name"]]
                a, bb = self.i(na), self.i(nb)
                self._add(A, row, a, 1.0)
                self._add(A, row, bb, -1.0)
                self._add(A, row, el["_ctrl_row"], -el["value"])
                self._add(A, a, row, 1.0)
                self._add(A, bb, row, -1.0)
            elif T == "BV":
                self._stamp_b_voltage(A, b, el, x_prev, t, mode)
            elif T == "BI":
                self._stamp_b_current(A, b, el, x_prev, t, mode)
            elif T == "D":
                self._stamp_diode(A, b, el, x_prev, mode)
            elif T == "Q":
                self._stamp_bjt(A, b, el, x_prev, mode)
            elif T == "M":
                self._stamp_mos(A, b, el, x_prev, mode)
            elif T == "J":
                self._stamp_jfet(A, b, el, x_prev, mode)
            elif T in ("S", "W"):
                # Piecewise linear, not nonlinear: one of two conductances is
                # stamped, picked by a state - and the state is NOT re-read here.
                # It is latched for the whole solve, and the driver asks the
                # CONVERGED answer whether it agrees (then re-solves if it does
                # not). Reading it from the iterate instead would silently depend
                # on the iteration path and would throw away the ON/OFF seed:
                # with VT=1 VH=0.5 and a 1.2 V control ngspice answers CLOSED for
                # `ON` and OPEN without it, which only a latched state can hold.
                self._conductance(A, na, nb, self.switch_conductance(el))
            elif T == "T":
                self._stamp_tline(A, b, el, x_prev, t, mode)
            elif T in ("O", "U"):
                # unreachable through parse_netlist (the card is expanded there);
                # raising rather than stamping nothing keeps a future refactor
                # from turning a lossy line into a silent wire
                raise ValueError("%s (a %s card) reached the solver unexpanded"
                                 % (el["name"], T))
        # Coupled inductors couple two BRANCH rows. Same companion model as one
        # inductor, with the cross term M/h: an L card's row already reads
        # v - (L/h)*i = -(L/h)*i_prev, so the mutual part is -(M/h)*i_other on
        # the same row plus -(M/h)*i_other_prev on the right-hand side.
        for cp in self.couplings:
            row1 = self.branch_idx[cp["_l1"]["name"]]
            row2 = self.branch_idx[cp["_l2"]["name"]]
            if mode == "ac":
                jw = complex(0.0, 2.0 * math.pi * cp["_f"])
                self._add(A, row1, row2, -jw * cp["_m"])
                self._add(A, row2, row1, -jw * cp["_m"])
            elif mode == "tran":
                rm = cp["_m"] / cp["_l1"]["_h"]
                self._add(A, row1, row2, -rm)
                self._add(A, row2, row1, -rm)
                self._rhs(b, row1, -rm * cp["_l2"]["_iprev"])
                self._rhs(b, row2, -rm * cp["_l1"]["_iprev"])
            # DC: d/dt is 0, both inductors are wires, and M has nothing to act on
        # gmin to ground on every node: the standard "no floating node" crutch
        if mode != "ac" and gmin:
            for k in range(self.n):
                A[k][k] += gmin
        return A, b

    def _x(self, x_prev, name):
        idx = self.i(name)
        return x_prev[idx - 1] if idx else 0j

    def _stamp_tline(self, A, b, el, x_prev, t, mode):
        """A lossless line, solved as the analysis needs it.

        DC: a pair of wires. v(n1) = v(n3) and v(n2) = v(n4), one constraint row
        and one current unknown that enters at n1, leaves at n3, enters at n4 and
        leaves at n2. A lossless line has no DC drop, so this is the model, not an
        approximation of it.

        AC: the exact Y parameters of a lossless line,
        Y11 = Y22 = -j*Y0*cot(theta), Y12 = Y21 = +j*Y0*csc(theta),
        theta = 2*pi*f*TD. Checked against the definition it comes from: a line
        terminated in Z0 must present exactly Z0 at its input and delay the
        output by theta.

        tran: the method of characteristics (Bergeron) - a conductance 1/Z0 at
        each port plus a current source holding that port's value from one delay
        ago. For an IDEAL line this is the solution, not a discretisation of it:
        the only error is where the delayed value is interpolated, and the only
        assumption is that nothing happened in between.
        """
        n1, n2, n3, n4 = el["nodes"]
        row = el["_row"]
        if mode == "tran":
            g = 1.0 / el["z0"]
            self._conductance(A, n1, n2, g)
            self._conductance(A, n3, n4, g)
            # the DC constraint unknown does not exist in time: no real quantity
            # is being solved for it, so it is parked at zero and disconnected
            self._add(A, row, row, 1.0)
            v1h, i1h, v2h, i2h = _tline_history(el, (t or 0.0) - el["td"])
            # i1(t) = g*v1(t) - (g*v2(t-TD) + i2(t-TD))
            self._current(b, n1, n2, -(g * v2h + i2h))
            # i2(t) = g*v2(t) - (g*v1(t-TD) + i1(t-TD))
            self._current(b, n3, n4, -(g * v1h + i1h))
            return
        if mode == "ac":
            theta = 2.0 * math.pi * el["_f"] * el["td"]
            s = math.sin(theta)
            # the driver refuses the singular frequencies before we get here; a
            # guard that raises would turn a netlist problem into a traceback
            if abs(s) < 1e-12:
                s = 1e-12 if s >= 0.0 else -1e-12
            y0 = 1.0 / el["z0"]
            y11 = complex(0.0, -y0 * math.cos(theta) / s)
            y12 = complex(0.0, y0 / s)
            a1, b1 = self.i(n1), self.i(n2)
            a2, b2 = self.i(n3), self.i(n4)
            # port 1: i1 = y11*v1 + y12*v2, current leaving n1 and entering n2
            self._add(A, a1, a1, y11)
            self._add(A, a1, b1, -y11)
            self._add(A, b1, a1, -y11)
            self._add(A, b1, b1, y11)
            self._add(A, a1, a2, y12)
            self._add(A, a1, b2, -y12)
            self._add(A, b1, a2, -y12)
            self._add(A, b1, b2, y12)
            # port 2: i2 = y12*v1 + y11*v2 (y12 is symmetric: reciprocal line)
            self._add(A, a2, a2, y11)
            self._add(A, a2, b2, -y11)
            self._add(A, b2, a2, -y11)
            self._add(A, b2, b2, y11)
            self._add(A, a2, a1, y12)
            self._add(A, a2, b1, -y12)
            self._add(A, b2, a1, -y12)
            self._add(A, b2, b1, y12)
            self._add(A, row, row, 1.0)
            return
        # DC (and the t=0 bias point of tran and ac): the line is a wire
        i1, i2, i3, i4 = self.i(n1), self.i(n2), self.i(n3), self.i(n4)
        self._add(A, row, i1, 1.0)
        self._add(A, row, i2, -1.0)
        self._add(A, row, i3, -1.0)
        self._add(A, row, i4, 1.0)
        self._add(A, i1, row, 1.0)
        self._add(A, i2, row, -1.0)
        self._add(A, i3, row, -1.0)
        self._add(A, i4, row, 1.0)

    def _stamp_diode(self, A, b, el, x_prev, mode="dc"):
        na, nk = el["nodes"]
        v = (self._x(x_prev, na) - self._x(x_prev, nk)).real
        p = self._diode_params(el)
        vlim = _limit_junction(v / p["N"], self.vt, p["IS"]) * p["N"]
        x = min(vlim / (p["N"] * self.vt), 40.0)
        i0 = p["IS"] * (math.exp(x) - 1.0)
        g = p["IS"] / (p["N"] * self.vt) * math.exp(x)
        g = max(g, 1e-12)
        self._conductance(A, na, nk, g)
        if mode == "ac":
            # in AC only the SMALL-SIGNAL conductance exists; the DC operating
            # current is not a source. Stamping it here gave 0.67 instead of
            # 0.006 - the bias current looked like an injected phasor.
            return
        c = i0 - g * vlim
        self._current(b, na, nk, c)

    def _stamp_bjt(self, A, b, el, x_prev, mode="dc"):
        nc, nb, ne = el["nodes"]
        vc = self._x(x_prev, nc).real
        vb = self._x(x_prev, nb).real
        ve = self._x(x_prev, ne).real
        ic, ib, ie = self._bjt_currents(el, vb, vc, ve)
        nodes = [nc, nb, ne]
        cur = [ic, ib, ie]
        volts = [vc, vb, ve]
        jac = _numeric_jac(lambda vs: self._bjt_currents(el, vs[1], vs[0], vs[2]),
                           volts)
        self._stamp_generic(A, b, nodes, cur, jac, volts, mode)

    def _stamp_mos(self, A, b, el, x_prev, mode="dc"):
        nd, ng, ns, nbb = el["nodes"]
        vd = self._x(x_prev, nd).real
        vg = self._x(x_prev, ng).real
        vs = self._x(x_prev, ns).real
        vb = self._x(x_prev, nbb).real
        cur = list(self._mos_currents(el, vd, vg, vs, vb))
        volts = [vd, vg, vs, vb]
        jac = _numeric_jac(lambda v: list(self._mos_currents(el, v[0], v[1], v[2], v[3])),
                           volts)
        self._stamp_generic(A, b, [nd, ng, ns, nbb], cur, jac, volts, mode)

    def _stamp_jfet(self, A, b, el, x_prev, mode="dc"):
        nd, ng, ns = el["nodes"]
        vd = self._x(x_prev, nd).real
        vg = self._x(x_prev, ng).real
        vs = self._x(x_prev, ns).real
        cur = list(self._jfet_currents(el, vd, vg, vs))
        volts = [vd, vg, vs]
        jac = _numeric_jac(lambda v: list(self._jfet_currents(el, v[0], v[1], v[2])),
                           volts)
        self._stamp_generic(A, b, [nd, ng, ns], cur, jac, volts, mode)

    def b_value(self, el, x, t=None):
        """Evaluate a B source's expression at the solution `x`."""
        vals = {}
        for dep in el["deps"]:
            idx = self.i(dep)
            vals[dep] = x[idx - 1].real if idx else 0.0
        return _b_eval(el["ir"], vals, 0.0 if t is None else t, el["where"])

    def _b_partials(self, el, volts, t):
        """df/dV_j for every node the expression reads (central differences).

        The same estimator as the device Jacobian on purpose: one derivative
        estimate shared with the solver is one place less for the answer and its
        derivative to disagree.
        """
        deps = el["deps"]
        base = _b_eval(el["ir"], dict(zip(deps, volts)), t, el["where"])
        out = []
        for j in range(len(deps)):
            h = max(1e-6, 1e-5 * abs(volts[j]))
            up = list(volts)
            dn = list(volts)
            up[j] += h
            dn[j] -= h
            # sqrt(V) at V=0 has a derivative of +inf, and the CENTRED difference
            # steps into -1e-6 and takes the whole run down with a maths error.
            # Stepping to one side only is the honest fallback: it is a real
            # slope of the expression, just not a centred one, so the answer near
            # a domain edge is the expression's problem and not a silent one.
            fu = fd = None
            try:
                fu = _b_eval(el["ir"], dict(zip(deps, up)), t, el["where"])
            except ValueError:
                fu = None
            try:
                fd = _b_eval(el["ir"], dict(zip(deps, dn)), t, el["where"])
            except ValueError:
                fd = None
            if fu is not None and fd is not None:
                out.append((fu - fd) / (2.0 * h))
            elif fu is not None:
                out.append((fu - base) / h)
            elif fd is not None:
                out.append((base - fd) / h)
            else:
                raise ValueError("expression: %s: no value on either side of the "
                                 "operating point for this derivative"
                                 % el["where"])
        return out

    def _stamp_b_voltage(self, A, b, el, x_prev, t, mode="dc"):
        """V(na) - V(nb) = f(V), linearised: the row carries -df/dV in each
        column the expression reads, and the constant part on the right."""
        row = self.branch_idx[el["name"]]
        na, nb = el["nodes"]
        a, bb = self.i(na), self.i(nb)
        self._add(A, row, a, 1.0)
        self._add(A, row, bb, -1.0)
        self._add(A, a, row, 1.0)
        self._add(A, bb, row, -1.0)
        f0 = self.b_value(el, x_prev, t)
        if not el["deps"]:
            if mode != "ac":
                self._rhs(b, row, f0)
            return
        volts = [self._x(x_prev, dep).real for dep in el["deps"]]
        jac = self._b_partials(el, volts, t)
        for j, dep in enumerate(el["deps"]):
            self._add(A, row, self.i(dep), -jac[j])
        if mode != "ac":
            self._rhs(b, row, f0 - sum(jac[j] * volts[j]
                                       for j in range(len(volts))))

    def _stamp_b_current(self, A, b, el, x_prev, t, mode="dc"):
        """I from n+ to n- = f(V), stamped exactly like the diode's I(V)."""
        na, nb = el["nodes"]
        a, bb = self.i(na), self.i(nb)
        f0 = self.b_value(el, x_prev, t)
        volts = [self._x(x_prev, dep).real for dep in el["deps"]]
        jac = self._b_partials(el, volts, t) if el["deps"] else []
        for j, dep in enumerate(el["deps"]):
            ci = self.i(dep)
            self._add(A, a, ci, jac[j])
            self._add(A, bb, ci, -jac[j])
        if mode != "ac":
            self._current(b, na, nb, f0 - sum(jac[j] * volts[j]
                                              for j in range(len(volts))))

    def _stamp_generic(self, A, b, nodes, currents, jac, volts, mode="dc"):
        idx = [self.i(n) for n in nodes]
        for i, ri in enumerate(idx):
            if mode != "ac":
                c = currents[i] - sum(jac[i][j] * volts[j] for j in range(len(nodes)))
                self._rhs(b, ri, -c)
            for j, ci in enumerate(idx):
                self._add(A, ri, ci, jac[i][j])


def _numeric_jac(fn, v0, rel=1e-5, floor=1e-6):
    """Central-difference Jacobian for a small device function."""
    m = len(v0)
    base = list(fn(v0))
    jac = [[0.0] * m for _ in range(m)]
    for j in range(m):
        h = max(floor, rel * abs(v0[j]))
        up = list(v0)
        dn = list(v0)
        up[j] += h
        dn[j] -= h
        fu = fn(up)
        fd = fn(dn)
        for i in range(m):
            jac[i][j] = (fu[i] - fd[i]) / (2.0 * h)
    # 1 S floor: a device with zero differential conductance makes the matrix
    # singular and Newton then wanders. SPICE achieves the same with gmin.
    for i in range(m):
        jac[i][i] += 1e-12
    return jac


# ------------------------------------------------------------------ analyses

def _newton(circ, x0, t=None, mode="dc", max_iter=DEFAULT_MAX_ITER,
            reltol=1e-6, vntol=1e-9, gmin=1e-12, vlimit=None):
    """Newton on the nonlinear MNA system.

    `vlimit` caps how far ANY node may move in one iteration. That is the cheap
    stand-in for SPICE's per-junction limiting (pnjlim): without it the first
    step of a diode circuit overshoots the exponential and the iteration then
    oscillates instead of converging - the symptom being "converged only with a
    huge gmin", i.e. a plausible wrong answer. A linear circuit is exempt: it
    solves exactly in one step and damping would only slow it down.
    """
    x = list(x0)
    for it in range(1, max_iter + 1):
        try:
            A, b = circ.assemble(x, t=t, mode=mode, gmin=gmin)
        except ValueError as exc:
            return None, it, str(exc)
        try:
            xn = _solve_dense(A, b)
        except ZeroDivisionError as exc:
            return None, it, str(exc)
        if vlimit:
            delta = max([abs(a - bb) for a, bb in zip(xn, x)] or [0.0])
            if delta > vlimit:
                f = vlimit / delta
                xn = [bb + f * (a - bb) for a, bb in zip(x, xn)]
        step = 0.0
        for a, bb in zip(xn, x):
            step = max(step, abs(a - bb) / (vntol + reltol * abs(a) + 1e-30))
        x = xn
        if step < 1.0:
            # the step being small is necessary but not sufficient: verify KCL on
            # the solution that will actually be reported
            A2, b2 = circ.assemble(x, t=t, mode=mode, gmin=gmin)
            res = 0.0
            for r in range(circ.size):
                acc = b2[r]
                biggest = 0.0
                for c in range(circ.size):
                    term = A2[r][c] * x[c]
                    acc -= term
                    if abs(term) > biggest:
                        biggest = abs(term)
                # PER ROW, not one scale for the whole solution. One global
                # scale set by the largest node voltage let a 1 mA residual in a
                # 1k branch pass as converged, because the same circuit also had
                # a 4000 V node: the row was judged "close enough" against
                # somebody else's volts. A row is converged when its own residual
                # is 1e-6 of its own biggest term.
                denom = 1e-9 + 1e-6 * biggest
                if abs(acc) > denom:
                    res = max(res, abs(acc) / denom)
            if res <= 1.0:
                return x, it, None
    return None, max_iter, "no convergence in %d iterations" % max_iter


def _dc(circ, t=None, uic_x=None, mode="dc"):
    """DC operating point, then the switch states made self-consistent.

    A switch is a STATE, and the state a card starts in (ON/OFF, else open) is
    still a real input at DC: ngspice answers OPEN for `ON` with a 0 V control
    (0 V is past VT-VH=1) and CLOSED for `ON` with a 1.2 V control inside a
    VT=1/VH=0.5 band - measured, both. So the solve is repeated until the answer
    agrees with the state it was solved under, exactly like the transient step
    loop, and bounded exactly like it: a control parked on a band edge makes the
    circuit bistable, and picking a side would be this solver's choice.
    """
    x0 = uic_x if uic_x is not None else [0j] * circ.size
    x, iters, why = _dc_solve(circ, x0, t, mode)
    if x is None or not circ.switches:
        return x, iters, why
    total = iters
    for _attempt in range(9):
        moved = False
        for el in circ.switches:
            want = circ.switch_wanted(el, circ._switch_control(el, x))
            if want != el["_on"]:
                el["_on"] = want
                moved = True
        if not moved:
            return x, total, None
        x2, i2, why2 = _dc_solve(circ, x, t, mode)
        if x2 is None:
            return None, total, why2
        x = x2
        total += i2
    return None, total, ("the switch state did not settle in 8 flips")


def _dc_solve(circ, x0, t, mode):
    """One DC solve from x0: plain Newton, then gmin stepping DOWN."""
    vlimit = None if circ.is_linear() else 1.0
    sol, iters, why = _newton(circ, x0, t=t, mode=mode, vlimit=vlimit)
    if sol is not None:
        return sol, iters, None
    # gmin stepping DOWN: converge the easy problem first, then take the crutch
    # away. Stepping UP (the tempting version) ends on a poisoned answer - it
    # leaks microamps through every node, which a BJT bias turns into a wrong
    # beta with no warning at all.
    x = x0
    total = iters
    last = why
    for exp in range(3, 13):
        gm = 10.0 ** (-exp)
        s2, i2, w2 = _newton(circ, x, t=t, mode=mode, gmin=gm, vlimit=vlimit)
        if s2 is None:
            last = w2
            break
        x = s2
        total += i2
        if exp == 12:
            circ.disabled.append("converged by gmin stepping (final gmin 1e-12 S)")
            return x, total, None
    return None, total, last


def _lossy_report(circ, rec, x):
    """One record per O/U card: its ports, and the model it stands for."""
    def v(n):
        idx = circ.i(n)
        return x[idx - 1].real if idx else 0.0

    (a1, a2), (b1, b2) = rec["ports"]
    v1 = v(a1) - v(a2)
    v2 = v(b1) - v(b2)
    first = rec["first"]
    if first is None:
        i1 = 0.0
    elif first["kind"] == "R":
        i1 = (v(first["a"]) - v(first["b"])) / first["value"]
    else:
        i1 = x[circ.branch_idx[first["name"]] - 1].real
    return {"name": rec["name"], "type": rec["type"], "model": rec["model"],
            "voltage_v": round(v1, 9),
            "segments": rec["segments"], "segments_from": rec["segments_from"],
            "length_m": rec["length_m"], "per_length": rec["per_length"],
            "totals": rec["totals"],
            "port1": {"voltage_v": round(v1, 9), "current_a": round(i1, 12)},
            "port2": {"voltage_v": round(v2, 9)},
            "current_convention": ("port 1 current is positive INTO n1 (the first "
                                   "terminal); a lumped line has no single current "
                                   "along it, so port 2's is not reported"),
            "lumped_model": ("%d-section ladder of R/L/C - the ladder IS the model, "
                             "and its section count sets the dispersion. Skin "
                             "effect, dielectric loss and frequency-dependent r/l "
                             "are not in it" % rec["segments"])}

def _element_report(circ, x, t=None, mode="dc"):
    out = []
    for el in circ.elements:
        if el.get("_internal"):
            # a ladder section is an implementation detail of the line above it;
            # reporting 20 unnamed R/L/C per O card would bury the answer
            continue
        T = el["type"]
        if T == "K":
            # no terminals, no voltage: report what the card actually asserts
            out.append({"name": el["name"], "type": "K",
                        "inductors": [el["_l1"]["name"], el["_l2"]["name"]],
                        "coupling": round(float(el["value"]), 12),
                        "mutual_h": round(el["_m"], 15),
                        "per_length": None,
                        "note": ("a K card is a statement about two inductors, not a "
                                 "device: it has no terminals and no current of its "
                                 "own")})
            continue
        na, nb = el["nodes"][0], el["nodes"][1]
        va = x[circ.i(na) - 1] if circ.i(na) else 0j
        vb = x[circ.i(nb) - 1] if circ.i(nb) else 0j
        v = (va - vb).real
        rec = {"name": el["name"], "type": T, "voltage_v": round(v, 9)}
        vv = lambda n: (x[circ.i(n) - 1] if circ.i(n) else 0j)  # noqa: E731
        if T == "A":
            # an A card's own report is the block, not a two-terminal voltage:
            # v(nodes[0]) - v(nodes[1]) would be in - out, which is not a quantity
            # anyone wants. Give the input, the output and which region it landed in.
            ins_ = list(el["inputs"])
            vins_ = [vv(n).real for n in ins_]
            vin_ = vins_[0]
            vout_ = vv(nb).real
            cur_ = x[circ.branch_idx[el["name"]] - 1].real
            raw_ = _xspice_eval(el, vins_)[0]
            if el["lo"] is None:
                region = "linear"
            elif raw_ < el["lo"]:
                region = "clipped at out_lower_limit"
            elif raw_ > el["hi"]:
                region = "clipped at out_upper_limit"
            else:
                region = "linear"
            rec = {"name": el["name"], "type": "A", "model": el["model"],
                   "kind": el["kind"], "output_v": round(vout_, 12),
                   "voltage_v": round(vout_, 12),
                   "current_a": round(cur_, 12),
                   "power_w": round(vout_ * cur_, 12),
                   "region": region}
            if len(ins_) == 1:
                # a one-port block is exactly the 0.0.39 shape: in, out, gain,
                # offset - the multi-port fields would only be noise here
                rec["input_v"] = round(vin_, 12)
                rec["gain"] = el["in_gain"][0]
                rec["in_offset"] = el["in_offset"][0]
            else:
                rec["inputs"] = ins_
                rec["input_vs"] = [round(v_, 12) for v_ in vins_]
                rec["in_gain"] = list(el["in_gain"])
                rec["in_offset"] = list(el["in_offset"])
                rec["out_gain"] = el["out_gain"]
                rec["out_offset"] = el["out_offset"]
            if el["lo"] is not None:
                rec["out_lower_limit"] = el["lo"]
                rec["out_upper_limit"] = el["hi"]
            if el["defaulted"]:
                rec["defaulted_model_params"] = list(el["defaulted"])
            out.append(rec)
            continue
        if T == "R":
            rec["current_a"] = round(v / el["value"], 12)
            rec["power_w"] = round(v * v / el["value"], 12)
        elif T in ("V", "I"):
            if T == "V":
                cur = x[circ.branch_idx[el["name"]] - 1]
                rec["current_a"] = round(-cur.real, 12)
                rec["power_w"] = round(-(cur.real) * _source_value(el, t or 0.0), 12)
                rec["value"] = round(_source_value(el, t or 0.0), 9)
            else:
                s = _source_value(el, t or 0.0)
                rec["current_a"] = round(-s, 12)
                rec["power_w"] = round(-s * v, 12)
                rec["value"] = round(s, 9)
        elif T in ("L", "E", "H"):
            cur = x[circ.branch_idx[el["name"]] - 1].real
            rec["current_a"] = round(cur, 12)
            rec["power_w"] = round(v * cur, 12)
        elif T == "F":
            ic = x[el["_ctrl_row"] - 1].real
            rec["control_current_a"] = round(ic, 12)
            rec["current_a"] = round(el["value"] * ic, 12)
            rec["power_w"] = round(v * el["value"] * ic, 12)
        elif T in ("BV", "BI"):
            val = circ.b_value(el, x, t)
            rec["expression"] = el["text"]
            rec["value"] = round(val, 9)
            if T == "BV":
                ib = x[circ.branch_idx[el["name"]] - 1].real
                rec["current_a"] = round(-ib, 12)
                rec["power_w"] = round(-ib * val, 12)
            else:
                rec["current_a"] = round(-val, 12)
                rec["power_w"] = round(-val * v, 12)
        elif T == "C":
            rec["charge_c"] = round(el["value"] * v, 15)
        elif T in ("S", "W"):
            g = circ.switch_conductance(el)
            rec["state"] = "on" if el["_on"] else "off"
            rec["resistance_ohm"] = round(1.0 / g, 12)
            rec["conductance_s"] = round(g, 15)
            rec["current_a"] = round(v * g, 15)
            rec["power_w"] = round(v * v * g, 15)
            ctl = circ._switch_control(el, x)
            if T == "S":
                rec["control_voltage_v"] = round(ctl, 12)
            else:
                rec["control_current_a"] = round(ctl, 12)
                rec["controlled_element"] = el["_ctrl_resolved"]
            rec["threshold"] = round(el["_th"], 12)
            rec["hysteresis"] = round(el["_hys"], 12)
            rec["initial_state"] = (None if el.get("_init") is None
                                    else ("on" if el["_init"] else "off"))
            rec["model_params"] = dict(el["_model_params"])
        elif T == "D":
            # report the CURRENT, not just the voltage: "the diode sits at 0.69 V"
            # is not checkable by the caller, "it carries 4.3 mA" is.
            p = circ._diode_params(el)
            # NOTE: the exponent must NOT be named `x` - that is the solution
            # vector, and rebinding it here made every element after the diode
            # index into a float (TypeError, or worse: a silent wrong report).
            xd = min(_limit_junction(v / p["N"], circ.vt, p["IS"]) / (p["N"] * circ.vt),
                     40.0)
            ID = p["IS"] * (math.exp(xd) - 1.0)
            g = max(p["IS"] / (p["N"] * circ.vt) * math.exp(xd), 1e-12)
            rec["current_a"] = round(ID, 15)
            rec["conductance_s"] = round(g, 12)
            rec["dynamic_resistance_ohm"] = round(1.0 / g, 9)
            rec["power_w"] = round(v * ID, 15)
            # report BOTH the nominal and the temperature-scaled saturation
            # current: the gap between them is the whole explanation for the
            # reported forward drop, and the caller should not have to guess it
            rec["model_params"] = {"IS": p["IS"], "IS_nominal": p["IS_nominal"],
                                   "N": p["N"], "TNOM_c": p["TNOM"],
                                   "XTI": p["XTI"], "EG_ev": p["EG"]}
        elif T == "Q":
            nc, nb, ne = el["nodes"]
            xc = x[circ.i(nc) - 1].real if circ.i(nc) else 0.0
            xb = x[circ.i(nb) - 1].real if circ.i(nb) else 0.0
            xe = x[circ.i(ne) - 1].real if circ.i(ne) else 0.0
            ic, ib, ie = circ._bjt_currents(el, xb, xc, xe)
            rec["voltage_v"] = round(xc - xe, 9)
            rec["vbe_v"] = round(xb - xe, 9)
            rec["vbc_v"] = round(xb - xc, 9)
            rec["vce_v"] = round(xc - xe, 9)
            rec["current_a"] = {"ib": round(ib, 15), "ic": round(ic, 15),
                                "ie": round(ie, 15)}
            rec["beta"] = (round(ic / ib, 6) if abs(ib) > 1e-18 else None)
            rec["current_convention"] = ("positive INTO the collector / base / "
                                         "emitter terminal")
            rec["power_w"] = round((xb - xe) * ib + (xc - xe) * ic, 15)
            rec["region"] = ("saturation" if abs(rec["vce_v"]) < abs(rec["vbe_v"])
                             else "forward active")
        elif T == "M":
            nd, ng, ns, nbb = el["nodes"]
            g = lambda n: (x[circ.i(n) - 1].real if circ.i(n) else 0.0)  # noqa: E731
            vd, vg, vs, vbb = g(nd), g(ng), g(ns), g(nbb)
            cur = circ._mos_currents(el, vd, vg, vs, vbb)
            pr = circ.models.get(el["model"], {}).get("params", {})
            nmos = circ.models.get(el["model"], {}).get("type") == "NMOS"
            sg = 1.0 if nmos else -1.0
            # gate transconductance by numerical differentiation, the same way the
            # Jacobian is built - no third formula to disagree with the solver
            h = 1e-6
            idp = circ._mos_currents(el, vd, vg + h, vs, vbb)[0]
            idm = circ._mos_currents(el, vd, vg - h, vs, vbb)[0]
            rec["voltage_v"] = round(sg * (vd - vs), 9)
            rec["vgs_v"] = round(sg * (vg - vs), 9)
            rec["vds_v"] = round(sg * (vd - vs), 9)
            rec["vov_v"] = round(sg * (vg - vs) - pr.get("VTO", 0.0), 9)
            rec["current_a"] = {"id": round(cur[0], 15)}
            rec["current_convention"] = "positive INTO the drain terminal"
            rec["gm_s"] = round((idp - idm) / (2.0 * h), 9)
            rec["power_w"] = round((vd - vs) * cur[0], 15)
            rec["region"] = ("saturation" if abs(sg * (vd - vs)) >
                             abs(rec["vov_v"]) else "linear/triode")
        elif T == "J":
            nd, ng, ns = el["nodes"]
            g = lambda n: (x[circ.i(n) - 1].real if circ.i(n) else 0.0)  # noqa: E731
            vd, vg, vs = g(nd), g(ng), g(ns)
            jf = el["_jf"]
            sg = 1.0 if jf["POLARITY"] == "NJF" else -1.0
            cur = circ._jfet_currents(el, vd, vg, vs)
            h = 1e-6
            idp = circ._jfet_currents(el, vd, vg + h, vs)[0]
            idm = circ._jfet_currents(el, vd, vg - h, vs)[0]
            vgs = sg * (vg - vs)
            vds = sg * (vd - vs)
            vov = vgs - jf["VTO"]
            rec["vgs_v"] = round(vgs, 9)
            rec["vds_v"] = round(vds, 9)
            rec["vov_v"] = round(vov, 9)
            rec["current_a"] = {"id": round(cur[0], 15)}
            rec["current_convention"] = "positive INTO the drain terminal"
            rec["gm_s"] = round((idp - idm) / (2.0 * h), 9)
            rec["power_w"] = round((vd - vs) * cur[0], 15)
            rec["region"] = ("cutoff" if vov <= 0.0 else
                             ("saturation" if vds >= vov else "linear"))
            rec["polarity"] = jf["POLARITY"]
            rec["model_params"] = {"VTO": jf["VTO"], "BETA": jf["BETA"],
                                   "LAMBDA": jf["LAMBDA"], "AREA": jf["AREA"],
                                   "BETA_eff": jf["BETA_eff"]}
            # This subset stamps no gate-channel junction, and that is exact until
            # the junction forward-biases: measured, the engine's own extra gate
            # current is below 1 uA until ~0.5 V of forward gate bias and 1 mA on a
            # 36 mA device at 5 V. So the number is reported, not assumed away.
            if jf["POLARITY"] == "NJF":
                fwd = max(vg - vd, vg - vs)
            else:
                fwd = max(vd - vg, vs - vg)
            rec["gate_junction_forward_v"] = round(fwd, 9)
            if fwd > 0.5:
                note = ("%s gate junction is forward biased by %g V; this subset "
                        "stamps no gate-channel diode, so the gate current here is "
                        "0 A by construction and the answer above is outside the "
                        "model" % (el["name"], fwd))
                rec["outside_model"] = note
                if note not in circ.disabled:
                    circ.disabled.append(note)
        elif T == "T":
            n1, n2, n3, n4 = el["nodes"]
            if mode == "tran":
                v1t, i1t, v2t, i2t = _tline_ports(circ, el, x, t or 0.0)
            else:
                # DC: the row unknown is the current in at n1 / out at n3, and the
                # second current is its negative by construction
                v1t = (vv(n1) - vv(n2)).real
                v2t = (vv(n3) - vv(n4)).real
                i1t = x[el["_row"] - 1].real
                i2t = -i1t
            rec["z0_ohm"] = el["z0"]
            rec["delay_s"] = el["td"]
            rec["port1"] = {"voltage_v": round(v1t, 9), "current_a": round(i1t, 12)}
            rec["port2"] = {"voltage_v": round(v2t, 9), "current_a": round(i2t, 12)}
            rec["current_convention"] = ("positive INTO the + terminal of each port "
                                         "(n1 and n3); the two currents sum to zero")
            rec["power_w"] = round(v1t * i1t + v2t * i2t, 12)
        out.append(rec)
    for lossy_rec in getattr(circ, "lossy", ()) or ():
        out.append(_lossy_report(circ, lossy_rec, x))
    return out


def _node_dict(circ, x, complex_ok=False):
    d = {}
    hidden = getattr(circ, "internal_nodes", ()) or ()
    for name in circ.node_list:
        if name in hidden:
            # a ladder's internal node is an implementation detail of the line
            # above it: reporting 40 of them per O card buries the answer, and
            # nothing a caller can act on lives there
            continue
        v = x[circ.i(name) - 1]
        if complex_ok:
            d[name] = {"re": round(v.real, 9), "im": round(v.imag, 9),
                       "mag": round(abs(v), 9),
                       "phase_deg": round(math.degrees(cmath.phase(v)), 6)}
        else:
            d[name] = round(v.real, 9)
    return d


PRECISION = (
    "linear solves are dense LU in double precision, so a well-conditioned "
    "circuit is good to ~1e-9 relative; the error that matters is the MODEL: "
    "level-1 MOSFET with KP*(W/L)*m, level-1 JFET with BETA*AREA (no gate "
    "junction, no RD/RS), Ebers-Moll BJT and an ideal Shockley diode "
    "with no series resistance, no junction capacitance and no temperature beyond "
    "the one stated. Noise is thermal 4kT/R, shot 2q*I and - since 0.0.54 - a "
    "DIODE's flicker KF*|Id|^AF/f (.noise, since 0.0.32). A converged answer is "
    "the answer to this netlist, not to the board"
)

NOT_COMPUTED = [
    "parameters that need a function, a node reference or a self-referential "
    "definition: .param and .subckt parameters take numbers and {+ - * / **} "
    "arithmetic on numbers and other parameters only, and expressions are not "
    "accepted inside .model parameters",
    "a .subckt nested inside another .subckt, and any directive other than "
    ".model inside a .subckt body",
    "only four XSPICE code models are solved: gain, limit, summer and mult, and "
    "only their algebra (out = out_offset + out_gain * SUM_i or PROD_i of "
    "in_gain_i*(v_i + in_offset_i), optionally clamped to the two bounds). Every "
    "other analogue code model carries state or a sample clock (integrator, ddt, "
    "s_xfer) or divides and accumulates (divider), and is refused by name rather "
    "than approximated; a limit or mult block is also refused inside .ac, because "
    "its small-signal gain depends on where the operating point sits. "
    "JFETs (J), switches (S/W), controlled "
    "sources (F/H) and mutual inductance (K) ARE solved, but not their second-order "
    "physics: a JFET has no gate-channel junction diode (the report says when a "
    "deck forward-biases it past 0.5 V), no RD/RS internal nodes, no device "
    "capacitance and no flicker noise (a KF= on a J/Q/M model card is REFUSED at "
    "the .noise analysis by name, because reporting thermal+shot for a deck that "
    "asked for 1/f would be a quiet answer; MEASURED, ngspice-47 itself moved its "
    "BJT/JFET/MOSFET noise by 0.0 % for KF 1e-15..1e-6); a switch has two "
    "resistance states, no "
    "charge injection, no capacitance and no break-before-make; K has no loss "
    "terms, no frequency-dependent core and no saturation; and an F/H gain is a "
    "constant, not a table",
    "MOSFET levels 2..7, BSIM, gate charge, capacitances and switching loss; W= "
    "L= and M= scale KP*(W/L)*m (level 1), while AD= AS= PD= PS= and the rest are "
    "read and reported as ignored, because junction capacitance and leakage are "
    "not modelled",
    "Laplace blocks (laplace(...)), the current of another element (I(Vname)), "
    "B-source noise, and derivative/ddt/idt operators",
    "a DISTRIBUTED line: an O card (LTRA) and a U card (URC) are turned into a "
    "lumped ladder of R/L/C, so the answer is the answer to that ladder - the "
    "section count (lump=, default %d) sets the dispersion and the answer only "
    "converges to the distributed one as the sections increase. Skin effect, "
    "dielectric loss, frequency-dependent r/l and the rise-time degradation of a "
    "real trace are NOT in it (an ideal lossless line, T, is solved exactly). "
    "ngspice-47 itself refuses an O card with nonzero G (\"Nonzero G (except RG) "
    "line not supported yet\"), so a g>0 deck cannot be cross-checked against it"
    % DEFAULT_LUMP,
    "distortion (.disto), Monte Carlo / worst-case (an `mc(...)` / `unif(...)` / "
    "`agauss(...)` value is refused by the parser rather than read as its nominal "
    "number), and the noise mechanisms this subset does not model: flicker (1/f) "
    "on anything but a diode (see the KF= note above), burst, avalanche, induced "
    "gate noise, the noise of a B source, and the thermal noise of anything not "
    "written as an R card (a capacitor's ESR is the ESR's)",
    "junction self-heating - the junction sits at the temperature you state, it "
    "does not rise with dissipation. IS(T) and Vt(T) are modelled (XTI/EG/TNOM), "
    "so a forward drop at a stated temperature is right, but device capacitances, "
    "mobility and breakdown versus temperature are not. A multi-valued `.temp` "
    "card IS a sweep (one independent solve per temperature, since 0.0.54), but a "
    "TEMPERATURE argument inside a model card (`temp=` on the element) is not read",
    "convergence for circuits that need continuation beyond gmin/source stepping",
]


def _switch_hint(circ):
    """Extra cause to check when a deck WITH switches will not settle at DC.

    A switch can be wired so that closing it removes the very voltage that holds
    it closed. Then no state is self-consistent, and the honest answer is an
    error with this sentence attached - the alternative is a plausible operating
    point that is simply one of the two states the solver happened to stop in.
    """
    switches = getattr(circ, "switches", None) or ()
    if not switches:
        return None
    return ("this deck has %s, and a switch can remove its own control (closing it "
            "shorts the node that switches it): then NO state is self-consistent - the "
            "circuit is bistable rather than solved, and "
            "any answer here would be the state the solver happened to stop in. "
            "Hysteresis (VH=/IH=) and ON/OFF decide which state the deck STARTS in, "
            "not whether a self-consistent state exists - so break the loop, or ask "
            "for the transient from an explicit state"
            % ", ".join(e["name"] for e in switches))

def _expression_failure(why):
    """A B-source expression with no value is not a convergence failure."""
    text = str(why or "")
    if text.startswith("expression:"):
        return err(text[len("expression: "):], "EXPRESSION_INVALID",
                   hint="a B source's expression was evaluated at a voltage where it "
                        "has no value (division by zero, sqrt or log of a negative, "
                        "an overflow). The netlist is not wrong; the expression is")
    return None

# Boltzmann and the elementary charge, CODATA-2018 exact values (the SI
# redefinition fixed them). A noise number whose constants were "about right"
# would be wrong in the fourth digit with nothing on the page to show it.
KB_J_PER_K = 1.380649e-23
E_CHARGE_C = 1.602176634e-19

def _sig10(x):
    """Ten significant digits: a 1e-19 V^2/Hz density is unreadable as 19 zeros."""
    return float("%.10g" % x)

def _noise_owner(circ, name):
    """The card a noise source belongs to.

    A ladder section's name is internal ("oline#r3"); the contribution belongs to
    the O card the caller wrote ("O1"), under its own spelling.
    """
    base = name.split("#", 1)[0]
    if base == name:
        return name
    for rec in getattr(circ, "lossy", ()) or ():
        if rec["name"].lower() == base.lower():
            return rec["name"]
    return base

def _noise_sources(circ, x):
    """What makes noise, at the bias point.

    Returns (sources, loud): `sources` is [(name, mechanism, a, b, psd, text)]
    where `psd` is either a float (A^2/Hz, frequency independent) or a callable
    f -> A^2/Hz (flicker's f^-1 falls out of the form, so one number would be a
    lie read at any other frequency). `loud` is the devices that carry a flicker
    coefficient this subset cannot model, which the caller refuses by name.

    Only mechanisms this subset actually models are listed, and each one names
    its formula. A device that contributes nothing here (an ideal source, a C or
    an L) is silent by construction, not by omission - the noise of a real
    capacitor's ESR is the ESR's, which is an R card you either wrote or did not.
    """
    kT = KB_J_PER_K * (circ.temp_c + 273.15)
    out = []

    def volt(n):
        idx = circ.i(n)
        return x[idx - 1].real if idx else 0.0

    def _flicker(el, i_amps):
        """KF*|I|^AF/f as a frequency-dependent density, or None.

        SPICE3's form for a junction: `KF*Id^AF/f`, one current source across
        the junction, in A^2/Hz. MEASURED against the bundled ngspice-47 on a
        forward-biased 1N4148-like junction (KF=1e-16, Id=3.3227e-5 A, 1 Hz and
        10 Hz): fitting the engine's two readings gives KF = 1.00112e-16, i.e.
        +0.11 % on the card's 1e-16, so the f^-1 shape and the |Id|^1 scaling
        are the engine's, not a guess. `AF` defaults to 1 (SPICE3's default).

        A device whose model carries KF but whose flicker form could NOT be
        verified here returns None and is named in `loud` instead of being
        quietly zero - see the Q/M/J branches.
        """
        pr = (circ.models.get(el["model"]) or {}).get("params") or {}
        kf = pr.get("KF", 0.0)
        try:
            kf = float(kf)
        except (TypeError, ValueError):
            kf = 0.0
        if kf <= 0.0:
            return None
        af = pr.get("AF", 1.0)
        try:
            af = float(af)
        except (TypeError, ValueError):
            af = 1.0
        amp = abs(i_amps)
        return (lambda f, kf=kf, af=af, amp=amp:
                kf * (amp ** af) / f if f > 0.0 else 0.0)

    loud = []
    for el in circ.elements:
        T = el["type"]
        if T in ("Q", "M", "J"):
            _pr = (circ.models.get(el["model"]) or {}).get("params") or {}
            try:
                _kf = float(_pr.get("KF") or 0.0)
            except (TypeError, ValueError):
                _kf = 0.0
            if _kf > 0.0:
                loud.append((el["name"], T, _kf))
        if T == "R":
            out.append((el["name"], "thermal", el["nodes"][0], el["nodes"][1],
                        4.0 * kT / el["value"], "4kT/R (Nyquist, a current source "
                        "across the resistor)"))
        elif T in ("S", "W"):
            out.append((el["name"], "thermal_switch", el["nodes"][0],
                        el["nodes"][1],
                        4.0 * kT * circ.switch_conductance(el),
                        "4kT/R at the resistance of the state the switch settled "
                        "in (RON when on, ROFF when off)"))
        elif T == "D":
            na, nk = el["nodes"]
            i_d, _p = circ.diode_current(el, volt(na) - volt(nk))
            out.append((el["name"], "shot", na, nk,
                        2.0 * E_CHARGE_C * abs(i_d),
                        "2q*|Id| (shot, a current source across the junction)"))
            fl = _flicker(el, i_d)
            if fl is not None:
                out.append((el["name"], "flicker", na, nk, fl,
                            "KF*|Id|^AF/f (1/f, a current source across the "
                            "junction; the f^-1 shape and the |Id|^1 scaling "
                            "were fitted to ngspice-47, KF read back +0.11 %)"))
        elif T == "Q":
            nc, nb, ne = el["nodes"]
            ic, ib, _ie = circ._bjt_currents(el, volt(nb), volt(nc), volt(ne))
            out.append((el["name"], "shot_collector", nc, ne,
                        2.0 * E_CHARGE_C * abs(ic),
                        "2q*|Ic| (shot, collector to emitter)"))
            out.append((el["name"], "shot_base", nb, ne,
                        2.0 * E_CHARGE_C * abs(ib),
                        "2q*|Ib| (shot, base to emitter, independent of Ic's)"))
        elif T == "M":
            nd, ng, ns, nbb = el["nodes"]
            v = [volt(nd), volt(ng), volt(ns), volt(nbb)]
            jac = _numeric_jac(lambda vv: list(circ._mos_currents(el, vv[0], vv[1],
                                                                 vv[2], vv[3])), v)
            gm = jac[0][1]
            out.append((el["name"], "channel_thermal", nd, ns,
                        4.0 * kT * (2.0 / 3.0) * abs(gm),
                        "4kT*(2/3)*gm (channel thermal, saturation form; no flicker, "
                        "no induced gate noise)"))
        elif T == "J":
            nd, ng, ns = el["nodes"]
            v = [volt(nd), volt(ng), volt(ns)]
            jac = _numeric_jac(lambda vv: list(circ._jfet_currents(el, vv[0], vv[1],
                                                                 vv[2])), v)
            gm = jac[0][1]
            out.append((el["name"], "channel_thermal", nd, ns,
                        4.0 * kT * (2.0 / 3.0) * abs(gm),
                        "4kT*(2/3)*gm (channel thermal, saturation form; the gate "
                        "junction's shot noise and flicker are not modelled, and "
                        "there is no gate current to make shot noise from)"))
    return out, loud

def _psd_probe_freqs(freqs):
    """The frequencies a frequency-dependent density is quoted at: the band ends.

    Two points and not one, because the whole content of a 1/f source is that
    its density is not the same at the two ends of the band - a single number
    would read like a constant and hide the only interesting fact about it.
    """
    if not freqs:
        return []
    if len(freqs) == 1:
        return [freqs[0]]
    return [freqs[0], freqs[-1]]

def _noise_band(points, key, geometric=False):
    """Integrate a density over the swept band. The band IS the sweep.

    The rule follows the sweep: a dec/oct sweep is uniform in ln(f), so the
    integral is taken in ln(f) (int PSD df = int PSD*f d(ln f)), and a lin sweep
    in f. Measured on an RC low-pass (docs/38), integrating a dec-10 sweep in f
    instead overstates the band noise by 0.44% while the geometric rule is
    within 0.001% - and a lin sweep does the opposite. One rule cannot be right
    for both grids, so the grid decides.
    """
    total = 0.0
    for k in range(1, len(points)):
        f0, f1 = points[k - 1]["freq_hz"], points[k]["freq_hz"]
        a, b = points[k - 1][key], points[k][key]
        if geometric and f0 > 0.0 and f1 > 0.0:
            total += 0.5 * (math.log(f1) - math.log(f0)) * (a * f0 + b * f1)
        else:
            total += 0.5 * (f1 - f0) * (a + b)
    return total

def _noise_analysis(circ, card, max_points):
    """Small-signal noise at the DC bias: one ADJOINT solve per frequency.

    Why an adjoint and not one solve per noise source: the output response to a
    current injected between two nodes is (y[a] - y[b]) where A^T y = e_out, and
    A is the same complex MNA matrix the AC analysis already builds. So one
    linear solve gives every source at once, exactly (this is a linear circuit
    once the bias point is fixed), and it cannot disagree with the AC answer
    because it IS the AC answer, read backwards.

    What is reported is a spectral density (V/sqrt(Hz)) per frequency plus the
    rms noise integrated over the swept band. Both are needed: a density at one
    frequency says nothing about what a receiver sees, and an integrated number
    alone hides which band produced it.
    """
    f0, f1 = card["fstart"], card["fstop"]
    if f0 <= 0 or f1 <= f0:
        return err("noise sweep needs 0 < fstart < fstop", "ARG_RANGE")
    pos, neg = card["out"]
    for node, which in ((pos, "first"), (neg, "second")):
        if node is not None and node not in circ.node_idx:
            return err("the .noise output %s names node %r, which no card connects"
                       % (which, node), "NETLIST_INVALID",
                       hint="a node nothing connects reads 0 V, so a typo would come "
                            "back as a beautifully quiet answer")
    src = None
    for el in circ.elements:
        if el["name"].lower() == str(card["src"]).lower():
            src = el
            break
    if src is None:
        return err("the .noise input source %r is not in the netlist" % card["src"],
                   "NETLIST_INVALID")
    if src["type"] not in ("V", "I"):
        return err("the .noise input source must be a V or I source; %s is a %s card"
                   % (src["name"], src["type"]), "ARG_INVALID")
    freqs = _sweep_freqs(card["sweep"], card["n"], f0, f1)
    asked = len(freqs)
    if asked > max_points:
        circ.disabled.append(
            "the .noise sweep asked for %d points but only the first %d were solved "
            "(up to %.6g Hz, not fstop %.6g Hz) - the integrated noise below covers "
            "the solved band only" % (asked, max_points, freqs[max_points - 1], f1))
        freqs = freqs[:max_points]
    x0, iters, why = _dc(circ)
    if x0 is None:
        bad = _expression_failure(why)
        if bad is not None:
            return bad
        return err("noise needs a DC operating point and it did not converge (%s)"
                   % why, "NOT_CONVERGED",
                   hint="every noise density here is evaluated AT the bias point "
                        "(4kT/R, 2q*Id, gm), so without a bias there is no noise")
    sources, loud = _noise_sources(circ, x0)
    if loud:
        # A device that states KF= is asking for 1/f noise. Reporting thermal +
        # shot without it would be a quiet number the caller asked not to get,
        # and the reference engine is no help either: MEASURED, ngspice-47 moved
        # its BJT/JFET/MOSFET answer by 0.0 % for KF between 1e-15 and 1e-6, so
        # "use engine='ngspice'" would return the same quiet number without
        # saying anything. The honest answer is to refuse and name the device.
        return err("noise cannot be reported: %s carr%s KF=, and flicker (1/f) "
                   "noise on a %s is not modelled by this subset - the f^-1 form "
                   "was verified against ngspice-47 for a DIODE only, where fitting "
                   "the engine's two readings reproduces the card's KF to 0.11 %%"
                   % (", ".join("%s (%s)" % (nm, kd) for (nm, kd, _kf) in loud),
                      "ies" if len(loud) == 1 else "y",
                      "JFET" if (loud[0][1] == "J" and len(loud) == 1) else
                      ("BJT" if (loud[0][1] == "Q" and len(loud) == 1) else
                       ("MOSFET" if (loud[0][1] == "M" and len(loud) == 1)
                        else "Q/M/J device"))),
                   "NOT_MODELED",
                   hint="MEASURED: ngspice-47 (the engine bundled with this "
                        "package) applies no BJT/JFET/MOSFET flicker at all - KF "
                        "from 1e-15 to 1e-6 changed its own noise answer by 0.0 % "
                        "- so there is no reference model to copy and "
                        "engine='ngspice' would return the same thermal+shot "
                        "number without telling you. Remove KF= (and AF=) from "
                        "those model cards if thermal + shot is the answer you "
                        "want; only a diode's KF is modelled here",
                   extra={"devices_with_unmodelled_flicker":
                          [{"element": nm, "type": kd, "kf": float(kf)}
                           for (nm, kd, kf) in loud]})
    e = [0j] * circ.size
    if pos is not None:
        e[circ.i(pos) - 1] += 1.0
    if neg is not None:
        e[circ.i(neg) - 1] -= 1.0
    # Unit excitation of the input source: only |gain| is used, so its phase and
    # magnitude are irrelevant - and requiring an AC keyword on the card would
    # make ".noise V(2) V1 ..." refuse a deck SPICE accepts.
    bu = [0j] * circ.size
    if src["type"] == "V":
        bu[circ.branch_idx[src["name"]] - 1] = 1.0
    else:
        a_node, b_node = src["nodes"][0], src["nodes"][1]
        if circ.i(a_node):
            bu[circ.i(a_node) - 1] += 1.0
        if circ.i(b_node):
            bu[circ.i(b_node) - 1] -= 1.0
    points = []
    gain_flat = None
    for f in freqs:
        for el in circ.elements:
            if el["type"] in ("C", "L", "T", "K"):
                el["_f"] = f
        bad = _tline_guard(circ, f)
        if bad is not None:
            return bad
        A, _b = circ.assemble(x0, mode="ac", gmin=0.0)
        At = [[A[r][c] for r in range(circ.size)] for c in range(circ.size)]
        try:
            y = _solve_dense(At, e)
        except ZeroDivisionError as exc:
            return err("the noise matrix is singular at %g Hz: %s" % (f, exc),
                       "SINGULAR",
                       hint="a floating node or a voltage-source loop has no AC "
                            "definition, and a noise density through it would be "
                            "infinite; fix the DC netlist first")
        gain = abs(sum(y[k] * bu[k] for k in range(circ.size)))
        if gain > 0.0 and gain_flat is None:
            gain_flat = gain
        per = {}
        for (name, _mech, a_node, b_node, psd, _text) in sources:
            ia, ib = circ.i(a_node), circ.i(b_node)
            h = ((y[ia - 1] if ia else 0j) - (y[ib - 1] if ib else 0j))
            owner = _noise_owner(circ, name)
            # a flicker density is f^-1, so it is evaluated AT this frequency:
            # one number for the whole sweep would be a lie at every point but
            # the one it was taken at
            psd_f = psd(f) if callable(psd) else psd
            per[owner] = per.get(owner, 0.0) + abs(h) ** 2 * psd_f
        onoise2 = sum(per.values())
        rec = {"freq_hz": round(f, 6),
               "onoise_v_per_sqrt_hz": _sig10(math.sqrt(onoise2)),
               "onoise_v2_per_hz": _sig10(onoise2),
               "gain_mag": _sig10(gain),
               "inoise_v_per_sqrt_hz": (_sig10(math.sqrt(onoise2 / (gain * gain)))
                                        if gain > 0.0 else None),
               "contributions_v2_per_hz": dict((k, _sig10(v))
                                              for k, v in sorted(per.items()))}
        points.append(rec)
    if not points:
        return err("the .noise sweep produced no frequency", "ARG_RANGE")
    if len(points) < 2:
        circ.disabled.append(
            "the sweep holds one frequency, so the integrated noise is 0 by "
            "construction: an integral needs a band, and one point has none "
            "(the spectral density at that point is still the answer)")
    # A density the input cannot reach has no input-referred value: dividing by
    # a gain of zero would print "inf" and look like a finding.
    if all(p["inoise_v_per_sqrt_hz"] is None for p in points):
        circ.disabled.append(
            "%s drives nothing that reaches %s at any swept frequency, so the input-"
            "referred noise is not defined and is reported as null (the output "
            "noise is still the answer)" % (src["name"], card["out_text"]))
    geometric = card["sweep"] in ("dec", "oct")
    onoise_rms = math.sqrt(max(0.0, _noise_band(points, "onoise_v2_per_hz",
                                                geometric)))
    if all(p["inoise_v_per_sqrt_hz"] is None for p in points):
        inoise_rms = None
    else:
        inoise2 = []
        for p in points:
            g = p["gain_mag"]
            inoise2.append(p["onoise_v2_per_hz"] / (g * g) if g > 0.0 else 0.0)
        inoise_rms = math.sqrt(max(0.0, _noise_band(
            [{"freq_hz": p["freq_hz"], "x": v} for p, v in zip(points, inoise2)],
            "x", geometric)))
    mech = {}
    for (name, mechanism, _a, _b, psd, text) in sources:
        owner = _noise_owner(circ, name)
        key = (owner, mechanism)
        cur = mech.get(key)
        if cur is None:
            cur = {"element": owner, "mechanism": mechanism,
                   "elements": 1, "formula": text}
            if callable(psd):
                # a density that follows the frequency has no single value, so
                # the two ends of the band are reported instead of a midpoint
                # that would look like a constant
                cur["frequency_dependent"] = True
                cur["psd_a2_per_hz_at"] = dict(
                    ("%g" % f, _sig10(psd(f)))
                    for f in _psd_probe_freqs(freqs))
            else:
                cur["psd_a2_per_hz"] = psd
            mech[key] = cur
        else:
            cur["elements"] += 1
            if callable(psd):
                cur["frequency_dependent"] = True
            else:
                cur["psd_a2_per_hz"] = cur.get("psd_a2_per_hz", 0.0) + psd
    return ok(value={"analysis": "noise",
                     "output": card["out_text"],
                     "input_source": src["name"],
                     "points": points,
                     "integrated": {
                         "onoise_v_rms": _sig10(onoise_rms),
                         "inoise_v_rms": (_sig10(inoise_rms)
                                          if inoise_rms is not None else None),
                         "band_hz": [round(freqs[0], 6), round(freqs[-1], 6)],
                         "method": (("trapezoid in ln(f) over the swept points (a "
                                     "%s sweep is uniform in ln f)" % card["sweep"])
                                    if geometric else
                                    ("trapezoid in f over the swept points (a lin "
                                     "sweep is uniform in f)")),
                         "note": "the band is the sweep: noise outside it is not in "
                                 "this number, and a coarse sweep can miss a narrow "
                                 "peak"},
                     "bias_point_v": _node_dict(circ, x0),
                     "sources": [mech[k] for k in sorted(mech)],
                     "iterations": iters},
              unit="V/sqrt(Hz) and V rms",
              model="mna_complex_ac_adjoint",
              basis=("the complex MNA matrix of the .ac analysis is solved adjointly "
                     "(A^T y = e_out) once per frequency, so the output response to "
                     "any current injected between two nodes is y[a] - y[b] and every "
                     "source is accounted for in the same solve; densities are "
                     "Nyquist 4kT/R, shot 2q*I and a diode's flicker "
                     "KF*|Id|^AF/f (evaluated at each swept frequency, not held at "
                     "one value)"),
              precision=("exact for the linearised circuit in double precision, so the "
                         "error that matters is the MODEL: thermal 4kT/R, shot 2q*I, "
                         "a level-1 MOSFET's (2/3)gm channel noise, and for a DIODE "
                         "only the flicker KF*|Id|^AF/f - whose f^-1 shape and |Id|^1 "
                         "scaling were fitted to ngspice-47 on a forward-biased "
                         "junction (KF read back 1.00112e-16 against the card's "
                         "1e-16, i.e. +0.11 %). No burst, no avalanche, no induced "
                         "gate noise, and NO flicker on a Q/M/J (those are refused "
                         "above rather than reported without it). The "
                         "integral is a trapezoid over the swept points in the "
                         "sweep's own variable, so it is a band-limited number and "
                         "any sweep under-resolves a peak narrower than its spacing"),
              assumptions={"temp_c": circ.temp_c,
                           "points": len(points),
                           "band_hz": [round(freqs[0], 6), round(freqs[-1], 6)],
                           "output": card["out_text"],
                           "input_source": src["name"],
                           "noiseless_by_construction": ("every V and I source, every "
                                                         "C and every L card"),
                           "defaulted_model_params": circ.defaulted},
              extra={"not_computed": NOT_COMPUTED, "warnings": circ.disabled})

def _sweep_freqs(sweep, npts, f0, f1):
    """The sweep's own frequencies. dec/oct are geometric, lin is uniform.

    One implementation for .ac and .noise on purpose: the same "dec 10" has to
    mean the same 61 points in both, or the two analyses would disagree about
    where the circuit was sampled.
    """
    npts = max(1, int(npts))
    if sweep == "dec":
        count = int(math.ceil(math.log10(f1 / f0) * npts)) + 1
        return [f0 * (f1 / f0) ** (i / max(1.0, (count - 1))) for i in range(count)]
    if sweep == "oct":
        count = int(math.ceil(math.log(f1 / f0, 2) * npts)) + 1
        return [f0 * (f1 / f0) ** (i / max(1.0, (count - 1))) for i in range(count)]
    return [f0 + (f1 - f0) * i / max(1.0, npts - 1) for i in range(npts)]

def _tline_guard(circ, f):
    """Refuse the frequency where a LOSSLESS line's admittance is infinite.

    cot(theta) diverges at every multiple of pi: the ideal line is a perfect
    short there and has no finite answer. Returning the huge number the matrix
    would produce is exactly the plausible-looking wrong answer this module
    exists to not produce. Shared by .ac and .noise, which solve the same
    matrix.
    """
    for el in circ.elements:
        if el["type"] != "T":
            continue
        theta = 2.0 * math.pi * f * el["td"]
        if abs(math.sin(theta)) < 1e-9:
            return err("sweep stopped at %g Hz: %s is at theta = 2*pi*f*TD = %g rad, "
                       "a multiple of pi, where a LOSSLESS line's admittance is "
                       "infinite. The ideal model has no finite answer there; sweep "
                       "off the resonance, or use an engine that models the line's "
                       "loss" % (f, el["name"], theta), "SINGULAR",
                       hint="a real line has loss, which is what makes this frequency "
                            "finite: an O card (a lossy line, implemented here since "
                            "0.0.31) or ngspice are the two ways to get a number "
                            "there")
    return None

def _simulate_builtin(netlist, analysis=None, max_iter=DEFAULT_MAX_ITER, max_points=200,
                       temp_c=None, _temp_sweep=False):
    """Run one analysis. Returns the standard envelope.

    `temp_c` overrides a `.temp` card in the netlist (the caller's stated
    temperature wins), and with neither the junction temperature is TEMP_C.

    A `.temp` card with MORE than one number is a sweep: the deck is re-solved
    once per temperature and every case is returned, instead of reading the
    first number and dropping the rest (which is how a deck answers a question
    nobody asked). `_temp_sweep` is internal and stops the recursion.
    """
    try:
        spec = parse_netlist(netlist)
    except ValueError as exc:
        return err(str(exc), "NETLIST_INVALID",
                   hint="the parser refuses a card it does not implement instead of "
                        "skipping it; see NOT_IMPLEMENTED_CARDS")
    temps = spec["options"].pop("temp_list", [])
    if temp_c is not None:
        try:
            spec["options"]["temp_c"] = float(temp_c)
        except (TypeError, ValueError):
            return err("temp_c must be a number", "ARG_INVALID")
        if len(temps) > 1:
            spec["options"]["temp_c_overrode"] = list(temps)
    if temp_c is None and len(temps) > 1 and not _temp_sweep:
        cases = []
        for t in temps:
            one = _simulate_builtin(netlist, analysis=analysis, max_iter=max_iter,
                                    max_points=max_points, temp_c=t,
                                    _temp_sweep=True)
            if (not isinstance(one, dict)) or one.get("status") != "ok":
                return one
            cases.append({"temp_c": t, "result": one})
        return ok(value={"analysis": "temperature_sweep",
                         "temperatures_c": list(temps),
                         "cases": cases,
                         "note": "the deck was solved once per temperature; each "
                                 "case is a complete answer under 'result', with "
                                 "its own assumptions.temp_c and its own warnings"},
                  unit="mixed", model="mna_newton_dc",
                  basis="one independent solve per entry of the .temp card (the "
                        "temperature changes IS(T), Vt and every junction "
                        "exponential, so a single bias point cannot stand in for "
                        "several)",
                  precision=PRECISION,
                  assumptions={"temperatures_c": list(temps),
                               "analyses": analysis or "auto"})
    try:
        circ = _Circuit(spec)
    except ValueError as exc:
        return err(str(exc), "NETLIST_INVALID")
    if circ.n == 0:
        return err("the netlist has no nodes besides ground", "NETLIST_INVALID")
    if not circ.has_ground:
        # Without a reference node the answer is only defined up to a constant -
        # gmin stepping will happily produce one anyway, which is exactly the
        # plausible-looking wrong number this module exists to not produce.
        return err("the netlist has no ground node: none of %s appears on any card"
                   % (", ".join(GROUND_NAMES),), "NETLIST_INVALID",
                   hint="every circuit needs a reference; without it any solution "
                        "plus an arbitrary constant is equally valid")
    if circ.size > 400:
        return err("circuit has %d unknowns; the dense solver here is meant for "
                   "board-level circuits (<= 400)" % circ.size, "TOO_LARGE",
                   hint="split the netlist, lower lump= on a lossy line (each "
                        "section costs a node and, with series inductance, a branch "
                        "unknown), or use ngspice for a full-size design")
    want = (analysis or (spec["analyses"][-1]["kind"] if spec["analyses"] else "op")).lower()
    if want not in ("op", "tran", "ac", "noise"):
        return err("analysis must be op, tran, ac or noise", "ARG_INVALID")

    if want == "op":
        x, iters, why = _dc(circ)
        if x is None:
            bad = _expression_failure(why)
            if bad is not None:
                return bad
            return err("DC operating point did not converge: %s" % why,
                       "NOT_CONVERGED",
                       hint=_switch_hint(circ) or
                       "check for a floating node, a voltage source loop or a "
                       "device with no DC path to ground")
        return ok(value={"analysis": "op",
                         "nodes_v": _node_dict(circ, x),
                         "elements": _element_report(circ, x),
                         "iterations": iters,
                         "unknowns": circ.size},
                  unit="mixed", model="mna_newton_dc",
                  basis="modified nodal analysis with Newton-Raphson and gmin/source "
                        "stepping; companion models are the textbook ones (Shockley "
                        "diode, Ebers-Moll injection BJT, Shichman-Hodges level 1)",
                  precision=PRECISION,
                  assumptions={"temp_c": circ.temp_c,
                               "vt_v": round(circ.vt, 8),
                               "ground_nodes": list(GROUND_NAMES),
                               "defaulted_model_params": circ.defaulted},
                  extra={"not_computed": NOT_COMPUTED,
                         "warnings": circ.disabled})

    if want == "tran":
        card = next((a for a in spec["analyses"] if a["kind"] == "tran"), None)
        if card is None:
            return err("no .tran card in the netlist", "ARG_REQUIRED",
                       hint="add '.tran <tstep> <tstop>'")
        h = card.get("tmax") or card["tstep"]
        tstop = card["tstop"]
        if h <= 0 or tstop <= 0:
            return err("tran step and stop time must be > 0", "ARG_RANGE")
        # tolerant ceil: 1e-3/1e-5 is 100.00000000000001 in binary floating
        # point, and a plain ceil then runs ONE step PAST tstop (the last point
        # lands at 1.01 ms for a 1 ms run). The relative epsilon only absorbs the
        # representation error, not a real remainder.
        raw_steps = tstop / h
        steps = int(math.ceil(raw_steps - 1e-9 * max(1.0, raw_steps)))
        if steps > 200000:
            return err("%d timesteps is too many for this solver" % steps, "TOO_LARGE",
                       hint="raise tstep or lower tstop")
        for el in circ.elements:
            if el["type"] in ("V", "I"):
                el["_tstop"] = tstop
        # initial conditions
        for el in circ.elements:
            if el["type"] == "C":
                el["_vprev"] = el.get("ic") if card.get("uic") and el.get("ic") else 0.0
            if el["type"] == "L":
                el["_iprev"] = el.get("ic") if card.get("uic") and el.get("ic") else 0.0
            if el["type"] == "T" and el["td"] < h:
                # The history table is indexed by step, so a delay shorter than
                # one step cannot be represented at all: the far end would have to
                # be read from a time that has not been solved yet. Rounding it to
                # one step is what a lumped approximation does, and it moves every
                # reflected edge (hence the ringing frequency) by a whole step, so
                # it is refused rather than warned about.
                return err("%s has TD=%g s, shorter than the timestep %g s: the "
                           "solver reads the far end from a time between two "
                           "steps, which does not exist yet. A delay the "
                           "transient cannot resolve would round to one step and "
                           "move every reflection, so it is refused"
                           % (el["name"], el["td"], h), "ARG_RANGE",
                           hint="use a tstep at or below TD (e.g. tstep=%g), or "
                                "drop the card if the line's delay does not matter "
                                "to the question" % (el["td"] / 10.0))
        x0, iters, why = _dc(circ)
        if x0 is None:
            bad = _expression_failure(why)
            if bad is not None:
                return bad
            return err("transient could not start: the t=0 operating point did not "
                       "converge (%s)" % why, "NOT_CONVERGED",
                       hint=_switch_hint(circ))
        for el in circ.elements:
            if el["type"] == "C":
                a, b = el["nodes"]
                va = x0[circ.i(a) - 1] if circ.i(a) else 0j
                vb = x0[circ.i(b) - 1] if circ.i(b) else 0j
                if not (card.get("uic") and el.get("ic")):
                    el["_vprev"] = (va - vb).real
            if el["type"] == "L":
                el["_iprev"] = x0[circ.branch_idx[el["name"]] - 1].real
            if el["type"] == "T":
                n1, n2, n3, n4 = el["nodes"]
                va = lambda n: (x0[circ.i(n) - 1].real if circ.i(n) else 0.0)  # noqa: E731
                i1 = x0[el["_row"] - 1].real
                # t=0 is the DC point, where a lossless line is a wire: the two
                # port voltages are equal and the two currents are opposite. That
                # is the only state the line can be in before it has carried
                # anything, so it is what every t < TD reads.
                el["_hist"] = [(0.0, va(n1) - va(n2), i1, va(n3) - va(n4), -i1)]
        # The state parked in `_on` came out of the DC solve, which already
        # applied the hysteresis band from the card's initial state; from here on
        # it simply carries from step to step.
        flips = 0
        points = []
        x = x0
        t = 0.0
        total_iter = iters
        stride = max(1, steps // max(1, max_points))
        points.append({"t": 0.0, "nodes_v": _node_dict(circ, x)})
        for k in range(1, steps + 1):
            t = k * h
            for el in circ.elements:
                if el["type"] in ("C", "L"):
                    el["_h"] = h
            xn, it2, why2 = _newton(circ, x, t=t, mode="tran")
            if xn is None:
                bad = _expression_failure(why2)
                if bad is not None:
                    return bad
                return err("transient failed at t=%g s: %s" % (t, why2), "NOT_CONVERGED",
                           hint="backward Euler is unconditionally stable, so this is a "
                                "model/convergence problem, not a timestep problem")
            total_iter += it2
            x = xn
            # The latched state can be one step stale: ask the new solution
            # whether it agrees, and if it does not, flip and re-solve THIS step.
            # Bounded on purpose - a switch whose control sits on its threshold
            # makes the circuit bistable, and drawing a curve through that point
            # would be this solver's choice rather than the deck's answer.
            if circ.switches:
                settled = False
                for _attempt in range(9):
                    moved = False
                    for el in circ.switches:
                        want = circ.switch_wanted(
                            el, circ._switch_control(el, x))
                        if want != el["_on"]:
                            el["_on"] = want
                            moved = True
                    if not moved:
                        settled = True
                        break
                    flips += 1
                    xn, it3, why3 = _newton(circ, x, t=t, mode="tran")
                    if xn is None:
                        bad = _expression_failure(why3)
                        if bad is not None:
                            return bad
                        return err("transient failed at t=%g s while settling %s: %s"
                                   % (t, ", ".join(e["name"]
                                                   for e in circ.switches), why3),
                                   "NOT_CONVERGED")
                    total_iter += it3
                    x = xn
                if not settled:
                    return err("the switch state at t=%g s did not settle in 8 "
                               "flips (%s). A control sitting on its threshold "
                               "makes the circuit bistable, and a curve drawn "
                               "across that point would be this solver's choice, "
                               "not the deck's answer"
                               % (t, ", ".join(e["name"] for e in circ.switches)),
                               "NOT_CONVERGED",
                               hint="give the model a hysteresis (VH= or IH=) or "
                                    "move the control away from the threshold")
            for el in circ.elements:
                if el["type"] == "C":
                    a, b = el["nodes"]
                    va = x[circ.i(a) - 1] if circ.i(a) else 0j
                    vb = x[circ.i(b) - 1] if circ.i(b) else 0j
                    el["_vprev"] = (va - vb).real
                if el["type"] == "L":
                    el["_iprev"] = x[circ.branch_idx[el["name"]] - 1].real
                if el["type"] == "T":
                    el["_hist"].append((t,) + _tline_ports(circ, el, x, t))
            if k % stride == 0 or k == steps:
                points.append({"t": round(t, 12), "nodes_v": _node_dict(circ, x)})
        value = {"analysis": "tran", "points": points,
                 "timestep_s": h, "steps": steps,
                 "elements_final": _element_report(circ, x, t, mode="tran"),
                 "iterations": total_iter}
        if circ.switches:
            # how often a latched state had to be flipped mid-step: zero is the
            # normal case (the control moved less than a step's worth) and a big
            # number is the deck telling you the edge is faster than the step
            value["switch_flips"] = flips
        return ok(value=value,
                  unit="mixed", model="mna_backward_euler",
                  basis="backward Euler companion models (C -> G=C/h in parallel with "
                        "I=C/h*v_prev, L -> R=L/h in series with V=L/h*i_prev) with "
                        "Newton at every timestep",
                  precision=("backward Euler is FIRST order: a step of %g s has a local "
                             "truncation error O(h^2) and global O(h), so a fast edge "
                             "needs a step well below the edge time. Nothing here "
                             "adapts the step - the step you asked for is the step used"
                             % h),
                  assumptions={"temp_c": circ.temp_c, "timestep_s": h,
                               "uic": bool(card.get("uic")),
                               "printing_every": stride,
                               "defaulted_model_params": circ.defaulted},
                  extra={"not_computed": NOT_COMPUTED, "warnings": circ.disabled})

    if want == "noise":
        card = next((a for a in spec["analyses"] if a["kind"] == "noise"), None)
        if card is None:
            return err("no .noise card in the netlist", "ARG_REQUIRED",
                       hint="add '.noise V(out) <source> dec <n> <fstart> <fstop>'")
        return _noise_analysis(circ, card, max_points)

    # AC
    card = next((a for a in spec["analyses"] if a["kind"] == "ac"), None)
    if card is None:
        return err("no .ac card in the netlist", "ARG_REQUIRED",
                   hint="add '.ac dec <points> <fstart> <fstop>'")
    f0, f1 = card["fstart"], card["fstop"]
    if f0 <= 0 or f1 <= f0:
        return err("ac sweep needs 0 < fstart < fstop", "ARG_RANGE")
    freqs = _sweep_freqs(card["sweep"], card["n"], f0, f1)
    asked = len(freqs)
    if asked > max_points:
        # Truncating a sweep is a silent lie unless it is said out loud: the last
        # reported point is not the end of the sweep, so a resonance inside the
        # kept range can be mistaken for the resonance of the circuit.
        circ.disabled.append(
            "the .ac sweep asked for %d points but only the first %d were solved "
            "(up to %.6g Hz, not fstop %.6g Hz) - raise max_points or lower the "
            "sweep density" % (asked, max_points, freqs[max_points - 1], f1))
        freqs = freqs[:max_points]
    if not any(el.get("ac") for el in circ.elements if el["type"] in ("V", "I")):
        circ.disabled.append(
            "no V/I source carries an AC specification, so nothing drives the .ac "
            "sweep and every node reads 0 (this is SPICE's own rule: write "
            "'V1 1 0 AC 1' - the bare 'V1 1 0 3' is a DC value only)")
    # A clipped block's small-signal gain is the gain inside the band and zero
    # outside it, and a mult block's is the OTHER input's value: both are decided
    # by where the operating point sits. 0.0.41 linearises them AT that point
    # (assemble() reads the bias out of x_prev, the same way it already does for a
    # diode), so the sweep is real - but an output that sits on a bound carries no
    # small signal at all, and a caller who sees a flat 0 deserves to be told why
    # instead of guessing that the deck is broken.
    x0, iters, why = _dc(circ)
    if x0 is None:
        bad = _expression_failure(why)
        if bad is not None:
            return bad
        return err("AC needs a DC operating point and it did not converge (%s)" % why,
                   "NOT_CONVERGED",
                   hint="AC is a small-signal linearisation AT the bias point; without "
                        "a bias there is no AC answer")
    for el in circ.elements:
        if el["type"] != "A" or el.get("kind") not in ("LIMIT", "MULT"):
            continue
        idxs = [circ.i(n) for n in el["inputs"]]
        vins0 = [x0[k - 1].real if k else 0.0 for k in idxs]
        f0, slopes = _xspice_ac_slopes(el, vins0)
        if el["kind"] == "LIMIT" and all(s == 0.0 for s in slopes):
            circ.disabled.append(
                "%s: this limit block is on or past its %s limit at the bias "
                "point (in=%g V, out=%g V), so its small-signal gain is 0 and its "
                "output carries no AC at any frequency - a flat trace is the bias "
                "showing through, not a broken deck"
                % (el["name"], "upper" if f0 == el["hi"] else "lower",
                   vins0[0], f0))
        elif el["kind"] == "MULT" and any(s == 0.0 for s in slopes):
            circ.disabled.append(
                "%s: at the bias point one of this mult block's factors is 0, so "
                "the small-signal gain of the whole product is 0 in every direction "
                "(a multiplier has no gain around zero)" % el["name"])
    points = []
    tline_warned = set()
    for f in freqs:
        for el in circ.elements:
            if el["type"] in ("C", "L", "T", "K"):
                el["_f"] = f
        bad = _tline_guard(circ, f)
        if bad is not None:
            return bad
        for el in circ.elements:
            if el["type"] != "T":
                continue
            s = abs(math.sin(2.0 * math.pi * f * el["td"]))
            if s < 1e-3 and el["name"] not in tline_warned:
                tline_warned.add(el["name"])
                circ.disabled.append(
                    "%s: at %g Hz the phase is %.3g rad from a multiple of pi, "
                    "where the lossless admittance goes to infinity; the numbers "
                    "near that frequency are ill-conditioned (the loss of a real "
                    "line is what keeps them finite)" % (el["name"], f, s))
        A, b = circ.assemble(x0, mode="ac", gmin=0.0)
        try:
            x = _solve_dense(A, b)
        except ZeroDivisionError as exc:
            return err("AC matrix is singular at %g Hz: %s" % (f, exc), "SINGULAR",
                       hint="a floating node or a voltage-source loop has no AC "
                            "definition; check the DC netlist first")
        points.append({"freq_hz": round(f, 6), "nodes_v": _node_dict(circ, x, True)})
    return ok(value={"analysis": "ac", "points": points,
                     "bias_point_v": _node_dict(circ, x0)},
              unit="mixed", model="mna_complex_ac",
              basis="complex MNA at each frequency, small-signal linearised at the DC "
                    "operating point (the standard SPICE .ac definition); an XSPICE "
                    "limit block contributes the gain inside its band and 0 outside "
                    "it, a mult block contributes the other inputs' bias values "
                    "(0.0.41, measured against ngspice-47); capacitors "
                    "j*w*C, inductors solved as a branch with v - j*w*L*i = 0",
              precision="linear and exact in double precision for the linearised "
                        "circuit; for a nonlinear device the answer is only as good as "
                        "the bias point, and no junction or gate capacitance is modelled "
                        "unless you add it as a lumped C",
              assumptions={"temp_c": circ.temp_c, "points": len(points),
                           "defaulted_model_params": circ.defaulted},
              extra={"not_computed": NOT_COMPUTED, "warnings": circ.disabled})


# ---------------------------------------------------------------------------
# External engine delegation
# ---------------------------------------------------------------------------
# Why this exists instead of "just vendor a full SPICE": a full engine is C
# (ngspice ~500k lines) or a numpy/scipy dependency (ahkab, PySpice). This
# server's contract is *zero runtime dependencies*, so the builtin subset stays
# the default and never has to be installed. What CAN be done without breaking
# that contract is to hand the same netlist to ngspice when the user already has
# it: no bundled binary, no wheel, no import - one subprocess, and the reply
# says which engine answered and whether that engine's path was verified here.
#
# Two rules that make this honest rather than "probably works":
#   * engine="ngspice" with no binary is an ERROR (ENGINE_NOT_FOUND). It never
#     silently falls back to the subset - a caller who asked for the reference
#     implementation must not receive a level-1 MOSFET answer instead.
#   * cross_check=True runs BOTH engines and reports the largest node-voltage
#     difference. That is the only way the delegation itself gets audited, and it
#     is exactly how a wrong raw-file parse would show up.

ENGINE_CHOICES = ("auto", "builtin", "ngspice")

# Tests (and an unusual install) can set this to an argv prefix, e.g.
#   [sys.executable, "/path/to/fake_ngspice.py"]
NGSPICE_CMD = None

NGSPICE_ENV = "PCB_AGENT_NGSPICE"

NGSPICE_TIMEOUT_S = 120.0

# Versions this repository has actually run the gold netlists against, with the
# measured agreement recorded in docs/21 and asserted in tests/test_spice_engine.py.
# Presence of a binary is not evidence; being on this list is.
VERIFIED_ENGINES = ("ngspice-47", "ngspice-41")

ENGINE_VERIFICATION = (
    "two engine builds were run against this module's gold netlists - ngspice-47 "
    "(Windows, MSYS2 build) and ngspice-41 (Windows, conda-forge build). Against the "
    "builtin solver, 47 gave: 10 V 1k/2k divider 5e-9 V apart, diode 0.35 mV, BJT "
    "17.8 mV (model, not arithmetic), RC step and RLC AC bit-identical. The two "
    "engines were then run side by side on the same decks: .op (divider, diode, BJT) "
    "and .ac (RLC) agree to 0.0 V, .tran disagrees by at most 1.24e-4 V on a 1 V step "
    "(1.2e-4 relative, one time point - the two builds step differently, so the "
    "comparison is of their own grids). Any other engine version is NOT covered by "
    "that evidence and reports verified_against_engine:false"
)

ENGINE_NOTE = (
    "engine='auto' uses ngspice when a binary is found (PCB_AGENT_NGSPICE or PATH) "
    "and the builtin stdlib solver otherwise; engine='ngspice' REFUSES rather than "
    "fall back, because a caller who asked for the reference implementation must not "
    "get a level-1 MOSFET answer in its place. engine='ngspice' also accepts the "
    "engine shipped in vendor/ngspice (GPL, called as a separate process) when no "
    "system install is found; engine='auto' deliberately does NOT consider it, so "
    "what 'auto' means on a given machine does not change when the bundle is added"
)

BUNDLED_NOTE = (
    "vendor/ngspice/ holds an unmodified ngspice-47 Windows build (GPL-2.0, see "
    "vendor/ngspice/NOTICE.md) as a SEPARATE PROGRAM: it is spawned as a subprocess "
    "and never linked, so this module stays MIT and stays import-free. It is reached "
    "by engine='ngspice' only after PCB_AGENT_NGSPICE, PATH and an installed copy "
    "have all come up empty"
)


def find_ngspice(allow_bundled=False):
    """Return an argv prefix for ngspice, or None. Never raises.

    `allow_bundled` adds vendor/ngspice as a candidate between PATH and the
    install-directory scan. It is OFF by default so that the answer to "what does
    this machine have installed" does not change, and so that engine='auto' keeps
    meaning exactly what it meant before the bundle existed. The bundle ranks
    above the install-directory scan but BELOW PATH and below
    PCB_AGENT_NGSPICE: those are explicit statements about this machine, and an
    explicit answer must not be overridden by a vendored binary. (0.0.112
    corrected this paragraph: it used to claim the bundle ranked above PATH and
    the install scan, which the code has never done. The behaviour is unchanged -
    only the description was wrong.)
    """
    if NGSPICE_CMD:
        return list(NGSPICE_CMD)
    import os
    env = (os.environ.get(NGSPICE_ENV) or "").strip().strip('"')
    if env:
        # A plain path may contain spaces, so it is taken whole when it names a
        # file; otherwise the value is split like a command line (an argv prefix
        # is how the tests inject the stand-in engine).
        if os.path.isfile(env):
            return [env]
        import shlex
        argv = [a for a in shlex.split(env, posix=True) if a]
        if argv:
            return argv
    import shutil
    for name in ("ngspice", "ngspice.exe", "ngspice_con", "ngspice_con.exe"):
        found = shutil.which(name)
        if found:
            return [found]
    if allow_bundled:
        bundled = find_bundled_ngspice()
        if bundled:
            return bundled
    # A binary unpacked by hand (as on this machine) is not on PATH. Look in the
    # two places a Windows install actually ends up, newest version first, so an
    # install does not silently need an environment variable to be seen.
    import glob
    roots = []
    for base in (os.environ.get("LOCALAPPDATA"),
                 os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)")):
        if base:
            roots.append(os.path.join(base, "ngspice*"))
    cands = []
    for pat in roots:
        cands.extend(glob.glob(pat))
    cands.sort(reverse=True)
    for d in cands:
        for name in ("ngspice_con.exe", "ngspice.exe"):
            p = os.path.join(d, "bin", name)
            if os.path.isfile(p):
                return [p]
            if os.path.isfile(os.path.join(d, name)):
                return [os.path.join(d, name)]
    return None


def bundled_ngspice_dir():
    """Absolute path of vendor/ngspice (whether or not it holds anything)."""
    import os
    here = os.path.dirname(os.path.abspath(__file__))
    up3 = os.path.join(here, os.pardir, os.pardir, os.pardir)
    return os.path.normpath(os.path.join(up3, "vendor", "ngspice"))

def find_bundled_ngspice():
    """Return an argv prefix for the engine in vendor/ngspice, or None.

    Never raises, never consults PATH: this is only the copy that ships with the
    repository. Absent - or a platform where a Windows .exe cannot run, which is
    how "the bundle is not part of this installation" looks - returns None.
    """
    import os
    import sys
    if not sys.platform.startswith("win"):
        return None
    path = os.path.join(bundled_ngspice_dir(), "bin", "ngspice_con.exe")
    return [path] if os.path.isfile(path) else None

def _engine_version(argv):
    import subprocess
    try:
        p = subprocess.run(list(argv) + ["--version"], capture_output=True,
                           timeout=20.0)
    except Exception:
        return None
    text = ((p.stdout or b"") + (p.stderr or b"")).decode("utf-8", "replace")
    first = None
    for line in text.splitlines():
        line = line.strip().lstrip("*").strip()
        if not line:
            continue
        if first is None:
            first = line
        # ngspice prints a banner: the informative line is the one naming itself
        if "ngspice" in line.lower():
            return line[:120]
    return first[:120] if first else None


def _parse_raw(data):
    """Parse an ngspice rawfile (ASCII 'Values:' or binary 'Binary:').

    Returns {"plotname", "flags", "complex", "vars": [names], "types": [...],
    "points": [[value, ...], ...]} where a complex plot stores values as
    (re, im) pairs flattened. Raises ValueError with the reason.
    """
    import struct
    if isinstance(data, str):
        data = data.encode("utf-8", "replace")
    marker = None
    for cand in (b"Values:\n", b"Values:\r\n", b"Binary:\n", b"Binary:\r\n"):
        idx = data.find(cand)
        if idx >= 0 and (marker is None or idx < marker[0]):
            marker = (idx, cand)
    if marker is None:
        raise ValueError("no 'Values:' or 'Binary:' section - this is not a rawfile")
    idx, cand = marker
    head = data[:idx].decode("utf-8", "replace")
    body = data[idx + len(cand):]

    meta = {}
    var_lines = []
    in_vars = False
    for line in head.splitlines():
        low = line.strip().lower()
        if low.startswith("variables:"):
            in_vars = True
            continue
        if in_vars:
            parts = line.split()
            if len(parts) >= 3:
                var_lines.append((parts[1], parts[2]))
            continue
        if ":" in line:
            k, v = line.split(":", 1)
            meta[k.strip().lower()] = v.strip()
    if not var_lines:
        raise ValueError("rawfile has no Variables: block")
    try:
        nvar = int(meta.get("no. variables", len(var_lines)))
        npts = int(meta.get("no. points", "0"))
    except ValueError:
        raise ValueError("rawfile header has non-integer No. Variables / No. Points")
    flags = (meta.get("flags") or "").lower()
    is_complex = "complex" in flags

    names = [v[0] for v in var_lines]
    types = [v[1] for v in var_lines]
    mult = 2 if is_complex else 1

    points = []
    used = len(data)          # ASCII: the token stream cannot be counted exactly
    if cand.startswith(b"Binary"):
        need = npt_unused = None
        size = 8 * nvar * mult
        if npts <= 0:
            npts = len(body) // size
        if len(body) < size * npts:
            raise ValueError("binary rawfile truncated: %d bytes for %d points x %d "
                             "variables" % (len(body), npts, nvar))
        used = idx + len(cand) + size * npts
        vals = struct.unpack("<%dd" % (nvar * mult * npts), body[:size * npts])
        step = nvar * mult
        for p in range(npts):
            row = list(vals[p * step:(p + 1) * step])
            if is_complex:
                row = [(row[2 * i], row[2 * i + 1]) for i in range(nvar)]
            points.append(row)
    else:
        # ASCII rawfile, measured on ngspice-47 (docs/43): one variable per LINE,
        # the point index prepended to the first line of each record, and a
        # complex value written "re,im" - two numbers in one cell. Splitting on
        # whitespace alone therefore merged "1.0,0.0" into a single token, which
        # read as "1 record but the header says 3 points" for every AC plot; both
        # "," and whitespace separate cells, and a record is 1 + nvar*mult of them.
        # The body also runs to the end of the file on a multi-plot rawfile
        # (.noise writes two plots), so the scan stops at the first line that is
        # not a value line and reports that offset as bytes_used - otherwise the
        # NEXT plot's "No. Points" would be compared against this plot's tokens.
        per_point = 1 + nvar * mult   # the point index comes first
        # An ASCII rawfile is pure ASCII, so a character offset IS a byte offset;
        # that is what lets bytes_used stay meaningful for the plot walk.
        text = body.decode("utf-8", "replace")
        toks = []
        stop = None
        pos = 0
        for line in text.splitlines(True):
            cells = [c for c in re.split(r"[,\s]+", line) if c]
            if cells:
                try:
                    for c in cells:
                        float(c)
                except ValueError:
                    stop = pos
                    break
                toks.extend(cells)
            pos += len(line)
        if stop is None:
            used = idx + len(cand) + len(body)
        else:
            used = idx + len(cand) + stop
        if len(toks) < per_point:
            raise ValueError("ASCII rawfile has %d numbers, need at least %d"
                             % (len(toks), per_point))
        count = len(toks) // per_point
        if npts and count != npts:
            raise ValueError("ASCII rawfile has %d records but the header says %d "
                             "points" % (count, npts))
        for p in range(count):
            chunk = toks[p * per_point:(p + 1) * per_point][1:]
            row = [float(t) for t in chunk]
            if is_complex:
                row = [(row[2 * i], row[2 * i + 1]) for i in range(nvar)]
            points.append(row)

    return {"plotname": meta.get("plotname") or "", "flags": flags,
            "complex": is_complex, "vars": names, "types": types,
            "points": points, "bytes_used": used}


_NODE_PREFIXES = ("v(", "V(")


def _raw_plots(data):
    """Every plot in one ngspice rawfile, oldest first.

    A .noise run writes TWO plots back to back (the spectral densities, then the
    integrated totals), and _parse_raw reads one plot at a time - so the file is
    walked plot by plot using the byte count each parse reports. An ASCII
    rawfile reports "the whole file", which ends the walk after one plot: an
    untrustworthy split is not attempted, and the caller says so.
    """
    if isinstance(data, str):
        data = data.encode("utf-8", "replace")
    out = []
    off = 0
    while off < len(data):
        try:
            p = _parse_raw(data[off:])
        except ValueError:
            break
        out.append(p)
        used = p.get("bytes_used") or 0
        if used <= 0 or off + used >= len(data):
            break
        off += used
    return out

def _raw_nodes(raw):
    """Pull v(node) vectors out of a parsed rawfile.

    Returns (nodes, scales) where nodes maps node name -> list of values and
    scales maps "time"/"frequency" -> list of the scale vector.
    """
    names = raw["vars"]
    scales = {}
    nodes = {}
    for i, name in enumerate(names):
        low = name.lower()
        if low in ("time", "frequency", "freq"):
            # In a complex (AC) rawfile EVERY vector is a (re, im) pair, the
            # frequency axis included - its imaginary part is always 0, and
            # taking the tuple as the frequency would put a tuple where a float
            # is expected.
            scales["time" if low == "time" else "frequency"] = \
                [(v[0] if isinstance(v, tuple) else v) for v in
                 [pt[i] for pt in raw["points"]]]
            continue
        if low.startswith("v(") and low.endswith(")"):
            node = name[2:-1]
            nodes[node] = [pt[i] for pt in raw["points"]]
    return nodes, scales


def _last_analysis_card(netlist):
    """Textual LAST .op/.tran/.ac card, for decks this module cannot parse.

    ngspice's own convention is the first card; this module's is the last, so the
    deck is filtered down to exactly one card and the difference never reaches the
    engine. Only used when parse_netlist refused the deck (X/.subckt, B, T, ...).
    """
    found = None
    for ln in str(netlist or "").splitlines():
        low = ln.strip().lower()
        if low.startswith(".op"):
            found = "op"
        elif low.startswith(".tran"):
            found = "tran"
        elif low.startswith(".ac"):
            found = "ac"
        elif low.startswith(".noise"):
            found = "noise"
    return found


def _tran_step_textual(netlist):
    """The second token of the last .tran card, or None. Tolerant on purpose."""
    step = None
    for ln in str(netlist or "").splitlines():
        parts = ln.strip().split()
        if parts and parts[0].lower() == ".tran":
            if len(parts) >= 3:
                step = parts[2]
    if step is None:
        return None
    try:
        return _value(step)
    except Exception:
        return None


def _deck_for_ngspice(netlist, want):
    """The user's netlist, made runnable by ngspice, with the ANALYSIS forced.

    Only the analysis the caller asked for is kept, so a deck with several
    cards cannot answer a different question than the one that was asked.
    """
    lines = [ln.rstrip() for ln in str(netlist or "").splitlines()]
    keep = []
    for ln in lines:
        low = ln.strip().lower()
        if not low or low.startswith("*"):
            keep.append(ln)
            continue
        # ".ends" terminates a .subckt and must survive: only the deck's own
        # .end is dropped (a prefix test would delete the subcircuit's body end).
        if low == ".end" or low.startswith(".end "):
            continue
        if low.startswith((".op", ".tran", ".ac", ".dc", ".four", ".noise")):
            continue      # re-added below, once, in the order that was asked for
        keep.append(ln)
    analyses = [ln.strip() for ln in lines
                if ln.strip().lower().startswith((".op", ".tran", ".ac", ".noise"))]
    picked = [a for a in analyses if a.lower().startswith("." + want)]
    if not picked:
        picked = [a for a in analyses if a.lower().startswith("." + want[:2])]
    if not picked:
        return None
    return "\n".join(["* pcb-agent-mcp deck"] + keep + picked + [".end"]) + "\n"


def _run_ngspice(argv, netlist, want, timeout_s=NGSPICE_TIMEOUT_S):
    """Run one analysis in a temp dir. Returns (raw_or_None, err_envelope_or_None)."""
    import os
    import subprocess
    import tempfile
    deck = _deck_for_ngspice(netlist, want)
    if deck is None:
        # No .op/.tran/.ac card survived the filter: make one from scratch is not
        # something to guess at (which timestep? how many decades?).
        return None, err("the netlist has no .%s card for ngspice to run" % want,
                         "ARG_REQUIRED",
                         hint="ngspice reads the analysis from the deck; add the card")
    tmp = tempfile.mkdtemp(prefix="pcb_spice_")
    try:
        deck_path = os.path.join(tmp, "deck.cir")
        raw_path = os.path.join(tmp, "out.raw")
        log_path = os.path.join(tmp, "ngspice.log")
        with open(deck_path, "w", encoding="ascii", errors="replace") as fh:
            fh.write(deck)
        cmd = list(argv) + ["-b", "-r", raw_path, "-o", log_path, deck_path]
        env = None
        # An unpacked ngspice keeps its XSPICE code models in ../lib/ngspice and
        # was compiled with an absolute default that does not exist on this
        # machine. Without them T (transmission line) cards and every XSPICE
        # device fail to load - a run that "works" but cannot parse half the
        # deck is exactly the kind of partial answer this module refuses.
        # Measured on ngspice-47 (docs/43): SPICE_LIB_DIR REPLACES the base the
        # start-up file is looked up from, so setting it on a tree that ships its
        # own share/ngspice/scripts/spinit turns a working engine into "can't find
        # the initialization file spinit" + MIF-ERROR on every A card. A tree with
        # an init file therefore needs nothing from us; only a tree without one
        # gets the pointer.
        if not os.environ.get("SPICE_LIB_DIR"):
            here = os.path.dirname(os.path.abspath(argv[0]))
            root = os.path.normpath(os.path.join(here, os.pardir))
            lib = os.path.join(root, "lib", "ngspice")
            init = os.path.join(root, "share", "ngspice", "scripts", "spinit")
            if os.path.isdir(lib) and not os.path.isfile(init):
                env = dict(os.environ)
                env["SPICE_LIB_DIR"] = lib
        try:
            proc = subprocess.run(cmd, cwd=tmp, capture_output=True,
                                  timeout=timeout_s, env=env)
        except subprocess.TimeoutExpired:
            return None, err("ngspice did not finish within %g s" % timeout_s,
                             "ENGINE_TIMEOUT",
                             hint="raise the deck's own limits or shrink the sweep; "
                                  "the run was killed, so there is no partial answer")
        log = ""
        try:
            with open(log_path, "r", encoding="utf-8", errors="replace") as fh:
                log = fh.read()
        except OSError:
            pass
        if proc.returncode != 0:
            tail = (log or (proc.stderr or b"").decode("utf-8", "replace"))
            tail = " ".join(tail.split())[-700:]
            return None, err("ngspice exited with %d" % proc.returncode,
                             "ENGINE_FAILED",
                             hint="the deck was passed through unchanged; the log tail "
                                  "is the engine's own complaint",
                             extra={"engine": "ngspice", "exit_code": proc.returncode,
                                    "log_tail": tail})
        try:
            with open(raw_path, "rb") as fh:
                data = fh.read()
        except OSError:
            return None, err("ngspice wrote no rawfile", "ENGINE_OUTPUT_INVALID",
                             hint="a run that exits 0 without a result is not an answer",
                             extra={"engine": "ngspice", "log_tail":
                                    " ".join(log.split())[-500:]})
        try:
            raw = _parse_raw(data)
        except ValueError as exc:
            return None, err("could not parse ngspice's rawfile: %s" % exc,
                             "ENGINE_OUTPUT_INVALID",
                             hint="the rawfile layout is documented and stable; a parse "
                                  "failure means the answer would be a guess, so it is "
                                  "refused",
                             extra={"engine": "ngspice",
                                    "raw_bytes": len(data)})
        if want == "noise":
            # the spectra are in the first plot, the integrated totals in the
            # second; both are the engine's own numbers
            raw["all_plots"] = _raw_plots(data)
        return (raw, log), None
    finally:
        import shutil as _shutil
        _shutil.rmtree(tmp, ignore_errors=True)


def _external_envelope(raw, want, argv, tstep_hint=None, cross=None,
                       parse_note=None):
    nodes, scales = _raw_nodes(raw)
    engine = {"engine": "ngspice", "path": argv[0], "argv": list(argv),
              "version": _engine_version(argv),
              "plotname": raw["plotname"]}
    version = (engine.get("version") or "")
    verified = any(tag in version.lower() for tag in VERIFIED_ENGINES)
    extra = {
        "engine_used": "ngspice",
        "engine": engine,
        "elements": None,
        "elements_note": ("the per-element report is produced by the builtin solver "
                          "from its own companion models; with an external engine the "
                          "answer is the engine's node voltages, and re-deriving "
                          "element currents here would be this server's arithmetic "
                          "presented as the engine's result"),
        "not_computed": NOT_COMPUTED,
        "verified_against_engine": verified,
        "engine_verification": (ENGINE_VERIFICATION if verified else
                                "this engine's version (%s) is not one this "
                                "repository has run the gold netlists against, so "
                                "nothing here vouches for its answers"
                                % (version or "unknown")),
        "engine_note": ENGINE_NOTE,
        "netlist_parsed": parse_note is None,
    }
    if parse_note is not None:
        # The deck went to the engine unchanged; what this module could not do is
        # build its own model of it. Say so instead of implying a full parse.
        extra["netlist_parse_note"] = (
            "this module's own parser refused the deck (%s), so the deck was handed "
            "to ngspice unchanged and the node list below is read from the engine's "
            "rawfile; there is no per-element report and no builtin cross-check"
            % parse_note)
    if cross:
        extra["cross_check"] = cross
    assumptions = {"engine": "ngspice (external process, deck passed through "
                             "unchanged except for the analysis cards)",
                   "rawfile": "parsed from the engine's own raw output"}
    basis = ("the netlist is handed to ngspice unchanged (only the analysis cards "
             "are filtered to the one requested) and ngspice's rawfile is parsed "
             "directly; this module adds no model of its own on this path")
    precision = ("the numbers are ngspice's, so the model error is ngspice's model "
                 "error; what this server guarantees here is only the plumbing: the "
                 "deck it wrote, the card it asked for, and that the parse of the "
                 "rawfile round-trips into the same node names")

    if want == "op":
        first = {name: _round_v(vals[0]) for name, vals in nodes.items()}
        return ok(value={"analysis": "op", "nodes_v": first},
                  unit="mixed", model="external_ngspice",
                  basis=basis, precision=precision, assumptions=assumptions,
                  extra=extra)

    if want == "tran":
        t = scales.get("time") or []
        pts = []
        for k in range(min(len(t), min([len(v) for v in nodes.values()] or [0]) or 0)):
            pts.append({"t": round(float(t[k]), 12),
                        "nodes_v": {n: _round_v(v[k]) for n, v in nodes.items()}})
        if not pts:
            return err("ngspice's transient rawfile carries no v(node) vector",
                       "ENGINE_OUTPUT_INVALID",
                       hint="an answer with no nodes is not an answer")
        return ok(value={"analysis": "tran", "points": pts,
                         "timestep_s": tstep_hint,
                         "points_returned": len(pts)},
                  unit="mixed", model="external_ngspice",
                  basis=basis, precision=precision, assumptions=assumptions,
                  extra=extra)

    if want == "noise":
        names = [str(x).lower() for x in raw["vars"]]
        for need in ("frequency", "onoise_spectrum"):
            if need not in names:
                return err("ngspice's noise rawfile carries no %r vector" % need,
                           "ENGINE_OUTPUT_INVALID",
                           hint="the plot names are documented (frequency, "
                                "onoise_spectrum, inoise_spectrum); a rename means "
                                "the parse below would be reading the wrong column")
        fi, oi = names.index("frequency"), names.index("onoise_spectrum")
        ii = names.index("inoise_spectrum") if "inoise_spectrum" in names else None
        pts = []
        for row in raw["points"]:
            def real(v):
                return float(v[0]) if isinstance(v, tuple) else float(v)
            pts.append({"freq_hz": round(real(row[fi]), 6),
                        "onoise_v_per_sqrt_hz": abs(real(row[oi])),
                        "inoise_v_per_sqrt_hz": (abs(real(row[ii]))
                                                 if ii is not None else None)})
        if not pts:
            return err("ngspice's noise rawfile has no frequency points",
                       "ENGINE_OUTPUT_INVALID")
        totals = {}
        for p in (raw.get("all_plots") or [])[1:]:
            lower = [str(x).lower() for x in p["vars"]]
            if not p["points"]:
                continue
            row = p["points"][0]
            for k, nm in enumerate(lower):
                if nm.startswith("v(") and nm.endswith("_total)"):
                    val = row[k]
                    totals[nm[2:-1]] = abs(float(val[0]) if isinstance(val, tuple)
                                           else float(val))
        return ok(value={"analysis": "noise", "points": pts,
                         "integrated": ({"onoise_v_rms": totals.get("onoise_total"),
                                         "inoise_v_rms": totals.get("inoise_total"),
                                         "method": "ngspice's own integration over "
                                                   "its sweep",
                                         "note": "these are the engine's numbers; its "
                                                 "input-referred total is not defined "
                                                 "the same way as the builtin's (it "
                                                 "divides the output integral by one "
                                                 "gain), so compare the spectra"
                                         } if totals else None)},
                  unit="V/sqrt(Hz) and V rms", model="external_ngspice",
                  basis=basis, precision=precision, assumptions=assumptions,
                  extra=extra)

    # ac
    f = scales.get("frequency") or []
    pts = []
    for k in range(min(len(f), min([len(v) for v in nodes.values()] or [0]) or 0)):
        row = {}
        for n, vals in nodes.items():
            val = vals[k]
            if isinstance(val, tuple):
                re_, im_ = val
                row[n] = {"re": _round_v(re_), "im": _round_v(im_),
                          "mag": _round_v(math.hypot(re_, im_)),
                          "phase_deg": round(math.degrees(math.atan2(im_, re_)), 6)}
            else:
                row[n] = {"re": _round_v(val), "im": 0.0,
                          "mag": _round_v(abs(val)), "phase_deg": 0.0}
        pts.append({"freq_hz": round(float(f[k]), 6), "nodes_v": row})
    if not pts:
        return err("ngspice's AC rawfile carries no v(node) vector",
                   "ENGINE_OUTPUT_INVALID",
                   hint="an answer with no nodes is not an answer")
    return ok(value={"analysis": "ac", "points": pts},
              unit="mixed", model="external_ngspice",
              basis=basis, precision=precision, assumptions=assumptions,
              extra=extra)


def _round_v(v):
    if isinstance(v, tuple):
        return (round(v[0], 9), round(v[1], 9))
    return round(float(v), 9)


def _cross_check(builtin, external, want):
    """Largest node-voltage disagreement between the two engines."""
    bval = (builtin.get("value") or {})
    eval_ = (external.get("value") or {})

    def pairs(b, e):
        out = []
        for node, bv in (b or {}).items():
            ev = (e or {}).get(node)
            if isinstance(bv, (int, float)) and isinstance(ev, (int, float)):
                out.append((node, abs(float(bv) - float(ev))))
        return out

    if want == "noise":
        bp = bval.get("points") or []
        ep = eval_.get("points") or []
        n = min(len(bp), len(ep))
        worst = 0.0
        for k in range(n):
            b = bp[k].get("onoise_v_per_sqrt_hz")
            e = ep[k].get("onoise_v_per_sqrt_hz")
            if isinstance(b, (int, float)) and isinstance(e, (int, float)):
                scale = max(abs(b), abs(e))
                if scale > 0.0:
                    worst = max(worst, abs(b - e) / scale)
        if n == 0:
            return {"compared_points": 0, "max_rel_diff_onoise": None,
                    "note": "no noise points in common between the two engines, so "
                            "nothing was compared - this is NOT a pass"}
        note = ("relative difference of the OUTPUT noise spectral density at every "
                "frequency both engines report; the mechanisms are the same "
                "(4kT/R, 2q*I) but the bias point is each engine's own, so a small "
                "number corroborates the transfer, not the device model. The "
                "input-referred totals are NOT compared: ngspice's integral divides "
                "by a single gain while the builtin divides per frequency")
        return {"compared_points": n,
                "max_rel_diff_onoise": round(worst, 12),
                "note": note}

    if want == "op":
        diffs = pairs(bval.get("nodes_v"), eval_.get("nodes_v"))
    elif want == "tran":
        bp = bval.get("points") or [{}]
        ep = eval_.get("points") or [{}]
        diffs = pairs(bp[0].get("nodes_v"), ep[0].get("nodes_v"))
    else:
        bp = bval.get("points") or [{}]
        ep = eval_.get("points") or [{}]
        b0 = bp[0].get("nodes_v") or {}
        e0 = ep[0].get("nodes_v") or {}
        diffs = []
        for node, bv in b0.items():
            ev = e0.get(node)
            if not isinstance(bv, dict) or not isinstance(ev, dict):
                continue
            diffs.append((node, abs(complex(bv.get("re", 0.0), bv.get("im", 0.0))
                                    - complex(ev.get("re", 0.0), ev.get("im", 0.0)))))
    if not diffs:
        return {"compared_nodes": 0, "max_abs_diff_v": None,
                "note": "no node names in common between the two engines, so nothing "
                        "could be compared - this is NOT a pass"}
    worst = max(diffs, key=lambda t: t[1])
    if want == "ac":
        note = ("point compared: the first sweep frequency only, because the "
                "builtin AC uses the deck's own sweep points")
    elif want == "op":
        note = "point compared: the operating point"
    else:
        note = ("point compared: the first point only (both engines start at the "
                "same DC bias; later points differ by each engine's own integrator)")
    return {"compared_nodes": len(diffs),
            "max_abs_diff_v": round(worst[1], 12),
            "worst_node": worst[0],
            "note": note,
            "elements": "not compared (the external path has no per-element report)"}


def simulate(netlist, analysis=None, max_iter=DEFAULT_MAX_ITER, max_points=200,
             temp_c=None, engine="builtin", cross_check=False):
    """Run one analysis, optionally delegating to ngspice.

    `engine`: "builtin" (the default), "ngspice", or "auto".
    The DEFAULT IS THE BUILTIN SOLVER ON PURPOSE: a default that silently
    changes its model set depending on what happens to be installed on the
    machine makes the same netlist answer differently on two computers, and the
    caller has no way to tell from the number alone.
    "ngspice" without a binary is ENGINE_NOT_FOUND - never a silent fall back.
    `cross_check=True` runs both engines and reports the largest node-voltage
    disagreement, which is how the delegation itself is audited.
    """
    choice = str(engine or "builtin").lower()
    if choice not in ENGINE_CHOICES:
        return err("engine must be one of %s" % ", ".join(ENGINE_CHOICES),
                   "ARG_INVALID",
                   hint=ENGINE_NOTE)
    def _builtin(reason):
        out = _simulate_builtin(netlist, analysis=analysis, max_iter=max_iter,
                                max_points=max_points, temp_c=temp_c)
        if isinstance(out, dict) and out.get("status") == "ok":
            out["engine_used"] = "builtin"
            out["engine_note"] = reason
        return out

    if choice == "builtin":
        return _builtin("engine='builtin' was asked for, so the stdlib subset solver "
                        "ran and no external engine was consulted")

    # Only on explicit request: reaching the bundle from 'auto' would change what
    # every existing caller gets just because this repository grew a 14 MB
    # directory, and "the same netlist answers differently after an upgrade" is
    # exactly what the DEFAULT IS THE BUILTIN rule forbids.
    argv = find_ngspice(allow_bundled=(choice == "ngspice"))
    if argv is None:
        if choice == "ngspice":
            return err("engine='ngspice' was requested but no ngspice binary was found",
                       "ENGINE_NOT_FOUND",
                       hint="install ngspice, or point %s at it, or ask for "
                            "engine='auto' / 'builtin'. This refuses instead of "
                            "answering with the subset, because those are different "
                            "device models" % NGSPICE_ENV,
                       extra={"tried": ["%s env var" % NGSPICE_ENV,
                                        "PATH: ngspice, ngspice_con",
                                        "bundled: %s"
                                        % (bundled_ngspice_dir()
                                           + "/bin/ngspice_con.exe")]})
        return _builtin("engine='auto' found no ngspice binary (%s or PATH), so the "
                        "stdlib subset solver ran; the answer is that subset's, not "
                        "the reference engine's" % NGSPICE_ENV)

    # Which analysis? Same rule as the builtin path: the caller's word, else the
    # LAST analysis card in the deck (SPICE's own convention is the first, so the
    # deck is filtered to exactly one card and the ambiguity never reaches ngspice).
    # Unparsable cards (X/.subckt, B, T, K, S, ...) are the whole reason to have a
    # real engine: on this path the answer is the engine's rawfile, not a model of
    # ours, so a parse failure is not fatal HERE. The deck is passed through and
    # the node list is read from the engine's own output. The builtin path still
    # refuses the same deck - nothing is papered over.
    spec = None
    parse_note = None
    try:
        spec = parse_netlist(netlist)
    except ValueError as exc:
        parse_note = str(exc)
    if spec is None:
        want = (analysis or _last_analysis_card(netlist) or "op").lower()
    else:
        want = (analysis
                or (spec["analyses"][-1]["kind"] if spec["analyses"] else "op")).lower()
    if want not in ("op", "tran", "ac", "noise"):
        return err("analysis must be op, tran, ac or noise", "ARG_INVALID")
    if temp_c is not None:
        try:
            temp_c = float(temp_c)
        except (TypeError, ValueError):
            return err("temp_c must be a number", "ARG_INVALID")
        if ".temp" in str(netlist).lower():
            return err("both a .temp card and temp_c were given; the deck is passed to "
                       "ngspice unchanged, so sending two temperatures would leave the "
                       "answer dependent on card order", "ARG_CONFLICT",
                       hint="keep one of them")
        netlist = str(netlist).rstrip() + "\n.temp %g\n" % temp_c

    tstep = None
    if spec is None:
        if want == "tran":
            tstep = _tran_step_textual(netlist)
    else:
        card = next((a for a in spec["analyses"] if a["kind"] == want), None)
        if card and want == "tran":
            tstep = card.get("tmax") or card.get("tstep")

    raw_log, problem = _run_ngspice(argv, netlist, want)
    if problem is not None:
        # 'auto' deliberately does not reach the bundled engine (see ENGINE_NOTE),
        # so a failure there must at least say the bundle exists and how to ask for
        # it - otherwise the only working engine on the machine is undiscoverable.
        if choice == "auto":
            bundled = find_bundled_ngspice()
            if bundled:
                problem["bundled_engine"] = bundled[0]
                problem["hint"] = ((problem.get("hint") or "") +
                                   "; engine='ngspice' would run the copy bundled at %s"
                                   % bundled[0])
        return problem
    raw, log = raw_log
    cross = None
    if cross_check:
        builtin = _simulate_builtin(netlist, analysis=analysis, max_iter=max_iter,
                                    max_points=max_points, temp_c=None)
        if builtin.get("status") == "ok":
            external_probe = _external_envelope(raw, want, argv, tstep_hint=tstep,
                                                parse_note=parse_note)
            cross = _cross_check(builtin, external_probe, want)
        else:
            cross = {"compared_nodes": 0, "max_abs_diff_v": None,
                     "note": "the builtin solver could not solve this netlist (%s), so "
                             "there was nothing to cross-check against - the external "
                             "answer stands alone and is NOT corroborated"
                             % builtin.get("reason_code")}
    return _external_envelope(raw, want, argv, tstep_hint=tstep, cross=cross,
                              parse_note=parse_note)
