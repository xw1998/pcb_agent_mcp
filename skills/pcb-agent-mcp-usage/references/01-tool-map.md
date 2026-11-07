# 工具全表（0.0.125 实测 205 个）

用途：查名字。语义以 `tool_search` / `tools/list` 的当前返回为准，本表只给一句话索引。

注意：默认档位 `core` 只广告其中约 45 个 + `tool_search` / `tool_call`。表里出现但清单里没有的，走 `tool_search` 找、`tool_call` 调。

## 读板 / 写板 / 度量（49）

- `board_summary` — Open a board and report what the file actually carries: counts, board outline bbox, stackup, design rules, and
- `board_components` — Every placed component with reference, value, library id, position (mm, Y up), rotation and pad count, plus a 
- `board_nets` — Per net: pad/track/via/zone counts and total routed length.
- `board_clearance` — Minimum edge-to-edge distance between copper of different nets, with the offending pairs named.
- `board_free_space` — Largest rectangles on a layer where a new component could sit, from an occupancy grid with a clearance margin.
- `board_routability` — Can each pad actually get off the pad, and how crowded is the layer? A pad with no way out is a hard blocker n
- `board_intent` — Check a DESIGN INTENT against a board before anything is routed.
- `board_dangling_copper` — Find the copper that hangs off a board attached at one end only, and cut the part of it that cannot be carryin
- `board_blockers` — What is actually in the way of one pad (or one point) on one layer, and what it would take to move each blocke
- `board_run_ledger` — What this server was asked to do to this board before, and what it said.
- `board_pour_connectivity` — Do the pads of the pour's net actually land inside the filled copper? This is a pad-CENTRE-in-polygon test and
- `board_net_report` — Per net: routed length, segments, vias, pads, and detour_ratio = length / MST over the net's pad centres (the smell test for "this net took the scenic route").
- `board_pour_audit` — Per zone, per FILLED ISLAND: area, pads of the zone's net inside, and SAME-NET VIAS inside; an island with neither is dead copper.
- `board_render` — Rasterise one copper layer to a PNG and return the path, so the routing can be LOOKED at instead of described.
- `board_angle_audit` — Three numbers that must not be mixed: segment HEADINGS (H/V/45/other - `other` is the only 45-degree-rule violation), VERTEX angles (135 chamfered / 90 square / <90 acid trap), and TEES (endpoint inside another same-net segment = a branch, not a bend). Derived: it measures geometry, KiCad DRC is still the authority.
- `board_zones` — What the board actually POURS: every zone with its net, layer, outline area, filled polygons and poured area.
- `board_add_zone` — Write ONE zone outline (polygon, optional holes) for a net/layer, then let KiCad's own ZONE_FILLER pour it. It validates (layer must be F.Cu/B.Cu, >=3 points, no duplicate points, non-zero area, net must exist on the board) and refuses something rather than guess; it does NOT invent/round/simplify the outline - the human draws the shape, the official engine pours the copper.
- `board_fill_zones` — Ask KiCad's own ZONE_FILLER to pour the zones, then report what it built (area, polygons, zones that poured no
- `board_rules` — Which net classes does this PROJECT declare, and what does a net resolve to? KiCad 7+ keeps net classes in the
- `board_rules_write` — Write net-class rules into the .kicad_pro: set_netclass (creates a class as a copy of Default, never invents o
- `board_cross_split` — Does any signal run over a break in its reference plane? REFUSES when the board carries no plane zones: the sp
- `board_loop_area` — Estimated signal/return loop area for one net (routed length x height to the nearest copper below).
- `board_antenna_keepout` — Measure what copper is really inside a proposed antenna keep-out rectangle on a board: every pad, track, via a
- `board_fidelity` — PROOF tool: does this server carry the WHOLE board? Reports node/byte counts, a token-identity round trip, the
- `board_view` — Read FACTS out of the converted board: nets, net classes, layers, stackup, setup, footprints, pads (exact size
- `board_drc` — AUTHORITATIVE design-rule check: runs KiCad's own DRC engine (kicad-cli) and returns its verdict - violations 
- `board_cross_check` — Check this server's DERIVED answers against the vendor's own verdict, on the same file, in both directions.
- `board_stats` — AUTHORITATIVE board statistics from KiCad's own calculator: size, area, copper area per side (so a poured zone
- `board_job` — Ask about a background job started by another tool (today: board_drc with background=true, which is how a larg
- `board_ops` — The operation catalogue of the write path: every op this server can apply to a KiCad board, its parameters, an
- `board_create` — Create a NEW empty KiCad board (stackup, design rules, closed outline) that KiCad itself accepts.
- `board_propose` — Plan a change to a KiCad board WITHOUT writing anything: returns a plan_id, the board digest before/after, per
- `board_apply` — Apply a proposal and prove it.
- `board_checkpoints` — List the checkpoints this server took before writing.
- `board_journal` — A tiny append-only progress journal under PCB_AGENT_HOME, so a session that dies mid-job can resume from ~1 Ki
- `board_restore` — Put a checkpoint back on disk.
- `board_progress` — Watch or stop a stepped apply.
- `board_ir_drop` — DC voltage drop across ONE net, built from the copper the board ACTUALLY carries: every track segment becomes 
- `board_vibration` — Natural frequencies of THIS board from its own outline and stackup, with every component sampled against the f
- `board_electrothermal` — Board-level 2-D electro-thermal: every conductor's I^2R feeds a plate solve, and the plate temperature feeds b
- `board_45_corners` — Measure the TURNS on a board, which this server never did before: clearance, edge and annular ring were all it
- `board_stitch_vias` — Put ground vias on the board - the one thing every via tool here could only COMPUTE (resistance, current, ther
- `board_pin_labels` — Write one silkscreen pin name per pin of a through-hole header, reading the name off the pad's NET (never a ha
- `board_fab_package` — Build a manufacturing package from a KiCad board: Gerbers (the board's own plot settings), Excellon drill (mm,
- `board_export_graphics` — Layer plot for humans: pdf / svg / dxf / ps / 3dpdf.
- `board_export_3d` — 3D solid export of a KiCad board: step / stpz / brep / glb / stl / ply / vrml / xao / u3d.
- `board_footprint_extents` — Real bounding boxes per footprint: courtyard (from F.CrtYd), pads, silk, copper graphics.
- `board_geometry_check` — Measure the copper instead of waiting for a DRC verdict: the closest pair of tracks, every pair that violates 
- `board_pad_obstacle_map` — Where can a pad legally go? Rasterises every track, via and pad grown by the clearance and returns the free ce
- `board_constraints_report` — List the numbers that will make the engine reject copper - min track width, clearances, via limits, per-netcla
- `board_fanout_order_check` — Decide whether a stack of 45 degree fan-out traces between two parallel header lines can avoid crossing AT ALL

## 原理图（11）

- `schematic_pcb_diff` — Compare a KiCad board against a schematic WITHOUT touching either: KiCad's own netlister answers the schematic
- `schematic_pcb_sync` — Turn schematic/board differences into board operations.
- `schematic_ops` — The operation catalogue of the SCHEMATIC write path (KiCad .kicad_sch): wires, buses, bus entries, junctions, 
- `schematic_propose` — Plan a change to a KiCad schematic WITHOUT writing anything: returns a plan_id, the document digest before/aft
- `schematic_apply` — Write a planned schematic change: checkpoint -> atomic write -> four gates - the bytes on disk are the planned
- `schematic_summary` — Read-only.
- `schematic_view` — Read-only.
- `schematic_hierarchy` — Read-only.
- `schematic_new_file` — Create an EMPTY .kicad_sch - a new page for a multi-page project.
- `schematic_review` — Grade a schematic / BOM against the missing-part catalogue: per-pin decoupling, bulk per rail, floating reset 
- `schematic_export` — Schematic output: bom (CSV, parsed into rows with a placement count), netlist (kicadsexpr / kicadxml / spice /

## 立创EDA 写入（13）

- `lceda_api_probe` — Speak to LCEDA Pro's API gateway rather than just looking at the port: opens a real RFC 6455 WebSocket to 127.
- `lceda_pro_status` — Is the LCEDA Pro IN-PROCESS bridge alive, and which build is it? Speaks the editor's own command-space pipe (n
- `lceda_pro_api` — Ask the running editor for its own API surface: the type list of a namespace, or the method list of one class,
- `lceda_pro_open` — Open a project INSIDE the running editor through its command space (not by starting a second exe) and return t
- `lceda_pro_sessions` — List the documents the running editor has open as drivable sessions (id, render id, state).
- `lceda_pro_close` — Close one session in the running editor (optionally destroy it).
- `lceda_pro_run` — Run a snippet INSIDE the running editor: the code is the body of an async function, gets the vendor's eda obje
- `lceda_pro_steps` — Run a design change inside the running editor ONE STEP AT A TIME, so it can be watched and interrupted.
- `lceda_pcb_write` — Perform ONE write inside the running LCEDA Pro editor and READ IT BACK.
- `lceda_pour` — Official copper pour in the running LCEDA Pro editor. A pour is TWO objects: `pcb_PrimitivePour` (border, writable) and `pcb_PrimitivePoured` (fill result, read-only). actions list/check/create/delete/save; no fill means `needs_editor_rebuild=true` + the exact Shift+B keystroke, because the API has no rebuild entry (98 namespaces scanned) and this server never draws copper itself.
- `lceda_write_snippet` — Build a save / move / create snippet for `lceda_pcb_write` out of the method table the running editor reports 
- `lceda_step_record` — Freeze a run of editor steps into a replayable script.
- `lceda_step_replay` — Replay a recorded step script inside the running editor and COMPARE each step's reply with the value that was 

## 立创专业版 inspect（1）

- `epro_inspect` — Inspect an EasyEDA Pro project WITHOUT interpreting it: the container (.epro ZIP or .eprj/.eprj2 SQLite projec

## 桌面应用（5）

- `app_detect` — Where is the EDA installed? Explicit path, else the running process image, else a drive scan, else the registr
- `app_status` — Is the EDA running, which instances exist, which project is loaded, and is the EasyEDA Pro API gateway listeni
- `app_launch` — Start the EDA detached (no project).
- `app_close` — Close the EDA by sending WM_CLOSE to its windows (same as clicking X, so the app may show a save dialog and st
- `app_open_project` — Open a .epro / .kicad_pro / .kicad_pcb in the right application, then CONFIRM by window title.

## 磁件（12）

- `magnetics_inductor` — Power-inductor sizing for CCM boost / buck / buck-boost from the load, the rails and the switching frequency: 
- `magnetics_dcm` — The DCM steady state - which is NOT CCM with a smaller ripple.
- `magnetics_core_loss` — Core loss for ONE period of a flux trajectory, with the coefficients YOU supply and the unit convention NAMED,
- `magnetics_material_core_loss` — Core loss density from the VENDOR's own printed fit, looked up by material name - the same Steinmetz/iGSE mach
- `magnetics_winding_ac` — Dowell's 1-D AC/DC resistance ratio F_R for a winding layer stack, so the DC resistance an ohmmeter reads beco
- `magnetics_fringing_loss` — Fringing-field loss: the AC copper loss caused by the field that leaks out of an air gap - the term Dowell's 1
- `magnetics_select_core` — Pick a REAL core for an (L, Ipk) requirement from data/magnetic_materials.json (vendor-published Ae / le / Ve 
- `magnetics_dc_bias` — How much of a powder core's initial permeability survives a DC bias - the number that decides whether the indu
- `magnetics_powder_curve` — The core's own CURVE, not a single number: the flux the printed magnetisation fit gives at a magnetising force
- `magnetics_core_loss_audit` — Score YOUR Steinmetz triple against the loss numbers the vendor actually prints.
- `magnetics_toroid_mlt` — Mean length of one turn on a toroid, and the DC copper loss that follows - the number a toroid's catalog does 
- `magnetics_data` — Read back the magnetic table: the materials (mu_i, Bsat at the temperatures the vendor published, Tc, density,

## 滤波器（7）

- `filter_prototype` — Lowpass prototype element values g1..gn for a Chebyshev or Butterworth response.
- `filter_lumped_bandpass` — Lumped LC bandpass filter by lowpass-to-bandpass transformation: L and C for every series and shunt branch, pl
- `filter_mc_yield` — Monte-Carlo tolerance yield of the lumped bandpass: every L and C gets its own multiplier from the stated tole
- `filter_coupled_microstrip` — Invert a symmetric coupled-microstrip pair: given the even/odd mode impedances, return w and s.
- `filter_coupled_resonator` — Edge-coupled half-wave-resonator bandpass in microstrip: turns the prototype g values into external Q, inter-r
- `filter_waveguide_cavity` — Rectangular waveguide cavity in ANY TE_mnp / TM_mnp mode: the resonant length for a given a and b, the unloade
- `filter_coupling_matrix` — Coupling matrix of a bandpass filter, evaluated as a real network: finite transmission zeros from a cross-coup

## 天线（7）

- `antenna_patch` — Rectangular microstrip patch: width, length (with the fringing correction dL), edge input resistance, inset fe
- `antenna_printed` — PCB printed quarter-wave antenna (IFA, meander, printed monopole) on a substrate: the resonant length comes ba
- `antenna_dipole_monopole` — Free-space thin-wire dipole or monopole: resonance length (0.475 lambda / 0.2375 lambda, the end effect includ
- `antenna_helix` — Axial-mode (end-fire) helix by Kraus' relations: gain in dBic, half-power beam width and approximate input imp
- `antenna_small_loop` — Electrically small loop: radiation resistance (exact below 0.1 lambda circumference), and a refusal above that
- `antenna_q_limit` — Chu/Harrington bandwidth ceiling for an antenna that must fit in a given box.
- `antenna_chip` — Ceramic chip antenna: the keep-out and matching checklist plus the near-field boundary, and an explicit refusa

## 匹配（7）

- `match_reflection` — The four ways of saying the same mismatch, each with its definition: reflection coefficient, VSWR, return loss
- `match_l` — Two-element L match, solved exactly (not a Q approximation).
- `match_pi` — Pi network (shunt-series-shunt, low-pass form) between two real terminations at a requested Q.
- `match_t` — T network (series-shunt-series, high-pass form), the dual of the pi network, at a requested Q.
- `match_stub` — Single-stub tuner: how far from the load the stub goes and how long it is, by closed form (both physical solut
- `match_quarter_wave` — Quarter-wave transformer: section impedance sqrt(Z0*R), length lambda_g/4, and the real limits.
- `match_bandwidth` — Bandwidth of a single-resonant match from its loaded Q: BW = f0/Q, and the VSWR <= S band scaled by (S-1)/sqrt

## 链路（5）

- `link_friis` — Link budget term by term: received power in free space with transmitter power, both gains, both feed losses an
- `link_max_range` — Free-space distance at which the received power falls to the sensitivity, with an optional fade margin.
- `link_aperture` — Effective aperture of an antenna of given gain: Ae = G*lambda^2/(4*pi).
- `link_far_field` — Where the far field starts for an antenna of a given largest dimension, by three criteria reported side by sid
- `link_fresnel` — First Fresnel zone radius at a point between two antennas, and the 60 % clearance a line-of-sight link needs.

## 馈线（2）

- `feedline_impedance` — Z0, effective permittivity, guided wavelength and loss of a microstrip, stripline, CPW or grounded CPW line.
- `feedline_width` — The trace width that gives a target impedance, for microstrip / stripline / CPW / GCPW and the edge-coupled pa

## PDN（5）

- `pdn_impedance` — |Z(f)| of a capacitor bank as seen by the die: parallel series-RLC capacitors plus optional plane capacitance.
- `pdn_step_response` — Rail droop in the TIME domain for a load step: how far the rail goes, when, and how long until it is back - on
- `pdn_plane_modes` — Peak |Z| at each parallel-plate (cavity) mode of a plane pair, from the single mode that rings: |Z_peak| = w*m
- `pdn_plane_impedance` — The plane impedance CURVE, not just its peaks: Z(f) = j*w*mu0*t * sum_mn A_mn/(k_mn^2 - k^2(1 - j/Q)) between 
- `pdn_plane_with_caps` — The plane impedance curve WITH the decoupling caps mounted on it: the cavity is an (N+1)-port network and ever

## 去耦（1）

- `decoupling_check` — Does this cap bank hold the rail inside a ripple target over a band? Needs the current, the band and the rippl

## 热（7）

- `thermal_stack` — 1-D thermal resistance through a stack of layers: Rth = sum(t/(k*A)), plus the temperature rise for a stated p
- `thermal_via_array` — Thermal resistance through a field of plated vias - the thermal twin of via_resistance, same annulus geometry.
- `thermal_plate` — 2-D steady-state temperature of a plate with point heat sources: k*t*lap(T) - 2h(T-Tamb) + q'' = 0 solved by S
- `thermal_transient` — Step response of a thermal RC network (Foster form): T(t) = Tamb + P*sum R_i(1 - exp(-t/tau_i)).
- `thermal_heatsink` — What Rth(junction-to-air) do I need? Solves the thermal budget and returns the target with a safety margin.
- `thermal_spreading` — Constriction (spreading) resistance of a rectangular heat source on a rectangular plate: how much resistance t
- `thermal_convection` — Convection coefficient from airflow or surface size, so it does not have to be invented.

## 振动（8）

- `vibration_plate_modes` — Natural frequencies and mode shapes of a rectangular thin plate (Kirchhoff): Rayleigh-Ritz on a monomial basis
- `vibration_plate_outline` — Natural frequencies of a thin plate whose outline is an ARBITRARY polygon - the real board edge instead of its
- `vibration_plate_stress` — Surface stress in a vibrating plate, with a MEASURED error bar.
- `vibration_random_response` — Random vibration: the g_rms a board sees under a PSD, and the displacement that follows.
- `vibration_fatigue` — Steinberg three-band fatigue plus Miner's rule: split the 3-sigma deflection into 3s/2s/1s bands at the textbo
- `vibration_shock` — Single-DOF shock response to a base acceleration pulse (step / rect / half_sine), integrated with Newmark beta
- `vibration_isolator` — Single-DOF transmissibility of a mount: |T| at the frequencies you ask for, the peak transmissibility and wher
- `vibration_shock_spectrum` — Shock response spectrum: sweep single-DOF oscillators across frequency and record the peak absolute accelerati

## 焊点（1）

- `solder_joint_fatigue` — Thermal-cycling life of a solder joint from the CTE mismatch.

## 跌落（1）

- `drop_board_strain` — Board surface strain under a drop or shock pulse, built from three already-solved pieces: the plate's first mo

## 腔体（3）

- `cavity_resonance` — Parallel-plate (cavity) modes of a plane pair: f_mn = c/(2 sqrt(er)) * sqrt((m/a)^2+(n/b)^2) (TM_mn0, perfect 
- `cavity_via_fence` — Via-fence pitch expressed as a fraction of the guided wavelength: pitch/lambda_g, phase per pitch, and the max
- `cavity_plane_q` — Damping of a plane-pair mode: copper Q, dielectric Q and their series sum.

## 粗糙度（1）

- `roughness_huray` — Huray "snowball" surface roughness: Rs multiplier from a nodular copper surface, instead of from one RMS heigh

## 平面（2）

- `plane_radiation_q` — Radiation Q of a plane-pair mode radiating from its OPEN edges - the loss cavity_plane_q says it does not mode
- `plane_open_end` — How far a BARE edge pulls a plane-pair resonance down: the fringing field at an open edge makes the plane elec

## 电热（2）

- `electrothermal_trace` — Self-consistent temperature of a straight DC trace: the copper gets hotter, the copper gets more resistive, so
- `electrothermal_conductor` — The same electro-thermal loop with the cold resistance supplied directly, so it applies to anything whose R20 

## 层叠（2）

- `stackup_material` — Which laminate covers this frequency, and what the table cannot see.
- `stackup_design` — A 4 or 6 layer stack for an RF / mixed-signal board: layer roles, copper weights, the dielectric split, the wi

## 射频（1）

- `rf_chain` — Cascade a receive/transmit chain: total gain, Friis noise figure with each stage's share of the excess noise, 

## 规则目录（1）

- `design_rules` — The review catalogues: every layout and schematic rule with its id, the fact it needs, the unit, how to measur

## 布局评审（1）

- `layout_review` — Grade this board's layout against the rule catalogue: reference-plane continuity, decoupling distance and laye

## 器件库（6）

- `part_library_status` — Find the LCEDA Pro standard part library on this machine and count what is in it.
- `part_search` — Search real parts by name, LCSC code or manufacturer part number and get back the device uuid, LCSC code, pack
- `part_get` — One device by uuid, with every attribute row the vendor catalogue carries (Manufacturer Part, Supplier Part, S
- `part_export` — Turn a catalogue device into KiCad library files (.kicad_sym and/or .kicad_mod) in the directory you name, and
- `part_footprint_pads` — The pad table of a catalogue footprint in mm (number, x, y, size, shape, rotation, drill, polygon) - the geome
- `part_cache_info` — Where part answers are cached on disk and for how long.

## 经验库（9）

- `experience_recall` — Read what this server has learned from boards it did not draw.
- `experience_check` — Check a board against the active library.
- `experience_candidate` — Write an observation down as a CANDIDATE.
- `experience_promote` — Turn a candidate into an active entry.
- `experience_outcome` — Record that an active entry did or did not hold on a real board.
- `experience_export` — The library as one self-contained JSON blob: what a person reads, what another agent imports, and what goes in
- `experience_import` — Merge another library in.
- `experience_harvest` — Read ONE board and write down what it says, as candidates (single-board metrics only; a hard rule is refused).
- `experience_status` — Is the library alive: active count, candidates nobody confirmed, file mtime.

## 布局质量与拓扑（2）

- `board_layout_metrics` — Continuous layout readings (spacing rhythm, orientation, alignment, edge margin, density, cluster separation) with percentiles against a reference set.
- `board_topology` — Functional groups, the nets between them, and the order power flows; the partition step before coordinates.

## 方案与布局库（7）

- `pattern_recall` — Read recipes (parameters + steps + a reading) and layouts (mm offsets from one anchor).
- `pattern_add` — Write a recipe or layout as a CANDIDATE; a parameter without a source is refused.
- `pattern_verify` — candidate -> verified; needs confirm=true, a reason, and at least one {from, reading}.
- `pattern_layout_plan` — Absolute coordinates for one board from a verified layout, plus what would collide; writes nothing.
- `pattern_layout_from_board` — Read a cluster off a real board into a layout payload (candidate).
- `pattern_use` — Record worked/failed after applying an artefact.
- `pattern_status` — Counts, and which verified entries have never been applied.

## DFM（2）

- `dfm_capability` — Validate a process capability document on its own, before any board is opened: which known rules it states, wh
- `dfm_check` — Compare THIS board against a supplied fab capability document: narrowest track, copper-to-copper gap, via dril

## 仿真（1）

- `sim_spice` — Run a real circuit simulation on a SPICE netlist: DC operating point (.op), transient (.tran), small-signal AC

## 自述（1）

- `calc_capabilities` — Self-description: what this server can compute, what it refuses to compute and why, and what is not implemente

## 元工具（2）

- `tool_search` — Find a tool that is not in your advertised tool list.
- `tool_call` — Run a tool returned by tool_search.

## 其余（29）

- `unit_convert` — Convert a length between PCB units. EasyEDA Pro stores numbers in mil (measured on 194 real archives - the uni
- `width_for_current` — Minimum trace width for a given current and allowed temperature rise. Requires the standard explicitly. Note: 
- `current_capacity` — Current a trace of known width can carry for a given allowed temperature rise. Inverse of width_for_current. I
- `via_current_allowed` — Current a via (or a group of parallel vias) can carry. Engineering extrapolation: barrel sidewall area fed to 
- `fusing_current` — Current that melts a trace in t_s seconds (Onderdonk 1928, adiabatic), and optionally how far that is from the
- `fusing_current_taper` — Fusing current, or time to melt, for a trace whose WIDTH CHANGES along it - a fuse neck, a via-fanout bottlene
- `repetitive_pulse_rating` — Largest current a trace can carry as a REPEATING pulse train (one that never melts), and - if you also pass cu
- `trace_impedance` — Forward solve: given geometry, what is the characteristic impedance. Closed-form IPC-2141A approximation, not 
- `width_for_impedance` — Reverse solve: given a target impedance and stackup, what width is required. This is the direction a designer 
- `lambda_over_20` — Maximum spacing for stitching / shielding vias around a ground fence, and the basis for guard-ring via pitch.
- `creepage_required` — Required creepage distance (shortest path ALONG the insulating surface), IEC 60664-1:2020-05 Table F.5. No EDA
- `clearance_required` — Required electrical clearance (shortest distance THROUGH air), IEC 60664-1 Tables F.2/F.8 corrected by the Tab
- `spacing_required` — Both distances at once, the way IEC 60664-1 actually applies them: creepage (Table F.5) and clearance (Tables 
- `safety_table_check` — Can the safety-distance / current-capacity tables be trusted? Runs the rules the data has to satisfy and retur
- `diff_impedance` — Differential impedance of an edge-coupled pair (microstrip or stripline) plus the single-ended value and the c
- `diff_width` — Reverse solve: the trace width that gives a target differential impedance at a fixed spacing. Reports the reac
- `diff_impedance_field` — Differential impedance from an actual 2-D field solution instead of a closed form. Slower (seconds) and answer
- `coplanar_field` — Coplanar (cpw / gcpw) impedance from an actual 2-D field solution, drawn on a full domain with no symmetry pla
- `coplanar_pair_field` — Edge-coupled coplanar pair (differential CPW / GCPW) from the 2-D field solver: Zdiff = 2*Z_odd plus the per-l
- `crosstalk_coupled_lines` — How much of an edge lands on the trace next to it: near-end (NEXT) and far-end (FEXT) crosstalk between two pa
- `trace_resistance` — DC resistance of a copper trace from its geometry: R = rho(T)*L/(w*t). Use it for the quiet half of the questi
- `via_resistance` — DC resistance of plated via barrels: the cross-section is the copper ANNULUS, not the drill circle. Answers 'h
- `trace_inductance` — Self-inductance of a straight trace (Grover ribbon formula). Order of magnitude +-20%: it ranks two layouts, i
- `ir_drop_chain` — Series IR drop along an ordered chain of copper segments - the case that is usually got wrong by averaging the
- `current_density_field` — 2-D steady-state current density inside copper: solves div(sigma*grad V) = 0 on the region you draw, so the nu
- `ac_sheet_resistance` — Skin effect in a flat conductor: R_ac/R_dc, the skin depth, the effective thickness and the J(z) profile, from
- `ac_round_conductor` — Skin effect in a ROUND conductor - a solid wire, a tube, or a plated via barrel - from the exact 2-D axisymmet
- `ac_self_heating` — Temperature of a conductor carrying AC current, solved self-consistently - the loop electrothermal_trace and e
- `fullwave_solver` — Is a full-wave electromagnetic solver installed on this machine, which version, and what does the handoff look

