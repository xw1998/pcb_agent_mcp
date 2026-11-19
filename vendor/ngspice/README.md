# vendor/ngspice — the bundled engine

The smallest subset of the official `ngspice-47_64.7z` Windows build that runs a
deck **including XSPICE `A` cards**, vendored so that
`sim_spice(engine="ngspice")` works on a machine that never installed ngspice.

Licence and provenance: **[NOTICE.md](NOTICE.md)**. This directory is a **mixed**
licence third-party program (mostly Modified BSD, XSPICE code models public
domain, `table.cm` GPLv2+, some LGPL components), the rest of the repository is
PolyForm Noncommercial 1.0.0 (non-commercial), and they are separate programs.

## Layout

```
vendor/ngspice/
  bin/ngspice_con.exe              console build, 7.6 MB
  bin/libomp140.x86_64.dll         \
  bin/samplerate.dll                > hard link deps: missing any one = exit 127,
  bin/sndfile.dll                  /  no message at all
  lib/ngspice/analog.cm            XSPICE code models, the seven spinit loads
  lib/ngspice/digital.cm
  lib/ngspice/spice2poly.cm
  lib/ngspice/table.cm
  lib/ngspice/tlines.cm
  lib/ngspice/xtradev.cm
  lib/ngspice/xtraevt.cm
  share/ngspice/scripts/spinit     loads the code models at start-up
  COPYING                          verbatim from the archive's docs/COPYING (Modified BSD + exception list)
  NOTICE.md                        provenance + licence position
```

`lib/` must be the sibling of `bin/`: the `codemodel ../lib/ngspice/*.cm` lines
inside `spinit` are resolved **relative to the directory holding the
executable**, so the upstream shape is kept verbatim.

## Three measured facts about how this build finds its code models

All three were established while adding this directory. Each has a control that
can fail, because "the `A` card ran" on its own does not say *why*.

1. **`spinit` is found relative to the executable, not the cwd.** Running
   `bin/ngspice_con.exe` from an unrelated directory still applies it — proven by
   the negative control below (a corrupted `analog.cm` breaks the deck from that
   same unrelated directory). No `SPICE_SCRIPTS` is needed, and none is set.
2. **The `.cm` files are genuinely used when `spinit` loads them.** Replace
   `lib/ngspice/analog.cm` with a text file and the `gain` model disappears:
   `Error opening code model "../lib/ngspice/analog.cm"` →
   `MIF-ERROR - unable to find definition of model gain1`. So the files are not
   decoration; keeping them intact matters.
3. **This build also has the code models compiled in**, which is why deleting
   `lib/` *and* `share/` leaves the same `A` card working (with no `spinit`
   nothing overwrites the built-in registry). Both configurations answer
   `V(10) = 2.0`; the files are kept because that is what upstream ships and what
   keeps the layout recognisable, not because the binary is broken without them.

Symptom to recognise: `Warning: can't find the initialization file spinit.`
together with `MIF-ERROR - unable to find definition of model <name>`. That is
**not** "this build has no XSPICE" and **not** a version problem — it is a
missing/broken `spinit` or `.cm` set, or a wrong model type name (`gain`, not
`amp`).

## Reproduce in 30 seconds

```bash
cd vendor/ngspice
cat > /tmp/gain.cir <<'EOF'
* xspice gain
V1 1 0 1
A1 1 10 gain1
.model gain1 gain(gain=2.0)
R1 10 0 1k
.op
.end
EOF
./bin/ngspice_con.exe -b /tmp/gain.cir | grep "V(10)"      # V(10)  2.000000e+00

# negative control: same run, code model set corrupted
cp -r . /tmp/ngspice_bad && echo garbage > /tmp/ngspice_bad/lib/ngspice/analog.cm
/tmp/ngspice_bad/bin/ngspice_con.exe -b /tmp/gain.cir | grep MIF-ERROR
```

## Removing it

Delete this directory. `find_ngspice()` stops offering it, `engine="auto"` falls
back to the builtin stdlib subset, and `engine="ngspice"` answers
`ENGINE_NOT_FOUND` instead of silently returning a level-1 answer. No other file
in the repository reads anything under `vendor/`.
