"""Inject hand-written segments/vias into the pcb, optionally stripping zones."""
import re, sys, json, uuid

NAMESPACE = uuid.UUID('6f1e9a52-0f2d-4c39-9ac2-7b8f6a1d2e34')

# same sheet offset as _sky_emit2: board centre -> A4 page centre
OX, OY = 148.5, 105.0


def strip_zones(text):
    out = []
    i = 0
    while True:
        j = text.find('(zone ', i)
        if j < 0:
            out.append(text[i:])
            break
        out.append(text[i:j])
        depth = 0
        k = j
        while k < len(text):
            if text[k] == '(':
                depth += 1
            elif text[k] == ')':
                depth -= 1
                if depth == 0:
                    break
            k += 1
        i = k + 1
    return ''.join(out)


def net_table(text):
    tab = {}
    for m in re.finditer(r'\(net (\d+) "([^"]*)"\)', text):
        tab[m.group(2)] = int(m.group(1))
    return tab


def netref(tab, name):
    return str(tab[name]) if name in tab else '"%s"' % name


def f(v):
    s = '%.4f' % v
    s = s.rstrip('0').rstrip('.')
    return '0' if s in ('-0', '') else s


def main():
    src = '_scratch/sky/sky_star.kicad_pcb'
    dst = sys.argv[1] if len(sys.argv) > 1 else '_scratch/sky/sky_routed.kicad_pcb'
    nozone = '--nozone' in sys.argv
    text = open(src, encoding='utf-8').read()
    if nozone:
        text = strip_zones(text)
    tab = net_table(text)
    rt = json.load(open('_scratch/sky/_routes.json'))
    rows = []
    for i, s in enumerate(rt['segs']):
        nid = netref(tab, s['net'])
        (x1, y1), (x2, y2) = s['pts']
        rows.append('  (segment (start %s %s) (end %s %s) (width %s) (layer "%s") (net %s) (uuid "%s"))'
                    % (f(x1 + OX), f(-y1 + OY), f(x2 + OX), f(-y2 + OY), f(s['w']), s['layer'], nid,
                       uuid.uuid5(NAMESPACE, 'seg%d' % i)))
    for i, v in enumerate(rt['vias']):
        nid = netref(tab, v['net'])
        rows.append('  (via (at %s %s) (size %s) (drill %s) (layers "F.Cu" "B.Cu") (net %s) (uuid "%s"))'
                    % (f(v['x'] + OX), f(-v['y'] + OY), f(v['size']), f(v['drill']), nid,
                       uuid.uuid5(NAMESPACE, 'via%d' % i)))
    k = text.rstrip().rfind(')')
    out = text[:k] + '\n'.join(rows) + '\n' + text[k:]
    open(dst, 'w', encoding='utf-8', newline='\n').write(out)
    print('wrote', dst, len(rows), 'items')


if __name__ == '__main__':
    main()
