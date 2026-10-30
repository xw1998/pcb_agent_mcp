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
T (lossless transmission line: a pair of wires at DC, exact Y parameters in
AC, the method of characteristics in transient).

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
NOT_IMPLEMENTED_CARDS = {
    "F": "current-controlled current source (needs a named branch)",
    "H": "current-controlled voltage source (needs a named branch)",
    "J": "JFET",
    "K": "coupled inductors / mutual inductance",
    "S": "voltage-controlled switch",
    "W": "current-controlled switch",
    "A": "XSPICE analogue behavioural block",
}

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
            "options": {}, "subckts": {}, "params": {}}
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
        if low.startswith(".temp"):
            spec["options"]["temp_c"] = _value(line.split()[1])
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
        if el["type"] not in ("D", "Q", "M"):
            continue
        model = el["model"]
        if model not in spec["models"]:
            raise ValueError("line %d: %s references .model %r which is not defined"
                             % (el.get("lineno", 0), el["name"], model))
        kind = spec["models"][model]["type"]
        want = {"D": ("D",), "Q": ("NPN", "PNP"), "M": ("NMOS", "PMOS")}[el["type"]]
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


def _value_or_expr(tok, lineno):
    """A number, or a ``{...}`` expression kept for later.

    A subcircuit's parameters are not in scope at its definition, so the value
    has no answer yet; it becomes one when an instance supplies (or defaults)
    them.
    """
    t = str(tok).strip()
    if len(t) > 2 and t.startswith("{") and t.endswith("}"):
        return ("expr", t[1:-1].strip())
    if _looks_numeric(t):
        return _value(t)
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

    out = []
    for body in sub["elements"]:
        copy = dict(body)
        # lower case throughout: internal node names are what ngspice prints in
        # its rawfile (x9.x1.m), and a name that differs only by case compares as
        # "no node in common" instead of a mismatch
        copy["name"] = "%s.%s" % (path, body["name"].lower())
        copy["nodes"] = tuple(mapped(n) for n in body["nodes"])
        if body.get("ctrl"):
            copy["ctrl"] = tuple(mapped(n) for n in body["ctrl"])
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
    bits = [kind] + inner.split()
    params = {}
    for b in bits[1:]:
        if "=" not in b:
            continue
        k, v = b.split("=", 1)
        params[k.strip().upper()] = _value(v)
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

def _parse_element(spec, line, lineno):
    parts = line.split()
    name = parts[0]
    letter = name[0].upper()
    if letter in NOT_IMPLEMENTED_CARDS:
        raise ValueError("line %d: %s (%s) is not implemented here: %s"
                         % (lineno, name, letter, NOT_IMPLEMENTED_CARDS[letter]))
    low = [p.lower() for p in parts]

    def val(idx, what):
        if len(parts) <= idx:
            raise ValueError("line %d: %s needs a %s" % (lineno, name, what))
        return parts[idx]

    if letter == "R":
        return {"type": "R", "name": name, "nodes": (_node(parts[1]), _node(parts[2])),
                "value": _value_or_expr(val(3, "resistance"), lineno)}
    if letter == "C":
        el = {"type": "C", "name": name, "nodes": (_node(parts[1]), _node(parts[2])),
              "value": _value_or_expr(val(3, "capacitance"), lineno), "ic": None}
        for p in low[4:]:
            if p.startswith("ic="):
                el["ic"] = _value(p.split("=", 1)[1])
        return el
    if letter == "L":
        el = {"type": "L", "name": name, "nodes": (_node(parts[1]), _node(parts[2])),
              "value": _value_or_expr(val(3, "inductance"), lineno), "ic": None}
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
        return {"type": letter, "name": name,
                "nodes": (_node(parts[1]), _node(parts[2])),
                "ctrl": (_node(parts[3]), _node(parts[4])),
                "value": _value_or_expr(val(5, "gain"), lineno)}
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
        return {"type": "M", "name": name, "lineno": lineno,
                "nodes": (_node(parts[1]), _node(parts[2]), _node(parts[3]),
                          _node(parts[4])), "model": model}
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
        # node table
        nodes = []
        for el in self.elements:
            for n in list(el["nodes"]) + list(el.get("ctrl") or ()):
                if n is not None and n not in nodes:
                    nodes.append(n)
        self.node_list = nodes
        self.has_ground = any(
            n is None for el in self.elements
            for n in list(el["nodes"]) + list(el.get("ctrl") or ()))
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
                    if el["type"] in ("V", "L", "E", "BV")]
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
        self.disabled = []        # non-ideal effects that were switched off
        self.defaulted = {}       # model params that fell back to a default
        self.lossy = spec.get("lossy") or []   # O/U cards, for the report only
        # Nodes that exist only inside a ladder: reported nowhere, because they
        # are not the caller's nodes (the O/U card is, with its section count).
        ext, inn = set(), set()
        for el in self.elements:
            for n in el["nodes"]:
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
        return not any(el["type"] in ("D", "Q", "M")
                       or (el["type"] in ("BV", "BI") and el["deps"])
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
        KP = pr.get("KP", 2e-5)
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

    # -- assembly
    def assemble(self, x_prev, t=None, mode="dc", gmin=1e-12, caps=None):
        """Build A, b. `x_prev` is the previous solution (for Newton and for the
        companion models). `caps` holds per-capacitor companion conductances."""
        A, b = self._mat()
        for el in self.elements:
            T = el["type"]
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
            elif T == "G":
                cp, cm = self.i(el["ctrl"][0]), self.i(el["ctrl"][1])
                a, bb = self.i(na), self.i(nb)
                g = el["value"]
                self._add(A, a, cp, g)
                self._add(A, a, cm, -g)
                self._add(A, bb, cp, -g)
                self._add(A, bb, cm, g)
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
            elif T == "T":
                self._stamp_tline(A, b, el, x_prev, t, mode)
            elif T in ("O", "U"):
                # unreachable through parse_netlist (the card is expanded there);
                # raising rather than stamping nothing keeps a future refactor
                # from turning a lossy line into a silent wire
                raise ValueError("%s (a %s card) reached the solver unexpanded"
                                 % (el["name"], T))
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
    """DC operating point with two recoveries, in the order SPICE uses them."""
    x0 = uic_x if uic_x is not None else [0j] * circ.size
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
        na, nb = el["nodes"][0], el["nodes"][1]
        va = x[circ.i(na) - 1] if circ.i(na) else 0j
        vb = x[circ.i(nb) - 1] if circ.i(nb) else 0j
        v = (va - vb).real
        rec = {"name": el["name"], "type": T, "voltage_v": round(v, 9)}
        vv = lambda n: (x[circ.i(n) - 1] if circ.i(n) else 0j)  # noqa: E731
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
        elif T in ("L", "E"):
            cur = x[circ.branch_idx[el["name"]] - 1].real
            rec["current_a"] = round(cur, 12)
            rec["power_w"] = round(v * cur, 12)
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
    "level-1 MOSFET, Ebers-Moll BJT and an ideal Shockley diode with no series "
    "resistance, no junction capacitance, no temperature beyond a fixed 27 C and "
    "no noise. A converged answer is the answer to this netlist, not to the board"
)

