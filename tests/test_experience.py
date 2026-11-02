"""The experience library: two chains, one red line, and every metric's failure side.

The design is docs/02 section B, the seed is measured on 20 real LCEDA-Pro boards
(docs/94). What this file holds to:

* **hard and soft do not share a verdict word** - a hard entry that does not hold
  is `violated` (a finding), a soft one is `outside_habit` (a question);
* **a metric that cannot run is `not_checkable_here`**, never a pass, because a
  check that could not run must not look like one that succeeded;
* **a candidate never becomes active on its own**, and no door that writes will
  take a seed id - otherwise a candidate could shadow an active shipped rule and
  switch it off with nobody confirming anything;
* **every guard has a control that fails**: the bad board here violates every
  hard rule and leaves every habit, and the float trap below is the one that
  actually bit during the corpus survey.
"""
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from pcb_agent_mcp import experience as X
from pcb_agent_mcp import tools
from pcb_agent_mcp.adapters.canonical import Board


def _track(x1, y1, x2, y2, net="SIG", width=0.25, layer="F.Cu"):
    return {"net": net, "layer": layer, "width": width,
            "start": (x1, y1), "end": (x2, y2)}


def _via(x, y, dia=0.6, drill=0.3, net="SIG"):
    return {"net": net, "x": x, "y": y, "drill": drill, "diameter": dia,
            "from": "F.Cu", "to": "B.Cu", "kind": "through"}


def _pad(number, x, y, layers=("*.Cu",), hole="round", size=(1.6, 1.6)):
    pad = {"number": number, "x": x, "y": y, "layers": list(layers),
           "layers_as_written": ["MULTI"] if list(layers) == ["*.Cu"] else ["TOP"],
           "shape": "circle", "size_mm": list(size), "net": "GND"}
    if hole:
        pad["hole_kind"] = hole
        pad["hole_mm"] = [0.8, 0.8]
    return pad


def good_board():
    """A board that holds every active entry: on the 45 grid, one via geometry
    with room around the drill, power wider than signal, a small width palette,
    drilled pads on every copper layer, nothing claimed about pad placement."""
    b = Board("test", "memory")
    b.stackup = [{"name": "F.Cu", "type": "copper"},
                 {"name": "B.Cu", "type": "copper"}]
    b.tracks = [
        _track(0, 0, 10, 0, net="SIG", width=0.25),
        _track(10, 0, 20, 10, net="SIG", width=0.25),
        _track(20, 10, 20, 20, net="SIG", width=0.25),
        _track(20, 20, 10, 10, net="SIG", width=0.25),
        _track(0, 5, 10, 5, net="VCC", width=0.8),
        _track(10, 5, 20, 5, net="VCC", width=0.8),
    ]
    b.vias = [_via(5, 5), _via(15, 5)]
    b.footprints = [{"ref": "J1", "x": 0, "y": 0, "layer": "TOP",
                     "pads": [_pad("1", 0, 0), _pad("2", 10, 0)]}]
    return b


