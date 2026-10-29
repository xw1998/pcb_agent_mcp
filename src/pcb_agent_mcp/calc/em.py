"""Full-wave electromagnetics: the seam where this server hands over, and stops.

Nothing in this repository solves Maxwell's equations in 3-D, and nothing here
pretends to. A pattern, an S11 sweep, a coupling coefficient, a cavity Q with a
field distribution - all of those need a mesh, a time or frequency domain solve
and a port definition, and every one of them is a research-grade program:
openEMS, Meep, gprMAX, Palace. Writing a tenth of one and calling it a solver
would produce numbers that look like answers and are not, which is the one
failure mode this project refuses above all others.

What CAN be done without breaking the zero-dependency contract is the same thing
`calc.spice` does for ngspice: **find out whether the user already has a solver,
say which version, and say exactly what to hand it.** That is a real answer to
"can you simulate this antenna" - it is "not here, and here is the program that
can, whether you have it, and what it needs from you".

Two rules, copied from the ngspice seam because they are the ones that keep a
delegation honest:

* asking for a named solver that is not installed is an ERROR
  (`SOLVER_NOT_FOUND`), never a silent "here are the closed-form numbers
  instead" - the caller asked for the reference implementation;
* a binary being present is NOT evidence that it works or that this server has
  ever run it: every answer carries `verified_here: false` until a real run is
  recorded, and `VERSION_VERIFICATION` names what has been done (nothing).
"""
import os
import re
import shutil
import subprocess

# What each solver is, how to find it, and what the handoff has to look like.
# `exes` are tried on PATH; `dirs` are the places the Windows installers put it,
# which `shutil.which` does not see.
DETECTORS = {
    "openems": {
        "display": "openEMS (FDTD, with CSXCAD geometry and AppCSXCAD viewer)",
        "env": "PCB_AGENT_OPENEMS",
        "exes": ("openEMS", "openEMS.exe", "openEMS.bat"),
        "dirs": (r"C:\Program Files\openEMS", r"C:\Program Files (x86)\openEMS",
                 r"C:\openEMS"),
        "version_arg": ("--version",),
        "handoff": ("an XML CSXCAD geometry plus an openEMS command file (.xml/.fsp): "
                    "the solver's own AppCSXCAD builds them from the stackup, the "
                    "copper and a port definition - this server does not emit them"),
        "needs_from_you": ["stackup with exact dielectric Dk/Df per layer",
                           "copper geometry (board_export writes Gerber/STEP)",
                           "port / feed definition and the frequency band"],
    },
    "meep": {
        "display": "Meep (FDTD, scriptable in Python or Scheme)",
        "env": "PCB_AGENT_MEEP",
        "exes": ("meep", "meep.exe", "meep-mpi", "meep-mpi.exe"),
        "dirs": (),
        "version_arg": ("--version",),
        "handoff": ("a Python or Scheme driver that builds the geometry and runs the "
                    "solve; Meep is a library, so the driver IS the input - this "
                    "server does not emit one"),
        "needs_from_you": ["a Meep installation and its Python bindings",
                           "the geometry as a script", "sources, ports and monitors"],
    },
    "gprmax": {
        "display": "gprMAX (FDTD for GPR / layered media)",
        "env": "PCB_AGENT_GPRMAX",
        "exes": ("gprMax", "gprMax.exe", "python -m gprMax"),
        "dirs": (),
        "version_arg": ("--version",),
        "handoff": "an input .in file; gprMAX is driven by its own CLI",
        "needs_from_you": ["the layered model as a .in file", "a source and receivers"],
    },
}

DEFAULT_NAMES = ("openems", "meep", "gprmax")

PROBE_TIMEOUT_S = 15.0

# Nothing here has ever been run against a real solver. Keep this string honest:
# it is what the reply quotes, so an empty evidence list must read as "none".
VERSION_VERIFICATION = (
    "this repository has never run any full-wave solver. The probe only reports "
    "that a binary exists and what it printed for --version; presence is not "
    "evidence that the solver works, that its mesh converged, or that this server "
    "can drive it. Every answer therefore carries verified_here: false"
)

# The three things a person actually wants, and which of them exist here.
WHAT_THIS_SERVER_DOES = (
    "closed-form starting geometry (calc.antenna / calc.feedline / calc.filter / "
    "calc.cavity): dimensions, impedances and mode frequencies where a closed form "
    "is exact or a documented approximation, each with what it cannot see"
)

WHAT_IT_WILL_NOT_DO = (
    "radiation patterns, gain, efficiency, S11 over a band, mutual coupling, "
    "cavity Q with a field distribution, detuning from a real enclosure, or any "
    "answer that requires solving Maxwell's equations in 3-D. Those need a mesh "
    "and a port, i.e. a full-wave program, and no closed form substitutes for one"
)

