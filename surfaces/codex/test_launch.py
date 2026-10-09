# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
import json
from pathlib import Path
import tempfile
import unittest
from launch import resolve, launch_environment, workspace_environment


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

    def family(self, path):
        directory = self.root / path
        (directory / ".claude-plugin").mkdir(parents=True)
        (directory / ".claude-plugin/plugin.json").write_text(json.dumps({"name": "family"}))
        engine = directory / "mise"
        engine.mkdir()
        for filename in ("server.py", "uv.lock", "pyproject.toml", "planetmodha-client.json"):
            (engine / filename).write_text("fixture")
        return directory

    def test_family_without_component_manifest_selects_its_engine_and_credentials(self):
        family = self.family("personal")
        self.register("family@family", [family])
        root, metadata = resolve("mise-home", self.registry)
        self.assertEqual(root, family / "mise")
        self.assertEqual(metadata["name"], "family")
        env = workspace_environment("mise-home", self.registry, home=self.root)
        self.assertEqual(env["MISE_EN_SPACE_OAUTH_CLIENT"], str(family / "mise/planetmodha-client.json"))
        self.assertEqual(env["MISE_EN_SPACE_DATA_DIR"], str(self.root / ".claude/plugins/data/family-family"))

    def test_missing_family_client_refuses_instead_of_using_itv_credentials(self):
        family = self.family("personal")
        (family / "mise/planetmodha-client.json").unlink()
        self.register("family@family", [family])
        with self.assertRaisesRegex(ValueError, "Personal Mise OAuth client missing"):
            workspace_environment("mise-home", self.registry, home=self.root)

    def test_retired_personal_plugin_does_not_override_family(self):
        family = self.family("personal")
        self.registry.write_text(json.dumps({"plugins": {
            "family@family": [{"scope": "user", "installPath": str(family)}],
            "mise-home@batterie-home": [{"scope": "user", "installPath": str(self.package("old", "mise-home"))}]
        }}))
        self.assertEqual(resolve("mise-home", self.registry)[0], family / "mise")

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
        wrong = self.family("wrong")
        (wrong / ".claude-plugin/plugin.json").write_text(json.dumps({"name": "mit"}))
        self.register("family@family", [wrong])
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

    def test_itv_takes_its_client_and_store_from_the_mit_kit(self):
        # bds-jasuha: the batterie kit ships no OAuth client; mit@mit carries ITV's.
        mit = self.root / "mitkit"
        (mit / "mise").mkdir(parents=True)
        (mit / "mise/itv-oauth-client.json").write_text("{}")
        self.registry.write_text(json.dumps({"plugins": {
            "batterie@batterie": [{"scope": "user", "installPath": str(self.kit("b"))}],
            "mit@mit": [{"scope": "user", "installPath": str(mit)}]}}))
        env = workspace_environment("mise", self.registry, home=self.root)
        self.assertEqual(env["MISE_EN_SPACE_OAUTH_CLIENT"], str(mit / "mise/itv-oauth-client.json"))
        self.assertEqual(env["MISE_EN_SPACE_DATA_DIR"], str(self.root / ".claude/plugins/data/mit-mit"))

    def test_itv_without_the_mit_kit_adds_nothing(self):
        self.register("batterie@batterie", [self.kit("b")])
        self.assertEqual(workspace_environment("mise", self.registry, home=self.root), {})

    def test_caller_identity_overrides_are_removed_without_mutating_caller(self):
        caller = {"MISE_TOKEN_PATH": "some-other-account", "MISE_CREDENTIALS": "ambient", "MISE_EN_SPACE_OAUTH_CLIENT": "other-client", "MISE_EN_SPACE_DATA_DIR": "other-store", "PATH": "preserved"}
        selected = launch_environment(self.root, caller)
        self.assertNotIn("MISE_TOKEN_PATH", selected)
        self.assertNotIn("MISE_CREDENTIALS", selected)
        self.assertNotIn("MISE_EN_SPACE_OAUTH_CLIENT", selected)
        self.assertNotIn("MISE_EN_SPACE_DATA_DIR", selected)
        self.assertEqual(selected["PATH"], "preserved")
        self.assertIn("MISE_TOKEN_PATH", caller)


if __name__ == "__main__":
    unittest.main()
