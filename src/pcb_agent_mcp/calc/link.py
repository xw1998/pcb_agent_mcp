"""Link budget, range and the far-field geometry an antenna test needs.

The arithmetic here is standard and exact; what is NOT here is the part that
usually decides whether a product works: multipath, body and enclosure loss,
antenna detuning when the product is held, interference, and the difference between
a datasheet gain measured in a chamber and the gain on this board. Every answer
therefore reports the free-space margin and says plainly that the number is an
upper bound on range, not a prediction.

One trap this module exists to avoid: the same link can be described by four
different numbers (transmit power, EIRP, ERP, field strength) and mixing them is
the classic way to be 2.15 dB wrong. ERP is defined against a half-wave dipole, so
it is 2.15 dB below EIRP - that conversion is done here explicitly, once.
"""
import math

from .result import ok, err
from .rf import C0_MM_PER_NS

C_M_PER_S = 299792458.0

# The free-space path loss constant for d in metres and f in MHz:
# 20*log10(4*pi/c) with the units folded in.
# FSPL(dB) = 20log10(d_m) + 20log10(f_MHz) + K, K = 20log10(4*pi/c) + 120 = -27.5522.
# The 120 (not 180) is the MHz: 20log10(f_Hz) = 20log10(f_MHz) + 120. Getting this
# constant wrong by 60 dB is a factor of 1000 in range and it looks completely
# plausible in the output, so tests/test_link.py pins the textbook 2400 MHz / 1 m =
# 40.05 dB pair against it.
FSPL_CONST_DB = 20.0 * math.log10(4.0 * math.pi / C_M_PER_S) + 120.0   # -27.5522

def fspl_db(freq_mhz, distance_m):
    return 20.0 * math.log10(freq_mhz) + 20.0 * math.log10(distance_m) + FSPL_CONST_DB

def _num(name, v):
    try:
        return float(v)
    except (TypeError, ValueError):
        raise ValueError("%s must be numeric" % name)

# ------------------------------------------------------------------ Friis
def friis(tx_dbm, freq_mhz, distance_m, tx_gain_dbi=0.0, rx_gain_dbi=0.0,
          tx_loss_db=0.0, rx_loss_db=0.0, other_loss_db=0.0):
    """Received power in free space, with every term named separately.

    Prx = Ptx + Gtx + Grx - Ltx - Lrx - Lother - FSPL(d, f)

    Each term is reported so a reader can see what was included: a link budget whose
    "other loss" hides the body loss is how a product ships with half the range.
    """
    try:
        ptx = _num("tx_dbm", tx_dbm)
        f = _num("freq_mhz", freq_mhz)
        d = _num("distance_m", distance_m)
        gtx = _num("tx_gain_dbi", tx_gain_dbi)
        grx = _num("rx_gain_dbi", rx_gain_dbi)
        ltx = _num("tx_loss_db", tx_loss_db)
        lrx = _num("rx_loss_db", rx_loss_db)
        lo = _num("other_loss_db", other_loss_db)
    except ValueError as exc:
        return err(str(exc), "ARG_INVALID")
    if f <= 0 or d <= 0:
        return err("freq_mhz and distance_m must be > 0", "ARG_RANGE")
    path = fspl_db(f, d)
    prx = ptx + gtx + grx - ltx - lrx - lo - path
    lam_mm = C0_MM_PER_NS / (f / 1000.0)
    return ok(
        value={"prx_dbm": round(prx, 3),
               "path_loss_db": round(path, 3),
               "eirp_dbm": round(ptx + gtx - ltx, 3),
               "erp_dbm": round(ptx + gtx - ltx - 2.15, 3),
               "terms": {"tx_dbm": ptx, "tx_gain_dbi": gtx, "rx_gain_dbi": grx,
                         "tx_loss_db": -ltx, "rx_loss_db": -lrx,
                         "other_loss_db": -lo, "path_loss_db": -round(path, 3)},
               "lambda_mm": round(lam_mm, 4)},
        unit="dBm",
        model="friis_free_space",
        basis=("Friis: Prx = Ptx + Gtx + Grx - Ltx - Lrx - Lother - FSPL, "
               "FSPL = 20log10(d_m) + 20log10(f_MHz) + %.4f; ERP = EIRP - 2.15 dB "
               "(ERP is referenced to a half-wave dipole, EIRP to an isotropic "
               "radiator)" % FSPL_CONST_DB),
        precision=("exact free-space arithmetic. The physics is the approximation: no "
                   "multipath, no ground reflection, no body or enclosure loss, no "
                   "fresnel-zone obstruction. A real link in a building is typically "
                   "10-20 dB worse, and indoor range is dominated by those terms"),
        assumptions={"freq_mhz": f, "distance_m": d,
                     "polarisation": "assumed matched; a cross-polarised pair loses "
                                     "another 20 dB or more",
                     "antennas": "gain in the direction of the other end, not peak"},
        extra={"warnings": [
            "this is an upper bound on range, not a prediction",
            "add body/enclosure loss (typically 3-10 dB at 2.4 GHz) into other_loss_db "
            "before quoting a range",
        ]},
    )

