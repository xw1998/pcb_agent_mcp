"""BOM / pick-and-place review plus normalisation, driven by data/jlc-bom-cpl-spec.json.

Ported from jlceda-pcb-ai-workflow (MIT) ``scripts/bom_cpl_guard.py``. Every parameter -
required columns, allowed layers, forbidden designator characters, the LCSC part regex -
lives in the JSON spec with its source, so this module holds no format threshold either.

Boundary kept from the original: **no network**, therefore it cannot check that an LCSC
part number matches the component. It reports format facts and lists what is missing; a
wrong part number is more dangerous than a missing one and is out of scope.
"""
import csv
import io
import os
import re

from . import spec

SPEC = spec.load("jlc-bom-cpl-spec.json")
P = SPEC["params"]
ASCII_MAX = 127     # ASCII code point - language constant, not a fabrication limit

_RANGE = re.compile(r"^([A-Za-z_]+)(\d+)-(?:([A-Za-z_]+))?(\d+)$")
_NUM = re.compile(r"^(-?\d+(?:\.\d+)?)\s*([A-Za-z]*)$")


class Unsupported(Exception):
    pass


def decode_bytes(raw):
    if raw[:2] in (bytes([255, 254]), bytes([254, 255])):
        return raw.decode("utf-16"), "utf-16"
    if raw[:3] == bytes([239, 187, 191]):
        return raw.decode("utf-8-sig"), "utf-8-sig"
    try:
        return raw.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        return raw.decode("gbk", "replace"), "gbk"


def read_table(path):
    ext = os.path.splitext(path)[1].lower()
    if ext in (".xls", ".xlsx"):
        raise Unsupported("xls/xlsx is not parsed: save as CSV first (the official reader only "
                          "takes the first worksheet's first 12 columns)")
    with open(path, "rb") as handle:
        text, encoding = decode_bytes(handle.read())
    lines = [line for line in text.splitlines() if line.strip()]
    if not lines:
        raise Unsupported("file is empty")
    head = lines[0]
    delim = "\t" if head.count("\t") >= head.count(",") and "\t" in head else ","
    rows = list(csv.reader(io.StringIO("\n".join(lines)), delimiter=delim))
    header = [re.sub(r"\s+", " ", h.replace("\ufeff", "")).strip() for h in rows[0]]
    body = [[c.strip() for c in r] for r in rows[1:] if any(c.strip() for c in r)]
    return {"header": header, "rows": body, "encoding": encoding,
            "delimiter": "TAB" if delim == "\t" else "COMMA",
            "blank_lines_removed": len(text.splitlines()) - len(lines)}


def col_index(header, name):
    low = [h.lower() for h in header]
    return low.index(name.lower()) if name.lower() in low else None


# ---------------------------------------------------------------- designators
def expand_designators(cell):
    problems, out = [], []
    bad_seps = [c for c in P["designator_separators_bad"] if c in cell]
    if bad_seps:
        problems.append(("BOM.DES.SEP", "illegal designator separator %s: %r" % (bad_seps, cell[:40])))
    pattern = r"[,\s" + re.escape("".join(P["designator_separators_bad"])) + r"]+"
    for token in [t for t in re.split(pattern, cell) if t]:
        match = _RANGE.match(token)
        if match:
            pre1, a, pre2, b = match.group(1), int(match.group(2)), match.group(3), int(match.group(4))
            if pre2 and pre2 != pre1:
                problems.append(("BOM.DES.RANGE", "range prefix mismatch %s" % token))
                continue
            if a >= b:
                problems.append(("BOM.DES.RANGE", "range start must be below its end %s" % token))
                continue
            out.extend("%s%d" % (pre1, n) for n in range(a, b + 1))
        else:
            out.append(token)
    return out, problems


def des_problems(designator):
    problems = []
    if len(designator) > P["designator_max_len"]:
        problems.append(("BOM.DES.LEN", "designator too long (%d): %s..." % (len(designator), designator[:20])))
    if any(ord(ch) > ASCII_MAX for ch in designator):
        problems.append(("BOM.DES.CHARS", "designator carries non-ASCII text: %s" % designator))
    bad = [ch for ch in designator if ch in P["designator_forbidden_chars"]]
    if bad:
        problems.append(("BOM.DES.CHARS", "designator carries forbidden character(s) %s: %s" % (bad, designator)))
    return problems