_STEPS = (
    "install a solver listed under `solvers` (or point PCB_AGENT_<NAME> at it)",
    "build the model inside that program's own tooling - it knows its mesh and port "
    "formats, and this server never emits them",
    "export the geometry from here first (board_export: Gerber for copper, STEP for "
    "the 3-D bodies) and take the stackup from board_summary, so the model starts "
    "from the real board",
    "sweep, then come back here for the closed-form sanity check that catches a "
    "model off by a factor - e.g. a 2.45 GHz quarter-wave monopole near 31 mm in air",
)

def _version_of(name, argv, timeout_s=PROBE_TIMEOUT_S):
    """First version-looking line the binary prints. Never raises, never hangs long."""
    try:
        got = subprocess.run(list(argv) + list(DETECTORS[name]["version_arg"]),
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             timeout=float(timeout_s))
        text = (got.stdout or b"").decode("utf-8", "replace")
        lines = [x.strip() for x in text.splitlines() if x.strip()]
        for line in lines:
            if re.search(r"\d+\.\d+", line):
                return line[:200], got.returncode
        return (lines[0][:200] if lines else None), got.returncode
    except (OSError, subprocess.SubprocessError):
        return None, None
    except Exception:                          # noqa: BLE001 - a probe is not evidence
        return None, None

def _env_of(name):
    """`(value, exists)` for the solver's override variable.

    A variable that points at nothing is NOT the same answer as "not installed":
    the usual cause is a typo or a moved install, so it is reported on its own
    line (`env_problem`) instead of being folded into "missing".
    """
    spec = DETECTORS[name]
    value = (os.environ.get(spec["env"]) or "").strip().strip('"')
    return value, bool(value) and os.path.isfile(value)

def _candidates(name):
    """Where this solver could be, in the order the evidence should be read."""
    spec = DETECTORS[name]
    env, exists = _env_of(name)
    if exists:
        yield env, "env %s" % spec["env"]
    for exe in spec["exes"]:
        if " " in exe:                          # "python -m gprMax"
            continue
        found = shutil.which(exe)
        if found:
            yield found, "on PATH"
    for root in spec["dirs"]:
        if not os.path.isdir(root):
            continue
        for exe in spec["exes"]:
            if " " in exe:
                continue
            path = os.path.join(root, exe)
            if os.path.isfile(path):
                yield path, "install directory"
        try:
            for sub in sorted(os.listdir(root)):
                for exe in spec["exes"]:
                    if " " in exe:
                        continue
                    path = os.path.join(root, sub, exe)
                    if os.path.isfile(path):
                        yield path, "install directory (versioned)"
        except OSError:
            pass

def detect(names=None, probe=True, timeout_s=PROBE_TIMEOUT_S):
    """Which full-wave solvers are on this machine. Absence is an answer, not an error."""
    want = list(names) if names else list(DEFAULT_NAMES)
    rows = []
    for name in want:
        spec = DETECTORS.get(name)
        if spec is None:
            raise ValueError("unknown solver %r (know: %s)"
                             % (name, ", ".join(sorted(DETECTORS))))
        argv, where = None, None
        for candidate, evidence in _candidates(name):
            argv, where = candidate, evidence
            break
        env, exists = _env_of(name)
        row = {"solver": name, "display": spec["display"],
               "env_var": spec["env"], "found": argv is not None,
               "path": argv, "how_found": where,
               "version": None, "exit_code": None,
               "verified_here": False,
               "handoff": spec["handoff"],
               "needs_from_you": list(spec["needs_from_you"])}
        if env and not exists:
            row["env_problem"] = ("%s = %r, which is not a file - either the path is "
                                  "wrong or the install moved, so `found` says nothing "
                                  "about whether it is installed" % (spec["env"], env))
        if argv and probe:
            row["version"], row["exit_code"] = _version_of(name, [argv], timeout_s)
        rows.append(row)
    return {"solvers": rows,
            "found": [r["solver"] for r in rows if r["found"]],
            "missing": [r["solver"] for r in rows if not r["found"]],
            "verification": VERSION_VERIFICATION}

def capabilities():
    """The honest matrix: what is here, what is not, and how to hand over."""
    got = detect(probe=False)
    return {"solvers": got["solvers"], "found": got["found"],
            "missing": got["missing"],
            "this_server_does": WHAT_THIS_SERVER_DOES,
            "this_server_will_not_do": WHAT_IT_WILL_NOT_DO,
            "how_to_hand_over": list(_STEPS),
            "verification": VERSION_VERIFICATION}

def solver_argv(name, probe=True, timeout_s=PROBE_TIMEOUT_S):
    """`(argv, row)` for one named solver, or `(None, row)` when it is not there."""
    got = detect(names=[name], probe=probe, timeout_s=timeout_s)
    row = got["solvers"][0]
    return (row["path"], row)
