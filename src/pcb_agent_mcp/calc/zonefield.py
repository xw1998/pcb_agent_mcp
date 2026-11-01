"""Copper pours (zones) as 2-D resistive sheets grafted onto the track graph.

`oracle.board_ir_drop` answers "how much voltage does this net lose on the way to
the load" from the tracks, the vias and the pads. A copper pour on the same net
is in parallel with all of that, and on a real board it is often the ONLY thing
that joins two halves of the net - which is exactly why the track-only answer on
a plane-heavy board comes back as `singular` instead of as a number.

So a zone is modelled here as what it physically is: a thin sheet of copper.

    R_sheet = rho(T) / t          [ohm per square]

and a square grid of those squares, four-connected, is a resistor mesh whose
cell-to-cell resistance does NOT depend on the grid pitch (a square is a square
whatever its size). That last property is the reason this is worth doing at all:
the discretisation does not bias the sheet resistance, it only limits how finely
the current can spread near a contact.

What is deliberately crude, and is said out loud in the envelope:

  * a pad or track end that sits inside (or within `link_mm` of) the pour is tied
    to it with a ~1 uOhm link - i.e. the pour is assumed to be SOLIDLY connected.
    Real pads are often connected by THERMAL RELIEF SPOKES, which have a real
    resistance. Where those exist this model is optimistic (it under-reports the
    drop). It cannot know: the file rarely states it and the fill geometry only
    sometimes shows it.
  * the fill is used as the geometry (`filled_polygons` when the file has them,
    otherwise the zone outline), at the grid pitch that was actually chosen.
  * zone priority, minimum width, and the clearance to other nets are not solved
    for - the fill already encodes them.

The pitch is floored at 0.2 mm: a pad contact is a point at that scale anyway,
so a 0.1 mm grid on a 20 mm^2 pour buys nothing but wall clock.
"""
import math

from . import currentfield as CF

ZONE_SHEET_MODEL = "zone_sheet_grid"

# A pad/track that lands inside the fill is joined with this. It is NOT zero
# because the solver needs a finite conductance; 1 uOhm is 25 um of 2 mm wide 1 oz
# copper, i.e. far below anything else in the answer.
LINK_OHM = 1e-6

def zone_layer_list(z):
    """The copper layers a zone covers, from either adapter's spelling."""
    lays = z.get("layers")
    if not lays:
        one = z.get("layer")
        lays = [one] if one else []
    return [l for l in lays if l]

def _polys_of(z):
    """The fill if the file carries it, else the outline. (polys, used_fill)"""
    fp = z.get("filled_polygons") or []
    polys = [p for p in fp if p and len(p) >= 3]
    if polys:
        return polys, True
    out = z.get("outline") or []
    if len(out) >= 3:
        return [out], False
    return [], False

def _poly_area(pts):
    """Shoelace, absolute: the area a polygon actually covers."""
    n = len(pts)
    a = 0.0
    for k in range(n):
        x1, y1 = pts[k]
        x2, y2 = pts[(k + 1) % n]
        a += x1 * y2 - x2 * y1
    return abs(a) * 0.5

def plan_layer(zones, layer, rho_ohm_mm, thick_um, grid_mm=None, max_cells=4000,
               min_grid_mm=0.2, max_grid_mm=2.0):
    """Rasterise every zone of `layer` into one union mask and pick a pitch.

    The pitch comes from the AREA THE FILL COVERS, not from its bounding box: a
    layer can carry five 2 x 2 mm islands 100 mm apart, and planning off the
    bounding box would give each island two cells. A 180 x 100 mm plane at
    0.1 mm would be 1.8 million unknowns, and no answer from that is better than
    the answer from a few thousand, because the near-contact spreading is already
    the crude part of this model.
    """
    polys = []
    used_fill = 0
    used_outline = 0
    for z in zones:
        if layer not in zone_layer_list(z):
            continue
        pp, fill = _polys_of(z)
        if not pp:
            continue
        polys.extend(pp)
        if fill:
            used_fill += 1
        else:
            used_outline += 1
    if not polys:
        return None

    xs = [p[0] for poly in polys for p in poly]
    ys = [p[1] for poly in polys for p in poly]
    x0, y0 = min(xs), min(ys)
    x1, y1 = max(xs), max(ys)
    w = x1 - x0
    hgt = y1 - y0
    if not (w > 0 and hgt > 0):
        return None
    area = 0.0
    for poly in polys:
        area += _poly_area(poly)
    if area <= 0.0:
        area = w * hgt

    if grid_mm is not None:
        h = float(grid_mm)
    else:
        h = math.sqrt(area / float(max_cells))
    h = min(max(h, min_grid_mm), max_grid_mm)

    mask = None
    nx = ny = 0
    cells = 0
    for _pass in range(6):
        nx = int(math.ceil(w / h)) + 1
        ny = int(math.ceil(hgt / h)) + 1
        if nx * ny > 4000000:
            return None
        mask = bytearray(nx * ny)
        for poly in polys:
            CF._fill_poly(mask, nx, ny, x0, y0, h, poly)
        cells = sum(mask)
        if cells == 0:
            return None
        if cells > max_cells and h < max_grid_mm:
            h = min(max_grid_mm, h * math.sqrt(cells / float(max_cells)) * 1.02)
            continue
        break
    t_mm = float(thick_um) * 1e-3
    return {"layer": layer, "nx": nx, "ny": ny, "x0": x0, "y0": y0, "h_mm": h,
            "mask": mask, "cells": cells, "thick_um": float(thick_um),
            "sheet_ohm_per_sq": float(rho_ohm_mm) / t_mm,
            "fill_polygons": used_fill, "outline_only": used_outline,
            "fill_area_mm2": area}