def parse_coord(cell):
    match = _NUM.match(cell.strip())
    if not match:
        return None, None
    return float(match.group(1)), (match.group(2).lower() or None)


# -------------------------------------------------------------------- BOM side
def check_bom(table, items):
    def add(ident, status, msg):
        items.append({"id": ident, "verdict": status, "msg": msg})

    header = table["header"]
    idx = {c: col_index(header, c) for c in P["bom_required_columns"]}
    missing = [c for c, i in idx.items() if i is None]
    if missing:
        add("BOM.COL.REQUIRED", "BLOCK", "BOM lacks required column(s) %s (header: %s)" % (missing, header))
        return None
    qty_i = col_index(header, "Quantity")
    part_i = None
    for name in ("Supplier Part", "嘉立创元件编号", "LCSC Part", "LCSC"):
        part_i = col_index(header, name)
        if part_i is not None:
            break
    seen, row_of, missing_lcsc, bad_lcsc, ambiguous, empties, totals = {}, {}, [], [], [], [], []
    for number, row in enumerate(table["rows"], 2):
        def get(i):
            return row[i] if i is not None and i < len(row) else ""
        comment, designators, footprint = get(idx["Comment"]), get(idx["Designator"]), get(idx["Footprint"])
        if (any(str(c).strip().lower() in P["total_row_markers"] for c in row[:2])
                or (not designators and comment.lower() in P["total_row_markers"])):
            totals.append(number)
            continue
        for label, value in (("Comment", comment), ("Designator", designators), ("Footprint", footprint)):
            if not value:
                empties.append("row %d %s" % (number, label))
        designator_list, problems = expand_designators(designators)
        for ident, msg in problems:
            add(ident, "BLOCK", "row %d %s" % (number, msg))
        for designator in designator_list:
            for ident, msg in des_problems(designator):
                add(ident, "BLOCK", "row %d %s" % (number, msg))
            key = designator.upper()
            if key in seen:
                other = seen[key]
                ident = "BOM.DES.DUP" if other == designator else "BOM.DES.CASE_DUP"
                add(ident, "BLOCK", "duplicate designator %s vs %s (rows %d, %d)"
                    % (other, designator, row_of[key], number))
            else:
                seen[key] = designator
                row_of[key] = number
        if qty_i is not None and get(qty_i):
            try:
                quantity = int(float(get(qty_i)))
                if quantity != len(designator_list):
                    add("BOM.QTY.MATCH", "BLOCK", "row %d Quantity=%s but %d designator(s) (%s)"
                        % (number, get(qty_i), len(designator_list), designators[:30]))
            except ValueError:
                add("BOM.QTY.MATCH", "BLOCK", "row %d Quantity is not an integer: %r"
                    % (number, get(qty_i)))
        if re.fullmatch(r"\d+", comment):
            ambiguous.append("row %d Comment=%r" % (number, comment))
        if part_i is not None:
            part = get(part_i)
            if not part:
                missing_lcsc.append(designators[:24])
            elif not re.match(P["lcsc_part_regex"], part):
                bad_lcsc.append("row %d %r" % (number, part))
    if empties:
        add("BOM.EMPTY.FIELD", "BLOCK", "required field(s) empty: " + "; ".join(empties[:8]))
    if totals:
        add("BOM.TOTAL_ROW", "WARN", "suspected total row(s) at %s - delete them" % (totals,))
    if ambiguous:
        add("BOM.COMMENT.AMBIG", "WARN", "; ".join(ambiguous[:6]))
    if part_i is None:
        add("BOM.LCSC.MISSING", "WARN", "no LCSC part column; matching will rely on Comment/Footprint "
            "alone")
    else:
        if missing_lcsc:
            add("BOM.LCSC.MISSING", "WARN", "%d row(s) without an LCSC part: %s" % (len(missing_lcsc), missing_lcsc[:6]))
        if bad_lcsc:
            add("BOM.LCSC.FORMAT", "WARN", "part number is not C+digits: " + "; ".join(bad_lcsc[:6]))
        add("BOM.LCSC.FORMAT", "WARN", "offline: cannot verify that a part number matches the "
            "component; a wrong number is more dangerous than a missing one - trust the order "
            "page's match result")
    return set(seen.values())


