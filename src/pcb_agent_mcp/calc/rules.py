"""Design-rule catalogues for board LAYOUT and for the SCHEMATIC's parts list.

Where the rules come from (each rule carries its own `source` string):

    kicad-free / SKILL.md                  decoupling, 3W, layer-change return
                                            vias, crystal keep-out, test points,
                                            silk over pad, 6 mil / 0.3 mm limits
    rf-circuit-design / SKILL.md 3.4-3.6   impedance tolerance, bend radius,
                                            RF spacing, antenna keep-out, via
                                            pitch, RF chain layout priorities
    circuit-analysis / SKILL.md            the missing-part checklist (decoupling,
                                            bulk, pull-up, gate resistor, reverse
                                            protection, fuse, TVS, bleeder,
                                            test points, unused pins) and the
                                            Critical/High/Medium/Low severity
                                            vocabulary, plus the L0-L3 evidence
                                            ladder this module reports per rule

The single most important property: a rule with no measurement is
`undetermined`, never `pass`. A review that turns missing data into a green
tick is worse than no review, because it launders an unknown into a decision.
For the same reason the answer lists, per undetermined rule, what exactly to
measure and the cheapest way to measure it (L0 = read the file, L1 = a
calculator or a multimeter, L2 = simulation or a bench test, L3 = a lab).

Nothing here inspects a board by itself. `layout_review` takes the facts YOU
measured (or the ones board_* tools read) and grades them; the catalogue tool
`design_rules` says which fact each rule needs.
"""
import math

from .result import ok, err
from . import current as _current
from . import creepage as _creepage
from . import rf as _rf

SEVERITIES = ("Critical", "High", "Medium", "Low")


def _n(v):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(x):
        return None
    return x


# ------------------------------------------------------------------ limit fns
def _limit_three_w(facts):
    w = _n(facts.get("trace_width_mm"))
    if w is None:
        return None, ("needs trace_width_mm: the 3W rule is three times THE "
                      "line's own width, so without the width there is no limit")
    return 3.0 * w, "3 x trace_width_mm"


def _limit_three_w_rf(facts):
    """3W on the RF LINE's own width, which the measurement already knows.

    `trace_width_mm` is the generic input a caller supplies; an RF rule graded
    against it would be graded against whichever trace the caller happened to
    have in mind. The ruleset measures the narrowest RF track on the board
    (`rf_line_width_mm`) and that is the width 3W is three times.
    """
    w = _n(facts.get("rf_line_width_mm"))
    if w is not None:
        return 3.0 * w, "3 x rf_line_width_mm (the narrowest RF track measured)"
    return _limit_three_w(facts)


def _limit_lambda20(facts):
    f = _n(facts.get("freq_ghz"))
    if f is None:
        return None, ("needs freq_ghz: lambda/20 has no value without a frequency "
                      "- picking 1 mm for a 2.4 GHz board is a guess, not a rule")
    er = _n(facts.get("er"))
    r = _rf.lambda_over_20(f, 4.3 if er is None else er)
    if r.get("status") != "ok":
        return None, r.get("message")
    return r["value"], "lambda_g/20 at %g GHz, er=%g" % (f, 4.3 if er is None else er)


def _limit_width_for_current(facts):
    i = _n(facts.get("current_a"))
    if i is None:
        return None, ("needs current_a: the required width comes from the IPC-2221 "
                      "chart for THIS current, so without it there is no limit")
    std = facts.get("current_standard")
    if not std:
        return None, ("needs current_standard ('ipc2221' or 'ipc2152'): the two "
                      "standards differ by more than 3x on the same question, so "
                      "picking one for the caller would be a wrong answer")
    d = _n(facts.get("allowed_delta_t_c"))
    oz = _n(facts.get("copper_oz"))
    layer = facts.get("layer") or "external"
    r = _current.width_for_current(i, 10.0 if d is None else d,
                                   1.0 if oz is None else oz, layer, std)
    if r.get("status") != "ok":
        return None, r.get("message")
    return float(r["value"]), ("%s for %.3g A at %.3g degC rise, %.3g oz, %s"
                                    % (std, i, 10.0 if d is None else d,
                                       1.0 if oz is None else oz, layer))