def build_sheets(zones, net, rho_ohm_mm, thick_um_by_layer, grid_mm=None,
                 max_cells_total=8000, min_grid_mm=0.2, max_grid_mm=2.0):
    """Rasterise every same-net, non-keepout zone, one grid per copper layer."""
    layers = []
    seen = []
    for z in zones or []:
        if z.get("net") != net:
            continue
        if z.get("keepout") or z.get("is_keepout"):
            continue
        for lay in zone_layer_list(z):
            if lay not in seen:
                seen.append(lay)
    if not seen:
        return {"layers": [], "cache_hit": False}
    per = max(400, int(max_cells_total // len(seen)))
    for lay in seen:
        t = (thick_um_by_layer or {}).get(lay)
        if t is None:
            continue
        info = plan_layer([z for z in zones if z.get("net") == net],
                          lay, rho_ohm_mm, t, grid_mm=grid_mm,
                          max_cells=per, min_grid_mm=min_grid_mm,
                          max_grid_mm=max_grid_mm)
        if info is not None:
            layers.append(info)
    return {"layers": layers}

def cell_index(info, x, y):
    i = int(math.floor((float(x) - info["x0"]) / info["h_mm"]))
    j = int(math.floor((float(y) - info["y0"]) / info["h_mm"]))
    if i < 0 or j < 0 or i >= info["nx"] or j >= info["ny"]:
        return None
    if not info["mask"][j * info["nx"] + i]:
        return None
    return (i, j)

def nearest_cell(info, x, y, link_mm):
    """Nearest filled cell within `link_mm`, and how far away it was."""
    h = info["h_mm"]
    ring = int(math.ceil(max(link_mm, h * 0.5) / h))
    i0 = int(math.floor((float(x) - info["x0"]) / h))
    j0 = int(math.floor((float(y) - info["y0"]) / h))
    best = None
    bestd = None
    for j in range(j0 - ring, j0 + ring + 1):
        if j < 0 or j >= info["ny"]:
            continue
        row = j * info["nx"]
        for i in range(i0 - ring, i0 + ring + 1):
            if i < 0 or i >= info["nx"]:
                continue
            if not info["mask"][row + i]:
                continue
            cx = info["x0"] + (i + 0.5) * h
            cy = info["y0"] + (j + 0.5) * h
            d = math.hypot(cx - float(x), cy - float(y))
            if d <= link_mm and (bestd is None or d < bestd):
                bestd = d
                best = (i, j)
    return best, bestd

def sheet_edges(info):
    """Every four-connected pair of filled cells, as one sheet resistance each."""
    nx = info["nx"]
    ny = info["ny"]
    mask = info["mask"]
    r = info["sheet_ohm_per_sq"]
    out = []
    for j in range(ny):
        base = j * nx
        right = base + nx
        for i in range(nx):
            if not mask[base + i]:
                continue
            if i + 1 < nx and mask[base + i + 1]:
                out.append(((i, j), (i + 1, j), r))
            if j + 1 < ny and mask[right + i]:
                out.append(((i, j), (i, j + 1), r))
    return out

def cell_xy(info, key):
    i, j = key
    return (info["x0"] + (i + 0.5) * info["h_mm"],
            info["y0"] + (j + 0.5) * info["h_mm"])
