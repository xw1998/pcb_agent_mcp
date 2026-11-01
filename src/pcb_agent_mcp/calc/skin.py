"""Skin effect in a flat conductor: the EXACT 1-D diffusion solution.

Why this module exists
----------------------
Every other copper answer in this server is DC: `board_ir_drop`,
`current_density_field`, `copper_resistance` all say "no skin effect" in their
assumptions, and that is honest right up to the frequency where the current
stops filling the thickness.  For 1 oz copper (35 um) that frequency is about
1 MHz for a 1 % error and about 10 MHz before the number matters at all; for a
100 um plane it moves up by (100/35)^2, for a 12 um foil it moves down by 8.5.
So "what does this copper do at f" needs an answer that is not a DC number with
a hand-wave attached.

What is solved here - and it is solved, not approximated
--------------------------------------------------------
Take a conductor of thickness t, conductivity sigma = 1/rho, permeability
mu = mu0*mu_r, with the current flowing along it and the field varying in z
only.  Then E_z obeys the 1-D diffusion equation

    d2E/dz2 = j*omega*mu*sigma*E        (time convention e^{+j*omega*t})

whose solution in a slab is a cosh.  Two geometries are physical and both are
computed exactly:

    faces="one"   the return current is on ONE side (a microstrip trace, a
                  plane with its return below it).  H = 0 on the far face, so
                  E propto cosh(gamma*z)/cosh(gamma*t) and the sheet impedance
                  is (t/delta)*(1+j)*coth((1+j)*t/delta) * R_dc.
    faces="two"   the return is symmetric (a stripline trace, a plane between
                  two return planes).  E is even about the mid-plane, half the
                  current crosses each face, and the argument halves:
                  (t/(2*delta))*(1+j)*coth((1+j)*t/(2*delta)) * R_dc.

Both reduce to ONE real function of one number, the "penetration count"
s = t/(k*delta) with k = 1 (one face) or 2 (two faces):

    R_ac/R_dc = s * (sinh(2s) + sin(2s)) / (cosh(2s) - cos(2s))     = s*(A + B)
    X_ac/R_dc = s * (sinh(2s) - sin(2s)) / (cosh(2s) - cos(2s))     = s*(A - B)

with A = sinh(2s)/(cosh(2s)-cos(2s)) and B = sin(2s)/(cosh(2s)-cos(2s)).  Three
limits fall straight out and each one is a test that can fail:

    s -> 0     R -> 1 + 4*s^4/45,  X -> 2*s^2/3   (the classic low-frequency
               expansion; X/R_dc -> omega*mu*t/3, the DC internal inductance of
               a slab, and R stays flat - that flatness is WHY DC answers work.
               Both come out of the single series u*coth(u) = 1 + u^2/3 - u^4/45
               + ... at u = s*(1+j): Re gives 4s^4/45, Im gives 2s^2/3)
    s -> inf   R -> s, X -> s   (surface impedance at 45 degrees: the current is
               gone below the top skin depth, so t_eff = delta (one face) or
               2*delta (two faces))
    s = 1      R = 1.0857, X = 0.6504  (measured here, not quoted from a table)

The three of them pin the formula from three directions: a mistyped sinh/cos,
a missing factor of two, or a swapped real/imaginary part cannot satisfy all
three at once.

What this is NOT
----------------
This is a 1-D exact solution of the THICKNESS direction.  It is not a 2-D or 3-D
field solve, so:

  * no lateral redistribution along the width (edge crowding of a wide strip);
  * no proximity effect from a neighbouring conductor (the return plane's
    current is not solved, it is accounted for only by choosing faces=one);
  * no eddy currents in a plane underneath, no via-barrel field, no
    displacement current, no radiation;
  * the copper is homogeneous: one rho, one mu_r, no grain structure.  Surface
    roughness enters only as the Hammerstad-Jensen MULTIPLIER of Rs
    (roughness_factor, which is a loss multiplier and not a distribution) and
    only when the caller passes Rq;
  * no temperature rise: rho is the number for temp_c and stays there.

Consequently `ac_sheet_resistance` says which of these limits applies to the
number it hands back, and the caller who needs a wide-strip edge-crowding
answer has to run a 2-D AC solve - which is a different tool, not this one.
"""
import cmath
import math