def max_range(tx_dbm, freq_mhz, sensitivity_dbm, tx_gain_dbi=0.0, rx_gain_dbi=0.0,
              tx_loss_db=0.0, rx_loss_db=0.0, other_loss_db=0.0, fade_margin_db=None):
    """Free-space range at which the received power equals the sensitivity."""
    try:
        ptx = _num("tx_dbm", tx_dbm)
        f = _num("freq_mhz", freq_mhz)
        sens = _num("sensitivity_dbm", sensitivity_dbm)
        margin = 0.0 if fade_margin_db is None else _num("fade_margin_db", fade_margin_db)
        budget = (ptx + _num("tx_gain_dbi", tx_gain_dbi) + _num("rx_gain_dbi", rx_gain_dbi)
                  - _num("tx_loss_db", tx_loss_db) - _num("rx_loss_db", rx_loss_db)
                  - _num("other_loss_db", other_loss_db) - margin)
    except ValueError as exc:
        return err(str(exc), "ARG_INVALID")
    if f <= 0:
        return err("freq_mhz must be > 0", "ARG_RANGE")
    allowed = budget - sens                     # the FSPL the link can afford
    # FSPL = 20log10(d) + 20log10(f) + K  =>  d = 10^((allowed - 20log10(f) - K)/20)
    d_m = 10.0 ** ((allowed - 20.0 * math.log10(f) - FSPL_CONST_DB) / 20.0)
    lam_mm = C0_MM_PER_NS / (f / 1000.0)
    out = {
        "range_m": round(d_m, 3),
        "range_km": round(d_m / 1000.0, 4),
        "allowed_path_loss_db": round(allowed, 3),
        "usable_budget_db": round(budget - sens, 3),
        "wavelength_mm": round(lam_mm, 4),
        "warnings": [
            "free space only: indoor range with walls and a body in the way is usually "
            "1/3 to 1/2 of this",
            "sensitivity is the datasheet number for a BER/PER the datasheet states - "
            "check which one, they differ by 10 dB or more across vendors",
        ],
    }
    if fade_margin_db is not None:
        out["fade_margin_db"] = margin
        out["note"] = "the fade margin was subtracted from the budget before solving"
    return ok(
        value=out,
        unit="m",
        model="friis_max_range",
        basis=("solve FSPL(d) = budget - sensitivity for d, with "
               "FSPL = 20log10(d_m)+20log10(f_MHz)+%.4f" % FSPL_CONST_DB),
        precision=("exact inversion of the free-space formula. As a RANGE it is an "
                   "upper bound: it assumes the antennas are pointed at each other and "
                   "polarisation-matched, which is not how a product is used"),
        assumptions={"freq_mhz": f, "sensitivity_dbm": sens,
                     "fade_margin_db": margin},
    )

def aperture(gain_dbi, freq_mhz):
    """Effective aperture of an antenna from its gain: Ae = G*lambda^2/(4*pi)."""
    try:
        g = _num("gain_dbi", gain_dbi)
        f = _num("freq_mhz", freq_mhz)
    except ValueError as exc:
        return err(str(exc), "ARG_INVALID")
    if f <= 0:
        return err("freq_mhz must be > 0", "ARG_RANGE")
    lam_m = C0_MM_PER_NS / (f / 1000.0) / 1000.0
    ae = 10.0 ** (g / 10.0) * lam_m ** 2 / (4.0 * math.pi)
    return ok(
        value={"gain_dbi": g, "effective_aperture_m2": ae,
               "effective_aperture_cm2": round(ae * 1e4, 4),
               "effective_aperture_mm2": round(ae * 1e6, 2),
               "lambda_mm": round(lam_m * 1000.0, 4)},
        unit="m^2",
        model="effective_aperture",
        basis="Ae = G*lambda^2/(4*pi), G the linear gain",
        precision="exact given the gain; the gain of a real board antenna in a real "
                  "enclosure is the uncertainty, not this formula",
        assumptions={"gain_dbi": g, "freq_mhz": f},
    )