# -------------------------------------------------------------------- CPL side
def check_cpl(table, items, board):
    def add(ident, status, msg):
        items.append({"id": ident, "verdict": status, "msg": msg})

    unit_pat = re.compile(r"\s*\((?:mm|mil|in|inch)\)\s*$|\s+(?:mm|mil)$", re.I)
    header = []
    for column in table["header"]:
        if unit_pat.search(column) and column.lower().startswith(("mid", "ref", "pad")):
            add("CPL.HEADER.UNIT", "BLOCK", "header must not carry a unit: %r (write %r)"
                % (column, unit_pat.sub("", column)))
            column = unit_pat.sub("", column)
        header.append(column)
    idx = {c: col_index(header, c) for c in P["cpl_required_columns"]}
    missing = [c for c, i in idx.items() if i is None]
    if missing:
        add("CPL.COL.REQUIRED", "BLOCK", "position file lacks required column(s) %s (header: %s)"
            % (missing, header))
        return None
    seen, xs, ys, units, bad_layer, bad_rot, bad_num = {}, [], [], set(), [], [], []
    for number, row in enumerate(table["rows"], 2):
        def get(name):
            return row[idx[name]] if idx[name] < len(row) else ""
        designator = get("Designator").strip('"')
        if not designator:
            add("CPL.COL.REQUIRED", "BLOCK", "row %d has an empty designator" % number)
            continue
        for ident, msg in des_problems(designator):
            add(ident.replace("BOM.", "CPL."), "BLOCK", "row %d %s" % (number, msg))
        key = designator.upper()
        if key in seen:
            add("CPL.DES.DUP", "BLOCK", "duplicate designator %s / %s" % (seen[key], designator))
        seen[key] = designator
        for label, bucket in (("Mid X", xs), ("Mid Y", ys)):
            value, unit = parse_coord(get(label))
            if value is None:
                bad_num.append("row %d %s=%r" % (number, label, get(label)))
            else:
                bucket.append(value)
                units.add(unit or "mm")
                if unit and unit not in P["allowed_coord_units"]:
                    bad_num.append("row %d %s unit %r not accepted" % (number, label, unit))
        if get("Layer").upper() not in P["allowed_layers"]:
            bad_layer.append("row %d %s=%r" % (number, designator, get("Layer")))
        rotation = get("Rotation")
        match = re.fullmatch(r"-?\d+(?:\.(\d+))?", rotation)
        if not match:
            bad_rot.append("row %d %s=%r" % (number, designator, rotation))
        elif match.group(1) and len(match.group(1)) > P["rotation_max_decimals"]:
            bad_rot.append("row %d %s has %d decimals > %d"
                           % (number, designator, len(match.group(1)), P["rotation_max_decimals"]))
    if bad_num:
        add("CPL.UNIT.CONSIST", "BLOCK", "unparsable coordinate or unit: " + "; ".join(bad_num[:6]))
    if len(units) > 1:
        add("CPL.UNIT.CONSIST", "BLOCK", "one file mixes units %s" % sorted(units))
    if bad_layer:
        add("CPL.LAYER", "BLOCK", "Layer may only be T/B/Top/Bottom: " + "; ".join(bad_layer[:6]))
    if bad_rot:
        add("CPL.ROT.NUMERIC", "BLOCK", "; ".join(bad_rot[:6]))
    unit = sorted(units)[0] if units else "mm"
    if unit == "mil" and any(parse_coord(r[idx["Mid X"]])[1] is None
                             for r in table["rows"] if idx["Mid X"] < len(r)):
        add("CPL.UNIT.MIL_SUFFIX", "BLOCK", "in mil every value must state its unit")
    if board and xs and ys:
        rx, ry = max(xs) - min(xs), max(ys) - min(ys)
        if unit == "mil":
            rx, ry = rx * 25.4 / 1000, ry * 25.4 / 1000
        if rx > board[0] or ry > board[1]:
            add("CPL.RANGE.BOARD", "BLOCK", "coordinate span %.3f x %.3f mm exceeds the board "
                "%s x %s mm (a part sits outside the board)" % (rx, ry, board[0], board[1]))
        else:
            add("CPL.RANGE.BOARD", "PASS", "coordinate span %.3f x %.3f mm fits the board %s x %s mm"
                % (rx, ry, board[0], board[1]))
    elif not board:
        add("CPL.RANGE.BOARD", "WARN", "no board size given (board_size or gerber), span not checked")
    return set(seen.values())


