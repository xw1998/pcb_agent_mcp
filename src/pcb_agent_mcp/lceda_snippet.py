"""Write snippets built from the APP'S OWN method table, never from a memory of it.

`lceda_write` runs a snippet the caller wrote, and it refuses to invent one
because the vendor's method names were never verified here. That refusal left a
hole: a caller who knows *what* they want (save / move / create) still has to
guess *which* method does it, and `eda.foo_bar()` guessed wrong throws inside
the editor at best - at worst it hits a similarly named method that does
something else.

`lceda_pro_api` closes the hole from the other end: the running editor will
report its own API surface, with the vendor's Chinese comments and, per class,
a `callPath` that already holds the runtime receiver (this is the mapping that
matters, because `PCB_Document` is not `pcb_Document`). So the split is:

    the NAMES come from the application (doc.api)
    the ARGUMENTS come from you      (nothing here knows an argument shape)
    the EFFECT comes from the probe   (lceda_write refuses a write nobody read)

Nothing in this module talks to the application, writes anything or claims a
write works; it is a pure function from "what the app says it has" plus "what
you want done" to one candidate snippet, and it REFUSES when the table does not
settle the question:

  * no method matches                 -> SNIPPET_NO_API
  * two methods match equally well     -> SNIPPET_AMBIGUOUS (with the candidates
                                          and their comments, so you can `pick`)
  * the receiver cannot be resolved    -> SNIPPET_NO_RECEIVER (a class dump with
                                          no `callPath` and no `property` given:
                                          lower-casing a class name is exactly
                                          the guess that produces a working-
                                          looking wrong snippet)

`verified_against_app` is `false` in every answer, and `not_verified` lists what
that costs: the method exists (the app said so) but its argument order, its
return value and its effect on the design are all unproven. A generated snippet
is a *candidate with evidence*, not a verified call.
"""

import json

# Keywords per operation. `names` are matched against the method name
# (case-insensitive substring), `comments` against the vendor's comment text.
# Both lists are short on purpose: a broad list ("add") buys ambiguity, and an
# ambiguous table is answered with SNIPPET_AMBIGUOUS rather than a coin flip.
OPS = {
    "save": {
        "names": ("save", "savetodisk", "commit"),
        "comments": ("保存", "存盘", "写入磁盘"),
        "wants_args": False,
        "why": "a save takes no target and has no observable state; the only fact "
               "is that the call returned without an error",
    },
    "move": {
        "names": ("move", "moveto", "translate", "setposition"),
        "comments": ("移动", "平移", "挪"),
        "wants_args": True,
        "why": "a move needs the object and the delta - both are yours to pass, "
               "because an argument shape is not something the app tells us",
    },
    "create": {
        "names": ("create", "add", "new", "place", "insert"),
        "comments": ("创建", "新建", "添加", "放置"),
        "wants_args": True,
        "why": "a create needs the thing to create; the method name is the app's, "
               "the arguments are yours",
    },
}

# Read-side keywords, used only to suggest a probe for a change-making op.
# ORDER IS THE EVIDENCE, strongest first: a query that reads the objects you are
# about to change (positions / transforms / component list) is a better probe than
# a bare `get` of anything. `getCanvasOrigin` and `getAllComponentsPosition` are
# both reads; only the second one can move when a component moves.
PROBE_NAMES = ("getposition", "getpositions", "getallpositions", "gettransform",
               "getallcomponents", "getcomponents", "getall", "get", "list")
PROBE_COMMENTS = ("获取", "读取", "查询", "位置", "列表")

class SnippetError(Exception):
    """A refusal from this module; the reason code is the answer, not a hint."""

    def __init__(self, message, reason_code, hint=None, extra=None):
        super().__init__(message)
        self.message = message
        self.reason_code = reason_code
        self.hint = hint
        self.extra = extra or {}

def _as_text(value):
    return value if isinstance(value, str) else ""