# ------------------------------------------------------- near/far field geometry
def far_field(size_mm, freq_mhz):
    """Where the far field starts for an antenna of this largest dimension.

    Two criteria, both reported because both are used in practice: the Fraunhofer
    distance 2D^2/lambda (the one that matters for a pattern measurement) and the
    near-field boundary lambda/(2*pi), which is where the reactive field has died
    down. A range that satisfies the second but not the first measures the near
    field, and the pattern it reports is not the antenna's.
    """
    try:
        D = _num("size_mm", size_mm)
        f = _num("freq_mhz", freq_mhz)
    except ValueError as exc:
        return err(str(exc), "ARG_INVALID")
    if D <= 0 or f <= 0:
        return err("size_mm and freq_mhz must be > 0", "ARG_RANGE")
    lam_mm = C0_MM_PER_NS / (f / 1000.0)
    fraunhofer = 2.0 * D * D / lam_mm
    reactive = lam_mm / (2.0 * math.pi)
    five_d = 5.0 * D
    return ok(
        value={"lambda_mm": round(lam_mm, 4),
               "fraunhofer_distance_mm": round(fraunhofer, 3),
               "reactive_near_field_end_mm": round(reactive, 3),
               "five_d_mm": round(five_d, 3),
               "D_over_lambda": round(D / lam_mm, 5),
               "far_field_starts_at_mm": round(max(fraunhofer, reactive, five_d), 3),
               "which_criterion": ("5*D" if five_d >= max(fraunhofer, reactive)
                                   else ("2*D^2/lambda" if fraunhofer >= reactive
                                         else "lambda/(2*pi)")),
               "note": ("the three criteria answer different questions: 5*D is the "
                        "engineering rule of thumb, 2*D^2/lambda is the Fraunhofer "
                        "distance a pattern measurement needs, and lambda/(2*pi) is "
                        "where the reactive field has decayed. The largest is used")},
        unit="mm",
        model="far_field_boundary",
        basis="r_ff = 2*D^2/lambda (Fraunhofer); reactive near field ends at "
              "lambda/(2*pi)",
        precision="exact geometry. It bounds where a pattern can be measured, and says "
                  "nothing about how large the error is just inside it",
        assumptions={"size_mm": D, "freq_mhz": f},
    )

def fresnel_radius(freq_mhz, d1_m, d2_m, n=1):
    """First Fresnel zone radius at a point between two antennas.

    r_n = sqrt(n*lambda*d1*d2/(d1+d2)). This is the clearance a line-of-sight link
    needs: obstructing 40 % of the first zone costs about 1 dB, obstructing 60 %
    costs 6 dB or more.
    """
    try:
        f = _num("freq_mhz", freq_mhz)
        d1 = _num("d1_m", d1_m)
        d2 = _num("d2_m", d2_m)
        nn = _num("n", n)
    except ValueError as exc:
        return err(str(exc), "ARG_INVALID")
    if f <= 0 or d1 <= 0 or d2 <= 0 or nn <= 0:
        return err("freq_mhz, d1_m, d2_m and n must be > 0", "ARG_RANGE")
    lam_m = C_M_PER_S / (f * 1e6)
    r = math.sqrt(nn * lam_m * d1 * d2 / (d1 + d2))
    return ok(
        value={"radius_m": round(r, 4), "radius_mm": round(r * 1000.0, 2),
               "lambda_m": round(lam_m, 5), "zone": nn,
               "clearance_60pct_m": round(0.6 * r, 4)},
        unit="m",
        model="fresnel_zone_radius",
        basis="r_n = sqrt(n*lambda*d1*d2/(d1+d2))",
        precision="exact geometry for a point-to-point path in free space; the 60 % "
                  "clearance figure is the usual engineering criterion, not a formula",
        assumptions={"freq_mhz": f, "d1_m": d1, "d2_m": d2, "n": nn},
    )