def bad_board():
    """The control: every hard rule broken, every habit left."""
    b = Board("test", "memory")
    b.stackup = [{"name": "F.Cu", "type": "copper"},
                 {"name": "B.Cu", "type": "copper"}]
    # one segment off the grid is 100 % of the board's segments
    b.tracks = [_track(0, 0, 10, 1.76, net="SIG", width=0.25)]      # ~10 deg
    widths = [0.1 + i * 0.01 for i in range(20)]
    b.tracks += [_track(0, 10 + i, 5, 10 + i, net="SIG%d" % i, width=w)
                 for i, w in enumerate(widths)]
    b.tracks += [_track(0, 40, 5, 40, net="VCC", width=0.1)]         # power NARROWER
    b.vias = [_via(5, 5, dia=0.6, drill=0.45),                       # ring 0.075
              _via(6, 5, dia=0.9, drill=0.4)]                        # second geometry
    b.footprints = [{"ref": "J2", "x": 0, "y": 0, "layer": "TOP",
                     "pads": [_pad("1", 0, 0, layers=("F.Cu",))]}]
    b.source["pad_transform"] = {"verified_by_app": False}
    return b


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.home = tempfile.mkdtemp(prefix="exphome_")
        cls.old_home = os.environ.get("PCB_AGENT_HOME")
        cls.old_switch = os.environ.get("PCB_AGENT_EXPERIENCE")
        os.environ["PCB_AGENT_HOME"] = cls.home
        os.environ.pop("PCB_AGENT_EXPERIENCE", None)

    @classmethod
    def tearDownClass(cls):
        if cls.old_home is None:
            os.environ.pop("PCB_AGENT_HOME", None)
        else:
            os.environ["PCB_AGENT_HOME"] = cls.old_home
        if cls.old_switch is None:
            os.environ.pop("PCB_AGENT_EXPERIENCE", None)
        else:
            os.environ["PCB_AGENT_EXPERIENCE"] = cls.old_switch
        shutil.rmtree(cls.home, ignore_errors=True)

    def setUp(self):
        for name in (X.FILENAME, X.FILENAME + ".tmp"):
            path = os.path.join(self.home, name)
            if os.path.exists(path):
                os.unlink(path)


class TheSeed(Base):
    def test_the_seed_ships_and_every_entry_is_measurable(self):
        lib = X.library()
        ids = [e["id"] for e in lib["entries"]]
        self.assertEqual(len(ids), 7, ids)
        self.assertEqual(len(set(ids)), len(ids))
        for entry in lib["entries"]:
            self.assertIn(entry["kind"], X.KINDS)
            self.assertEqual(entry["status"], "active")
            self.assertIn(entry["trigger"]["board_metric"], X.METRICS)
            self.assertTrue(entry["claim"].strip())
            self.assertTrue(entry["action"].strip())
            self.assertTrue(entry["owner"].strip(),
                            "an entry with no owner is a rumour: %s" % entry["id"])
            self.assertTrue(entry["evidence"]["sources"],
                            "no source, no evidence: %s" % entry["id"])
        counts = lib["counts"]
        self.assertEqual(counts["status_active"], 7)
        self.assertEqual(counts["kind_hard"] + counts["kind_soft"], 7)
        self.assertEqual(counts["kind_hard"], 3)

    def test_every_metric_has_an_entry_that_uses_it(self):
        used = set(e["trigger"]["board_metric"] for e in X.library()["entries"])
        self.assertEqual(used, set(X.METRICS),
                         "a metric nothing measures is dead code, and a metric "
                         "with no seed entry never had its numbers checked")

    def test_the_seed_is_read_only(self):
        seed_id = X.library()["entries"][0]["id"]
        with self.assertRaises(ValueError):
            X.candidate({"id": seed_id, "kind": "soft", "claim": "x",
                         "trigger": {"board_metric": "distinct_track_widths"}})
        with self.assertRaises(ValueError):
            X.promote(seed_id, confirm=True, reason="x")
        with self.assertRaises(KeyError):
            X.outcome(seed_id, hit=True)

    def test_a_hand_edited_shadow_is_named_out_loud(self):
        seed = X.library()["entries"][0]
        blob = {"schema": X.SCHEMA, "entries": [dict(seed, status="candidate")]}
        with open(os.path.join(self.home, X.FILENAME), "w", encoding="utf-8") as fh:
            json.dump(blob, fh)
        lib = X.library()
        self.assertEqual(lib["shadowed_seed_ids"], [seed["id"]])
        # and the shadow really does win - which is why every write door refuses
        # a seed id: this state is only reachable by hand.
        row = [e for e in lib["entries"] if e["id"] == seed["id"]][0]
        self.assertEqual(row["status"], "candidate")


