"""Run a simulation on the board's own copper.

`board_net.build` produces the deck; this module attaches what a board does not
contain - sources, loads, an analysis - and hands it to `calc.spice.simulate`.
There is no second physics here: the R comes from `board_ir_drop`'s graph, the L
and C from `sim.board`, and the solve from the engine that `sim_spice` already
exposes. What was missing was the middle, and that is `build`.

Two things this deliberately does NOT do
----------------------------------------
It does not invent a ground. A node is tied to 0 V only because the caller named
it, and the tie is written into the deck as a 0 V source so the return current
is a number the engine reports (`ground_current_a`) instead of a note. With no
tie and no voltage source the circuit is defined only up to a constant, which is
the classic way to get a plausible wrong number out of a network, so that case
is refused.

It does not put components on the board. A capacitor in the deck is the copper's
own shunt capacitance, not a 100 nF part; a load is the caller's. A board
simulation without a load is a board with nothing drawing current, and it will
answer zero - accurately.
"""

import math

from . import board as _B
from ..calc import spice as _S

_MISSING = "not supplied by the caller and not inferred"


def _err(msg, code, hint=None):
    return {"status": "error", "reason_code": code, "message": msg, "hint": hint}


def _resolve(terms, ref, what):
    """A pad name or a deck node name -> deck node name."""
    if ref is None:
        return None, None
    r = str(ref)
    if r in terms:
        return terms[r], "pad"
    low = r.lower()
    for pad, node in terms.items():
        if node.lower() == low:
            return node, "node"
    return None, None


def _analysis_cards(analysis):
    """-> (card text, kind, refusal)"""
    if analysis is None:
        return ".op", "op", None
    if isinstance(analysis, str):
        a = {"kind": analysis}
    else:
        a = dict(analysis or {})
    kind = str(a.get("kind") or a.get("analysis") or "op").lower()
    if kind == "op":
        return ".op", "op", None
    if kind == "tran":
        try:
            tstep = float(a["tstep_s"] if "tstep_s" in a else a["tstep"])
            tstop = float(a["tstop_s"] if "tstop_s" in a else a["tstop"])
        except (KeyError, TypeError, ValueError):
            return None, kind, ("a transient analysis needs tstep_s and tstop_s: a "
                                "time base nobody stated is a waveform nobody asked "
                                "for")
        if tstep <= 0 or tstop <= 0 or tstop < tstep:
            return None, kind, "tstep_s and tstop_s must be > 0 with tstop >= tstep"
        return ".tran %.12g %.12g" % (tstep, tstop), kind, None
    if kind == "ac":
        try:
            f0 = float(a["fstart_hz"] if "fstart_hz" in a else a["fstart"])
            f1 = float(a["fstop_hz"] if "fstop_hz" in a else a["fstop"])
            pts = int(a.get("points") or 10)
        except (KeyError, TypeError, ValueError):
            return None, kind, "an AC analysis needs fstart_hz and fstop_hz"
        sweep = str(a.get("sweep") or "dec").lower()
        if sweep not in ("dec", "oct", "lin"):
            return None, kind, "sweep must be dec, oct or lin"
        if f0 <= 0 or f1 <= 0 or f1 < f0 or pts < 1:
            return None, kind, "fstart_hz, fstop_hz and points must be positive"
        return ".ac %s %d %.12g %.12g" % (sweep, pts, f0, f1), kind, None
    return None, kind, "analysis.kind must be op, tran or ac"


