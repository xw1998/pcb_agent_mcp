"""Cascaded RF chain: gain, noise figure, intercept point, sensitivity.

Relations (all standard, all checked against the worked example in
rf-circuit-design / SKILL.md section 4.4):

    Friis:      F_total = F1 + (F2-1)/G1 + (F3-1)/(G1 G2) + ...
    cascade:    NF_total(dB) = 10 log10(F_total)
    IP3:        1/IIP3 = 1/IIP3_1 + G1/IIP3_2 + (G1 G2)/IIP3_3 + ...
                (powers in mW, i.e. dBm converted; G are numeric gains)
    floor:      kTB = -174 dBm/Hz at 290 K
    sens:       P_min = -174 + NF + 10 log10(B) + SNR_required

The formula is only as good as its inputs, and it is fed the way the skill's
own example is written: each stage is a block whose gain, NF and (optionally)
linearity are known AT THAT STAGE'S OWN impedance. A filter in front of an LNA
is a stage with gain = -insertion loss and NF = insertion loss - that is what
"the first stage decides" actually means, and it is not a footnote.
"""
import math

from .result import ok, err

T0_DBM_PER_HZ = -173.975  # 10 log10(k T0 * 1 Hz) with T0 = 290 K, in dBm


def _f(name, v):
    try:
        x = float(v)
    except (TypeError, ValueError):
        raise ValueError(name + " must be numeric")
    if not math.isfinite(x):
        raise ValueError(name + " must be finite")
    return x


def _db2lin_power(db):
    return 10.0 ** (db / 10.0)


def _lin2db_power(x):
    return 10.0 * math.log10(x)