def _limit_creepage(facts):
    v = _n(facts.get("voltage_v"))
    if v is None:
        return None, ("needs voltage_v: creepage/clearance are a function of the "
                      "working voltage, pollution degree and material group")
    kw = {"pollution_degree": int(facts.get("pollution_degree") or 2),
          "material_group": facts.get("material_group") or "IIIa"}
    if facts.get("pcb_material") is not None:
        kw["pcb_material"] = bool(facts.get("pcb_material"))
    if facts.get("transient_v") is not None:
        kw["transient_v"] = facts["transient_v"]
    r = _creepage.spacing_required(v, **kw)
    if r.get("status") != "ok":
        return None, r.get("message")
    lim = max(r["value"]["creepage_mm"], r["value"]["clearance_mm"])
    return lim, ("IEC 60664-1 spacing for %.3g V (pollution %s, group %s): "
                 "creepage %.3f mm / clearance %.3f mm, the larger applies"
                 % (v, kw["pollution_degree"], kw["material_group"],
                    r["value"]["creepage_mm"], r["value"]["clearance_mm"]))


# ------------------------------------------------------------------- catalogues
def _r(rid, title, category, severity, fact, unit, kind, limit, how, level,
       source, why, limit_fn=None):
    d = {
        "id": rid, "title": title, "category": category, "severity": severity,
        "fact": fact, "unit": unit, "kind": kind,
        "limit": limit, "how_to_measure": how, "evidence_level": level,
        "source": source, "why": why,
    }
    if limit_fn:
        d["limit_fn"] = limit_fn
    return d


