"""Uniform result envelope for every Calc call.

Envelope always has 'status' == 'ok' | 'error'.
Business fields must never be named 'status'.
"""

def require_basis(basis):
    if not basis or not str(basis).strip():
        raise ValueError("basis (standard clause / model name) is mandatory")


def ok(value, unit, model, basis, precision, assumptions, extra=None):
    require_basis(basis)
    if precision is None or str(precision).strip() == "":
        raise ValueError("precision is mandatory")
    out = {
        "status": "ok",
        "value": value,
        "unit": unit,
        "model": model,
        "basis": basis,
        "precision": precision,
        "assumptions": assumptions or {},
    }
    if extra:
        out.update(extra)
    return out


def err(message, reason_code, hint=None, extra=None):
    out = {
        "status": "error",
        "reason_code": reason_code,
        "message": message,
    }
    if hint:
        out["hint"] = hint
    if extra:
        out.update(extra)
    return out