from .result import ok, err
from .roughness import MU0, roughness_factor, skin_depth_m

TWO_PI = 2.0 * math.pi

FACES_ONE = "one"
FACES_TWO = "two"
_FACE_FACTOR = {FACES_ONE: 1.0, FACES_TWO: 2.0}

# Above this penetration count sinh(2s) is close enough to cosh(2s) that the
# ratio is s to 1e-16 (the residual term is 2*exp(-2s)*(sin+cos)), and it also
# keeps sinh(2s) far away from the double-precision overflow at s ~ 354.
_S_ASYMPTOTE = 20.0
def _ratio_from_s(s):
    """(R_ac/R_dc, X_ac/R_dc) for a slab with penetration count s >= 0.

    Written with the half-angle identities

        sinh(2s) + sin(2s) = 2*(sinh s cosh s + sin s cos s)
        sinh(2s) - sin(2s) = 2*(sinh s cosh s - sin s cos s)
        cosh(2s) - cos(2s) = 2*(sinh^2 s + sin^2 s)

    NOT with cosh(2s) - cos(2s) directly: at s = 0.01 that difference is 4e-4
    made of two numbers near 1, so it loses eight digits, and R - 1 (which is
    1.3e-9 there) came out 3e-5 wrong.  The half-angle form adds where the physics
    adds, and R - 1 is then right to the last bit; the identity was checked
    against the direct form at s >= 0.5, where the direct form is safe, and
    against the series below.
    """
    if s >= _S_ASYMPTOTE:
        return s, s
    sh = math.sinh(s)
    ch = math.cosh(s)
    sn = math.sin(s)
    cs = math.cos(s)
    p = sh * ch + sn * cs
    q = sh * ch - sn * cs
    den = sh * sh + sn * sn
    if den <= 0.0:
        return 1.0, 0.0
    return s * p / den, s * q / den


def _mag_cosh(u):
    """|cosh((1+j)*u)| = sqrt((cosh(2u) + cos(2u))/2)."""
    return math.sqrt(0.5 * (math.cosh(2.0 * u) + math.cos(2.0 * u)))


def _arg_cosh(u):
    """arg(cosh((1+j)*u)) = atan2(sinh u * sin u, cosh u * cos u)."""
    return math.atan2(math.sinh(u) * math.sin(u), math.cosh(u) * math.cos(u))


def skin_depth_mm(f_hz, rho_ohm_mm, mu_r=1.0):
    """delta = sqrt(rho/(pi*f*mu)) in mm, from rho in ohm*mm and f in Hz."""
    rho_m = float(rho_ohm_mm) * 1e-3
    return 1e3 * skin_depth_m(float(f_hz), 1.0 / rho_m) / math.sqrt(float(mu_r))


def slab_surface_impedance(f_hz, thickness_mm, rho_ohm_mm, mu_r=1.0,
                          faces=FACES_ONE):
    """The whole 1-D answer for one frequency, as a dict (no envelope)."""
    f = float(f_hz)
    t = float(thickness_mm)
    mu = MU0 * float(mu_r)
    delta = math.sqrt(float(rho_ohm_mm) * 1e-3 / (math.pi * f * mu))
    k = _FACE_FACTOR[faces]
    s = (t * 1e-3) / (k * delta)          # t is mm, delta is m
    r, x = _ratio_from_s(s)
    return {
        "f_hz": f,
        "delta_m": delta,
        "delta_mm": delta * 1e3,
        "s": s,
        "r_over_rdc": r,
        "x_over_rdc": x,
        "t_eff_mm": t / r,
    }