LAYOUT_RULES = [
    _r("layout.reference_plane_continuous",
       "no signal runs over a split in its reference plane",
       "return path", "Critical", "signal_crosses_plane_split", "bool", "bool_false",
       False,
       "board_cross_split on the real board, or look at every zone on the plane "
       "layer under each signal",
       "L1", "ADI《PCB设计秘籍》p23/p38 (接地层割裂: 返回电流必须绕行、电感增加; 一般规则: 接地层或直流电压层不应被分隔开) [T1]",
       "a slot in the reference turns the return current into a loop antenna: "
       "it is the single largest EMC mistake and it is invisible in the schematic"),
    _r("layout.decoupling_distance",
       "each decoupling capacitor sits within 3 mm of the pin it serves",
       "power integrity", "High", "decoupling_max_distance_to_pin_mm", "mm",
       "max", 3.0,
       "board_components positions plus the schematic's rail mapping, or measure "
       "pad-to-pad on the layout",
       "L0", "ADI《PCB设计秘籍》p13/p42 (陶瓷电容应尽量靠近IC电源引脚; 去耦电容应靠近各自的引脚) — 原则有原文, 3 mm 阈值是本仓库默认值 [T1]",
       "beyond about 3 mm the trace inductance dominates and the cap stops "
       "bypassing anything at the frequencies it was bought for"),
    _r("layout.decoupling_same_layer",
       "the decoupling cap is on the same layer as the pin, no via in between",
       "power integrity", "Medium", "decoupling_via_in_path", "bool", "bool_false",
       False,
       "look at the cap's own connection: a via between pad and pin adds ~1 nH",
       "L0", "ADI《PCB设计秘籍》p42 (好的方法是将去耦电容直接接到引脚上, 而不通过过孔) [T1]",
       "a via in the decoupling path adds inductance that no capacitance value "
       "can buy back"),
    _r("layout.bulk_per_ic", "one bulk capacitor (>=1 uF) per IC, per rail",
       "power integrity", "Medium", "ics_without_bulk_cap", "count", "count_max", 0,
       "count ICs and their bulk caps in the schematic",
       "L0", "kicad-free (每 IC 10 uF)",
       "the bulk cap holds the rail through load steps; the 100 nF only handles "
       "the switching edge"),
    _r("layout.hf_per_rail",
       "every rail that feeds several pins has a high-frequency capacitor "
       "(<= 220 nF) on it",
       "power integrity", "High", "rails_without_hf_cap", "count", "count_max", 0,
       "count the capacitors per rail in the schematic or the BOM; one 100 nF per "
       "rail clears this rule, placement is layout.decoupling_distance",
       "L0", "docs/144 电源树 (每域一颗 10uF 输出) + docs/154 P1 去耦 + "
       "kicad-free (每 IC 0.1uF 与 10uF 并联)",
       "the bulk cap holds the rail through a load step but is inductive at the "
       "switching edge; without a small part the rail has no low-impedance path "
       "above a few hundred kHz, and the bulk counter reads that hole as covered"),
    _r("layout.stitching_via_pitch",
       "ground fence / stitching vias are closer than lambda_g/20",
       "shielding", "High", "stitching_via_pitch_mm", "mm", "max", None,
       "measure the pitch between vias in the fence on the copper layers",
       "L1", "rf-circuit-design 3.5; cavity_via_fence",
       "a fence coarser than lambda/20 radiates through the gaps it was built to "
       "close", limit_fn=_limit_lambda20),
    _r("layout.rf_z0_tolerance",
       "RF lines hold their target impedance within 5 percent",
       "impedance", "High", "rf_line_z0_error_pct", "percent", "max", 5.0,
       "re-solve each RF line with feedline_impedance on the FAB's stackup (not "
       "the datasheet er)",
       "L2", "rf-circuit-design 3.4 (50 ohm +/-5 %)",
       "a 10 % impedance error already costs about -0.5 dB return loss at the "
       "band edge and moves the match you just tuned"),
    _r("layout.rf_right_angle_bend",
       "no 90 degree (or sharper) bend on an RF line",
       "impedance", "High", "has_right_angle_bend_on_rf", "bool", "bool_false", False,
       "look at the routed RF nets' vertices; a right angle is a lumped "
       "capacitance and an etch trap",
       "L0", "ROHM DC/DC layout (拐角布线不要设计为直角，设计为45°或圆弧状); ADI《PCB设计秘籍》p48 (差分对布线时应避免急转弯 90°) [T1, 2 sources]; this project forbids arcs, so 45 only [user rule]",
       "a right angle is a small shunt capacitance at the corner plus a place "
       "the etchant undercuts"),
    _r("layout.rf_bend_radius", "bends on RF lines keep radius >= 3 line widths",
       "impedance", "Medium", "rf_min_bend_radius_mm", "mm", "min", None,
       "measure the inner radius of the tightest bend on the RF net",
       "L1", "rf-circuit-design 3.4 (最小弯曲半径 3w)",
       "below 3w the corner behaves as a shunt capacitance and the local width "
       "no longer matches the impedance", limit_fn=_limit_three_w_rf),
    _r("layout.rf_spacing_3w",
       "adjacent same-layer traces keep 3 line widths of edge-to-edge gap",
       "isolation", "High", "rf_min_spacing_to_other_trace_mm", "mm", "min", None,
       "board_clearance restricted to the RF nets, or measure the closest approach",
       "L1", "ADI《PCB设计秘籍》p48 (同层铜皮铺地: 从走线到铜皮的推荐最小间隙为走线宽度的3倍) — 原语境是差分对到铜皮, 3W 用于线-线属外推 [T1]",
       "3W is the point where coupling to a neighbour stops moving the impedance; "
       "below it the neighbour is part of your matching network",
       limit_fn=_limit_three_w_rf),
    _r("layout.rf_to_board_edge",
       "RF lines stay at least 3 mm from the board edge",
       "isolation", "Medium", "rf_min_distance_to_edge_mm", "mm", "min", 3.0,
       "board_clearance against the outline, or measure on the layout",
       "L1", "rf-circuit-design 3.4 (射频线与板边 > 3 mm)",
       "the edge radiates and the milled edge tolerance is not impedance "
       "controlled"),
    _r("layout.antenna_keepout",
       "the antenna keeps 10 mm clear of other RF lines and copper",
       "isolation", "High", "antenna_min_distance_to_other_rf_mm", "mm", "min", 10.0,
       "board_antenna_keepout on the proposed rectangle - it reports what copper "
       "is really inside",
       "L1", "rf-circuit-design 3.4 (天线与其他射频线 > 10 mm)",
       "copper inside the near field detunes and absorbs; the datasheet keep-out "
       "assumes nothing is there"),
    _r("layout.layer_change_return_via",
       "every RF / fast-signal layer change has ground vias beside it",
       "return path", "High", "layer_changes_without_return_via", "count",
       "count_max", 0,
       "look at each via on an RF net: a ground via within a few tenths of a mm "
       "on both sides",
       "L0", "rf-circuit-design 3.4 (换层处理); kicad-free (换层加地过孔)",
       "without a return via the current jumps the plane pair at the via and that "
       "discontinuity is both a reflection and a radiator"),
    _r("layout.diffpair_skew",
       "differential pairs are matched within 5 mil (0.127 mm)",
       "impedance", "High", "diffpair_length_skew_mm", "mm", "max", 0.127,
       "board_nets length per net, differenced inside each pair",
       "L1", "ADI《PCB设计秘籍》p47 (成对差分走线的长度须相同; 信号须同时到达接收器) — 5 mil 容差是本仓库默认值, 原文未给数字 [T1]",
       "skew turns the differential signal into common mode, which is exactly "
       "what the pair was built to avoid"),
    _r("layout.diffpair_gap_constant",
       "the gap inside a differential pair does not change along the run",
       "impedance", "Medium", "diffpair_gap_varies", "bool", "bool_false", False,
       "walk the pair's centre line and compare gaps",
       "L0", "ADI《PCB设计秘籍》p47 (同一差分对内的走线间距在全长范围内须保持不变, 否则阻抗不匹配导致反射) [T1]",
       "a local gap change is a local impedance change, i.e. a reflection; the "
       "coupling is set by the gap, not by the nominal width"),
    _r("layout.power_trace_width",
       "power traces are wide enough for their current at the allowed rise",
       "power integrity", "High", "power_trace_width_mm", "mm", "min", None,
       "board_nets per net, plus the current each rail actually carries",
       "L1", "ADI《PCB设计秘籍》p39/p44 (大电流走线应短而宽; 小信号 10~15 mil, 大电流网络建议至少 20 mil) — 书本是固定下限, 按电流算是 IPC-2221 口径 [T1]",
       "an undersized trace is a fuse you did not design: the drop is invisible "
       "until the board browns out or the mask discolours",
       limit_fn=_limit_width_for_current),
    _r("layout.power_pad_thermal_vias",
       "power pads that must dissipate have a via field under them",
       "thermal", "Medium", "power_pad_has_thermal_vias", "bool", "bool_true", True,
       "count vias inside the pad / thermal relief of each hot part",
       "L1", "ADI《PCB设计秘籍》p27/p30/p41 (裸露焊盘各部分都要有过孔连到地, 每区多个过孔; 大电流回路用多个过孔并塞孔; 这些过孔不要用热风焊盘) [T1]",
       "a bottom-side plane only helps if the heat can get there; the via field "
       "is the only path through the laminate"),
    _r("layout.magnetic_keepout",
       "no track crosses an inductor or ferrite bead body on its own layer or "
       "the layer beside it",
       "isolation", "High", "trace_under_magnetic", "bool", "bool_false", False,
       "board_components and board_nets: every track whose centreline (grown by "
       "half its width) meets a magnetic part's pad hull, on the part's own "
       "side and on the one copper layer next to it",
       "L0", "ADI RAQ#164 (no sensitive control traces under an inductor, internal layers or backside); NEXTPCB inductor-placement; ROHM DC/DC layout (电感正下方禁布线) [3 sources agree]",
       "a switching inductor is a field source sitting on a small closed loop: "
       "copper under it is a shorted turn that heats the part and couples the "
       "ripple into every track that crosses it, and a bead is the same "
       "argument at a smaller scale"),
    _r("layout.magnetic_keepout_graze",
       "no track edge grazes a magnetic part's pad hull",
       "isolation", "Low", "trace_under_magnetic_graze", "bool", "bool_true",
       False,
       "board_nets whose copper edge meets a magnetic pad hull but whose axis "
       "stays outside it",
       "L3", "derived: the same three sources state the body is off-limits, so the corner of a rectangular pad hull is not a place to aim at either [3 sources]",
       "the hull is a rectangle over the pads, so its corners are empty space; "
       "a diagonal track clipping such a corner is 0.1 mm from real copper. "
       "Measuring the edge here keeps the P1 crossing number honest instead of "
       "inflating it (17 hits minus 9 corner clips = 8 real crossings)"),
    _r("layout.magnetic_keepout_deep",
       "no other-net copper sits under a magnetic part on deeper layers",
       "isolation", "Low", "trace_under_magnetic_deep", "bool", "bool_false",
       False,
       "board_nets and board_vias on every copper layer below the one next to "
       "the part",
       "L1", "ADI RAQ#164 + ROHM (any layer incl. backside) vs NEXTPCB (backside allowed over a solid GND) - conflict recorded, this repo takes the strict reading",
       "the field is shielded by the plane underneath, not absent: copper two "
       "or three layers down still sees the fringe. It is a placement cost, so "
       "it is graded P2 and reported separately from the crossing on the part's "
       "own side - one number for both hid a 15-object failure inside 173"),
    _r("layout.magnetic_via_keepout",
       "no via sits under an inductor or ferrite bead",
       "isolation", "High", "via_under_magnetic", "bool", "bool_false", False,
       "board_vias whose centre lies inside a magnetic part's pad hull",
       "L0", "ADI RAQ#164 (a solid ground plane even under the coil); NEXTPCB (solid, unbroken ground plane) - a via breaks it, so this is derived not quoted",
       "a via in the middle of the inductor's loop punches the plane the field "
       "is trying to stay out of, and the switch node's return current then "
       "has a path straight through the part"),
    _r("layout.magnetic_to_hs_digital",
       "a switching inductor does not sit inside a high-speed digital part's escape "
       "corridor",
       "isolation", "High", "magnetic_to_hs_digital_min_mm", "mm", "min", 3.0,
       "measure pad-edge to pad-edge: every pad of every magnetic that owns a pad "
       "on a switch-node net, against every pad of every non-connector part that "
       "carries three or more high-speed interface pads (DDR / MIPI / RMII / LCD / "
       "SDMMC / USB / CLK / OSC). The fact reports the worst pair and the list "
       "behind it, so the answer names the two parts",
       "L1", "ADI RAQ#164 + ROHM DC/DC layout (电感远离敏感线路) put the field source "
       "and the sensitive node in different places; the 3 mm floor is this "
       "repository's default, the same scale `layout.decoupling_max_distance_to_pin_mm` "
       "uses [T1]",
       "the keep-out rules answer 'is copper under the part' and stop there, so a "
       "buck cell parked against the DDR reads clean: on RK3506 L3 (8.2 uH, the "
       "+1V35_DDR rail) sat 0.85 mm from the memory footprint and 2.12 mm from a "
       "DDR address pad with every keep-out reporting clean. The field itself is "
       "not the whole story - a shielded 4x4 mm part at 2 mm induces about 0.2 mV "
       "into a 10 mm DDR trace 0.1 mm above its plane, far under the receiver's "
       "margin - the three costs that are real are the shared return current under "
       "a switching loop, the escape corridor the part consumes, and the input-loop "
       "di/dt that has nowhere tight to go once the cell is wedged between two "
       "digital parts"),
    _r("layout.crystal_keepout_adjacent",
       "no copper crosses a crystal body on the layers beside it either",
       "isolation", "Medium", "trace_under_crystal_inner", "bool", "bool_false",
       False,
       "board_nets on every copper layer other than the crystal's own side, "
       "inside the crystal body",
       "L0", "GENUWAY (keep the area under the crystal well-grounded; keep clock/switching lines away) - the inner-layer extension is inferred, hence P2 not P1",
       "the oscillator node is high impedance and the coupling that gets "
       "forgotten is the one through the laminate: a track two layers down "
       "still pulls the frequency, and it is invisible to a check that only "
       "looks at the crystal's own side"),
    _r("layout.crystal_keepout",
       "no trace runs under a crystal or its load capacitors",
       "isolation", "Medium", "trace_under_crystal", "bool", "bool_false", False,
       "board_components plus board_nets in the crystal's courtyard",
       "L0", "kicad-free (晶振下方禁走线)",
       "coupling into the crystal node is both a frequency pull and a jitter "
       "source, and the node is high impedance so it needs very little"),
    _r("layout.crystal_guard",
       "the crystal is surrounded by a grounded guard and a local ground",
       "isolation", "Low", "crystal_has_guard_ring", "bool", "bool_true", True,
       "look at the copper around the crystal footprint",
       "L0", "kicad-free (晶振…包地)",
       "the guard ring keeps neighbouring return current out of the oscillator's "
       "own ground"),
    _r("layout.silkscreen_over_pad",
       "silkscreen does not print over an exposed pad",
       "assembly", "Low", "silkscreen_over_pad", "bool", "bool_false", False,
       "board_summary / board_view silkscreen vs pads, or the fab's DFM report",
       "L0", "kicad-free (丝印压焊盘)",
       "silk on a pad either blocks solder or is clipped by the fab anyway - a "
       "cosmetic rule with a real solderability cost"),
    _r("layout.test_points_present",
       "every rail and every external connector has a probeable test point",
       "assembly", "Medium", "test_points_on_rails_and_connectors", "bool",
       "bool_true", True,
       "count test points per net in the schematic/board",
       "L0", "kicad-free (测试点); rf-circuit-design 4.1",
       "without a test point the only way to probe is to touch a component, and "
       "that is how a production line loses a day"),
    _r("layout.min_via_hole", "via drill stays at or above 0.3 mm",
       "manufacturability", "Medium", "min_via_hole_mm", "mm", "min", 0.3,
       "board_stats smallest drill, or the fab's capability document",
       "L1", "kicad-free (最小过孔 0.3 mm) - a cheap-process convention, not a law",
       "below the process floor the plating aspect ratio goes out of control, "
       "and the via that fails is the one under the BGA"),
    _r("layout.min_trace_width", "the narrowest trace stays at or above 6 mil",
       "manufacturability", "Low", "min_trace_width_mm", "mm", "min", 0.1524,
       "board_stats narrowest track",
       "L1", "kicad-free (线宽线距 > 6 mil)",
       "6 mil is the no-extra-cost floor at most fabs; thinner is possible but "
       "priced and yields worse"),
]

