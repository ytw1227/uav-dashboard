"""Admission and scene boundaries of the local, inference-only map adapter."""
import hashlib
from pathlib import Path
import tempfile
import unittest

from nicegui_dashboard.team_demo import demo_survey_supported, needs_demo, resolve_demo_scene, study_roles


class DemonstrationInputTests(unittest.TestCase):
    def test_ordinary_flight_and_demonstration_roles_are_separate(self):
        admitted = {"train", "calibration", "confirmation"}
        for role in admitted:
            self.assertFalse(needs_demo({"study_role": role}, admitted))
        protected = {"study_role": "legacy_protected"}
        self.assertTrue(needs_demo(protected, admitted))
        self.assertEqual(protected["study_role"], "legacy_protected")
        self.assertTrue(needs_demo(None, admitted))
        with self.assertRaises(ValueError):
            needs_demo({"study_role": "quarantine"}, admitted)

    def test_only_the_original_checksum_pinned_demonstration_file_is_accepted(self):
        with tempfile.TemporaryDirectory() as tmp:
            core = Path(tmp)
            relative = Path("dashboard_execution/maps/example/scene.local.json")
            scene = core / relative
            scene.parent.mkdir(parents=True)
            scene.write_bytes(b"test scene")
            card = {"key": "example", "path": str(relative), "joint_split": "external_demonstration",
                    "sha256": hashlib.sha256(scene.read_bytes()).hexdigest()}
            self.assertEqual(resolve_demo_scene(core, card), scene.resolve())
            for changes in ({"key": "../example"}, {"path": str(scene.resolve())},
                            {"sha256": "b" * 64}, {"joint_split": "train"}):
                with self.subTest(changes=changes), self.assertRaises(ValueError):
                    resolve_demo_scene(core, {**card, **changes})
            scene.unlink()
            with self.assertRaises(ValueError):
                resolve_demo_scene(core, card)

    def test_new_survey_recipe_requires_compatibility_check(self):
        source = ("def survey_for(region, direction):\n"
                  "    entry = registry()[region]\n"
                  "    if entry['study_role'] not in ('calibration', 'confirmation'):\n"
                  "        raise ValueError('unknown')\n"
                  "    return new_route(region, direction)\n")
        self.assertEqual(study_roles(source), {"calibration", "confirmation"})
        self.assertFalse(demo_survey_supported(source))
        self.assertIsNone(study_roles("def custom_survey(): pass"))