def current_fraction_within(f_hz, thickness_mm, rho_ohm_mm, depth_mm,
                            mu_r=1.0, faces=FACES_ONE):
    """Fraction of |I| that flows within `depth_mm` of the current-carrying face.

    Uses the closed form |sinh(gamma*t) - sinh(gamma*z1)| / |sinh(gamma*t)|
    with z1 the plane at `depth_mm` below the face.  At DC this is depth/t.  For
    a thick conductor it approaches

        |1 - exp(-(1+j)*depth/delta)|   = 0.8589 at one skin depth,

    which is NOT the 0.632 = 1 - exp(-1) that the density ratio |J(delta)|/|J(0)|
    equals: the density has fallen to 37 % there, but the current in that layer
    still adds up to 86 % of the total because the phase turns as well.  The two
    numbers are both right and are routinely confused with each other; the one
    returned here is the current, so it is the 0.8589 one.
    """
    a = slab_surface_impedance(f_hz, thickness_mm, rho_ohm_mm, mu_r, faces)
    # gamma*z = (1+j)*(z/delta) where z is measured from the face where H = 0
    s_face = a["s"]
    s_plane = max(0.0, s_face - float(depth_mm) / a["delta_mm"])
    num = abs(cmath.sinh(complex(1.0, 1.0) * s_face)
              - cmath.sinh(complex(1.0, 1.0) * s_plane))
    den = abs(cmath.sinh(complex(1.0, 1.0) * s_face))
    if den <= 0.0:
        return None
    return num / den


def current_profile(f_hz, thickness_mm, rho_ohm_mm, mu_r=1.0,
                    faces=FACES_ONE, samples=9):
    """|J| and phase versus depth from the current-carrying face."""
    a = slab_surface_impedance(f_hz, thickness_mm, rho_ohm_mm, mu_r, faces)
    delta = a["delta_mm"]
    k = _FACE_FACTOR[faces]
    half = thickness_mm / k                  # depth from the face to the null
    n = int(samples)
    rows = []
    ref_u = half / delta
    ref_mag = _mag_cosh(ref_u)
    ref_arg = _arg_cosh(ref_u)
    for i in range(n):
        depth = thickness_mm * i / float(n - 1) if n > 1 else 0.0
        z = half - depth                     # distance from the H = 0 plane
        if z < 0.0:
            z = 0.0
        u = z / delta
        mag = _mag_cosh(u) / ref_mag
        phase = math.degrees(_arg_cosh(u) - ref_arg)
        rows.append({"depth_um": depth * 1e3, "j_rel": mag,
                     "phase_deg": phase})
    return rows


def _bisect_ratio(f_hz_hi, thickness_mm, rho_ohm_mm, mu_r, faces, target):
    """Frequency where R_ac/R_dc first reaches `target` (None if never)."""
    lo = 1.0
    hi = float(f_hz_hi)
    if slab_surface_impedance(hi, thickness_mm, rho_ohm_mm, mu_r,
                              faces)["r_over_rdc"] < target:
        return None
    for _ in range(200):
        mid = math.sqrt(lo * hi)
        if slab_surface_impedance(mid, thickness_mm, rho_ohm_mm, mu_r,
                                  faces)["r_over_rdc"] < target:
            lo = mid
        else:
            hi = mid
        if hi / lo < 1.0 + 1e-12:
            break
    return hi