SCHEMATIC_RULES = [
    _r("sch.isolation_spacing",
       "galvanic isolation gaps meet IEC 60664 creepage and clearance",
       "safety", "Critical", "min_isolation_gap_mm", "mm", "min", None,
       "measure the gap on the layout (not in the schematic - the schematic has "
       "no distances)",
       "L1", "IEC 60664-1 via creepage.py; circuit-analysis (隔离间距)",
       "this is the one rule where being wrong is a shock hazard rather than a "
       "yield problem", limit_fn=_limit_creepage),
    _r("sch.power_pins_without_decoupling",
       "every supply pin of every IC has its own 100 nF",
       "power integrity", "Critical", "power_pins_without_100nf", "count",
       "count_max", 0,
       "walk the netlist: each VDD/VCC/AVDD/VDDA pin needs a capacitor to the "
       "same ground the IC uses",
       "L0", "circuit-analysis (漏件清单: 去耦); kicad-free",
       "one cap shared by two pins is two caps' worth of inductance away from "
       "one of them"),
    _r("sch.reset_enable_floating",
       "reset / enable / mode pins are never left floating",
       "logic", "High", "reset_or_enable_pins_floating", "count", "count_max", 0,
       "for each such pin find the resistor or driver that sets its level, and "
       "check the internal pull defaults in the datasheet",
       "L0", "circuit-analysis (上/下拉); 防硬规则幻觉",
       "a floating enable is a part that switches on in a state nobody chose - "
       "usually during power ramp, which is also when it matters most"),
    _r("sch.leds_without_series_resistor",
       "every LED has a current-setting element",
       "protection", "High", "leds_without_series_resistor", "count",
       "count_max", 0,
       "for each LED find the resistor or constant-current driver and compute "
       "the current from the actual rail",
       "L0", "circuit-analysis (可复算: LED 电流)",
       "an LED straight across a rail is a short with a colour"),
    _r("sch.mosfets_without_gate_resistor",
       "MOSFET gates have a series resistor or a real gate driver",
       "protection", "Medium", "mosfets_without_gate_resistor", "count",
       "count_max", 0,
       "look at every gate: series R, or a driver whose output impedance and "
       "current are specified",
       "L0", "circuit-analysis (漏件清单: 栅极电阻)",
       "the gate is a capacitor the driver has to charge; without a resistor the "
       "edge rings and the EMI is the ring"),
    _r("sch.connectors_without_esd",
       "every external connector has ESD / TVS protection",
       "protection", "High", "connectors_without_esd", "count", "count_max", 0,
       "list connectors that leave the board, then the TVS/ESD part on each line",
       "L0", "circuit-analysis (漏件清单: TVS/ESD); rf-circuit-design 5.1",
       "the ESD event arrives through the connector; a protection part on the "
       "other side of a series resistor protects nothing"),
    _r("sch.reverse_polarity_protection",
       "a DC input cannot be destroyed by reversing the supply",
       "protection", "Medium", "has_reverse_polarity_protection", "bool",
       "bool_true", True,
       "look for a series FET, an ideal-diode controller or at least a diode on "
       "the input",
       "L0", "circuit-analysis (漏件清单: 防反接)",
       "reverse polarity is the most common field failure on a DC jack, and the "
       "cheapest fix is a part you place now"),
    _r("sch.input_fuse",
       "the input path has a fuse, PTC or current limit",
       "protection", "Medium", "has_input_fuse_or_ptc", "bool", "bool_true", True,
       "find the element and check its hold current against the rail's real draw",
       "L0", "circuit-analysis (漏件清单: 保险丝/PTC)",
       "a fuse is not for the load, it is for the wire and the connector that "
       "would otherwise become the fuse"),
    _r("sch.regulator_compensation",
       "every regulator / converter has the compensation its datasheet requires",
       "power integrity", "Medium", "regulators_without_compensation", "count",
       "count_max", 0,
       "for each regulator: the datasheet's typical application circuit, then "
       "which of its parts are missing here",
       "L0", "circuit-analysis (漏件清单: 补偿)",
       "an uncompensated loop is stable on the bench at room temperature and "
       "oscillates at the temperature corner or with a different load cap"),
    _r("sch.output_discharge_path",
       "rails that power a connector have a defined discharge path",
       "protection", "Low", "has_output_discharge_path", "bool", "bool_true", True,
       "look for a bleeder resistor or an active discharge in the regulator",
       "L0", "circuit-analysis (漏件清单: 放电通路)",
       "a rail with no load holds its charge and presents it to whatever is "
       "plugged in next"),
    _r("sch.unused_pins_floating",
       "unused inputs are tied off, not floating",
       "logic", "Medium", "unused_pins_left_floating", "count", "count_max", 0,
       "for each unused gate/pin: tied to a rail, to ground, or explicitly NC "
       "per the datasheet",
       "L0", "circuit-analysis (可复算: 接口电平)",
       "a floating CMOS input sits at half rail, draws crossbar current and "
       "injects noise into everything sharing that ground"),
    _r("sch.test_points_on_rails",
       "every rail has a test point",
       "assembly", "Low", "rails_without_test_point", "count", "count_max", 0,
       "count rails and their test points",
       "L0", "kicad-free (测试点)",
       "the bring-up sequence needs to look at each rail before anything else "
       "can be trusted"),
    _r("sch.rf_ports_have_matching",
       "every RF port has its matching network designed, not 'TBD'",
       "impedance", "Medium", "rf_ports_without_matching", "count", "count_max",
       0,
       "for each RF port the datasheet's recommended match (often a specific "
       "L/C with a specific layout) is present",
       "L0", "rf-circuit-design 5.1 (匹配网络元件值合理)",
       "the chip's datasheet match is part of the part: without it the gain and "
       "the return loss are not the datasheet's numbers"),
    _r("sch.pa_supply_cap_stages",
       "a PA / high-current RF stage has the full bypass ladder",
       "power integrity", "Medium", "pa_supply_cap_stages", "count", "min", 3,
       "count distinct decades on the PA rail (100 uF + 10 uF + 100 nF ...)",
       "L1", "rf-circuit-design 2.3 (PA 偏置: 100uF+10uF+100nF+10pF)",
       "each decade covers one band of the transient; a PA without the bottom "
       "decade modulates its own supply on every pulse"),
]