def simulate(board, nets=None, drives=None, loads=None, analysis=None,
             ground=None, outputs=None, engine="auto", plating_um=None,
             copper_um=None, quantize_mm=0.01, temp_c=20.0, model_zones=False,
             include_lc=True, max_nodes=60000, max_points=200):
    """Extract, attach, solve. Returns the deck too, so the run can be audited."""
    # Which pads this run is going to wire: an island of copper that no wired pad
    # touches cannot be solved (it floats) and cannot answer anything either, so
    # build drops it - and names it. Collecting these before the extraction is
    # what makes that drop a measurement instead of a guess.
    wired = []
    for row in list(drives or []) + list(loads or []):
        if not isinstance(row, dict):
            continue
        for k in ("at", "ref"):
            if row.get(k) is not None:
                wired.append(str(row[k]))
    if ground is not None:
        wired.append(str(ground))
    for o in (outputs or []):
        wired.append(str(o))
    built = _B.build(board, nets=nets, plating_um=plating_um,
                     copper_um=copper_um, quantize_mm=quantize_mm,
                     temp_c=temp_c, model_zones=model_zones,
                     include_lc=include_lc, max_nodes=max_nodes,
                     keep_pads=wired or None)
    if built.get("status") != "ok":
        return built
    v = built["value"]
    deck = [ln for ln in v["deck"].splitlines() if not ln.startswith("*")]
    terms = {t["pad"]: t["node"] for t in v["terminals"]}

    # A wired pad that no track reaches is the simplest way to get a board that
    # floats and still answers: the tie lands on a node of its own, every other
    # node keeps an arbitrary potential, and whether the answer is refused or
    # quietly gmin-solved depends on the engine. Naming it here is the only
    # version of that which cannot be misread.
    _no_copper = set(v.get("copperless_pads") or [])
    for sk in v["skipped"]:
        if sk.get("reason_code") == "PAD_WITHOUT_COPPER":
            _no_copper.update(sk.get("pads") or [])
    hit = []
    for p in wired:
        if p in _no_copper and p not in hit:
            hit.append(p)
    if hit:
        return _err("pad(s) %s are named in this run, but no track on their net "
                    "reaches them, so they are not in the deck: a pad with no "
                    "copper can neither ground anything nor carry a load"
                    % ", ".join(hit[:6]), "NOT_CONNECTED",
                    hint="value.skipped lists every pad that was left out and why; "
                         "a pad whose only connection is a copper pour is one of "
                         "these unless model_zones=true puts the pour in the "
                         "network")

    def _pad_net(pad):
        """Which net a pad is on, so a pad that was left out of `nets` can be
        named with the net that has to be added rather than as "unknown"."""
        want = str(pad)
        for fp in (board.footprints or []):
            ref = str(fp.get("ref") or fp.get("reference") or "")
            for pd in (fp.get("pads") or []):
                num = str(pd.get("number") or pd.get("name") or "")
                if "%s.%s" % (ref, num) == want:
                    return pd.get("net")
        return None

    def _island_of(pad):
        """Why a pad that exists on the board is not in the deck."""
        net = _pad_net(pad)
        if net is None:
            return None, None
        for isl in (v.get("islands") or []):
            if isl["net"] == net and pad in isl["pads"]:
                return net, isl
        for sk in v["skipped"]:
            if sk.get("net") == net and pad in (sk.get("pads") or []):
                return net, sk
        return net, None

    def _miss(what, ref_):
        net, why = _island_of(str(ref_))
        if net is None:
            return ("%s %r is not a pad or node on this board" % (what, ref_))
        if why is not None:
            return ("%s %r is a pad of net %r, but it is not in the deck: %s"
                    % (what, ref_, net, why.get("reason") or why))
        return ("%s %r is a pad of net %r, which was not extracted; add it to "
                "nets=[...]" % (what, ref_, net))

    card, kind, why = _analysis_cards(analysis)
    if card is None:
        return _err(why, "ARG_INVALID")

    notes = []
    _cut = [s for s in v["skipped"]
            if s.get("reason_code") in ("ISLAND_NOT_WIRED",
                                        "PAD_WITHOUT_COPPER", "FLOATING_NET")]
    if _cut:
        notes.append("%d net/island(s) are not in the deck because no wired pad "
                     "touches them; they are listed in board.skipped with their "
                     "pads (a floating island solves to nothing and would make the "
                     "matrix singular instead of saying so)" % len(_cut))
    gnode, how = (None, None)
    if ground is not None:
        gnode, how = _resolve(terms, ground, "ground")
        if gnode is None:
            return _err(_miss("ground", ground), "ARG_INVALID",
                        hint="use a pad from value.terminals, e.g. %s"
                             % (sorted(terms)[:3] or "U1.1"))
        deck.append("Vgnd %s 0 0" % gnode)
        notes.append("node %s is tied to 0 V by a 0 V source written into the deck "
                     "(the ground tie is a source, not copper); its current is "
                     "reported as ground_current_a when the engine reports element "
                     "currents, and it is the board's return current ONLY when the "
                     "sources are referenced to another net's copper (give a drive "
                     "or a load a ref); a source referenced to this same node closes "
                     "its loop inside itself and the tie carries nothing" % gnode)
    ref = gnode or "0"

    def _ref_of(row, what):
        """A drive/load may name its own reference, so a loop can go through
        two nets of real copper instead of through the ground tie."""
        name = row.get("ref")
        if name is None:
            return ref, None
        if str(name) in ("0", "gnd0", "artificial-0"):
            # the engine's own 0 V: for a load that is not on the board (a
            # connector going somewhere else), so the return current must still
            # come back through the board's ground tie
            return "0", None
        node, _ = _resolve(terms, name, what)
        if node is None:
            return None, _miss("%s ref" % what, name)
        return node, None

    seen_names = set()
    n_src = 0
    for d in (drives or []):
        if not isinstance(d, dict):
            return _err("each drive must be an object", "ARG_INVALID")
        node, _ = _resolve(terms, d.get("at"), "drive")
        if node is None:
            return _err(_miss("drive at", d.get("at")), "ARG_INVALID",
                        hint="pads look like U1.1; the list is value.terminals")
        n_src += 1
        dref, bad = _ref_of(d, "drive")
        if bad:
            return _err(bad, "ARG_INVALID")
        k = str(d.get("kind") or ("current" if "current_a" in d else "dc")).lower()
        if k in ("dc", "voltage", "v"):
            val = d.get("voltage_v", d.get("value", 0.0))
            deck.append("V%d %s %s %.12g" % (n_src, node, dref, float(val)))
        elif k in ("current", "i"):
            if "current_a" not in d:
                return _err("a current drive needs current_a", "ARG_INVALID")
            deck.append("I%d %s %s %.12g"
                        % (n_src, dref, node, float(d["current_a"])))
        elif k in ("pulse", "sin", "sine"):
            need = (["v1", "v2", "td", "tr", "tf", "pw", "per"] if k == "pulse"
                    else ["vo", "va", "freq"])
            missing = [x for x in need if x not in d]
            if missing:
                return _err("a %s drive is missing %s" % (k, ", ".join(missing)),
                            "ARG_INVALID")
            if k == "pulse":
                deck.append("V%d %s %s PULSE(%.12g %.12g %.12g %.12g %.12g %.12g "
                            "%.12g)" % ((n_src, node, dref) + tuple(
                                float(d[x]) for x in need)))
            else:
                deck.append("V%d %s %s SIN(%.12g %.12g %.12g)"
                            % (n_src, node, dref, float(d["vo"]), float(d["va"]),
                               float(d["freq"])))
        else:
            return _err("drive kind %r is not supported (dc | current | pulse | sin)"
                        % (k,), "ARG_INVALID")
        seen_names.add(node)

    n_load = 0
    for l in (loads or []):
        if not isinstance(l, dict):
            return _err("each load must be an object", "ARG_INVALID")
        node, _ = _resolve(terms, l.get("at"), "load")
        if node is None:
            return _err(_miss("load at", l.get("at")), "ARG_INVALID")
        n_load += 1
        lref, bad = _ref_of(l, "load")
        if bad:
            return _err(bad, "ARG_INVALID")
        if "r_ohm" in l:
            deck.append("RL%d %s %s %.12g" % (n_load, node, lref, float(l["r_ohm"])))
        elif "i_a" in l:
            deck.append("IL%d %s %s %.12g" % (n_load, node, lref, float(l["i_a"])))
        elif "c_f" in l:
            deck.append("CL%d %s %s %.12g" % (n_load, node, lref, float(l["c_f"])))
        else:
            return _err("a load needs r_ohm, i_a or c_f", "ARG_INVALID")
        seen_names.add(node)

    # ---- which vectors to ask the engine for
    want = []
    for name in (outputs or []):
        node, _ = _resolve(terms, name, "output")
        if node is None:
            return _err("output %r is not a pad or node on this board" % (name,),
                        "ARG_INVALID")
        want.append(node)
    if not want and kind != "op":
        # `.save` is what keeps a 6 000-node board from returning 6 000 curves
        base = sorted(seen_names)
        want = base[:60]
        if base:
            notes.append("%d vector(s) saved (%d node(s) in this deck); pass "
                         "outputs=[...] to choose them" % (len(want),
                                                          v["counts"]["nodes"]))
    # `.save` is ngspice's output limiter. The builtin solver refuses a card it
    # does not implement (by design), so writing it there would make a solvable
    # board look unsolvable; instead the answer is trimmed here, which is the
    # same thing done later and works for every engine.
    save_card = str(engine or "").lower() == "ngspice"
    if want and kind != "op" and save_card:
        deck.append(".save " + " ".join("v(%s)" % n for n in want))
    elif want and kind != "op":
        notes.append("%d vector(s) are trimmed to the requested outputs; engine %r "
                     "gets no `.save` card (it would refuse one) so it solves the "
                     "full deck and returns every node"
                     % (len(want), engine))

    if not n_src:
        if not gnode:
            return _err("nothing drives this board and nothing ties it to 0 V: every "
                        "solution differs by an arbitrary constant, so every voltage "
                        "in the answer would be a choice and not a measurement. "
                        "Give a drive and a ground, or read the deck with "
                        "board_netlist", "UNDERDETERMINED",
                        hint="a drive is {'at': 'U1.1', 'kind': 'dc', "
                             "'voltage_v': 3.3}; ground is a pad on the return net")
        notes.append("no drive was given: the only source in the deck is the 0 V tie "
                     "at %s, so every node is at 0 V and this answer says nothing "
                     "about the board" % gnode)
    if n_src and not gnode and all("voltage_v" in (d or {}) or
                                   str((d or {}).get("kind") or "").lower()
                                   in ("dc", "voltage", "v")
                                   for d in (drives or [])):
        notes.append("no ground was given, so every voltage in the answer is "
                     "relative to the artificial node 0; pass ground='GNDpad' to "
                     "reference it to real copper")
    # ---- everything in the deck must reach the reference. A wired pad that no
    # track touches grounds nothing: the tie lands on a node of its own and the
    # rest of the net floats. Measured before this check existed - grounding a pad
    # with no copper on it returned status ok and 1000.000000 mV on a net that was
    # connected to nothing; the engine had quietly solved a floating network with
    # its gmin. A network that floats has no unique solution, so it is refused
    # instead of answered.
    if gnode:
        parent = {}

        def _find(x):
            parent.setdefault(x, x)
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        # Only a resistance, an inductance or a voltage source gives a node a
        # DC path to the reference. A capacitor is an open at DC and a current
        # source fixes no potential, so counting them would call a floating
        # network connected - which is exactly the wrong answer, since the
        # floating network is the one that solves to arbitrary numbers.
        for ln in deck:
            if ln.startswith(".") or ln[:1] not in ("R", "L", "V"):
                continue
            _tok = ln.split()
            if len(_tok) < 4:
                continue
            ra, rb = _find(_tok[1]), _find(_tok[2])
            if ra != rb:
                parent[ra] = rb
        root0 = _find("0")
        stray = sorted(n for n in list(parent) if _find(n) != root0)
        if stray:
            rev = {}
            for pad, node in terms.items():
                rev.setdefault(node, []).append(pad)
            named = []
            for n in stray:
                named.extend(rev.get(n, []))
            return _err(
                "the deck has %d node(s) with no resistive or source path to the "
                "0 V tie at %s%s; a floating piece of copper has no unique "
                "solution, so any voltage reported from it would be a solver "
                "artifact"
                % (len(stray), gnode,
                   (": " + ", ".join(named[:6])) if named else
                   " (none of them carries a pad)"),
                "NOT_CONNECTED",
                hint="the usual cause is a wired pad whose net has no copper "
                     "reaching it (no track and no via), so tying it to ground "
                     "grounds nothing; value.terminals lists what is in the deck "
                     "and value.skipped says which pads were left out and why")

    deck.append(card)
    text = "\n".join(deck)

    out = _S.simulate(text, analysis=kind, engine=engine, max_points=max_points)
    if out.get("status") != "ok":
        out.setdefault("extra", {})["deck"] = text
        out["extra"]["deck_note"] = ("the deck that failed is included verbatim so "
                                    "the failure can be reproduced")
        return out

    res = out["value"]
    keep = set(n.lower() for n in want) or None

    # ---- the ground tie's current, if this engine reports element currents.
    # The note above promised a number, so either produce it or say it is not
    # there; a field named in a note and missing from the answer is the same
    # defect as a field that is invented.
    if gnode:
        tie = None
        for el in (res.get("elements") or []):
            if str(el.get("name") or "") == "Vgnd":
                tie = el.get("current_a")
                break
        if tie is None:
            notes.append("the 0 V tie's current is not reported: engine %r returns "
                         "node voltages only" % (out.get("engine_used")
                                                  or (out.get("extra") or {})
                                                  .get("engine_used") or "?",))
        else:
            out["value"]["ground_current_a"] = tie
            out["value"]["ground_current_sign"] = (
                "positive = the 0 V rail pushes current INTO the tied node (the "
                "engine's own i(V) convention, cross-checked on a 1 ohm load: "
                "1 V across 1 ohm reports +1 A through the source)")
            notes.append("the 0 V tie carried %.6g A (Vgnd): that is the return "
                         "current arriving at %s" % (tie, gnode))
    if kind == "op":
        nodes = res.get("nodes_v") or {}
        if keep:
            nodes = {k: x for k, x in nodes.items() if k.lower() in keep}
        strongest = sorted(nodes.items(), key=lambda kv: -abs(kv[1] or 0.0))[:20]
        out["value"]["board_nodes_v"] = dict(strongest)
        out["value"]["node_count_reported"] = len(nodes)
    elif keep:
        for p in (res.get("points") or []):
            nv = p.get("nodes_v")
            if isinstance(nv, dict):
                p["nodes_v"] = {k: x for k, x in nv.items() if k.lower() in keep}
    out["value"]["board"] = {
        "nets": v["counts"]["nets"],
        "nodes": v["counts"]["nodes"],
        "elements": v["counts"]["elements"],
        "skipped": v["skipped"],
        "islands": v.get("islands") or [],
        "copperless_pads": v.get("copperless_pads") or [],
        "return_net": v["return_net"],
        "references": v["references"],
        "drives": n_src, "loads": n_load, "ground": gnode,
        "analysis": kind,
        "notes": notes,
        "not_modelled": v["not_modelled"],
        "deck": text,
        "deck_type": "the BOARD's own copper as R/L/C; every component and source "
                     "in it was attached by the caller",
    }
    out.setdefault("extra", {})
    out["extra"]["board_deck_lines"] = len(deck)
    out["assumptions"] = dict(out.get("assumptions") or {})
    out["assumptions"]["board"] = ("R from oracle.board_ir_drop's graph (the same "
                                   "builder that answers the IR-drop question), L "
                                   "and C from the transmission line each segment "
                                   "forms (calc.feedline)")
    out["basis"] = (out.get("basis") or "") + (
        "; the deck is this server's extraction of the board file and the answer is "
        "the engine's solve of that deck")
    return out
