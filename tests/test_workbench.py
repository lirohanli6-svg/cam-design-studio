import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

from camtool import Project, ProjectError, calculate, export_design, optimize
from camtool.workbench import (PlanComparison, RouteHistory, apply_parameter_profile, cam_degrees,
    comparison_metrics, demo_project, diagnostics, index_at_time, ordered_deviations,
    parameter_profile, perturbation_analysis, read_measured_track, time_axis)


class WorkbenchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.normal=calculate(demo_project())
        cls.problem=calculate(demo_project(True))

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.folder=Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)

    def test_two_reproducible_cases_have_real_different_check_results(self):
        self.assertTrue(self.normal.valid)
        self.assertFalse(self.problem.valid)
        names={c.name for c in self.problem.checks if c.status=='fail'}
        self.assertTrue({'前轮转角','压力角','圆形障碍物'}<=names)
        self.assertEqual(demo_project(True).to_dict(),demo_project(True).to_dict())

    def test_timeline_uses_physical_rear_distance_and_clamps_seeking(self):
        r=self.normal
        times=time_axis(r)
        self.assertAlmostEqual(times[-1],r.stats['time'])
        self.assertEqual(index_at_time(r,-10),0)
        self.assertEqual(index_at_time(r,1e6),len(times)-1)
        i=len(times)//3
        self.assertEqual(index_at_time(r,float(times[i])),i)
        self.assertAlmostEqual(cam_degrees(r)[-1],r.stats['cam_usage']*360)

    def test_diagnostics_have_traceable_actual_limits_and_route_cam_locations(self):
        rows=diagnostics(self.problem)
        steering=next(d for d in rows if d.name=='前轮转角')
        self.assertEqual(steering.route_index,int(np.argmax(abs(self.problem.delta))))
        self.assertIn('5',steering.limit)
        self.assertAlmostEqual(steering.angle_deg,float(cam_degrees(self.problem)[steering.route_index]))
        self.assertIsNotNone(steering.point)
        obstacle=next(d for d in rows if d.name=='圆形障碍物')
        self.assertIsNotNone(obstacle.point)
        self.assertIn('最短距离',obstacle.actual)
        self.assertTrue(obstacle.basis and obstacle.advice)

    def test_return_segment_problem_does_not_fabricate_race_location(self):
        result=copy.deepcopy(self.normal)
        result.pressure[-1]=100
        pressure=next(d for d in diagnostics(result) if d.name=='压力角')
        self.assertEqual(pressure.angle_deg,360)
        self.assertIsNone(pressure.route_index)
        self.assertIsNone(pressure.point)
        self.assertIn('回程',pressure.scope)

    def test_comparison_truthfully_reports_no_improvement_and_tradeoffs(self):
        pair=PlanComparison.capture(self.normal,self.normal)
        self.assertIn('未改善',pair.verdict())
        self.assertEqual(len(comparison_metrics(self.normal)),8)
        pair.optimized.stats['max_pressure']*=.9
        pair.optimized.stats['max_steer']*=1.1
        self.assertIn('取舍',pair.verdict())

    def test_comparison_preserves_and_exports_both_original_projects(self):
        before=self.normal.project.to_dict()
        candidate=copy.deepcopy(self.normal)
        candidate.project.route.tension=.9
        pair=PlanComparison.capture(self.normal,candidate)
        candidate.project.vehicle.wheelbase=999
        self.assertNotEqual(pair.optimized.project.vehicle.wheelbase,999)
        pair.export(self.folder)
        self.assertEqual(Project.load(self.folder/'原方案.cam.json').to_dict(),before)
        self.assertEqual(Project.load(self.folder/'优化方案.cam.json').route.tension,.9)
        with self.assertRaises(ProjectError): pair.export(self.folder)

    def test_uncomputable_original_is_preserved_without_fabricated_comparison(self):
        p=Project.demo(); p.mechanism.pivot_x=-104.6; p.cam.pressure_limit=60
        pair=PlanComparison.capture(None,self.normal,p,'原机构无法到达')
        self.assertIn('无法判断改善',pair.verdict())
        self.assertTrue(all(value is None for value in comparison_metrics(pair.original).values()))
        pair.export(self.folder)
        self.assertEqual(Project.load(self.folder/'原方案.cam.json').to_dict(),p.to_dict())
        self.assertEqual(json.loads((self.folder/'优化对比.json').read_text(encoding='utf-8'))['original_path'],[])

    def test_route_history_restores_precision_tangents_and_redo_branch(self):
        h=RouteHistory()
        a={'points':[[10.123456789,20]],'tangents':[4]}
        b={'points':[[40,50]],'tangents':[]}
        h.commit(a,b)
        self.assertEqual(h.undo(b),a)
        self.assertEqual(h.redo(a),b)
        h.undo(b)
        c={'points':[[90,10]],'tangents':[]}
        h.commit(a,c)
        self.assertIsNone(h.redo(c))

    def measured_file(self,metadata=None,rows='0,30,40\n1,60,40\n'):
        meta={'unit':'cm','frame':'field_xy','axes':'x_right_y_up','point':'sensor','kind':'synthetic','source':'自动测试构造数据（非实测）'}
        if metadata: meta.update(metadata)
        path=self.folder/'track.csv'
        path.write_text(''.join(f'# {k}={v}\n' for k,v in meta.items())+'t_s,x,y\n'+rows,encoding='utf-8-sig')
        return path

    def test_imported_track_requires_metadata_and_converts_explicit_units(self):
        path=self.measured_file()
        track=read_measured_track(path)
        np.testing.assert_array_equal(track.path,[[300,400],[600,400]])
        self.assertEqual(track.sha256,hashlib.sha256(path.read_bytes()).hexdigest())
        self.assertIn('非实测',track.metadata['source'])

    def test_ambiguous_track_and_invalid_time_or_placeholder_source_are_rejected(self):
        for meta,rows in [({'unit':'unknown'},'0,0,0\n1,1,1'),({'frame':'body'},'0,0,0\n1,1,1'),
                          ({'source':'请填写真实来源'},'0,0,0\n1,1,1'),({},'1,0,0\n0,1,1')]:
            with self.subTest(meta=meta,rows=rows):
                with self.assertRaises(ProjectError): read_measured_track(self.measured_file(meta,rows))

    def test_parameter_profiles_do_not_change_route_or_mutate_original(self):
        original=Project.demo(); before=original.to_dict()
        measured=copy.deepcopy(original); measured.vehicle.wheelbase=135; measured.cam.phase=12
        profile=parameter_profile(measured,'自动测试参数配置（非实测）')
        loaded=apply_parameter_profile(original,json.loads(json.dumps(profile)))
        self.assertEqual(loaded.vehicle.wheelbase,135)
        self.assertEqual(loaded.cam.phase,12)
        self.assertEqual(loaded.route.waypoints,original.route.waypoints)
        self.assertEqual(original.to_dict(),before)
        with self.assertRaises(ProjectError): apply_parameter_profile(original,[])
        measured.vehicle.wheelbase=-1
        with self.assertRaises(ProjectError): parameter_profile(measured,'invalid')

    def test_perturbation_keeps_the_actual_dxf_fixed_and_wheel_change_alters_motion(self):
        p=Project.demo(); p.route.waypoints=[[300,400],[1300,400]]; p.cam.points=360
        r=calculate(p); files=export_design(r,self.folder)
        digest=hashlib.sha256(files['dxf'].read_bytes()).hexdigest()
        rows=perturbation_analysis(r,files['dxf'],0,1,0,0,2,4)
        self.assertEqual(len(rows),2)
        self.assertTrue(all(row['dxf_sha256']==digest and row['prediction'] for row in rows))
        self.assertAlmostEqual(rows[1]['path'][-1][0]-rows[0]['path'][-1][0],10,delta=.1)
        self.assertEqual(hashlib.sha256(files['dxf'].read_bytes()).hexdigest(),digest)

    def test_perturbation_respects_user_range_validation_and_cancellation(self):
        with self.assertRaises(ProjectError): perturbation_analysis(self.normal,self.folder/'unused',-100,1,0,0)
        with self.assertRaisesRegex(ProjectError,'停止'):
            perturbation_analysis(self.normal,self.folder/'unused',0,0,0,0,cancelled=lambda:True)

    def test_calculate_and_optimization_support_cancellation(self):
        with self.assertRaisesRegex(ProjectError,'停止'): calculate(Project.demo(),cancelled=lambda:True)
        with self.assertRaisesRegex(ProjectError,'停止'): optimize(Project.demo(),cancelled=lambda:True)

    def test_ordered_track_deviations_do_not_use_an_earlier_lap_for_a_later_point(self):
        path=np.array([[0.,0.],[10.,0.],[20.,0.],[0.,0.]])
        rows=ordered_deviations(path,[[0,0],[20,0],[0,0]],.1)
        self.assertTrue(all(r['passed'] for r in rows))
        self.assertAlmostEqual(rows[-1]['position'],3)
        rows=ordered_deviations(np.array([[0.,0.],[20.,0.]]),[[20,0],[0,0]],.1)
        self.assertFalse(rows[-1]['passed'])

    def test_old_project_schema_one_is_still_compatible(self):
        path=Path(__file__).resolve().parents[2]/'太阳能小车凸轮设计工作台_v1.1.0'/'演示项目.cam.json'
        if path.is_file():
            before=hashlib.sha256(path.read_bytes()).digest()
            project=Project.load(path)
            project.save(self.folder/'copy.cam.json')
            self.assertEqual(Project.load(self.folder/'copy.cam.json').to_dict(),project.to_dict())
            self.assertEqual(hashlib.sha256(path.read_bytes()).digest(),before)
        else:
            self.assertEqual(Project.from_dict(Project.demo().to_dict()).to_dict()['version'],1)


if __name__=='__main__': unittest.main()