def _class_entry(dump):
    """One class-level `doc.api` answer -> (class name, receiver, methods).

    Accepts what the bridge actually returns, and also a bare
    {class_name, property, methods} record so a caller can paste a trimmed
    table. Anything else is refused: guessing the shape of the app's answer is
    how a generator starts inventing.
    """
    if not isinstance(dump, dict):
        raise SnippetError("api_dump must be the object doc.api returned",
                           "ARG_INVALID")
    raw = dump.get("methods")
    if raw is None:
        if dump.get("types") is not None:
            raise SnippetError(
                "this dump is a NAMESPACE table ({types: [...]}), which lists "
                "classes but no methods", "ARG_INVALID",
                hint="ask doc.api for one class (class_name=...), then pass that "
                     "answer (or pass `session` + `class_name` and let this tool "
                     "query it)")
        raise SnippetError("api_dump has no `methods` list", "ARG_INVALID",
                           hint="pass the class-level doc.api answer: "
                                "{title, remarks, methods: [{name, comment}], "
                                "callPath}")
    if not isinstance(raw, list):
        raise SnippetError("api_dump['methods'] must be a list", "ARG_INVALID")
    methods = []
    for entry in raw:
        if isinstance(entry, dict):
            name = _as_text(entry.get("name")).strip()
            comment = _as_text(entry.get("comment")).strip()
        else:
            name, comment = _as_text(entry).strip(), ""
        if not name:
            continue                      # an unnamed method cannot be called
        methods.append({"name": name, "comment": comment})
    if not methods:
        raise SnippetError("api_dump lists no usable method names", "ARG_INVALID")
    cls = _as_text(dump.get("class_name") or dump.get("className")
                   or dump.get("title")).strip()
    return cls, _as_text(dump.get("callPath")).strip(), methods, dump

def resolve_receiver(dump, class_name=None, prop=None):
    """The runtime receiver (`eda.pcb_Document.`) for that class.

    Order of authority: an explicit `property` argument, then the dump's own
    `callPath` (the app ships it), then `classToProperty` if the caller pasted a
    namespace dump alongside. Lower-casing the class name is NEVER done: that is
    the specific guess that turns `PCB_Document` into `pCB_Document`.
    """
    if prop:
        prop = prop.strip()
        if not prop.isidentifier():
            raise SnippetError("property %r is not an identifier" % (prop,),
                               "ARG_INVALID")
        return "eda.%s." % prop, "caller"
    path = _as_text(dump.get("callPath")).strip()
    if path:
        if not path.endswith("."):
            path += "."
        if not path.startswith("eda.") or not path.endswith("."):
            raise SnippetError(
                "the dump's callPath %r does not look like 'eda.<prop>.'" % (path,),
                "SNIPPET_NO_RECEIVER",
                hint="pass `property` explicitly (the app's callConventions maps "
                     "PCB_Document -> pcb_Document)")
        return path, "callPath"
    mapping = ((dump.get("callConventions") or {}).get("classToProperty") or {})
    cls = (class_name or _as_text(dump.get("class_name"))
           or _as_text(dump.get("className")) or _as_text(dump.get("title")))
    if cls and mapping.get(cls):
        return "eda.%s." % mapping[cls], "classToProperty"
    raise SnippetError(
        "cannot resolve the runtime receiver for this class", "SNIPPET_NO_RECEIVER",
        hint="pass `property` (the runtime name, e.g. pcb_Document). The class "
             "name is NOT the property name and this tool will not lower-case it "
             "into one")

