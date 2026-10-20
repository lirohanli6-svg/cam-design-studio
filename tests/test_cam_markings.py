import math
import tempfile
import unittest
from pathlib import Path

import ezdxf
import numpy as np

from camtool import Project, calculate, export_design
from camtool.markings import cam_markings


class CamAssemblyMarkingTests(unittest.TestCase):
    def test_start_mark_is_at_initial_roller_contact_with_phase_and_both_mechanisms(self):
        for kind in ("flat", "ball"):
            for direction in (-1, 1):
                with self.subTest(kind=kind, direction=direction):
                    project = Project.demo()
                    project.mechanism.kind = kind
                    project.cam.phase = 73
                    project.cam.direction = direction
                    result = calculate(project)
                    markings = cam_markings(result)
                    angle = -math.radians(project.cam.phase)
                    rotation = np.array([[math.cos(angle), -math.sin(angle)],
                                         [math.sin(angle), math.cos(angle)]])
                    installed_start = rotation @ markings.start
                    # The zero-distance contact must sit on the initial ball /
                    # roller, even when the exported outline is phase-rotated.
                    distance = np.linalg.norm(installed_start-result.contact_centers[0])
                    self.assertAlmostEqual(distance, project.mechanism.roller, places=8)
                    self.assertGreater(np.linalg.norm(markings.start-result.profile[200]), 1)

    def test_arrow_shows_physical_rotation_not_reverse_profile_sampling_order(self):
        for direction in (-1, 1):
            with self.subTest(direction=direction):
                project = Project.demo()
                project.cam.direction = direction
                result = calculate(project)
                markings = cam_markings(result)
                arrow_angles = np.unwrap(np.arctan2(markings.arrow[:, 1], markings.arrow[:, 0]))
                profile_angles = np.unwrap(np.arctan2(result.pitch[:, 1], result.pitch[:, 0]))
                self.assertTrue(np.all(np.diff(arrow_angles)*direction > 0))
                self.assertLess((profile_angles[-1]-profile_angles[0])*direction, 0)
                # Both arrowhead wings trail the tangent at the physical tip.
                tangent = direction*np.array([-markings.arrow[-1, 1], markings.arrow[-1, 0]])
                for wing in markings.arrowhead[[0, 2]]:
                    self.assertLess(float((wing-markings.arrow[-1]) @ tangent), 0)

    def test_dxf_has_start_and_rotation_on_separate_reference_layers(self):
        project = Project.demo()
        project.cam.phase = -41
        project.cam.direction = -1
        result = calculate(project)
        with tempfile.TemporaryDirectory() as folder:
            files = export_design(result, Path(folder))
            drawing = ezdxf.readfile(files["dxf"])
            self.assertFalse(drawing.audit().has_errors)
            space = drawing.modelspace()
            start = list(space.query('CIRCLE[layer=="START_REFERENCE"]'))
            self.assertEqual(len(start), 1)
            np.testing.assert_allclose(tuple(start[0].dxf.center)[:2], result.profile[0], atol=1e-10)
            arrows = list(space.query('LWPOLYLINE[layer=="ROTATION_REFERENCE"]'))
            self.assertEqual(len(arrows), 2)
            texts = list(space.query('TEXT[layer=="ROTATION_REFERENCE"]'))
            self.assertIn("ROTATION: CW", texts[0].dxf.text)
            outline = list(space.query('LWPOLYLINE[layer=="CAM_OUTLINE"]'))[0]
            np.testing.assert_allclose(list(outline.get_points("xy")), result.profile[:-1])
            self.assertTrue(outline.closed)
            report = files["report"].read_text(encoding="utf-8")
            self.assertIn("出发点 S0", report)
            self.assertIn("顺时针", report)

    def test_outline_only_export_still_excludes_assembly_references(self):
        project = Project.demo()
        project.cam.reference_layers = False
        result = calculate(project)
        with tempfile.TemporaryDirectory() as folder:
            files = export_design(result, Path(folder))
            drawing = ezdxf.readfile(files["dxf"])
            self.assertEqual({entity.dxf.layer for entity in drawing.modelspace()},
                             {"CAM_OUTLINE", "CENTER_HOLE"})


if __name__ == "__main__":
    unittest.main()
