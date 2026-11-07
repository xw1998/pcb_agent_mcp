"""Dump the pad/absolute-coordinate net table used by the routing scripts."""
import sys, json, math
sys.path.insert(0, '.')
sys.path.insert(0, '_scratch/sky')
from _sky_model import model

MIL = 0.0254
m = model('examples/ProPrj_立创梁山派·天空星开发板_2026-11-05.epro2')
origin = json.load(open('_scratch/sky/_origin.json'))
byref = {c['ref']: c for c in m['components']}
nets = {}

for ref, (ox, oy, rot, layer) in origin.items():
    c = byref[ref]
    th = math.radians(rot)
    ct, st = math.cos(th), math.sin(th)
    for p in c['pads']:
        num = str(p['num'])
        net = m['padnet'].get((c['id'], num)) or ''
        if not net:
            continue
        px = (p['x'] or 0) * MIL
        py = (p['y'] or 0) * MIL
        ax = ox + px * ct - py * st
        ay = oy + px * st + py * ct
        w = (p['w'] or 0) * MIL
        h = (p['h'] or 0) * MIL
        pang = 0 if abs(w - h) < 1e-6 else (rot + (p.get('angle') or 0)) % 360
        nets.setdefault(net, []).append(
            dict(ref=ref, num=num, x=round(ax, 4), y=round(ay, 4),
                 w=round(w, 4), h=round(h, 4), a=round(pang, 2),
                 layer='B' if layer == 2 else 'F', shape=p['type']))

json.dump(nets, open('_scratch/sky/_nets.json', 'w'), indent=1, ensure_ascii=False)
print('nets', len(nets), 'pads', sum(len(v) for v in nets.values()))
for n in sorted(nets, key=lambda k: -len(nets[k]))[:12]:
    print('%-24s %d' % (n, len(nets[n])))
