import copy
import tempfile
import unittest
from pathlib import Path

import numpy as np

from camtool import Project, ProjectError, calculate
from camtool.project import read_waypoints


class SafetyWorkflowTests(unittest.TestCase):
    def test_passive_wheel_diameter_does_not_change_drive_odometry(self):
        p = Project.demo()
        first = calculate(p)
        p.vehicle.passive_diameter = 150
        second = calculate(p)
        np.testing.assert_allclose(first.drive_s, second.drive_s)

    def test_ball_center_stays_on_its_actual_pivot_circle(self):
        p = Project.demo()
        p.mechanism.kind = "ball"
        result = calculate(p)
        distances = np.linalg.norm(result.contact_centers-result.pivot, axis=1)
        np.testing.assert_allclose(distances, 25.0, atol=1e-8)

    def test_flat_mount_mirror_changes_follower_motion(self):
        p = Project.demo()
        left = calculate(p)
        p.mechanism.side = "right"
        right = calculate(p)
        index = int(np.argmax(np.abs(left.stroke)))
        self.assertLess(left.stroke[index]*right.stroke[index], 0)

    def test_inadequate_single_cam_revolution_does_not_export_truncated_profile(self):
        p = Project.demo()
        p.cam.ratio = 1
        result = calculate(p)
        self.assertFalse(result.valid)
        self.assertEqual(len(result.profile), 0)

    def test_obstacle_and_pressure_limit_failures_are_visible(self):
        p = Project.demo()
        p.route.obstacles = [[1100, 400, 100]]
        p.cam.pressure_limit = 1
        result = calculate(p)
        failures = {c.name for c in result.checks if c.status == "fail"}
        self.assertIn("圆形障碍物", failures)
        self.assertIn("压力角", failures)

    def test_nan_and_duplicate_consecutive_points_are_rejected(self):
        p = Project.demo()
        p.vehicle.drive_diameter = float("nan")
        with self.assertRaises(ProjectError):
            calculate(p)
        p = Project.demo()
        p.route.waypoints[1] = copy.copy(p.route.waypoints[0])
        with self.assertRaises(ProjectError):
            calculate(p)

    def test_csv_preserves_order_and_rejects_bad_rows(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/"points.csv"
            path.write_text("x,y\n10,20\n30,40\n", encoding="utf-8-sig")
            self.assertEqual(read_waypoints(path), [[10, 20], [30, 40]])
            path.write_text("x,y\n10,20\nnan,40\n", encoding="utf-8-sig")
            with self.assertRaises(ProjectError):
                read_waypoints(path)

    def test_rotated_start_pose_and_null_heading_are_resolved(self):
        p = Project.demo()
        p.route.waypoints = [[300, 400], [300, 1400]]
        p.route.heading = None
        result = calculate(p)
        np.testing.assert_allclose(result.rear[0], [300, 340], atol=1e-8)
        self.assertLess(abs(result.delta).max(), 1e-8)

    def test_malformed_saved_project_is_rejected_before_ui_loading(self):
        data = Project.demo().to_dict()
        data["vehicle"]["wheelbase"] = "not a dimension"
        with self.assertRaises(ProjectError):
            Project.from_dict(data)

    def test_slider_toggle_cannot_be_reported_as_feasible(self):
        p = Project.demo()
        p.mechanism.arm_angle = 90
        p.mechanism.pivot_x = 0
        result = calculate(p)
        self.assertFalse(result.valid)
        self.assertTrue(any(c.name == "机构可逆传动" and c.status == "fail" for c in result.checks))

    def test_small_obstacle_between_samples_is_still_detected(self):
        p = Project.demo()
        p.route.waypoints = [[300, 400], [1300, 400]]
        p.route.step = 20
        p.route.obstacles = [[310, 400, 1]]
        result = calculate(p)
        self.assertFalse(result.valid)
        self.assertTrue(any(c.name == "圆形障碍物" and c.status == "fail" for c in result.checks))

    def test_extreme_independent_start_is_rejected_before_sampling(self):
        p = Project.demo()
        p.route.start_x = -1e9
        with self.assertRaises(ProjectError):
            calculate(p)