def board_from_gerber(path):
    from . import gerber as gg
    files = gg.read_package(path)
    _items, meas, _notes, _layers = gg.check_package(files, None)
    if "board_w" not in meas:
        raise Unsupported("cannot read the board outline size from the Gerber package")
    return (meas["board_w"], meas["board_h"])


def run_checks(bom_path=None, cpl_path=None, board=None):
    items, info = [], {}
    bom_set = cpl_set = None
    for label, path in (("BOM", bom_path), ("CPL", cpl_path)):
        if not path:
            continue
        table = read_table(path)
        info[label] = {"file": path, "encoding": table["encoding"],
                       "delimiter": table["delimiter"], "rows": len(table["rows"])}
        if table["encoding"] == "utf-16" or table["delimiter"] == "TAB":
            items.append({"id": "FILE.ENC.NATIVE", "verdict": "WARN",
                          "msg": "%s is EDA-native (%s + %s): normalise to UTF-8 comma CSV before "
                                 "uploading" % (label, table["encoding"], table["delimiter"])})
        if label == "BOM":
            bom_set = check_bom(table, items)
        else:
            cpl_set = check_cpl(table, items, board)
    if bom_set is not None and cpl_set is not None:
        only_bom, only_cpl = sorted(bom_set - cpl_set), sorted(cpl_set - bom_set)
        if only_bom or only_cpl:
            items.append({"id": "X.BOM_CPL.SET", "verdict": "BLOCK",
                          "msg": "BOM and position file disagree: BOM only %s; position only %s"
                                 % (only_bom[:10], only_cpl[:10])})
        else:
            items.append({"id": "X.BOM_CPL.SET", "verdict": "PASS",
                          "msg": "BOM and position file carry the same designators (%d)" % len(bom_set)})
    blocks = sum(1 for item in items if item["verdict"] == "BLOCK")
    return {"info": info, "items": items, "exit": 1 if blocks else 0,
            "verdict": "blocked - do not upload for assembly" if blocks else
                       "no blocker inside the declared scope (does not prove part numbers or "
                       "polarity are right)"}


# ---------------------------------------------------------------- normalisation
def normalize(src, dst, kind):
    table = read_table(src)
    header, rows = table["header"], table["rows"]
    out = []
    if kind == "cpl":
        coord_cols = [i for i, c in enumerate(header)
                      if c.lower() in ("mid x", "mid y", "ref x", "ref y", "pad x", "pad y")]
        units = set()
        for row in rows:
            for i in coord_cols:
                if i < len(row):
                    value, unit = parse_coord(row[i])
                    if value is not None:
                        units.add(unit or "mm")
        keep = len(units) == 1 and "mil" in units
        for row in rows:
            new = list(row)
            for i in coord_cols:
                if i < len(new):
                    value, unit = parse_coord(new[i])
                    if value is not None:
                        new[i] = (new[i] if not keep else (new[i] if unit else new[i] + "mil")).strip()
            out.append(new)
    else:
        out = [list(row) for row in rows]
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(header)
    writer.writerows(out)
    with open(dst, "w", encoding="utf-8", newline="") as handle:
        handle.write(buffer.getvalue())
    return {"from": table["encoding"] + "/" + table["delimiter"], "to": "utf-8/COMMA",
            "rows": len(out), "blank_lines_removed": table["blank_lines_removed"]}