def cascade(stages, bandwidth_hz=None, required_snr_db=None):
    """Gain, NF and IIP3 of a chain of blocks, plus where each number came from."""
    if not isinstance(stages, (list, tuple)) or not stages:
        return err("stages must be a non-empty list", "ARG_REQUIRED",
                   hint="each stage: {name, gain_db, nf_db, iip3_dbm?}")

    parsed = []
    for i, st in enumerate(stages):
        if not isinstance(st, dict):
            return err("stage %d is not an object" % i, "ARG_INVALID")
        name = str(st.get("name") or ("stage%d" % (i + 1)))
        try:
            g = _f(name + ".gain_db", st.get("gain_db"))
        except ValueError as exc:
            return err(str(exc), "ARG_REQUIRED",
                       hint="a block without a gain is a block that was not "
                            "specified; a passive loss is a negative gain")
        nf = st.get("nf_db")
        if nf is not None:
            try:
                nf = _f(name + ".nf_db", nf)
            except ValueError as exc:
                return err(str(exc), "ARG_INVALID")
            if nf < 0:
                return err(name + ": nf_db cannot be negative", "ARG_RANGE",
                           hint="0 dB is the noiseless-termination ideal; a "
                                "passive attenuator has NF = its loss")
        ip3 = st.get("iip3_dbm", st.get("oip3_dbm"))
        kind = "iip3" if st.get("iip3_dbm") is not None else (
            "oip3" if st.get("oip3_dbm") is not None else None)
        if ip3 is not None:
            try:
                ip3 = _f(name + ".iip3_dbm", ip3)
            except ValueError as exc:
                return err(str(exc), "ARG_INVALID")
            if kind == "oip3":
                ip3 = ip3 - g  # refer it to the input of its own stage
        parsed.append({"name": name, "gain_db": g, "nf_db": nf, "iip3": ip3})

    # ---- gain
    g_total_db = sum(p["gain_db"] for p in parsed)
    g_total = _db2lin_power(g_total_db)

    # ---- noise figure (needs every stage)
    contributions = []
    f_total = None
    missing = [p["name"] for p in parsed if p["nf_db"] is None]
    if not missing:
        g_cum = 1.0
        parts = []
        for i, p in enumerate(parsed):
            excess = _db2lin_power(p["nf_db"]) - 1.0
            term = excess if i == 0 else excess / g_cum
            parts.append(term)
            g_cum *= _db2lin_power(p["gain_db"])
        f_total = 1.0 + sum(parts)
        nf_total_db = _lin2db_power(f_total)
        for p, term in zip(parsed, parts):
            # excess noise factor each stage's own (F-1) contributes
            contributions.append({
                "stage": p["name"],
                "excess_factor_share": round(term / (f_total - 1.0), 4)
                if f_total > 1.0 else 0.0,
            })
    else:
        nf_total_db = None

    # ---- intercept point
    ip3_stages = [p for p in parsed if p["iip3"] is not None]
    iip3_total_db = None
    missing_ip3 = []
    if len(ip3_stages) == len(parsed):
        inv = 0.0
        g_cum = 1.0
        for p in parsed:
            inv += g_cum / _db2lin_power(p["iip3"])
            g_cum *= _db2lin_power(p["gain_db"])
        iip3_total_db = _lin2db_power(1.0 / inv)
    elif ip3_stages:
        missing_ip3 = [p["name"] for p in parsed if p["iip3"] is None]
    else:
        missing_ip3 = [p["name"] for p in parsed]

    out = {
        "stages": [{"name": p["name"], "gain_db": p["gain_db"],
                    "nf_db": p["nf_db"],
                    "iip3_dbm": None if p["iip3"] is None else round(p["iip3"], 3)}
                   for p in parsed],
        "gain_total_db": round(g_total_db, 4),
        "gain_total_linear": round(g_total, 6),
        "nf_total_db": None if nf_total_db is None else round(nf_total_db, 4),
        "noise_contributions": contributions,
        "iip3_total_dbm": None if iip3_total_db is None else round(iip3_total_db, 4),
        "oip3_total_dbm": None if iip3_total_db is None
                          else round(iip3_total_db + g_total_db, 4),
        "answers_only_when_every_stage_is_known": {
            "nf_total_db": ("every stage needs nf_db; missing: " + ", ".join(missing))
                           if missing else "complete",
            "iip3_total_dbm": ("every stage needs iip3_dbm/oip3_dbm; missing: "
                               + ", ".join(missing_ip3))
                              if iip3_total_db is None else "complete",
        },
    }

    if nf_total_db is not None and contributions:
        top = max(contributions, key=lambda c: c["excess_factor_share"])
        out["dominant_noise_stage"] = {
            "stage": top["stage"],
            "share_of_excess_noise": top["excess_factor_share"],
            "meaning": "the excess noise factor (F-1) is what is added on top "
                       "of a noiseless chain; the share says where it comes "
                       "from, and it moves the total only through that stage "
                       "and the gain in front of it",
        }

    if bandwidth_hz is not None:
        try:
            b = _f("bandwidth_hz", bandwidth_hz)
        except ValueError as exc:
            return err(str(exc), "ARG_INVALID")
        if b <= 0:
            return err("bandwidth_hz must be > 0", "ARG_RANGE")
        if nf_total_db is None:
            out["sensitivity_dbm"] = None
            out["sensitivity_why_not"] = ("needs the cascade NF, which needs "
                                          "every stage's nf_db")
        else:
            snr = 0.0
            if required_snr_db is not None:
                try:
                    snr = _f("required_snr_db", required_snr_db)
                except ValueError as exc:
                    return err(str(exc), "ARG_INVALID")
            floor = T0_DBM_PER_HZ + 10.0 * math.log10(b)
            out["thermal_floor_dbm"] = round(floor, 3)
            out["sensitivity_dbm"] = round(floor + nf_total_db + snr, 3)
            out["sensitivity_definition"] = (
                "input level that yields the required SNR in this bandwidth, "
                "i.e. -174 + NF + 10log10(B) + SNR; a real receiver also has "
                "spurs, blocking and phase noise, none of which are here")
            out["required_snr_db"] = snr
            out["bandwidth_hz"] = b
    else:
        out["sensitivity_dbm"] = None
        out["sensitivity_why_not"] = ("no bandwidth given; kTB without B is "
                                      "not a number")

    return ok(
        value=out, unit="chain",
        model="friis_cascade_gain_nf_iip3",
        basis=("Friis: F = F1 + (F2-1)/G1 + ...; "
               "1/IIP3 = 1/IIP3_1 + G1/IIP3_2 + ...; "
               "kTB = -174 dBm/Hz at 290 K; "
               "P_min = -174 + NF + 10log10(B) + SNR"),
        precision=("exact arithmetic on the numbers you give. What it cannot "
                   "know: mismatch between stages (the -10 dB S11 you have is a "
                   "real loss), the correlation of the IP3 contributions (the "
                   "formula is worst case, not a measurement), compression "
                   "versus IP3 (10 dB apart is a rule of thumb, not a law) and "
                   "any frequency dependence"),
        assumptions={"oip3_dbm_is_converted_to_input_referred": True,
                     "stage_impedances": "50 ohm assumed by the dBm arithmetic",
                     "temperature_k": 290},
    )
