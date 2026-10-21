import tempfile
import unittest
import numpy as np
from pathlib import Path

from camtool import Project, calculate, export_design, optimize


class ProjectWorkflowTests(unittest.TestCase):
    def test_team_can_save_and_reopen_a_project(self):
        project = Project.demo()
        project.name = "我们的小车"
        project.vehicle.wheelbase = 135.0
        with tempfile.TemporaryDirectory() as folder:
            file = Path(folder) / "测试项目.cam.json"
            project.save(file)
            reopened = Project.load(file)
        self.assertEqual(reopened.to_dict(), project.to_dict())

    def test_straight_route_has_known_distance_and_no_steering(self):
        p = Project.demo()
        p.route.waypoints = [[300, 400], [1300, 400]]
        p.route.start_x, p.route.start_y = 300, 400
        p.vehicle.speed = 100
        result = calculate(p)
        self.assertAlmostEqual(result.stats["drive_distance"], 1000, places=5)
        self.assertAlmostEqual(result.stats["time"], 10, places=5)
        self.assertLess(abs(result.delta).max(), 1e-8)
        self.assertAlmostEqual(result.rear[0, 0], 240, places=5)

    def test_straight_cam_is_a_circle_with_roller_radius_removed(self):
        p = Project.demo()
        p.route.waypoints = [[300, 400], [1300, 400]]
        result = calculate(p)
        self.assertEqual(len(result.profile), 1201)
        radii = np.linalg.norm(result.profile, axis=1)
        self.assertLess(abs(radii-55.0).max(), 0.001)
        np.testing.assert_allclose(result.profile[0], result.profile[-1], atol=1e-10)
        self.assertLess(result.stats["max_pressure"], 0.01)

    def test_curved_cam_is_forward_checked_against_ordered_checkpoints(self):
        result = calculate(Project.demo())
        self.assertLess(result.stats["replay_error"], 5.0)
        self.assertLess(result.stats["checkpoint_error"], 5.0)
        self.assertTrue(result.valid, result.checks)

    def test_design_exports_openable_mm_dxf_and_csv(self):
        import csv
        import ezdxf
        with tempfile.TemporaryDirectory() as folder:
            result = calculate(Project.demo())
            paths = export_design(result, Path(folder))
            doc = ezdxf.readfile(paths["dxf"])
            self.assertEqual(doc.units, 4)
            self.assertFalse(doc.audit().has_errors)
            outline = doc.modelspace().query('LWPOLYLINE[layer=="CAM_OUTLINE"]')[0]
            self.assertTrue(outline.closed)
            self.assertEqual(len(outline), 1200)
            with paths["motion_csv"].open(encoding="utf-8-sig", newline="") as stream:
                rows = list(csv.DictReader(stream))
            self.assertEqual(len(rows), len(result.drive_s))
            self.assertIn("steer_deg", rows[0])

    def test_optimization_is_repeatable_and_keeps_checkpoints(self):
        p = Project.demo()
        p.optimization.iterations = 1
        first = optimize(p)
        second = optimize(p)
        np.testing.assert_allclose(first.profile, second.profile, atol=1e-10)
        self.assertEqual(first.project.route.waypoints, p.route.waypoints)
        self.assertLess(first.stats["checkpoint_error"], p.route.tolerance)

    def test_optimization_searches_when_initial_route_cannot_be_calculated(self):
        p = Project.demo()
        p.mechanism.pivot_x = -104.7
        p.cam.pressure_limit = 60
        p.optimization.iterations = 1
        result = optimize(p)
        self.assertGreater(len(result.profile), 0)
        self.assertNotEqual(result.project.route.tension, .75)


if __name__ == "__main__":
    unittest.main()
