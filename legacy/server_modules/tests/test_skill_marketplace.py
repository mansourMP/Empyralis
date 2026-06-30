import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from server_modules import installed_skills, skills_registry


def _write_skill(
    root: Path,
    *,
    name: str = "sample-skill",
    description: str = "Sample marketplace skill.",
    author: str = "Empyralis",
    runtime: dict | None = None,
) -> Path:
    skill_dir = root / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "skill.json").write_text(
        json.dumps(
            {
                "name": name,
                "version": "1.0.0",
                "description": description,
                "author": author,
                "tools": ["http_request"],
                "slash_commands": ["/sample"],
                "requires": ["requests"],
                "source": f"https://example.com/{name}.git",
                "runtime": runtime or {},
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    (skill_dir / "handler.py").write_text("print('ok')\n", encoding="utf-8")
    (skill_dir / "README.md").write_text(f"# {name}\n\n{description}\n", encoding="utf-8")
    return skill_dir


class SkillMarketplaceTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.bundled_root = self.root / "bundled"
        self.workspace_root = self.root / "workspace"
        self.global_root = self.root / "global"
        self.bundled_root.mkdir(parents=True, exist_ok=True)
        self.workspace_root.mkdir(parents=True, exist_ok=True)
        self.global_root.mkdir(parents=True, exist_ok=True)
        self.registry_file = self.root / "marketplace" / "registry.json"

        self.patchers = [
            patch.object(installed_skills, "bundled_skills_root", return_value=self.bundled_root),
            patch.object(installed_skills, "workspace_skills_root", return_value=self.workspace_root),
            patch.object(installed_skills, "global_skills_root", return_value=self.global_root),
            patch.object(skills_registry, "bundled_skills_root", return_value=self.bundled_root),
            patch.object(skills_registry, "workspace_skills_root", return_value=self.workspace_root),
            patch.object(skills_registry, "MARKETPLACE_REGISTRY_FILE", self.registry_file),
            patch.object(skills_registry, "MARKETPLACE_PUBLISH_DIR", self.root / "marketplace" / "published"),
        ]
        for patcher in self.patchers:
            patcher.start()

    def tearDown(self):
        for patcher in reversed(self.patchers):
            patcher.stop()
        self.temp_dir.cleanup()

    def test_list_installed_skills(self):
        _write_skill(
            self.workspace_root,
            name="alpha-skill",
            runtime={"skill_class": "system", "action_class": "read", "execution_adapter": "web_search"},
        )

        payload = skills_registry.list_marketplace_skills()

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["count"], 1)
        self.assertEqual(payload["items"][0]["id"], "alpha-skill")
        self.assertTrue(payload["items"][0]["enabled"])
        self.assertEqual(payload["items"][0]["skill_class"], "system")
        self.assertEqual(payload["items"][0]["execution_adapter"], "web_search")

    @patch("server_modules.skills_registry._install_requirements")
    @patch("server_modules.skills_registry._clone_git_source")
    def test_install_from_github_url(self, clone_mock, _install_requirements_mock):
        source_repo = self.root / "source-repo"
        _write_skill(source_repo, name="github-skill")

        def _fake_clone(source_url: str, target_dir: Path) -> None:
            self.assertEqual(source_url, "https://github.com/example/github-skill")
            target_dir.mkdir(parents=True, exist_ok=True)
            for child in source_repo.iterdir():
                destination = target_dir / child.name
                if child.is_dir():
                    import shutil
                    shutil.copytree(child, destination)
                else:
                    destination.write_text(child.read_text(encoding="utf-8"), encoding="utf-8")

        clone_mock.side_effect = _fake_clone

        payload = skills_registry.install_marketplace_skill(source_url="https://github.com/example/github-skill")

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["item"]["id"], "github-skill")
        self.assertTrue((self.workspace_root / "github-skill" / "skill.json").exists())

    @patch("server_modules.skills_registry._install_requirements")
    @patch("server_modules.skills_registry._clone_git_source")
    def test_invalid_manifest_rejected(self, clone_mock, _install_requirements_mock):
        source_repo = self.root / "invalid-source"
        skill_dir = source_repo / "invalid-skill"
        skill_dir.mkdir(parents=True, exist_ok=True)
        (skill_dir / "skill.json").write_text(
            json.dumps({"name": "invalid-skill", "version": "1.0.0"}),
            encoding="utf-8",
        )
        (skill_dir / "handler.py").write_text("print('bad')\n", encoding="utf-8")

        def _fake_clone(_source_url: str, target_dir: Path) -> None:
            import shutil
            shutil.copytree(source_repo, target_dir, dirs_exist_ok=True)

        clone_mock.side_effect = _fake_clone

        with self.assertRaises(ValueError):
            skills_registry.install_marketplace_skill(source_url="https://github.com/example/invalid-skill")

    @patch("server_modules.skills_registry._install_requirements")
    @patch("server_modules.skills_registry._clone_git_source")
    def test_uninstall_removes_from_registry(self, clone_mock, _install_requirements_mock):
        source_repo = self.root / "removable-source"
        _write_skill(source_repo, name="remove-me")

        def _fake_clone(_source_url: str, target_dir: Path) -> None:
            import shutil
            shutil.copytree(source_repo, target_dir, dirs_exist_ok=True)

        clone_mock.side_effect = _fake_clone

        skills_registry.install_marketplace_skill(source_url="https://github.com/example/remove-me")
        before = skills_registry.list_marketplace_skills()
        self.assertEqual(len(before["items"]), 1)

        removed = skills_registry.uninstall_marketplace_skill("remove-me")

        self.assertTrue(removed["ok"])
        after = skills_registry.list_marketplace_skills()
        self.assertEqual(after["items"], [])
        registry = installed_skills.load_installed_skill_registry()
        self.assertNotIn("remove-me", registry["items"])


if __name__ == "__main__":
    unittest.main()