CATALOGUES = {"layout": LAYOUT_RULES, "schematic": SCHEMATIC_RULES}


def design_rules(kind="layout", ids=None, severity=None):
    """The rule catalogue: ids, the fact each needs, and how to measure it."""
    k = str(kind or "layout").strip().lower()
    if k not in CATALOGUES:
        return err("kind must be layout or schematic", "ARG_INVALID")
    rules = CATALOGUES[k]
    if ids:
        want = set(str(i) for i in (ids if isinstance(ids, (list, tuple)) else [ids]))
        unknown = sorted(want - set(r["id"] for r in rules))
        if unknown:
            return err("unknown rule id(s): " + ", ".join(unknown), "ARG_INVALID",
                       extra={"known_ids": [r["id"] for r in rules]})
        rules = [r for r in rules if r["id"] in want]
    if severity:
        want = str(severity).capitalize()
        if want not in SEVERITIES:
            return err("severity must be one of " + ", ".join(SEVERITIES),
                       "ARG_INVALID")
        rules = [r for r in rules if r["severity"] == want]
    rows = []
    for r in rules:
        row = {kk: r[kk] for kk in ("id", "title", "category", "severity", "fact",
                                    "unit", "kind", "how_to_measure",
                                    "evidence_level", "source", "why")}
        if "limit_fn" in r:
            row["limit"] = "computed from other facts (see how_to_measure)"
        else:
            row["limit"] = r["limit"]
        rows.append(row)
    facts = sorted(set(r["fact"] for r in rules) |
                   set(("trace_width_mm", "rf_line_width_mm", "freq_ghz",
                        "er", "current_a",
                        "current_standard", "allowed_delta_t_c", "copper_oz",
                        "layer", "voltage_v", "pollution_degree",
                        "material_group", "pcb_material", "transient_v")))
    return ok(
        value={"kind": k, "count": len(rows), "rules": rows,
               "facts_a_rule_may_need": facts,
               "facts_not_rules": "trace_width_mm / freq_ghz / current_a / "
                                  "current_standard / voltage_v and friends are "
                                  "INPUTS that compute a limit; they are not "
                                  "themselves graded"},
        unit="rule_catalogue", model="design_rule_catalogue",
        basis="kicad-free + rf-circuit-design + circuit-analysis checklists, "
              "each rule carrying its own source string",
        precision="a checklist is not a proof: passing a rule means this "
                  "particular failure mode was measured and not found, not that "
                  "the design is correct",
        assumptions={"severity": "Critical/High/Medium/Low follows the "
                                 "circuit-analysis vocabulary",
                     "evidence_levels": "L0 read the file, L1 calculate or "
                                        "multimeter, L2 simulate or bench, "
                                        "L3 lab"},
    )


