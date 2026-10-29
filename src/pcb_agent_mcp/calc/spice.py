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
D (Shockley), Q (Ebers-Moll injection), M (Shichman-Hodges level 1).

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
    "B": "behavioural source (arbitrary expressions)",
    "T": "lossless transmission line",
    "O": "lossy transmission line",
    "U": "uniform distributed RC line",
    "A": "XSPICE analogue behavioural block",
}

SUFFIX = {
    "t": 1e12, "g": 1e9, "meg": 1e6, "k": 1e3,
    "m": 1e-3, "u": 1e-6, "n": 1e-9, "p": 1e-12, "f": 1e-15,
}

DEFAULT_MAX_ITER = 200


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
            "options": {}, "subckts": {}}
    lines = str(text or "").splitlines()
    sub = None
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
            raise ValueError("line %d: .param is not implemented here; parameters "
                             "exist only on .subckt definitions and on their X "
                             "instances, where the value is actually in scope"
                             % lineno)
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
    return spec


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
    try:
        return _value(t)
    except ValueError:
        raise ValueError("line %d: %r is neither a number nor a {…} expression"
                         % (lineno, t))


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
                raise ValueError("%s: %r is not a parameter in scope here (%s)"
                                 % (where, node.id,
                                    ", ".join(sorted(env)) if env else "none"))
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
            "models": {}, "lineno": lineno}


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
    out = []
    for el in spec["elements"]:
        if el["type"] != "X":
            out.append(el)
            continue
        out.extend(_instantiate(spec, el, el["name"].lower(), {}, (), 0))
    spec["elements"] = out
    for el in spec["elements"]:
        for key in ("value", "gain"):
            if isinstance(el.get(key), tuple):
                raise ValueError("line %d: %s has a {…} value with no subcircuit "
                                 "parameter scope in effect"
                                 % (el.get("lineno", 0), el["name"]))


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
    env = {}
    for key, val in sub["params"].items():
        env[key] = _resolve_value(val, caller_env,
                                  "%s (.subckt %s default %s)"
                                  % (where, el["sub"], key))
    for key, val in el["overrides"].items():
        if key not in sub["params"]:
            raise ValueError("%s: %s sets parameter %r, which .subckt %r does not "
                             "declare (%s)"
                             % (where, el["name"], key, el["sub"],
                                ", ".join(sorted(sub["params"])) or "none"))
        env[key] = _resolve_value(val, caller_env,
                                  "%s (%s %s)" % (where, el["name"], key))
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
        for key in ("value", "gain"):
            if key in copy:
                copy[key] = _resolve_value(copy[key], env,
                                           "%s (%s %s)"
                                           % (where, copy["name"], key))
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
            dc = _value(p.split("=", 1)[1])
            i += 1
        elif p == "dc":
            dc = _value(parts[i + 1])
            i += 2
        elif p == "ac":
            mag = _value(parts[i + 1]) if i + 1 < len(parts) else 1.0
            phase = _value(parts[i + 2]) if i + 2 < len(parts) else 0.0
            ac = (mag, phase)
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
            nums = [_value(x) for x in body.replace(",", " ").split()
                    if x.strip() not in ("", ")")]
            wave = {"kind": kind, "args": nums}
            i = j
        else:
            # a bare number is the DC value
            dc = _value(parts[i])
            i += 1
    return {"type": letter, "name": name, "nodes": nodes, "dc": dc,
            "wave": wave, "ac": ac}


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
        pw = a[5] if len(a) > 5 else 0.0
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
        self.n = len(nodes)
        branches = [el for el in self.elements if el["type"] in ("V", "L", "E")]
        self.branch_idx = {}
        for k, el in enumerate(branches):
            self.branch_idx[el["name"]] = self.n + 1 + k
        self.size = self.n + len(branches)
        self.disabled = []        # non-ideal effects that were switched off
        self.defaulted = {}       # model params that fell back to a default

    # -- helpers
    def is_linear(self):
        return not any(el["type"] in ("D", "Q", "M") for el in self.elements)

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
                    mag, phase = el.get("ac") or (1.0, 0.0)
                    self._rhs(b, row, cmath.rect(mag, math.radians(phase)))
                else:
                    self._rhs(b, row, _source_value(el, t if t is not None else 0.0))
            elif T == "I":
                if mode == "ac":
                    mag, phase = el.get("ac") or (1.0, 0.0)
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
            elif T == "D":
                self._stamp_diode(A, b, el, x_prev, mode)
            elif T == "Q":
                self._stamp_bjt(A, b, el, x_prev, mode)
            elif T == "M":
                self._stamp_mos(A, b, el, x_prev, mode)
        # gmin to ground on every node: the standard "no floating node" crutch
        if mode != "ac" and gmin:
            for k in range(self.n):
                A[k][k] += gmin
        return A, b

    def _x(self, x_prev, name):
        idx = self.i(name)
        return x_prev[idx - 1] if idx else 0j

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
        A, b = circ.assemble(x, t=t, mode=mode, gmin=gmin)
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
                for c in range(circ.size):
                    acc -= A2[r][c] * x[c]
                res = max(res, abs(acc))
            scale = 1e-9 + 1e-6 * max([abs(v) for v in x] or [0.0])
            if res <= scale:
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