class TheVerdicts(Base):
    def test_a_board_that_holds_everything_passes_everything(self):
        got = X.report(good_board())
        self.assertEqual(got["hard_violations"], [])
        self.assertEqual(got["outside_habit"], [])
        self.assertEqual(got["unverified"], [])
        self.assertEqual(got["not_checkable_here"], [])
        self.assertEqual(got["checked"], 7)

    def test_the_control_board_breaks_every_hard_rule_and_leaves_every_habit(self):
        got = X.report(bad_board())
        self.assertEqual(sorted(got["hard_violations"]),
                         ["exp.seed.lceda-through-hole-spans",
                          "exp.seed.via-annular-ring"],
                         "a hard rule that cannot fail on a board built to break it "
                         "is not a check")
        self.assertEqual(sorted(got["outside_habit"]),
                         ["exp.seed.power-wider-than-signal",
                          "exp.seed.track-angle-on-45",
                          "exp.seed.track-width-palette",
                          "exp.seed.via-palette-single"])
        self.assertEqual(got["unverified"],
                         ["exp.seed.lceda-pad-placement-unverified"])

    def test_soft_never_borrows_the_word_a_defect_uses(self):
        got = X.report(bad_board())
        soft_ids = set(e["id"] for e in X.library()["entries"]
                       if e["kind"] == "soft")
        for row in got["rows"]:
            if row["id"] in soft_ids:
                self.assertIn(row["verdict"], ("passes", "outside_habit",
                                               "not_checkable_here"),
                              "%s is soft and said %r" % (row["id"], row["verdict"]))

    def test_a_metric_that_cannot_run_is_not_a_pass(self):
        """No tracks, no vias, no pads: three metrics have nothing to measure.

        The wrong answer here is `passes`, and it is the wrong answer that a
        counter-based library produces by default."""
        empty = Board("test", "memory")
        got = X.report(empty)
        rows = {r["id"]: r["verdict"] for r in got["rows"]}
        self.assertEqual(rows["exp.seed.track-angle-on-45"], "not_checkable_here")
        self.assertEqual(rows["exp.seed.via-annular-ring"], "not_checkable_here")
        self.assertEqual(rows["exp.seed.power-wider-than-signal"],
                         "not_checkable_here")
        self.assertEqual(rows["exp.seed.lceda-through-hole-spans"],
                         "not_checkable_here")
        self.assertEqual(sorted(got["not_checkable_here"]),
                         ["exp.seed.lceda-through-hole-spans",
                          "exp.seed.power-wider-than-signal",
                          "exp.seed.track-angle-on-45",
                          "exp.seed.track-width-palette",
                          "exp.seed.via-annular-ring",
                          "exp.seed.via-palette-single"])
        self.assertEqual(got["hard_violations"], [])

    def test_a_ring_exactly_on_the_floor_is_not_below_it(self):
        """The float trap that the corpus survey hit for real: (0.5 - 0.3)/2 is
        0.09999999999999998 in binary, and comparing that against 0.1 reported
        1497 vias on one real board as too thin. One micron of slack is the fix,
        and it must not let a genuinely thin ring through."""
        b = good_board()
        b.vias = [_via(5, 5, dia=0.5, drill=0.3)]
        row = [r for r in X.report(b)["rows"]
               if r["id"] == "exp.seed.via-annular-ring"][0]
        self.assertEqual(row["verdict"], "passes", row["observed"])
        b.vias = [_via(5, 5, dia=0.5, drill=0.3002)]      # ring 0.0999
        row = [r for r in X.report(b)["rows"]
               if r["id"] == "exp.seed.via-annular-ring"][0]
        self.assertEqual(row["verdict"], "violated", row["observed"])

    def test_the_same_via_written_in_two_units_is_one_geometry(self):
        """0.6096 mm and 0.61 mm are the same 24 mil via; five real boards write
        both, and counting them as two geometries called 5 good boards bad."""
        b = good_board()
        b.vias = [_via(5, 5, dia=0.6096, drill=0.3048),
                  _via(6, 5, dia=0.61, drill=0.305)]
        row = [r for r in X.report(b)["rows"]
               if r["id"] == "exp.seed.via-palette-single"][0]
        self.assertEqual(row["observed"]["distinct"], 1)
        self.assertEqual(row["verdict"], "passes")
        b.vias.append(_via(7, 5, dia=0.9, drill=0.4))
        row = [r for r in X.report(b)["rows"]
               if r["id"] == "exp.seed.via-palette-single"][0]
        self.assertEqual(row["verdict"], "outside_habit")

    def test_report_writes_nothing(self):
        X.report(bad_board())
        self.assertFalse(os.path.exists(os.path.join(self.home, X.FILENAME)),
                         "experience_check is a reading; it must not leave a file")

    def test_scope_filters_the_report(self):
        got = X.report(good_board(), scope="format.lceda")
        self.assertEqual(got["checked"], 2)