def _grade(rule, facts):
    fact = rule["fact"]
    unit = rule["unit"]
    sev = rule["severity"]
    out = {
        "id": rule["id"], "title": rule["title"], "severity": sev,
        "category": rule["category"], "fact": fact, "unit": unit,
        "how_to_measure": rule["how_to_measure"],
        "evidence_level": rule["evidence_level"], "source": rule["source"],
        "why": rule["why"],
    }
    raw = facts.get(fact)
    limit = rule["limit"]
    limit_note = None
    if "limit_fn" in rule:
        limit, limit_note = rule["limit_fn"](facts)
    out["limit"] = limit
    if limit_note:
        out["limit_from"] = limit_note

    kind = rule["kind"]
    if raw is None:
        out["state"] = "undetermined"
        out["reason"] = "no measurement supplied for %r" % fact
        out["next_check"] = rule["how_to_measure"]
        return out

    if kind in ("bool_false", "bool_true"):
        val = raw if isinstance(raw, bool) else None
        if val is None:
            out["state"] = "undetermined"
            out["reason"] = "%r must be true/false, got %r" % (fact, raw)
            out["next_check"] = rule["how_to_measure"]
            return out
        out["measured"] = val
        expect = bool(limit)
        out["state"] = "pass" if val == expect else "fail"
        return out

    num = _n(raw)
    if num is None:
        out["state"] = "undetermined"
        out["reason"] = "%r must be a number, got %r" % (fact, raw)
        out["next_check"] = rule["how_to_measure"]
        return out
    out["measured"] = num
    if limit is None:
        out["state"] = "undetermined"
        out["reason"] = ("the limit could not be computed: "
                         + (limit_note or "missing inputs"))
        out["next_check"] = rule["how_to_measure"]
        return out
    if kind in ("max", "count_max"):
        out["state"] = "pass" if num <= limit + 1e-12 else "fail"
    elif kind in ("min",):
        out["state"] = "pass" if num >= limit - 1e-12 else "fail"
    else:  # pragma: no cover - catalogue is closed
        out["state"] = "undetermined"
        out["reason"] = "unknown rule kind %r" % kind
    return out


