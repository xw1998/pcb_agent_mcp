"""Where the shipped tables are found, and the two ways that could lie.

Eleven JSON tables travel with this server and they live OUTSIDE the package, in
`<repo>/data/`, because every document in `docs/` names them by that path and a
table you can open in an editor is a table you can check against the page it was
transcribed from. That placement means "where is the table" depends on how the
server was installed - a git clone, an editable install, a wheel, or an operator
pointing `PCB_AGENT_DATA` somewhere else.

So every claim here has a control that must fail:

* a table listed in TABLES must exist in the checkout (a table added to `data/`
  without being listed is a table `find()` will not look for);
* with only the wheel's share-directory present, `find()` must still locate it -
  otherwise `pip install` ships a server whose every table is missing;
* a name nothing carries must come back as `None` from `find()` and as a raised
  `MissingTable` from `require()`, never as an empty string or an empty table:
  "no file" and "the file says nothing" are different answers, and the
  experience seed used to confuse them.
"""
import json
import os
import sys
import tempfile
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "src"))

from pcb_agent_mcp import datafiles as DF          # noqa: E402
from pcb_agent_mcp import experience as X          # noqa: E402


class TestTheListMatchesTheDirectory(unittest.TestCase):
    def test_every_json_in_data_is_listed_and_every_listed_one_exists(self):
        folder = DF.repo_data_dir()
        self.assertTrue(os.path.isdir(folder), folder)
        on_disk = sorted(n for n in os.listdir(folder) if n.endswith(".json"))
        self.assertEqual(sorted(DF.TABLES), on_disk,
                         "a table was added to data/ without being listed in "
                         "datafiles.TABLES, or a listed table was deleted")

    def test_the_checkout_finds_every_table(self):
        rep = DF.report()
        self.assertEqual(rep["missing"], [], rep["searched"])
        self.assertEqual(rep["found"], len(DF.TABLES))
        self.assertEqual(rep["dirs_used"], [DF.repo_data_dir()])


class TestInstallLayouts(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pcb_datafiles_")
        self._env = os.environ.pop("PCB_AGENT_DATA", None)
        self._prefix = sys.prefix

    def tearDown(self):
        os.environ.pop("PCB_AGENT_DATA", None)
        if self._env is not None:
            os.environ["PCB_AGENT_DATA"] = self._env
        sys.prefix = self._prefix
        for root, dirs, files in os.walk(self.tmp, topdown=False):
            for name in files:
                os.unlink(os.path.join(root, name))
            for name in dirs:
                os.rmdir(os.path.join(root, name))
        os.rmdir(self.tmp)

    def _share_copy(self, name):
        folder = os.path.join(self.tmp, "share", "pcb_agent_mcp", "data")
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, name)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"synthetic": True}, fh)
        return path

    def test_the_wheel_share_directory_is_searched(self):
        """A wheel installs data/ under <prefix>/share/pcb_agent_mcp/data."""
        # A name no checkout carries, so only the share copy can answer it.
        path = self._share_copy("__only_in_share__.json")
        sys.prefix = self.tmp
        self.assertEqual(DF.find("__only_in_share__.json"), path)

    def test_the_override_wins_over_the_checkout(self):
        """PCB_AGENT_DATA must beat a stale copy in the checkout."""
        folder = os.path.join(self.tmp, "corrected")
        os.makedirs(folder)
        path = os.path.join(folder, "creepage_tables.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"synthetic": True}, fh)
        os.environ["PCB_AGENT_DATA"] = folder
        self.assertEqual(DF.find("creepage_tables.json"), path)

    def test_a_missing_table_is_none_not_a_path_to_nothing(self):
        self.assertIsNone(DF.find("__no_such_table__.json"))
        with self.assertRaises(DF.MissingTable) as caught:
            DF.require("__no_such_table__.json")
        message = str(caught.exception)
        self.assertIn("__no_such_table__.json", message)
        for folder in DF.candidates():
            self.assertIn(folder, message,
                          "the error must name every place that was searched")


class TestTheSeedMissIsNotAnEmptyLibrary(unittest.TestCase):
    def setUp(self):
        self._seed = X.SEED

    def tearDown(self):
        X.SEED = self._seed

    def test_a_healthy_seed_adds_no_keys(self):
        """A found seed answers exactly what it answered before this change."""
        self.assertNotIn("seed_found", X.library())
        self.assertNotIn("seed_found", X.status())

    def test_a_missing_seed_says_so(self):
        X.SEED = os.path.join(tempfile.gettempdir(), "__no_such_seed__.json")
        lib = X.library()
        self.assertIs(lib["seed_found"], False)
        self.assertIn("not readable", lib["seed_note"])
        st = X.status()
        self.assertIs(st["seed_found"], False)
        self.assertIn("NOT found", st["message"])

    def test_the_seed_entries_are_unchanged_by_the_diagnostic(self):
        diag = {}
        direct = X._read_seed()
        with_diag = X._read_seed(diag)
        self.assertEqual([e["id"] for e in direct], [e["id"] for e in with_diag])
        self.assertIs(diag["seed_found"], True)


if __name__ == "__main__":
    unittest.main()
