import ast
import copy
import hashlib
import json
import math
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import ezdxf
import numpy as np

from camtool import Project, calculate, export_design
from camtool import verification as verify_module
from camtool.verification import (FAIL, PASS, UNKNOWN, VerificationInput,
                                  export_verification, verify_dxf)
from camtool.version import VERSION


class IndependentVerificationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)

    def circle(self, radius=55.0, points=720):
        doc = ezdxf.new('R2010')
        doc.units = 4
        angles = np.linspace(0, 2*math.pi, points, endpoint=False)
        doc.modelspace().add_lwpolyline(np.column_stack([radius*np.cos(angles), radius*np.sin(angles)]),
                                      close=True, dxfattribs={'layer': 'CAM_OUTLINE'})
        path = self.folder/'circle.dxf'
        doc.saveas(path)
        return path

    def straight(self):
        p = Project.demo()
        p.route.waypoints = [[300, 400], [1300, 400]]
        q = VerificationInput.from_project(p, 1000/(math.pi*p.vehicle.drive_diameter*p.cam.ratio)*360)
        q.angle_step = 3
        return q

    def asymmetric(self):
        p = Project.demo()
        p.cam.phase = 37
        p.cam.direction = -1
        design = calculate(p)
        path = export_design(design, self.folder)['dxf']
        q = VerificationInput.from_project(p, design.stats['cam_usage']*360)
        # The C2 route includes a real starting steering transient; use the
        # current 0.5° default rather than weakening the 1.5 mm convergence gate.
        q.angle_step = 0.5
        q.stability_mm = 1.5
        nominal = np.column_stack([np.degrees(design.cam_angle), np.degrees(design.cam_delta)])
        return path, q, design.target_path, nominal

    def test_circle_recovers_straight_motion_without_main_motion_functions(self):
        q, path = self.straight(), self.circle()
        with patch('camtool.engine.follower_positions', side_effect=AssertionError('must not use generator')):
            result = verify_dxf(path, q)
        self.assertTrue(result.kinematic_pass, [(c.name,c.detail) for c in result.checks if c.status == FAIL])
        self.assertLess(np.abs(result.steering).max(), 0.003)
        np.testing.assert_allclose(result.path[-1], [1300,400], atol=0.2)
        self.assertAlmostEqual(result.path_angles[-1], q.run_end)
        self.assertEqual(result.angles[-1], 360)
        self.assertLess(result.path_angles[-1], 60)

    def test_constant_radius_cam_has_known_nonzero_steering_and_circle_trajectory(self):
        q = self.straight()
        m = q.project.mechanism
        delta = math.radians(8)
        pin_x = q.pivot_x+m.arm*math.cos(delta)
        center_y = q.pivot_y+m.arm*math.sin(delta)+math.sqrt(m.link*m.link-(pin_x-q.guide_x)**2)
        path = self.circle(center_y-m.roller)
        q.run_end = 200/(math.pi*q.project.vehicle.drive_diameter*q.project.cam.ratio)*360
        k = math.tan(delta)/q.project.vehicle.wheelbase
        distance = 200/(1+q.project.vehicle.track/2*k)
        turn = k*distance
        rear0 = np.array([240.,400.])
        rear = rear0+np.array([math.sin(turn)/k,(1-math.cos(turn))/k])
        expected = rear+q.project.vehicle.sensor_x*np.array([math.cos(turn),math.sin(turn)])
        q.project.route.waypoints = [[300,400],expected.tolist()]
        result = verify_dxf(path,q)
        self.assertLess(np.abs(result.steering-8).max(),0.003)
        np.testing.assert_allclose(result.path[-1],expected,atol=0.05)

    def test_ball_mechanism_completes_the_actual_contact_to_motion_chain(self):
        q = self.straight()
        q.project.mechanism.kind = 'ball'
        q = VerificationInput.from_project(q.project,q.run_end)
        q.angle_step = 3
        result = verify_dxf(self.circle(),q)
        self.assertTrue(result.kinematic_pass)
        self.assertGreater(len(result.path),10)

    def test_asymmetric_cam_detects_wrong_direction_and_install_phase(self):
        path,q,reference,nominal = self.asymmetric()
        good = verify_dxf(path,q,reference=reference,nominal=nominal)
        self.assertTrue(good.kinematic_pass,[(c.name,c.detail) for c in good.checks if c.status == FAIL])
        for change in ('direction','install_angle'):
            with self.subTest(change=change):
                bad = copy.deepcopy(q)
                if change=='direction': bad.direction *= -1
                else: bad.install_angle += 25
                result = verify_dxf(path,bad,reference=reference,nominal=nominal)
                self.assertFalse(result.kinematic_pass)
                self.assertTrue(any(c.name in ('名义转角比较','顺序打卡及偏差','原路线偏差') and c.status==FAIL for c in result.checks))

    def test_local_outline_damage_is_detected_and_has_problem_angle(self):
        path,q,reference,nominal = self.asymmetric()
        doc = ezdxf.readfile(path)
        outline = list(doc.modelspace().query('LWPOLYLINE[layer=="CAM_OUTLINE"]'))[0]
        points = np.array(list(outline.get_points('xy')))
        for index in range(248,253):
            points[index] *= (np.linalg.norm(points[index])+4)/np.linalg.norm(points[index])
        outline.set_points(points,format='xy')
        doc.saveas(path)
        result=verify_dxf(path,q,reference=reference,nominal=nominal)
        self.assertFalse(result.kinematic_pass)
        self.assertTrue(any(i['angle_deg'] is not None for i in result.issues))

    def test_nominal_csv_values_cannot_drive_or_repair_motion(self):
        q,path=self.straight(),self.circle()
        a=verify_dxf(path,q)
        b=verify_dxf(path,q,nominal=[[0,25],[360,25]])
        np.testing.assert_array_equal(a.path,b.path)
        np.testing.assert_array_equal(a.steering,b.steering)
        self.assertTrue(any(c.name=='名义转角比较' and c.status==FAIL for c in b.checks))

    def test_illegal_dxf_and_open_or_self_intersecting_outline_fail(self):
        path=self.folder/'bad.dxf'
        path.write_text('not a DXF',encoding='utf-8')
        self.assertFalse(verify_dxf(path,self.straight()).kinematic_pass)
        for closed,points in [(False,[(0,0),(30,0),(0,20)]),(True,[(0,0),(20,20),(0,20),(30,0)])]:
            doc=ezdxf.new('R2010'); doc.units=4
            doc.modelspace().add_lwpolyline(points,close=closed,dxfattribs={'layer':'CAM_OUTLINE'})
            doc.saveas(path)
            result=verify_dxf(path,self.straight())
            self.assertEqual(result.checks[0].status,FAIL)
            self.assertEqual(len(result.path),0)

    def test_unsupported_circle_entity_and_arc_bulges_are_explicit(self):
        path=self.folder/'unsupported.dxf'
        doc=ezdxf.new('R2010'); doc.units=4
        doc.modelspace().add_circle((0,0),55,dxfattribs={'layer':'CAM_OUTLINE'})
        doc.saveas(path)
        result=verify_dxf(path,self.straight())
        self.assertEqual(result.checks[0].status,UNKNOWN)
        self.assertIn('CIRCLE',result.checks[0].detail)
        doc=ezdxf.new('R2010'); doc.units=4
        doc.modelspace().add_lwpolyline([(0,0,1),(100,0,0),(100,100,0)],format='xyb',close=True,dxfattribs={'layer':'CAM_OUTLINE'})
        doc.saveas(path)
        self.assertIn('bulge',verify_dxf(path,self.straight()).checks[0].detail)

    def test_unreachable_mount_and_multiple_linkage_solutions_do_not_choose_a_branch(self):
        path=self.circle()
        q=self.straight(); q.pivot_y+=1000
        bad=verify_dxf(path,q)
        self.assertTrue(any(c.name=='整圈几何接触' and c.status==FAIL for c in bad.checks))
        p=self.straight().project
        p.mechanism.arm_angle=90
        q=VerificationInput.from_project(p,55); q.angle_step=5
        result=verify_dxf(path,q)
        self.assertTrue(any(c.name=='整圈多解' and c.status==FAIL for c in result.checks),[(c.name,c.detail) for c in result.checks])
        self.assertFalse(len(result.path))

    def test_coarse_step_can_fail_convergence_instead_of_claiming_a_proof(self):
        path,q,reference,nominal=self.asymmetric()
        q.angle_step=8; q.stability_mm=0.1
        result=verify_dxf(path,q,reference=reference,nominal=nominal)
        self.assertTrue(any(c.name=='精度收敛' and c.status==FAIL for c in result.checks))

    def test_report_has_hash_version_parameters_and_unknown_dynamics(self):
        q,path=self.straight(),self.circle()
        q.sources['车辆']='实测：卡尺，记录 A-01（测试声明）'
        result=verify_dxf(path,q)
        files=export_verification(result,self.folder/'report')
        data=json.loads(files['result'].read_text(encoding='utf-8'))
        self.assertEqual(data['software_version'],VERSION)
        self.assertEqual(data['sha256'],hashlib.sha256(path.read_bytes()).hexdigest())
        self.assertEqual(data['parameters']['sources']['车辆'],q.sources['车辆'])
        for name in ('弹簧压紧力及脱离接触','摩擦与驱动力矩','轮胎打滑及实车性能'):
            self.assertTrue(any(c.name==name and c.status==UNKNOWN for c in result.checks))
        self.assertIn('未实施实车试验',files['report'].read_text(encoding='utf-8'))
        reopened=VerificationInput.from_dict(json.loads(files['parameters'].read_text(encoding='utf-8')))
        self.assertEqual(reopened.to_dict(),q.to_dict())
        convergence=next(c for c in result.checks if c.name=='精度收敛')
        convergence.status=UNKNOWN
        self.assertFalse(result.kinematic_pass, 'Unverified core kinematics cannot be promoted to passed')

    def test_independent_module_does_not_import_the_generation_engine(self):
        tree=ast.parse(Path(verify_module.__file__).read_text(encoding='utf-8'))
        for node in ast.walk(tree):
            if isinstance(node,ast.ImportFrom):
                self.assertNotIn(node.module,('engine','plotting','markings','export'))

    def test_run_interval_start_pose_and_obstacles_are_checked_independently(self):
        q,path=self.straight(),self.circle()
        q.run_start=40
        q.run_end+=40
        q.start_heading=90
        q.project.route.waypoints=[[300,400],[300,1400]]
        q.project.route.obstacles=[[300,900,20]]
        result=verify_dxf(path,q)
        np.testing.assert_allclose(result.path[0],[300,400],atol=1e-9)
        np.testing.assert_allclose(result.path[-1],[300,1400],atol=0.2)
        self.assertTrue(any(c.name=='圆形障碍物' and c.status==FAIL for c in result.checks))
        self.assertTrue(any(i['angle_deg'] is not None and '障碍物' in i['reason'] for i in result.issues))

    def test_limits_and_boundary_fail_with_locations(self):
        q,path=self.straight(),self.circle(58)
        q.project.vehicle.max_steer=1
        q.project.mechanism.max_stroke=0.1
        q.project.route.field=[0,350,0,500]
        result=verify_dxf(path,q)
        for name in ('整圈转角限位','整圈行程','场地边界'):
            self.assertTrue(any(c.name==name and c.status==FAIL for c in result.checks),name)
        self.assertTrue(any(i['position_mm'] is not None for i in result.issues))

    def test_degenerate_continuous_contact_and_unknown_mechanism_are_explicit(self):
        q=self.straight()
        doc=ezdxf.new('R2010'); doc.units=4
        doc.layers.new('CAM_OUTLINE')
        doc.modelspace().add_lwpolyline([(-20,-20),(20,-20),(20,80),(-20,80)],close=True,dxfattribs={'layer':'CAM_OUTLINE'})
        path=self.folder/'degenerate.dxf'; doc.saveas(path)
        q.guide_x=22
        result=verify_dxf(path,q)
        self.assertTrue(any(c.name=='退化接触配置' and c.status==UNKNOWN for c in result.checks))
        self.assertFalse(len(result.path))
        q.project.mechanism.kind='unsupported'
        with self.assertRaises(ValueError):
            verify_dxf(path,q)


if __name__=='__main__':
    unittest.main()