def review(kind, facts):
    """Grade every rule in a catalogue against the facts you measured."""
    k = str(kind or "layout").strip().lower()
    if k not in CATALOGUES:
        return err("kind must be layout or schematic", "ARG_INVALID")
    if not isinstance(facts, dict):
        return err("facts must be an object {fact_name: value}", "ARG_INVALID")
    if not facts:
        return err("no facts supplied", "ARG_MISSING",
                   hint="call design_rules first: it lists every fact the rules "
                        "need, and every one of those has a definite answer",
                   extra={"facts_to_supply": sorted(
                       set(r["fact"] for r in CATALOGUES[k]))})

    rules = CATALOGUES[k]
    checks = [_grade(r, facts) for r in rules]
    known_facts = set(r["fact"] for r in rules) | {
        "trace_width_mm", "rf_line_width_mm", "freq_ghz", "er", "current_a",
        "current_standard",
        "allowed_delta_t_c", "copper_oz", "layer", "voltage_v",
        "pollution_degree", "material_group", "pcb_material", "transient_v"}
    unknown = sorted(set(facts) - known_facts)

    def pick(state):
        return [c for c in checks if c["state"] == state]

    fails = pick("fail")
    undet = pick("undetermined")
    passes = pick("pass")
    fails.sort(key=lambda c: SEVERITIES.index(c["severity"]))
    undet.sort(key=lambda c: SEVERITIES.index(c["severity"]))

    worst = fails[0]["severity"] if fails else None
    verdict = ("fails_found" if fails else
               ("nothing_measured" if not passes else "measured_clean"))
    if not fails and undet:
        verdict = "clean_but_incomplete"

    out = {
        "subject": k,
        "verdict": verdict,
        "counts": {"pass": len(passes), "fail": len(fails),
                   "undetermined": len(undet), "rules": len(checks)},
        "worst_failing_severity": worst,
        "fails": fails,
        "undetermined": undet,
        "passes": [{"id": c["id"], "measured": c.get("measured"),
                    "limit": c.get("limit")} for c in passes],
        "unknown_facts_ignored": unknown,
        "what_this_does_not_prove": (
            "a pass means one named failure mode was measured and not found. It "
            "says nothing about the failure modes no rule names - thermal "
            "cycling, creep, tolerance stack-up, EMC beyond the layout rules, or "
            "the ones the fab's process adds. An undetermined rule is not a "
            "pass and must not be reported as one."),
    }
    if unknown:
        out["unknown_facts_note"] = (
            "these keys matched no rule and no computed limit; a typo here "
            "silently turns a rule into 'undetermined'")
    return ok(
        value=out, unit="review", model="rule_catalogue_against_measured_facts",
        basis="kicad-free + rf-circuit-design + circuit-analysis checklists; "
              "limits computed with current.py / creepage.py / rf.py where a "
              "physical law exists",
        precision="as good as the facts supplied: every rule names its evidence "
                  "level (L0 read / L1 calculate / L2 simulate or bench / L3 lab)",
        assumptions={"thresholds_are_conventions": True,
                     "missing_fact_never_passes": True},
    )
