# NOTICE — bundled ngspice binary

This directory ships a **third-party program**, not code written for this
project. Nothing here is covered by this repository's MIT licence.

| item | value |
| --- | --- |
| program | ngspice (circuit level simulation program) |
| version | **ngspice-47**, creation date `Aug 11 2026 13:55:25` |
| binary | `bin/ngspice_con.exe` + the three DLLs it links against |
| upstream | <https://ngspice.sourceforge.io/> |
| upstream source | <https://sourceforge.net/projects/ngspice/files/ng-spice-rework/47/> |
| archive downloaded | `ngspice-47_64.7z` (Windows 64-bit build, 13 814 879 bytes) |
| archive sha256 | `59225971bd68cdd1199443649aa4615a9e6d684933f205ab49006a3942518f5a` |
| licence | **mixed**, see below and `COPYING` (verbatim from the archive's `docs/COPYING`) |
| modifications | **none** — the files are byte-for-byte copies out of the archive |

## Licence, stated accurately

ngspice is **not** a single-licence project, and `COPYING` says so itself: the
default is the **`Modified BSD' (3-clause) licence**, with a short list of
exceptions. The parts that matter for *this* bundle:

| component | ships as | licence |
| --- | --- | --- |
| ngspice frontend / analogue devices | `bin/ngspice_con.exe` | Modified BSD |
| XSPICE code models (analogue, digital, xtradev, xtraevt, spice2poly, tlines) | `lib/ngspice/*.cm` | **public domain** (Georgia Tech) |
| `xspice/icm/table` | `lib/ngspice/table.cm` | **GPLv2 or newer** |
| KLU solver, numparam | compiled into the exe | LGPLv2 / LGPLv2+ |
| OSDI compact models (MPLv2.0) | **not shipped** — the `.osdi` files were left out | — |
| sndfile / samplerate / libomp DLLs | `bin/*.dll` | LGPLv2.1 / BSD / Apache-2.0-with-LLVM-exception |

So the honest summary is: **mostly Modified BSD + public-domain XSPICE, with one
GPLv2+ code model (`table`) and (L)GPL components inside the binary.** All of
them permit redistribution of an unmodified binary provided the notices and the
licence text travel with it and the corresponding source is available — which is
why `COPYING` is kept here verbatim and why the source location is named above
rather than claimed under this repository's MIT terms.

If you rebuild or patch any of these files, the upstream obligations change from
"point at the source" to "publish your source", and the GPLv2+ part
(`table.cm`) additionally forbids shipping a modified version under a
more restrictive licence.

## Why it is here

`sim_spice(engine="ngspice")` delegates to ngspice for everything the builtin
stdlib subset does not solve — XSPICE `A` behavioural blocks above all. Before
`0.0.37` that delegation required the user to install ngspice themselves, and a
hand-unpacked install routinely cannot even load its code models. The bundled
binary removes that step: **an unmodified copy of the upstream build, called as
an ordinary subprocess, with a per-run temporary working directory.**

## How the two licences coexist

This repository's own code is MIT. The bundled program is BSD/LGPL/GPL, and the
two are **separate programs**: the MIT code never links against ngspice, only
`subprocess.run`s it, and to the OS this is the same relationship as "my shell
script calls `grep`". Nothing in `src/` includes a header from the bundle, links
an object from it, or vendors a line of its source.

Removing this directory is always safe: `find_ngspice()` simply stops seeing it,
`engine="auto"` is unaffected either way, `engine="ngspice"` answers
`ENGINE_NOT_FOUND`, and **nothing else in the repository depends on it.**

## What was deliberately left out of the archive

The archive unpacks to 57 MB. `vendor/ngspice/` is 14 MB. Dropped, and why:

| dropped | why |
| --- | --- |
| `bin/ngspice.exe` (8.6 MB, GUI build) | this project only ever runs `-b` batch mode |
| `lib/ngspice/*.osdi` (16 files, 5.6 MB) | OpenVAF/OSDI compact models (`psp103`, `bsimbulk`, `hicumL2`, …). `spinit` only loads them when the `osdi_enabled` variable is set, and it starts unset; none of this project's decks use them (and they are the only MPLv2.0 part) |
| `share/ngspice/scripts/*` except `spinit` | `devaxis`, `setplot`, `spectrum`, `ghnggen`, `ciderinit`, the GHDL shim sources — none are read by a `-b -r` run |
| `examples/`, `docs/` except `COPYING` | documentation and 2.9 MB of example decks |

Kept, with reasons:

* `bin/libomp140.x86_64.dll`, `bin/sndfile.dll`, `bin/samplerate.dll` — **hard
  link dependencies**. Remove any one and `ngspice_con.exe` exits 127 with no
  message at all (`ldd` lists all three). `sndfile`/`samplerate` are pulled in
  even though this project never touches audio or file I/O.
* `lib/ngspice/*.cm` — the seven XSPICE code models `spinit` loads. Without them
  every `A` card fails with `MIF-ERROR - unable to find definition of model …`
  (measured: corrupting `analog.cm` is enough to break the `gain` model).
* `share/ngspice/scripts/spinit` — the init file that loads the code models at
  all, found relative to the executable as `../share/ngspice/scripts/spinit`.
