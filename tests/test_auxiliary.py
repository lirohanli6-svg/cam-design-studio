import copy
import hashlib
import math
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import numpy as np
from camtool.project import Project, ProjectError
from camtool.engine import calculate, validate
from camtool.route_shape import required_nodes, derivatives
from camtool.auxiliary_search import SearchSettings, search_auxiliaries
from camtool.candidate_audit import audit_candidate


class AuxiliaryTests(unittest.TestCase):
    def test_legacy_projects_keep_required_order_and_empty_auxiliaries(self):
        data=Project.demo().to_dict(); expected=copy.deepcopy(data['route']['waypoints'])
        for key in ('auxiliary_points','steer_rate_limit','start_steer'): data['route'].pop(key)
        p=Project.from_dict(data)
        self.assertEqual(p.route.waypoints,expected)
        self.assertEqual(p.route.auxiliary_points,[])
        with tempfile.TemporaryDirectory() as folder:
            file=Path(folder)/'old.cam.json'; p.save(file)
            self.assertEqual(Project.load(file).to_dict(),p.to_dict())

    def test_soft_auxiliary_does_not_become_an_interpolated_visit(self):
        p=Project.demo(); baseline=calculate(p,precision=False)
        p.route.auxiliary_points=[[1,1600.,1100.]]
        r=calculate(p,precision=False)
        self.assertEqual(r.project.route.waypoints,baseline.project.route.waypoints)
        self.assertEqual(len(r.project.route.waypoints),5)
        self.assertGreater(np.min(np.linalg.norm(r.target_path-[1600,1100],axis=1)),50)
        self.assertLess(r.stats['checkpoint_error'],p.route.tolerance)
        self.assertGreater(abs(r.stats['sensor_distance']-baseline.stats['sensor_distance']),1)

    def test_shared_nonzero_node_curvature_and_clamped_start_tangent(self):
        p=Project.demo(); nodes=required_nodes(p)
        first,second=derivatives(nodes,0.,p.route.tension,[])
        self.assertAlmostEqual(first[0,1],0.,places=10)
        self.assertGreater(np.linalg.norm(second[1:-1]),1e-5)
        # The public result retains exact ordered constraints, no zero-curvature
        # interpolation condition is imposed at internal required nodes.
        r=calculate(p,precision=False)
        for point in nodes: self.assertLess(np.linalg.norm(r.target_path-point,axis=1).min(),1e-6)

    def test_known_straight_and_circular_arc_kinematics(self):
        p=Project.demo();p.route.waypoints=[[300.,400.],[1300.,400.]]
        r=calculate(p)
        self.assertLess(abs(r.delta).max(),1e-9)
        self.assertTrue(r.precision['stable'])
        self.assertAlmostEqual(r.stats['min_curvature_radius'],57.,delta=.01)
        radius=1000.; sx=p.vehicle.sensor_x
        t=np.linspace(0,math.pi/3,9)
        sensor=np.column_stack([300+radius*np.sin(t)+sx*np.cos(t),400+radius*(1-np.cos(t))+sx*np.sin(t)])
        p.route.waypoints=sensor.tolist();p.route.start_x,p.route.start_y=sensor[0]
        p.route.start_steer=math.degrees(math.atan(p.vehicle.wheelbase/radius));p.route.tension=1.
        r=calculate(p,precision=False)
        self.assertLess(np.max(abs(np.degrees(r.delta)-p.route.start_steer)),.15)
        self.assertLess(r.stats['checkpoint_error'],p.route.tolerance)

    def test_auxiliary_invalid_index_nonfinite_and_outside_are_explicit(self):
        for row in ([50,500.,500.],[.2,500.,500.],[1,float('nan'),500.],[1,6000.,500.]):
            p=Project.demo();p.route.auxiliary_points=[row]
            with self.assertRaises(ProjectError): validate(p)

    def test_precision_reports_differences_and_unstable_is_not_proof(self):
        r=calculate(Project.demo())
        self.assertEqual(set(r.precision['metrics']),{'max_pressure','min_curvature_radius','min_contact_gap'})
        self.assertFalse(r.precision['stable'])
        self.assertIn('有限采样',next(c.detail for c in r.checks if c.name=='主设计采样复算'))
        self.assertAlmostEqual(r.stats['min_contact_gap'],r.geometry['gaps'].min())
        k=r.geometry['index']; center=r.pitch[k]; closest=r.geometry['nearest'][k]
        self.assertAlmostEqual(np.linalg.norm(center-closest)-r.project.mechanism.roller,r.geometry['minimum'])

    def test_refined_geometry_failure_preserves_coarse_evidence_as_failed(self):
        # Losing contact/closure at a denser sampling must become an explicit
        # failing result, not erase the coarse evidence or claim stability.
        with patch('camtool.engine.calculate',side_effect=ProjectError('细采样连杆无法闭合')):
            r=calculate(Project.demo())
        self.assertGreater(len(r.profile),0)
        self.assertFalse(r.valid)
        self.assertFalse(r.precision['stable'])
        self.assertTrue(any(c.name=='精度复算约束' and c.status=='fail' for c in r.checks))

    def test_search_includes_late_segments_and_does_not_modify_hardware(self):
        p=Project.demo();p.route.waypoints=[[300.+i*200,400.] for i in range(14)]
        before=p.to_dict()
        o=search_auxiliaries(p,SearchSettings(first_segment=10,last_segment=13,evaluations=4,seconds=30,maximum_points=0))
        self.assertEqual({row['segment'] for row in o.trials},{10,11,12,13})
        self.assertEqual(p.to_dict(),before)
        for key in ('vehicle','mechanism','cam'):
            self.assertEqual(o.candidate.project.to_dict()[key],before[key])
        self.assertEqual(o.candidate.project.route.waypoints,p.route.waypoints)

    def test_search_no_improvement_and_infeasible_are_truthful(self):
        p=Project.demo(); p.route.obstacles=[[300.,400.,30.]]
        o=search_auxiliaries(p,SearchSettings(evaluations=2,seconds=15))
        self.assertFalse(o.candidate.valid)
        self.assertIn('未找到满足全部',o.status)
        self.assertIsNotNone(o.verification)
        self.assertFalse(o.verification.kinematic_pass)

    def test_final_candidate_reads_exported_dxf_without_nominal_driving_data(self):
        p=Project.demo();p.route.waypoints=[[300.,400.],[1300.,400.]]
        r=calculate(p)
        # Corrupt all nominal arrays after export geometry was formed. Independent
        # movement still follows the actual circle in DXF, not these arrays.
        r.cam_delta[:]=1.;r.delta[:]=1.;r.contact_centers[:]=999.
        with patch('camtool.engine.follower_positions',side_effect=AssertionError('not independent')):
            audit,blob=audit_candidate(r)
        self.assertTrue(audit.kinematic_pass)
        self.assertEqual(audit.sha256,hashlib.sha256(blob).hexdigest())
        self.assertLess(abs(audit.steering).max(),.01)

    def test_budget_validation_cancel_and_export_preserve_original(self):
        for settings in (SearchSettings(radius=float('nan')),SearchSettings(evaluations=1.5),SearchSettings(first_segment=9)):
            with self.assertRaises(ProjectError): settings.validate(4)
        p=Project.demo()
        with self.assertRaisesRegex(ProjectError,'取消'): search_auxiliaries(p,SearchSettings(),lambda:True)
        p.route.waypoints=[[300.,400.],[1300.,400.]]
        o=search_auxiliaries(p,SearchSettings(evaluations=1,seconds=10,maximum_points=0))
        self.assertFalse(o.improved)
        self.assertIn('未确认改善',o.status)
        with tempfile.TemporaryDirectory() as folder:
            output=Path(folder)/'search';o.export(output)
            self.assertEqual(Project.load(output/'原方案.cam.json').to_dict(),p.to_dict())
            self.assertTrue((output/'候选DXF独立二次检验/二次检验结果.json').is_file())
            with self.assertRaises(ProjectError): o.export(output)