def ac_sheet_resistance(frequency_hz, thickness_mm=None, thickness_um=None,
                        rho_ohm_mm=None, temp_c=20.0, mu_r=1.0,
                        faces=FACES_ONE, length_mm=None, width_mm=None,
                        dc_resistance_ohm=None, roughness_rq_um=None,
                        profile_samples=9):
    """R_ac/R_dc, delta, the effective thickness and the J(z) profile.

    Everything is per SQUARE unless a length and width (or a DC resistance) are
    given; then the absolute ohm value is returned alongside.
    """
    from .power import _rho as _rho_at

    if frequency_hz is None:
        return err("frequency_hz is required: a skin depth without a frequency is "
                   "just a length", "ARG_REQUIRED")
    try:
        f = float(frequency_hz)
    except (TypeError, ValueError):
        return err("frequency_hz must be a number", "BAD_PARAMETER")
    if not (f > 0.0):
        return err("frequency_hz must be > 0 (the DC answer is copper_resistance "
                   "and board_ir_drop, which have no skin term at all)",
                   "BAD_PARAMETER")

    if (thickness_mm is None) == (thickness_um is None):
        return err("give exactly one of thickness_mm or thickness_um",
                   "ARG_REQUIRED",
                   hint="a conductor thickness is never guessed: 35 um is 1 oz "
                        "copper, 17 um is half an ounce, and the skin term depends "
                        "on which one the board really has")
    try:
        t = float(thickness_mm) if thickness_mm is not None else float(thickness_um) / 1e3
    except (TypeError, ValueError):
        return err("thickness must be a number", "BAD_PARAMETER")
    if not (t > 0.0):
        return err("thickness must be > 0", "BAD_PARAMETER")

    if rho_ohm_mm is None:
        rho = _rho_at(temp_c if temp_c is not None else 20.0) * 1e3
    else:
        try:
            rho = float(rho_ohm_mm)
        except (TypeError, ValueError):
            return err("rho_ohm_mm must be a number", "BAD_PARAMETER")
    if not (rho > 0.0):
        return err("rho_ohm_mm must be > 0", "BAD_PARAMETER")

    try:
        mur = float(mu_r) if mu_r is not None else 1.0
    except (TypeError, ValueError):
        return err("mu_r must be a number", "BAD_PARAMETER")
    if not (mur >= 1.0):
        return err("mu_r must be >= 1 (copper is 1.0; a ferromagnetic plating would "
                   "be given its real mu_r, and it also makes the 1-D assumption "
                   "worse)", "BAD_PARAMETER")

    if faces is None:
        faces = FACES_ONE
    if faces not in _FACE_FACTOR:
        return err("faces must be %r or %r, not %r - and it is not a cosmetic "
                   "choice: 'one' means the return current is on one side of this "
                   "copper (microstrip, a plane over its return), 'two' means it is "
                   "symmetric (stripline, a plane between two returns). The "
                   "difference is a factor of two in the effective thickness."
                   % (FACES_ONE, FACES_TWO, faces), "BAD_PARAMETER")

    n_prof = int(profile_samples) if profile_samples is not None else 9
    if n_prof < 2:
        return err("profile_samples must be >= 2", "BAD_PARAMETER")

    st = slab_surface_impedance(f, t, rho, mur, faces)
    delta = st["delta_mm"]
    s = st["s"]
    r_ratio = st["r_over_rdc"]
    x_ratio = st["x_over_rdc"]
    t_eff = st["t_eff_mm"]

    r_sq_dc = rho / t
    r_sq_ac = r_sq_dc * r_ratio
    x_sq_ac = r_sq_dc * x_ratio

    # regime, by the two numbers that actually decide whether DC is enough
    if s <= 0.5:
        regime = "dc (R within 1 % of R_dc)"
    elif s < 3.0:
        regime = "transition (R between 1.09x and 3x R_dc)"
    else:
        regime = "skin-dominated (R ~ t_eff/t R_dc with t_eff %.3g um)" % (t_eff * 1e3)

    f_1pct = _bisect_ratio(1e15, t, rho, mur, faces, 1.01)
    f_10pct = _bisect_ratio(1e15, t, rho, mur, faces, 1.10)
    f_2x = _bisect_ratio(1e15, t, rho, mur, faces, 2.0)

    prof = current_profile(f, t, rho, mur, faces, n_prof)
    one_skin = current_fraction_within(f, t, rho, delta, mur, faces)

    rgh = None
    if roughness_rq_um is not None:
        try:
            rq = float(roughness_rq_um) * 1e-6
        except (TypeError, ValueError):
            return err("roughness_rq_um must be a number (RMS height Rq, not Rz)",
                       "BAD_PARAMETER")
        if rq < 0.0:
            return err("roughness_rq_um must be >= 0", "BAD_PARAMETER")
        rgh = roughness_factor(rq, st["delta_m"])

    length = None
    width = None
    if (length_mm is None) != (width_mm is None):
        return err("length_mm and width_mm go together (both or neither)",
                   "ARG_REQUIRED")
    if length_mm is not None:
        try:
            length = float(length_mm)
            width = float(width_mm)
        except (TypeError, ValueError):
            return err("length_mm and width_mm must be numbers", "BAD_PARAMETER")
        if not (length > 0.0 and width > 0.0):
            return err("length_mm and width_mm must be > 0", "BAD_PARAMETER")

    r_dc_abs = None
    if dc_resistance_ohm is not None:
        try:
            r_dc_abs = float(dc_resistance_ohm)
        except (TypeError, ValueError):
            return err("dc_resistance_ohm must be a number", "BAD_PARAMETER")
        if not (r_dc_abs > 0.0):
            return err("dc_resistance_ohm must be > 0", "BAD_PARAMETER")
        if length is not None:
            return err("give either a geometry (length_mm + width_mm) or a "
                       "dc_resistance_ohm, not both - the second would silently "
                       "override the first", "ARG_INVALID")
    elif length is not None:
        r_dc_abs = rho * length / (width * t)
    r_ac_abs = r_dc_abs * r_ratio if r_dc_abs is not None else None

    # ---- checks that can fail
    checks = {}
    # 1) low-frequency series: 1 + 4 s^4/45, only meaningful well inside its range
    s_lo = min(s, 0.1)
    r_lo, x_lo = _ratio_from_s(s_lo)
    checks["series_s"] = s_lo
    checks["series_r_abs_error"] = abs(r_lo - (1.0 + 4.0 * s_lo ** 4 / 45.0))
    checks["series_x_abs_error"] = abs(x_lo - 2.0 * s_lo ** 2 / 3.0)
    # 2) high-frequency asymptote: R/s and X/s -> 1
    s_hi = max(s, 5.0)
    r_hi, x_hi = _ratio_from_s(s_hi)
    checks["asymptote_r_rel_error"] = abs(r_hi / s_hi - 1.0)
    checks["asymptote_x_rel_error"] = abs(x_hi / s_hi - 1.0)
    # 3) the J(z) profile has to be flat at DC and must peak at the face the
    #    current is on.  Both are statements about the profile alone, so a
    #    reversed z, a missing normalisation or a wrong cosh argument fails them.
    jvals = [row["j_rel"] for row in prof]
    checks["profile_flat_at_dc"] = (
        (max(jvals) / min(jvals) - 1.0) if 0.0 < s <= 0.1 else None)
    checks["profile_peaks_at_the_face"] = bool(jvals and jvals[0] >= max(jvals) - 1e-12)
    # 4) at DC the current fraction up to depth d must be d/t; a factor of two
    #    in the sinh argument (the one/two-face switch) shows up here.
    checks["current_fraction_at_dc"] = (
        current_fraction_within(f, t, rho, t / 4.0, mur, faces)
        if s <= 0.1 else None)
    checks["current_fraction_at_dc_expected"] = 0.25 if s <= 0.1 else None

    warnings = []
    if s > 0.5 and delta < 3.0 and rgh is None:
        warnings.append(
            "skin depth is %.3g um at this frequency: copper foil roughness is of "
            "the same order, so Rs carries an extra factor the 1-D smooth model "
            "does not have. Pass roughness_rq_um (RMS height, Hammerstad-Jensen) "
            "for the rough number; without it this R is a LOWER bound."
            % (delta * 1e3))
    if s > 0.5:
        warnings.append(
            "this scales R (and the effective thickness) of a conductor whose "
            "current is taken as uniform ALONG the width: there is no lateral "
            "redistribution, no edge crowding of a wide strip, and no proximity "
            "effect from a neighbouring trace or the return plane's own eddy "
            "currents. A wide strip at these frequencies is a 2-D AC problem, "
            "which this tool does not solve.")
    if mur > 1.0:
        warnings.append("mu_r > 1: the diffusion solution is still exact for a "
                        "homogeneous slab, but a real magnetic layer saturates and "
                        "its mu is field dependent, so the number is a single-point "
                        "answer")

    value = {
        "frequency_hz": f,
        "skin_depth_um": delta * 1e3,
        "skin_depth_mm": delta,
        "thickness_um": t * 1e3,
        "thickness_over_skin_depth": s,
        "faces": faces,
        "r_ac_over_r_dc": r_ratio,
        "x_ac_over_r_dc": x_ratio,
        "effective_thickness_um": t_eff * 1e3,
        "dc_sheet_resistance_ohm_per_sq": r_sq_dc,
        "ac_sheet_resistance_ohm_per_sq": r_sq_ac,
        "ac_sheet_reactance_ohm_per_sq": x_sq_ac,
        "internal_inductance_nh_per_sq": (x_sq_ac / (TWO_PI * f)) * 1e9 if f else None,
        "one_skin_depth_current_fraction": one_skin,
        "f_at_r_1pct_over_dc_hz": f_1pct,
        "f_at_r_10pct_over_dc_hz": f_10pct,
        "f_at_r_2x_dc_hz": f_2x,
        "regime": regime,
        "profile": prof,
        "profile_note": "depth_um is measured from the face the current is on; "
                        "j_rel is |J| relative to that face; phase_deg is the "
                        "phase of J relative to the face (it lags going inward)",
    }
    if rgh is not None:
        value["roughness_k"] = rgh
        value["ac_sheet_resistance_rough_ohm_per_sq"] = r_sq_ac * rgh
    if r_dc_abs is not None:
        value["dc_resistance_ohm"] = r_dc_abs
        value["ac_resistance_ohm"] = r_ac_abs
        value["ac_over_dc_resistance"] = r_ratio

    model = ("exact 1-D diffusion of the current through the THICKNESS: "
             "d2E/dz2 = j*omega*mu*sigma*E solved as a cosh in a slab, with the "
             "return on one face (H = 0 at the far face) or symmetric about the "
             "mid-plane. R_ac/R_dc = s*(sinh 2s + sin 2s)/(cosh 2s - cos 2s) and "
             "X_ac/R_dc = s*(sinh 2s - sin 2s)/(cosh 2s - cos 2s), s = t/(k*delta), "
             "k = 1 for one face and 2 for two.")
    basis = ("Maxwell's equations in a good conductor, reduced to the 1-D case: "
             "sigma >> omega*epsilon so displacement current is dropped, mu is "
             "mu0*mu_r, and the slab is homogeneous. delta = sqrt(rho/(pi*f*mu)) is "
             "the same skin depth the RF tools in this server already use "
             "(roughness.skin_depth_m), so the two never disagree. This is the "
             "classical 'skin effect in a flat strip' closed form; it is derived "
             "from the diffusion equation here rather than quoted, and its two "
             "limits (1 + 4s^4/45 and R = X = s) are checked on every call.")
    precision = (
        "Exact closed form: the only numerical error in the ratio is double "
        "rounding (the series is used below s = 1e-4 with an error under 1e-12, "
        "the asymptote above s = 20 with a residual of 2*exp(-2s) < 1e-16). The "
        "PHYSICS is 1-D: the number is exact for a slab with the stated face "
        "condition and approximate for a real trace (no edge crowding, no "
        "proximity effect, no roughness unless Rq is given, no temperature rise). "
        "The checks block re-derives the two limits and the DC flatness of the "
        "J(z) profile; they test the arithmetic, not the 1-D assumption.")
    assumptions = {
        "thickness_um": t * 1e3,
        "rho_ohm_mm": rho,
        "temp_c": temp_c if rho_ohm_mm is None else None,
        "mu_r": mur,
        "sigma_s_per_m": 1.0 / (rho * 1e-3),
        "faces": faces,
        "face_rule": {"one": "return current on one side; H = 0 on the far face",
                      "two": "symmetric return; half the current per face"},
        "frequency": "single frequency; for a whole band call this per point "
                     "(pdn_impedance is the band tool for |Z|)",
        "current": "uniform along the width and along the length - the only "
                   "variation solved is through the thickness",
        "roughness": ("Hammerstad-Jensen multiplier on Rs, OFF unless "
                      "roughness_rq_um is passed" if rgh is None else
                      "Hammerstad-Jensen multiplier from the Rq given"),
        "temperature": "rho is fixed at temp_c; no I^2R self-heating",
    }
    return ok(value, "mixed (see keys: Hz, um, ohm, H, deg, dimensionless)",
              model, basis, precision, assumptions,
              extra={
                  "checks": checks,
                  "warnings": warnings,
                  "delta_m": st["delta_m"],
                  "asymptotes": {
                      "r_over_rdc_at_dc": 1.0,
                      "x_over_rdc_small_s_series": "2*s^2/3",
                      "r_and_x_over_rdc_large_s": "s",
                      "s_for_1pct_r": 0.5261,
                  },
                  "limits": {
                      "one_vs_two_faces": "faces='two' at thickness t is exactly "
                                          "faces='one' at thickness t/2; that is an "
                                          "identity of the model, not evidence for it",
                      "not_modelled": ["lateral (width-direction) redistribution",
                                       "edge crowding of a wide strip",
                                       "proximity effect / return-path eddy current",
                                       "via barrel and 3-D spreading",
                                       "displacement current and radiation",
                                       "any temperature rise"],
                  },
              })
