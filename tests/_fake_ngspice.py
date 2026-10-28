"""A stand-in for ngspice, so the delegation plumbing can be tested on a machine
that does not have the real engine.

It is launched exactly the way the real one is:

    python _fake_ngspice.py -b -r <out.raw> -o <log> <deck.cir>

and `--version` prints one line, like the real binary. Everything it should
answer is read from the JSON file named by $FAKE_SPEC, because the parent
process is the one that decides what the "engine" says:

    {"nodes": {"1": 10.0, "2": 5.0}, "kind": "op",
     "format": "ascii", "rc": 0, "write_raw": true, "log": ""}

    kind    "op" | "tran" | "ac"      (ac values are [re, im] pairs)
    format  "ascii" | "binary"
    rc      exit status to return
    write_raw  false => exit 0 without producing a rawfile (the "silent
               nothing" case that must be reported as ENGINE_OUTPUT_INVALID)
"""
import json
import os
import struct
import sys


def _write_ascii(path, spec):
    nodes = spec.get("nodes") or {}
    kind = spec.get("kind", "op")
    names = list(nodes.keys())
    if kind == "tran":
        vars_ = [("time", "time")] + [("v(%s)" % n, "voltage") for n in names]
        rows = []
        for p, t in enumerate(spec.get("times") or [0.0]):
            vals = [t] + [float(nodes[n][p]) for n in names]
            rows.append(vals)
        plotname, flags = "Transient Analysis", "real"
    elif kind == "ac":
        vars_ = [("frequency", "frequency")] + [("v(%s)" % n, "voltage")
                                                for n in names]
        rows = []
        for p, f in enumerate(spec.get("freqs") or [1.0]):
            # in a complex plot every vector is a (re, im) pair, the frequency
            # axis included - real ngspice writes the 0 imaginary part too
            vals = [float(f), 0.0]
            for n in names:
                re_, im_ = nodes[n][p]
                vals.extend([float(re_), float(im_)])
            rows.append(vals)
        plotname, flags = "AC Analysis", "complex"
    else:
        vars_ = [("v(%s)" % n, "voltage") for n in names]
        rows = [[float(nodes[n]) for n in names]]
        plotname, flags = "Operating Point", "real"

    out = ["Title: fake ngspice", "Date: never", "Plotname: " + plotname,
           "Flags: " + flags, "No. Variables: %d" % len(vars_),
           "No. Points: %d" % len(rows), "Variables:"]
    for i, (name, typ) in enumerate(vars_):
        out.append("\t%d\t%s\t%s" % (i, name, typ))
    out.append("Values:")
    for p, row in enumerate(rows):
        for i, v in enumerate(row):
            text = "%.15e" % v
            out.append(("%d\t%s" % (p, text)) if i == 0 else ("\t" + text))
    with open(path, "w", encoding="ascii") as fh:
        fh.write("\n".join(out) + "\n")


def _write_binary(path, spec):
    nodes = spec.get("nodes") or {}
    kind = spec.get("kind", "op")
    names = list(nodes.keys())
    complex_ = kind == "ac"
    if kind == "tran":
        vars_ = [("time", "time")] + [("v(%s)" % n, "voltage") for n in names]
        rows = [[t] + [float(nodes[n][p]) for n in names]
                for p, t in enumerate(spec.get("times") or [0.0])]
        plotname, flags = "Transient Analysis", "real"
    elif complex_:
        vars_ = [("frequency", "frequency")] + [("v(%s)" % n, "voltage")
                                                for n in names]
        rows = [[float(f), 0.0] + [c for n in names
                                   for c in (float(nodes[n][p][0]),
                                             float(nodes[n][p][1]))]
                for p, f in enumerate(spec.get("freqs") or [1.0])]
        plotname, flags = "AC Analysis", "complex"
    else:
        vars_ = [("v(%s)" % n, "voltage") for n in names]
        rows = [[float(nodes[n]) for n in names]]
        plotname, flags = "Operating Point", "real"

    head = ["Title: fake ngspice", "Date: never", "Plotname: " + plotname,
            "Flags: " + flags, "No. Variables: %d" % len(vars_),
            "No. Points: %d" % len(rows), "Variables:"]
    for i, (name, typ) in enumerate(vars_):
        head.append("\t%d\t%s\t%s" % (i, name, typ))
    head.append("Binary:")
    with open(path, "wb") as fh:
        fh.write(("\n".join(head) + "\n").encode("ascii"))
        for row in rows:
            fh.write(struct.pack("<%dd" % len(row), *row))


def main(argv):
    if "--version" in argv:
        # $FAKE_VERSION lets a test impersonate a specific real version string,
        # which is how the VERIFIED_ENGINES gating is exercised without a binary.
        sys.stdout.write(os.environ.get(
            "FAKE_VERSION", "fake-ngspice 1.0 (stand-in for tests)") + "\n")
        return 0
    spec_path = os.environ.get("FAKE_SPEC")
    if not spec_path:
        sys.stderr.write("FAKE_SPEC is not set\n")
        return 2
    with open(spec_path, "r", encoding="utf-8") as fh:
        spec = json.load(fh)
    log_path = None
    raw_path = None
    for i, a in enumerate(argv):
        if a == "-r" and i + 1 < len(argv):
            raw_path = argv[i + 1]
        elif a == "-o" and i + 1 < len(argv):
            log_path = argv[i + 1]
    if log_path and spec.get("log"):
        with open(log_path, "w", encoding="utf-8") as fh:
            fh.write(spec["log"])
    rc = int(spec.get("rc", 0))
    if rc != 0:
        return rc
    if not spec.get("write_raw", True) or not raw_path:
        return 0
    if spec.get("format", "ascii") == "binary":
        _write_binary(raw_path, spec)
    else:
        _write_ascii(raw_path, spec)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