class TheLoop(Base):
    NEW = {"id": "exp.local.t", "kind": "hard", "scope": "fab",
           "claim": "no via ring under 0.2 mm on this board",
           "action": "ask before shrinking the ring",
           "trigger": {"board_metric": "via_annular_ring_below", "min_mm": 0.2}}

    def test_a_candidate_is_not_recalled_and_not_applied(self):
        X.candidate(dict(self.NEW))
        self.assertEqual(X.recall()["matched"], 7,
                         "a candidate must not appear in the active library")
        self.assertEqual(X.recall(status="candidate")["matched"], 1)
        self.assertEqual(X.recall(status=None)["matched"], 8)
        got = X.report(good_board())
        self.assertNotIn("exp.local.t", [r["id"] for r in got["rows"]])

    def test_promotion_needs_a_person(self):
        X.candidate(dict(self.NEW))
        with self.assertRaises(PermissionError):
            X.promote("exp.local.t")
        with self.assertRaises(PermissionError):
            X.promote("exp.local.t", confirm="yes", reason="truthy is not true")
        self.assertEqual(X.recall()["matched"], 7)
        got = X.promote("exp.local.t", confirm=True, reason="checked by hand")
        self.assertEqual(got["status"], "active")
        self.assertEqual(got["promoted_by"], "checked by hand")
        self.assertEqual(X.recall()["matched"], 8)
        # and now it judges boards, at the threshold its author chose
        row = [r for r in X.report(good_board())["rows"]
               if r["id"] == "exp.local.t"][0]
        # the good board's vias have a 0.15 mm ring: above the 0.10 floor the
        # seed checks, below the 0.2 the author of this entry asked for
        self.assertEqual(row["verdict"], "violated")
        self.assertEqual(row["observed"]["below"], 2)

    def test_promotion_refuses_a_second_time(self):
        X.candidate(dict(self.NEW))
        X.promote("exp.local.t", confirm=True, reason="first")
        with self.assertRaises(ValueError):
            X.promote("exp.local.t", confirm=True, reason="again")

    def test_outcome_counts_and_does_not_change_status(self):
        X.candidate(dict(self.NEW))
        X.promote("exp.local.t", confirm=True, reason="hand")
        got = X.outcome("exp.local.t", hit=False, note="board-a")
        self.assertEqual(got["evidence"]["violated"], 1)
        self.assertEqual(got["evidence"]["sources"], ["board-a"])
        self.assertEqual(got["status"], "active",
                         "an outcome is a counter, not a demotion")
        got = X.outcome("exp.local.t", hit=True, note="board-b")
        self.assertEqual(got["evidence"]["hits"], 1)
        self.assertEqual(got["evidence"]["violated"], 1)

    def test_retire_takes_it_out_of_the_active_library(self):
        X.candidate(dict(self.NEW))
        X.promote("exp.local.t", confirm=True, reason="hand")
        X.retire("exp.local.t", reason="the fab moved to 0.10")
        self.assertEqual(X.recall()["matched"], 7)
        self.assertEqual(X.recall(status="retired")["matched"], 1)

    def test_an_entry_that_cannot_be_checked_is_refused_at_the_door(self):
        for bad in (
            dict(self.NEW, id=""),
            dict(self.NEW, id="exp.local.a", kind="guess"),
            dict(self.NEW, id="exp.local.b", claim="  "),
            dict(self.NEW, id="exp.local.c", trigger={}),
            dict(self.NEW, id="exp.local.d",
                 trigger={"board_metric": "no_such_metric"}),
        ):
            with self.assertRaises(ValueError, msg=repr(bad.get("id"))):
                X.candidate(bad)
        self.assertFalse(os.path.exists(os.path.join(self.home, X.FILENAME)))


