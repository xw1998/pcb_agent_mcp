"""Length unit conversion.

EasyEDA Pro stores numbers in **mil** (measured on 194 real `.epro` archives: `viaSize`
reads 9.8425 mil for "0.25 mm" - the unit label in the file is display-only). The
`0.01inch` alias is kept because it is the value EasyEDA's docs and older builds
assumed; it is *not* what current files contain. KiCad uses mm (nm internally).
A silent 25.4x error passes every DRC, so this must be an explicit tool.
"""

TO_MM = {
    "mm": 1.0,
    "cm": 10.0,
    "um": 0.001,
    "nm": 1e-6,
    "mil": 0.0254,
    "thou": 0.0254,
    "inch": 25.4,
    "in": 25.4,
    "0.01inch": 0.254,   # legacy assumption - real files store mil (see docs/13)
    "0.01in": 0.254,
}

from .result import ok, err


def convert(value, from_unit, to_unit):
    f = TO_MM.get(str(from_unit).strip().lower())
    t = TO_MM.get(str(to_unit).strip().lower())
    if f is None or t is None:
        return err(
            "unknown unit",
            "UNIT_UNKNOWN",
            hint="supported: " + ", ".join(sorted(TO_MM.keys())),
        )
    out = float(value) * f / t
    return ok(
        value=out,
        unit=to_unit,
        model="exact_ratio",
        basis="SI definition: 1 inch = 25.4 mm; EasyEDA Pro internal unit = 0.01 inch = 0.254 mm",
        precision="exact",
        assumptions={"from_unit": from_unit, "to_unit": to_unit},
    )