def candidates(methods, op, spec=None):
    """Rank the app's methods against one operation. No fuzzy distance, no model."""
    spec = spec or OPS.get(op)
    if spec is None:
        raise SnippetError("unknown op %r" % (op,), "ARG_INVALID",
                           hint="op is one of %s" % (", ".join(sorted(OPS)),))
    out = []
    for m in methods:
        name = m["name"]
        low = name.lower()
        comment = m["comment"]
        hit_name = next((t for t in spec["names"] if t in low), None)
        hit_comment = next((t for t in spec["comments"] if t in comment), None)
        if not hit_name and not hit_comment:
            continue
        # 3 = the method is NAMED exactly the operation (save is `save`), 2 = the
        # name contains it (saveAs, moveComponent), 1 = only the vendor's Chinese
        # comment says so - weaker evidence, reported as such. Exactness has to
        # outrank containment, or `save`/`saveAs` come out "equally good" and the
        # caller is asked to pick between the right answer and a wrong one.
        if hit_name:
            score = 3 if low == hit_name else 2
        else:
            score = 1
        # A read ("get...") is not a write even when its comment shares a word.
        if score == 1 and low.startswith(("get", "is", "query", "list", "find")):
            continue
        out.append({"name": name, "comment": comment, "score": score,
                    "matched": hit_name or hit_comment})
    out.sort(key=lambda c: (-c["score"], len(c["name"]), c["name"]))
    return out

def _args_text(args):
    """Positional JS arguments, from strings (verbatim) or JSON-able values."""
    if args is None:
        return [], []
    if not isinstance(args, (list, tuple)):
        raise SnippetError("argsv must be a list of positional arguments",
                           "ARG_INVALID")
    parts, note = [], []
    for i, a in enumerate(args):
        if isinstance(a, str):
            if not a.strip():
                raise SnippetError("argument %d is an empty string" % (i + 1),
                                   "ARG_INVALID")
            parts.append(a)          # the caller's JavaScript, inserted as written
        else:
            parts.append(json.dumps(a))
            note.append(i + 1)       # 1-based: this one was encoded, not verbatim
    return parts, note

def build(op, dump, argsv=None, pick=None, prop=None, class_name=None,
          name_tokens=None, comment_tokens=None):
    """One candidate snippet from the app's table, or a refusal that says why.

    Returns a report dict. Raises `SnippetError` only for malformed input; every
    *semantic* dead end (nothing matches / two things match) comes back as
    `status: "ambiguous"` or `status: "error"` with the candidates attached,
    because those are answers about the application, not about the request.
    """
    spec = OPS.get(op)
    if spec is None:
        raise SnippetError("unknown op %r" % (op,), "ARG_INVALID",
                           hint="op is one of %s" % (", ".join(sorted(OPS)),))
    if name_tokens:
        spec = dict(spec, names=tuple(name_tokens))
    if comment_tokens:
        spec = dict(spec, comments=tuple(comment_tokens))
    cls, call_path, methods, raw = _class_entry(dump)
    receiver, how = resolve_receiver({"callPath": call_path,
                                      "classToProperty": (raw or {}).get("classToProperty"),
                                      "className": cls}, class_name=class_name,
                                     prop=prop)
    found = candidates(methods, op, spec)

    report = {
        "op": op,
        "class": cls or None,
        "receiver": receiver,
        "receiver_from": how,
        "ranked": found[:6],
        "verified_against_app": False,
        "not_verified": [
            "the method's argument order and types (the app reports names and "
            "comments, not signatures)",
            "what the method returns",
            "that calling it changes the design - that is what the probe in "
            "lceda_pcb_write is for",
        ],
        "why_this_op_exists": spec["why"],
    }
    chosen = None
    if pick:
        hit = [c for c in found if c["name"] == pick or
               ("%s.%s" % (receiver, c["name"])) == pick or
               c["name"] == pick.split(".")[-1]]
        if not hit:
            report.update({"status": "error", "reason_code": "SNIPPET_NO_API",
                           "message": "pick %r is not among the methods that match "
                                      "this op" % (pick,),
                           "hint": "see `ranked`; pick must be one of those names"})
            return report
        chosen = hit[0]
    else:
        top = [c for c in found if c["score"] == found[0]["score"]] if found else []
        if not found:
            report.update({"status": "error", "reason_code": "SNIPPET_NO_API",
                           "message": "the app's table for %s has no method that "
                                      "reads like %r" % (cls or "this class", op),
                           "hint": "widen the search with `name_tokens` / "
                                   "`comment_tokens`, or write the snippet "
                                   "yourself and pass it to lceda_pcb_write"})
            return report
        if len(top) > 1:
            report.update({"status": "ambiguous",
                           "reason_code": "SNIPPET_AMBIGUOUS",
                           "message": "%d methods match %r equally well on this "
                                      "class; picking one would be a guess"
                                      % (len(top), op),
                           "hint": "pass `pick` with the name you meant (and read "
                                   "its comment in `ranked`)",
                           "candidates": [c["name"] for c in top]})
            return report
        chosen = top[0]

    parts, jsoned = _args_text(argsv)
    if spec["wants_args"] and not parts:
        report.update({"status": "error", "reason_code": "SNIPPET_NO_ARGS",
                       "message": "%r needs arguments and none were given" % (op,),
                       "hint": "pass `argsv`: a list of positional JavaScript "
                               "arguments. This tool knows the method NAME from "
                               "the app, not its signature"})
        return report
    code = "return await %s%s(%s);" % (receiver, chosen["name"], ", ".join(parts))
    report.update({
        "status": "ok",
        "method": chosen["name"],
        "method_comment": chosen["comment"] or None,
        "confidence": "name" if chosen["score"] >= 2 else "comment_only",
        "evidence": ("exact_name" if chosen["score"] == 3 else
                     "name_contains_operation" if chosen["score"] == 2 else
                     "vendor_comment_only"),
        "code": code,
        "arg_count": len(parts),
        "args_from_caller": True,
        "json_encoded_args": jsoned,
    })
    return report

