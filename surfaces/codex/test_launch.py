# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
import json
from pathlib import Path
import tempfile
import unittest
from launch import resolve, launch_environment


class LaunchSelection(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.registry = self.root / "installed.json"

    def package(self, path, name):
        directory = self.root / path
        (directory / ".claude-plugin").mkdir(parents=True)
        (directory / ".claude-plugin/plugin.json").write_text(json.dumps({"name": name}))
        for filename in ("server.py", "uv.lock", "pyproject.toml"):
            (directory / filename).write_text("fixture")
        return directory

    def register(self, key, paths):
        self.registry.write_text(json.dumps({"plugins": {key: [{"scope": "user", "installPath": str(path)} for path in paths]}}))

    def test_registry_update_changes_selected_version(self):
        first = self.package("one", "mise")
        second = self.package("two", "mise")
        self.register("mise@batterie", [first])
        self.assertEqual(resolve("mise", self.registry)[0], first)
        self.register("mise@batterie", [second])
        self.assertEqual(resolve("mise", self.registry)[0], second)

    def test_home_never_falls_back_to_itv(self):
        self.register("mise@batterie", [self.package("itv", "mise")])
        with self.assertRaisesRegex(ValueError, "found 0"):
            resolve("mise-home", self.registry)

    def test_mismatched_package_is_rejected(self):
        self.register("mise-home@batterie-home", [self.package("wrong", "mise")])
        with self.assertRaisesRegex(ValueError, "identity"):
            resolve("mise-home", self.registry)

    def test_ambiguous_user_install_is_rejected(self):
        self.register("mise@batterie", [self.package("one", "mise"), self.package("two", "mise")])
        with self.assertRaisesRegex(ValueError, "found 2"):
            resolve("mise", self.registry)

    def test_caller_identity_overrides_are_removed_without_mutating_caller(self):
        caller = {"MISE_TOKEN_PATH": "some-other-account", "MISE_CREDENTIALS": "ambient", "PATH": "preserved"}
        selected = launch_environment(self.root, caller)
        self.assertNotIn("MISE_TOKEN_PATH", selected)
        self.assertNotIn("MISE_CREDENTIALS", selected)
        self.assertEqual(selected["PATH"], "preserved")
        self.assertIn("MISE_TOKEN_PATH", caller)


if __name__ == "__main__":
    unittest.main()