NOT_COMPUTED = [
    "parameters that need a function, a node reference or a self-referential "
    "definition: .param and .subckt parameters take numbers and {+ - * / **} "
    "arithmetic on numbers and other parameters only, and expressions are not "
    "accepted inside .model parameters",
    "a .subckt nested inside another .subckt, and any directive other than "
    ".model inside a .subckt body",
    "current-controlled sources (F/H), switches (S/W), JFETs, coupled inductors (K)",
    "MOSFET levels 2..7, BSIM, gate charge, capacitances and switching loss",
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
    "noise (.noise), distortion (.disto), Monte Carlo / worst-case",
    "temperature SWEEP (one temperature per run: `.temp` / temp_c), and junction "
    "self-heating - the junction sits at the temperature you state, it does not "
    "rise with dissipation. IS(T) and Vt(T) are modelled (XTI/EG/TNOM), so a "
    "forward drop at a stated temperature is right, but device capacitances, "
    "mobility and breakdown versus temperature are not",
    "convergence for circuits that need continuation beyond gmin/source stepping",
]


def _expression_failure(why):
    """A B-source expression with no value is not a convergence failure."""
    text = str(why or "")
    if text.startswith("expression:"):
        return err(text[len("expression: "):], "EXPRESSION_INVALID",
                   hint="a B source's expression was evaluated at a voltage where it "
                        "has no value (division by zero, sqrt or log of a negative, "
                        "an overflow). The netlist is not wrong; the expression is")
    return None

