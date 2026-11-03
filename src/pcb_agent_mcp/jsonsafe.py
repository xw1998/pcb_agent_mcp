"""What may go on the wire, decided once.

Two places serialise a physics answer: the JSON-RPC envelope (`server._dumps`)
and the text a tool's answer travels in (`tools.result_to_text`). Both used
`json.dumps` with its default `allow_nan=True`, which writes bare `NaN` and
`Infinity`. Those are not JSON, so a strict client rejects the whole message and
the turn dies - the same failure mode as an unhandled exception, only quieter: a
single divide-by-zero deep inside a calculator could end the conversation.

The rule here is that the transport never dies on the payload: non-finite floats
become null and anything else json cannot represent becomes its string. Nothing
is dropped - a missing key would look like the tool never computed it.
"""
import json

__all__ = ["sanitise", "dumps"]


def sanitise(value):
    """JSON-safe copy: non-finite floats -> None, exotic objects -> str."""
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            return None
        return value
    if isinstance(value, dict):
        return {str(k): sanitise(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [sanitise(v) for v in value]
    if value is None or isinstance(value, (str, int, bool)):
        return value
    return str(value)


def dumps(payload):
    """Strict JSON; fall back to a sanitised copy instead of raising."""
    try:
        return json.dumps(payload, ensure_ascii=False, allow_nan=False,
                          separators=(",", ":"))
    except (TypeError, ValueError):
        return json.dumps(sanitise(payload), ensure_ascii=False,
                          separators=(",", ":"))
