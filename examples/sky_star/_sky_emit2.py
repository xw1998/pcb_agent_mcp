"""Emit the re-floorplanned 天空星 board: my own placement, no copper yet."""
import sys, json
sys.path.insert(0, '.')
sys.path.insert(0, '_scratch/sky')
from _sky_model import model

MIL = 0.0254
BW, BH = 44.0, 72.0
# A4 sheet is 297 x 210; put the board centre on the sheet centre so the
# drawing opens inside the page frame instead of hanging off its corner.
OX, OY = 148.5, 105.0
LAYERS = [
    (0, 'F.Cu', 'signal'), (31, 'B.Cu', 'signal'),
    (36, 'B.SilkS', 'user', 'B.Silkscreen'), (37, 'F.SilkS', 'user', 'F.Silkscreen'),
    (34, 'B.Paste', 'user'), (35, 'F.Paste', 'user'),
    (38, 'B.Mask', 'user'), (39, 'F.Mask', 'user'),
    (46, 'B.CrtYd', 'user'), (47, 'F.CrtYd', 'user'),
    (44, 'Edge.Cuts', 'user'),
]


def f(v, n=4):
    s = ('%.*f' % (n, v)).rstrip('0').rstrip('.')
    return s if s not in ('-0', '') else '0'


SHAPE = {'RECT': 'rect', 'ROUND': 'circle', 'OVAL': 'oval', 'ELLIPSE': 'oval'}


def emit(m, origin, out):
    nets = m['nets']
    idx = {n: i + 1 for i, n in enumerate(nets)}
    L = []
    A = L.append
    A('(kicad_pcb (version 20241229) (generator "pcb_agent_mcp")')
    A('  (general (thickness 1.6))')
    A('  (paper "A4")')
    A('  (layers')
    for row in LAYERS:
        A('    (%d "%s" %s)' % row if len(row) == 3 else '    (%d "%s" %s "%s")' % row)
    A('  )')
    A('  (setup (pad_to_mask_clearance 0))')
    A('  (net 0 "")')
    for n in nets:
        A('  (net %d "%s")' % (idx[n], n.replace('"', '')))
    byref = {c['ref']: c for c in m['components']}
    for ref in sorted(origin, key=lambda r: (origin[r][3], r)):
        ox, oy, rot, layer = origin[ref]
        c = byref[ref]
        side = 'B' if layer == 2 else 'F'
        A('  (footprint "LCEDA:%s" (layer "%s.Cu") (at %s %s %s) (attr smd)'
          % (ref, side, f(ox + OX), f(-oy + OY), f(rot)))
        hide = ' hide' if ref[:1] in 'RCD' or ref.startswith(('LED', 'RN')) else ''
        mir = ' (justify mirror)' if layer == 2 else ''
        A('    (property "Reference" "%s" (at 0 %s 0) (layer "%s.SilkS") (effects (font (size 0.8 0.8) (thickness 0.13))%s%s))'
          % (ref, f(-1.6 if layer == 1 else 1.6), side, mir, hide))
        A('    (property "Value" "%s" (at 0 %s 0) (layer "%s.Fab") (effects (font (size 0.6 0.6) (thickness 0.1)) hide))'
          % (c['device'][:24], f(1.8), side))
        for p in c['pads']:
            num = str(p['num'])
            net = m['padnet'].get((c['id'], num)) or ''
            netstr = ' (net %d "%s")' % (idx[net], net.replace('"', '')) if net in idx else ''
            w = (p['w'] or 0) * MIL
            h = (p['h'] or 0) * MIL
            if w < 0.05:
                w = 0.6
            if h < 0.05:
                h = 0.6
            if abs(w - h) < 1e-6:
                pang = 0.0
            else:
                pang = (rot + (p.get('angle') or 0)) % 360.0
            A('    (pad "%s" smd %s (at %s %s %s) (size %s %s) (layers "%s.Cu" "%s.Paste" "%s.Mask")%s)'
              % (num, SHAPE.get((p.get('type') or 'RECT').upper(), 'rect'),
                 f(p['x'] * MIL), f(-p['y'] * MIL), f(pang), f(w), f(h), side, side, side, netstr))
        A('  )')
    # pours.  Only the OUTLINE is mine; the copper itself is poured by KiCad's
    # ZONE_FILLER - no hand-drawn copper anywhere.
    #   B.Cu : one GND plane, corners bevelled 45 deg.
    #   F.Cu : +3V3 as ONE shaped plate (x +-21.5, y +-35.5) at a priority above
    #          GND, because +3V3 feeds 45 pads spread over the whole board and the
    #          old rail version left 38 endpoints stranded; +5V keeps its own
    #          higher-priority band along the input-protection row; GND then fills
    #          every gap that is left (and is stitched to B.Cu afterwards).
    #   Priorities are written out explicitly: two pours of the SAME net that
    #   overlap at the SAME priority is a `zones_intersect` error.
    import uuid
    ZONES = [
        ('GND', 'B.Cu', 0, [(-17.8, -34.8), (17.8, -34.8), (20.8, -31.8), (20.8, 31.8),
                            (17.8, 34.8), (-17.8, 34.8), (-20.8, 31.8), (-20.8, -31.8)]),
        ('+3V3', 'F.Cu', 6, [(-21.5, -35.5), (21.5, -35.5), (21.5, 35.5), (-21.5, 35.5)]),
        ('+5V', 'F.Cu', 11, [(-11.90, -25.60), (9.20, -25.60), (9.20, -24.90),
                             (-5.65, -24.90), (-5.65, -24.20), (-6.60, -24.20),
                             (-6.60, -24.90), (-11.90, -24.90)]),
        ('GND', 'F.Cu', 0, [(-20.8, -34.8), (20.8, -34.8), (20.8, 34.8), (-20.8, 34.8)]),
    ]
    for k, (net, layer, prio, pts) in enumerate(ZONES):
        if net not in idx:
            continue
        A('  (zone (net %d) (net_name "%s") (layer "%s") (uuid "%s") (priority %d)'
          % (idx[net], net, layer, uuid.uuid5(uuid.NAMESPACE_DNS, 'sky-%d-%s' % (k, net)), prio))
        A('    (hatch edge 0.5) (connect_pads yes (clearance 0.2)) (min_thickness 0.2)')
        A('    (fill yes (thermal_gap 0.2) (thermal_bridge_width 0.2) (island_removal_mode 0))')
        A('    (polygon (pts %s))' % ' '.join('(xy %s %s)' % (f(x + OX), f(-y + OY)) for x, y in pts))
        A('  )')
    A('  (gr_rect (start %s %s) (end %s %s) (stroke (width 0.1) (type default)) (fill none) (layer "Edge.Cuts"))'
      % (f(-BW / 2 + OX), f(-BH / 2 + OY), f(BW / 2 + OX), f(BH / 2 + OY)))
    A(')')
    open(out, 'w', encoding='utf-8').write('\n'.join(L) + '\n')
    return out


if __name__ == '__main__':
    m = model('examples/ProPrj_立创梁山派·天空星开发板_2026-11-05.epro2')
    origin = {k: tuple(v) for k, v in json.load(open('_scratch/sky/_origin.json')).items()}
    out = emit(m, origin, '_scratch/sky/sky_star.kicad_pcb')
    print(out, len(origin), 'footprints')