def _simulate_builtin(netlist, analysis=None, max_iter=DEFAULT_MAX_ITER, max_points=200,
                       temp_c=None):
    """Run one analysis. Returns the standard envelope.

    `temp_c` overrides a `.temp` card in the netlist (the caller's stated
    temperature wins), and with neither the junction temperature is TEMP_C.
    """
    try:
        spec = parse_netlist(netlist)
    except ValueError as exc:
        return err(str(exc), "NETLIST_INVALID",
                   hint="the parser refuses a card it does not implement instead of "
                        "skipping it; see NOT_IMPLEMENTED_CARDS")
    if temp_c is not None:
        try:
            spec["options"]["temp_c"] = float(temp_c)
        except (TypeError, ValueError):
            return err("temp_c must be a number", "ARG_INVALID")
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
    if want not in ("op", "tran", "ac"):
        return err("analysis must be op, tran or ac", "ARG_INVALID")

    if want == "op":
        x, iters, why = _dc(circ)
        if x is None:
            bad = _expression_failure(why)
            if bad is not None:
                return bad
            return err("DC operating point did not converge: %s" % why, "NOT_CONVERGED",
                       hint="check for a floating node, a voltage source loop or a "
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
                       "converge (%s)" % why, "NOT_CONVERGED")
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
        return ok(value={"analysis": "tran", "points": points,
                         "timestep_s": h, "steps": steps,
                         "elements_final": _element_report(circ, x, t, mode="tran"),
                         "iterations": total_iter},
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

    # AC
    card = next((a for a in spec["analyses"] if a["kind"] == "ac"), None)
    if card is None:
        return err("no .ac card in the netlist", "ARG_REQUIRED",
                   hint="add '.ac dec <points> <fstart> <fstop>'")
    f0, f1 = card["fstart"], card["fstop"]
    if f0 <= 0 or f1 <= f0:
        return err("ac sweep needs 0 < fstart < fstop", "ARG_RANGE")
    npts = max(1, int(card["n"]))
    if card["sweep"] == "dec":
        count = int(math.ceil(math.log10(f1 / f0) * npts)) + 1
        freqs = [f0 * (f1 / f0) ** (i / max(1.0, (count - 1))) for i in range(count)]
    elif card["sweep"] == "oct":
        count = int(math.ceil(math.log(f1 / f0, 2) * npts)) + 1
        freqs = [f0 * (f1 / f0) ** (i / max(1.0, (count - 1))) for i in range(count)]
    else:
        freqs = [f0 + (f1 - f0) * i / max(1.0, npts - 1) for i in range(npts)]
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
    x0, iters, why = _dc(circ)
    if x0 is None:
        bad = _expression_failure(why)
        if bad is not None:
            return bad
        return err("AC needs a DC operating point and it did not converge (%s)" % why,
                   "NOT_CONVERGED",
                   hint="AC is a small-signal linearisation AT the bias point; without "
                        "a bias there is no AC answer")
    points = []
    tline_warned = set()
    for f in freqs:
        for el in circ.elements:
            if el["type"] in ("C", "L", "T"):
                el["_f"] = f
        for el in circ.elements:
            if el["type"] != "T":
                continue
            theta = 2.0 * math.pi * f * el["td"]
            s = abs(math.sin(theta))
            if s < 1e-9:
                # cot(theta) is infinite here: the lossless line is a short at
                # every multiple of pi, and an ideal short has no finite answer.
                # Returning the huge number the matrix would produce is exactly
                # the plausible-looking wrong answer this module exists to not
                # produce.
                return err("AC sweep stopped at %g Hz: %s is at theta = 2*pi*f*TD "
                           "= %g rad, a multiple of pi, where a LOSSLESS line's "
                           "admittance is infinite. The ideal model has no "
                           "finite answer there; sweep off the resonance, or use "
                           "an engine that models the line's loss"
                           % (f, el["name"], theta), "SINGULAR",
                           hint="a real line has loss, which is what makes this "
                                "frequency finite; O cards (lossy lines) are not "
                                "implemented here")
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
                    "operating point (the standard SPICE .ac definition); capacitors "
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
    "get a level-1 MOSFET answer in its place"
)


def find_ngspice():
    """Return an argv prefix for ngspice, or None. Never raises."""
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
    if cand.startswith(b"Binary"):
        need = npt_unused = None
        size = 8 * nvar * mult
        if npts <= 0:
            npts = len(body) // size
        if len(body) < size * npts:
            raise ValueError("binary rawfile truncated: %d bytes for %d points x %d "
                             "variables" % (len(body), npts, nvar))
        vals = struct.unpack("<%dd" % (nvar * mult * npts), body[:size * npts])
        step = nvar * mult
        for p in range(npts):
            row = list(vals[p * step:(p + 1) * step])
            if is_complex:
                row = [(row[2 * i], row[2 * i + 1]) for i in range(nvar)]
            points.append(row)
    else:
        toks = body.decode("utf-8", "replace").split()
        per_point = 1 + nvar * mult      # the point index comes first
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
            "points": points}


_NODE_PREFIXES = ("v(", "V(")


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
            continue
        keep.append(ln)
    analyses = [ln.strip() for ln in lines
                if ln.strip().lower().startswith((".op", ".tran", ".ac"))]
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
        if not os.environ.get("SPICE_LIB_DIR"):
            here = os.path.dirname(os.path.abspath(argv[0]))
            lib = os.path.normpath(os.path.join(here, os.pardir, "lib", "ngspice"))
            if os.path.isdir(lib):
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

    argv = find_ngspice()
    if argv is None:
        if choice == "ngspice":
            return err("engine='ngspice' was requested but no ngspice binary was found",
                       "ENGINE_NOT_FOUND",
                       hint="install ngspice, or point %s at it, or ask for "
                            "engine='auto' / 'builtin'. This refuses instead of "
                            "answering with the subset, because those are different "
                            "device models" % NGSPICE_ENV,
                       extra={"tried": ["%s env var" % NGSPICE_ENV,
                                        "PATH: ngspice, ngspice_con"]})
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
    if want not in ("op", "tran", "ac"):
        return err("analysis must be op, tran or ac", "ARG_INVALID")
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