def suggest_probe(dump, prop=None, class_name=None, pick=None):
    """A read-only snippet that a caller can use as the before/after probe.

    Same discipline as `build`: the NAME comes from the app's table. What the
    read actually returns is unknown, which is why the report says so - a probe
    whose value does not move proves nothing, and a probe that reads the wrong
    object looks exactly like one that does.
    """
    cls, call_path, methods, raw = _class_entry(dump)
    receiver, how = resolve_receiver({"callPath": call_path,
                                      "className": cls}, class_name=class_name,
                                     prop=prop)
    found = []
    for m in methods:
        low = m["name"].lower()
        hit_name = next((t for t in PROBE_NAMES if t in low), None)
        hit_comment = next((t for t in PROBE_COMMENTS if t in m["comment"]), None)
        if not hit_name and not hit_comment:
            continue
        found.append({"name": m["name"], "comment": m["comment"],
                      "score": 2 if hit_name else 1,
                      "matched": hit_name or hit_comment,
                      "rank": (PROBE_NAMES.index(hit_name) if hit_name
                               else len(PROBE_NAMES))})
    # Most specific read first: `getAllComponentsPosition` must beat
    # `getCanvasOrigin`, because only the first one can move when the design does.
    found.sort(key=lambda c: (-c["score"], c["rank"], len(c["name"]), c["name"]))
    report = {"class": cls or None, "receiver": receiver,
              "receiver_from": how, "ranked": found[:6],
              "verified_against_app": False,
              "not_verified": ["that the value this reads is the object you are "
                               "about to change",
                               "that the value changes when the design changes"]}
    if pick:
        hit = [c for c in found if c["name"] == pick.split(".")[-1]]
        if not hit:
            report.update({"status": "error", "reason_code": "SNIPPET_NO_API",
                           "message": "pick %r is not a read on this class" % (pick,),
                           "hint": "see `ranked`"})
            return report
        chosen = hit[0]
    elif not found:
        report.update({"status": "error", "reason_code": "SNIPPET_NO_API",
                       "message": "the app's table for %s has no method that reads "
                                  "like a state query" % (cls or "this class"),
                       "hint": "write the probe yourself; a probe you cannot point "
                               "at an object is worse than none"})
        return report
    else:
        chosen = found[0]
    report.update({"status": "ok", "method": chosen["name"],
                   "method_comment": chosen["comment"] or None,
                   "code": "return await %s%s();" % (receiver, chosen["name"]),
                   "confidence": "name" if chosen["score"] >= 2 else "comment_only",
                   "evidence": ("specific_read" if chosen["score"] == 2
                                else "vendor_comment_only")})
    return report
