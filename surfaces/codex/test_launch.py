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

    def kit(self, path):
        # The kit fold (bds-jakemi): mise is the `mise` component of batterie@batterie.
        self.package(f"{path}/mise", "mise")
        return self.root / path

    def test_registry_update_changes_selected_version(self):
        first = self.kit("one")
        second = self.kit("two")
        self.register("batterie@batterie", [first])
        self.assertEqual(resolve("mise", self.registry)[0], first / "mise")
        self.register("batterie@batterie", [second])
        self.assertEqual(resolve("mise", self.registry)[0], second / "mise")

    def test_home_never_falls_back_to_itv(self):
        self.register("batterie@batterie", [self.kit("itv")])
        with self.assertRaisesRegex(ValueError, "found 0"):
            resolve("mise-home", self.registry)

    def test_mismatched_package_is_rejected(self):
        self.register("mise-home@batterie-home", [self.package("wrong", "mise")])
        with self.assertRaisesRegex(ValueError, "identity"):
            resolve("mise-home", self.registry)

    def test_ambiguous_user_install_is_rejected(self):
        self.register("batterie@batterie", [self.kit("one"), self.kit("two")])
        with self.assertRaisesRegex(ValueError, "found 2"):
            resolve("mise", self.registry)

    def test_prefold_mise_install_is_not_the_kit(self):
        self.register("mise@batterie", [self.package("old", "mise")])
        with self.assertRaisesRegex(ValueError, "found 0"):
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