class ImportIsNotAPromotion(Base):
    def test_an_imported_active_entry_lands_as_a_candidate(self):
        blob = {"schema": X.SCHEMA, "entries": [
            {"id": "exp.imported.a", "kind": "soft", "status": "active",
             "claim": "boards keep their vias under 0.8 mm",
             "action": "ask why", "trigger": {"board_metric": "distinct_via_geometries",
                                              "max_distinct": 2}},
            {"id": "exp.imported.b", "kind": "hard", "claim": "broken",
             "trigger": {"board_metric": "no_such_metric"}},
        ]}
        got = X.import_entries(blob)
        self.assertEqual(got["added"], ["exp.imported.a"])
        self.assertEqual(got["demoted_to_candidate"], ["exp.imported.a"])
        self.assertEqual([s["id"] for s in got["skipped"]], ["exp.imported.b"])
        self.assertEqual(X.recall()["matched"], 7,
                         "an import cannot make a rule; only a person can")

    def test_an_import_cannot_shadow_a_seed_entry(self):
        seed = X.library()["entries"][0]
        got = X.import_entries({"entries": [dict(seed, claim="hijacked")]})
        self.assertEqual(got["added"], [])
        self.assertEqual([s["id"] for s in got["skipped"]], [seed["id"]])


class TheSwitch(Base):
    def test_off_is_a_named_refusal_not_an_empty_list(self):
        os.environ["PCB_AGENT_EXPERIENCE"] = "0"
        try:
            self.assertFalse(X.enabled())
            got = tools.TOOLS["experience_recall"]["handler"]({})
            self.assertEqual(got["status"], "error")
            self.assertEqual(got["reason_code"], "DATA_UNAVAILABLE")
            got = tools.TOOLS["experience_check"]["handler"]({"path": __file__})
            self.assertEqual(got["reason_code"], "DATA_UNAVAILABLE")
        finally:
            os.environ.pop("PCB_AGENT_EXPERIENCE", None)
        self.assertTrue(X.enabled())


class TheTools(Base):
    def test_recall_and_check_speak_the_envelope(self):
        got = tools.TOOLS["experience_recall"]["handler"]({"kind": "hard"})
        self.assertEqual(got["status"], "ok")
        self.assertEqual(got["value"]["matched"], 3)
        self.assertTrue(got["basis"])

    def test_promote_through_the_tool_refuses_without_confirm(self):
        tools.TOOLS["experience_candidate"]["handler"]({
            "id": "exp.local.z", "kind": "soft", "claim": "x",
            "metric": "distinct_track_widths"})
        got = tools.TOOLS["experience_promote"]["handler"](
            {"id": "exp.local.z", "confirm": False, "reason": "x"})
        self.assertEqual(got["status"], "error")
        self.assertEqual(got["reason_code"], "ARG_INVALID")
        got = tools.TOOLS["experience_promote"]["handler"](
            {"id": "exp.local.z", "confirm": True, "reason": "hand"})
        self.assertEqual(got["status"], "ok")

    def test_import_takes_a_file_or_a_blob(self):
        path = os.path.join(self.home, "blob.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"entries": [
                {"id": "exp.file.a", "kind": "soft", "claim": "x",
                 "trigger": {"board_metric": "distinct_track_widths"}}]}, fh)
        got = tools.TOOLS["experience_import"]["handler"]({"path": path})
        self.assertEqual(got["status"], "ok", got)
        self.assertEqual(got["value"]["added"], ["exp.file.a"])


if __name__ == "__main__":
    unittest.main()