def _element_report(circ, x, t=None):
    out = []
    for el in circ.elements:
        T = el["type"]
        na, nb = el["nodes"][0], el["nodes"][1]
        va = x[circ.i(na) - 1] if circ.i(na) else 0j
        vb = x[circ.i(nb) - 1] if circ.i(nb) else 0j
        v = (va - vb).real
        rec = {"name": el["name"], "type": T, "voltage_v": round(v, 9)}
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
        out.append(rec)
    return out


def _node_dict(circ, x, complex_ok=False):
    d = {}
    for name in circ.node_list:
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
    "top-level .param, and .subckt parameters that are not plain numbers or "
    "{+ - * / **} arithmetic on numbers and parameters (no functions, no node "
    "references, no expressions in .model parameters)",
    "a .subckt nested inside another .subckt, and any directive other than "
    ".model inside a .subckt body",
    "current-controlled sources (F/H), switches (S/W), JFETs, coupled inductors (K)",
    "MOSFET levels 2..7, BSIM, gate charge, capacitances and switching loss",
    "BEHAVIOURAL sources (B), Laplace blocks, arbitrary expressions",
    "transmission lines and distributed RC",
    "noise (.noise), distortion (.disto), Monte Carlo / worst-case",
    "temperature SWEEP (one temperature per run: `.temp` / temp_c), and junction "
    "self-heating - the junction sits at the temperature you state, it does not "
    "rise with dissipation. IS(T) and Vt(T) are modelled (XTI/EG/TNOM), so a "
    "forward drop at a stated temperature is right, but device capacitances, "
    "mobility and breakdown versus temperature are not",
    "convergence for circuits that need continuation beyond gmin/source stepping",
]


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
    circ = _Circuit(spec)
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
                   hint="split the netlist, or use ngspice for a full-size design")
    want = (analysis or (spec["analyses"][-1]["kind"] if spec["analyses"] else "op")).lower()
    if want not in ("op", "tran", "ac"):
        return err("analysis must be op, tran or ac", "ARG_INVALID")

    if want == "op":
        x, iters, why = _dc(circ)
        if x is None:
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
        # initial conditions
        for el in circ.elements:
            if el["type"] == "C":
                el["_vprev"] = el.get("ic") if card.get("uic") and el.get("ic") else 0.0
            if el["type"] == "L":
                el["_iprev"] = el.get("ic") if card.get("uic") and el.get("ic") else 0.0
        x0, iters, why = _dc(circ)
        if x0 is None:
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
            if k % stride == 0 or k == steps:
                points.append({"t": round(t, 12), "nodes_v": _node_dict(circ, x)})
        return ok(value={"analysis": "tran", "points": points,
                         "timestep_s": h, "steps": steps,
                         "elements_final": _element_report(circ, x, t),
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
    x0, iters, why = _dc(circ)
    if x0 is None:
        return err("AC needs a DC operating point and it did not converge (%s)" % why,
                   "NOT_CONVERGED",
                   hint="AC is a small-signal linearisation AT the bias point; without "
                        "a bias there is no AC answer")
    points = []
    for f in freqs:
        for el in circ.elements:
            if el["type"] in ("C", "L"):
                el["_f"] = f
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
