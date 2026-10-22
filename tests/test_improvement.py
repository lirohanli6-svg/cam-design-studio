"""Permission and numerical evidence tests for the design improvement assistant."""
import copy
import hashlib
import json
import math
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

from camtool import Project, ProjectError, calculate
from camtool.improvement import (Adjustment, Candidate, TrialSettings, assert_permissions, compare,
                                physical_cost, run_improvement, software_pass)
from camtool.workbench import demo_project


class ImprovementTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.project=demo_project();cls.project.cam.base_radius=25.
        # This fixture verifies a completed actual-DXF audit, not a 90-second
        # performance promise. Budget expiry/cancellation has separate tests.
        cls.settings=TrialSettings([Adjustment('cam.base_radius',25,60)],mode='structure',allow_installation_change=True,evaluations=4,seconds=300,audit_top=1)
        cls.outcome=run_improvement(cls.project,cls.settings)
        cls.candidate=cls.outcome.candidates[0]

    def test_real_structure_trial_resolves_pressure_without_relaxing_limit(self):
        o=self.outcome;c=self.candidate
        self.assertEqual({x.name for x in o.baseline.checks if x.status=='fail'},{'压力角'})
        self.assertGreater(o.baseline.stats['max_pressure'],45)
        self.assertTrue(software_pass(c.result) and c.independent_pass)
        self.assertLess(c.result.stats['max_pressure'],45)
        self.assertEqual(c.project.cam.pressure_limit,45)
        self.assertEqual(c.project.cam.base_radius,42.5)
        self.assertEqual(o.original_project.to_dict(),self.project.to_dict())

    def test_only_authorized_scalar_changes_and_sampling_matches(self):
        c=self.candidate;o=self.outcome
        assert_permissions(self.project,c.project,self.settings)
        expected=self.project.to_dict();expected['cam']['base_radius']=42.5
        self.assertEqual(c.project.to_dict(),expected)
        for row in o.trials:
            q=Project.from_dict(row['project']);assert_permissions(self.project,q,self.settings)
            self.assertTrue(25<=q.cam.base_radius<=60)
        a,b=c.comparison['baseline_sampling'],c.comparison['candidate_sampling']
        self.assertEqual(a['requested'],b['requested']);self.assertEqual(a['route_step'],b['route_step'])
        self.assertEqual(self.project.cam.base_radius,25)

    def test_locked_limits_vehicle_route_and_sampling_are_enforced(self):
        for group,key,number in [('cam','pressure_limit',80),('vehicle','drive_diameter',200),('vehicle','max_steer',70),
                                 ('route','tolerance',50),('route','step',2),('cam','points',4000),('mechanism','steer_zero',1)]:
            q=copy.deepcopy(self.candidate.project);setattr(getattr(q,group),key,number)
            with self.assertRaisesRegex(ProjectError,'未授权'):assert_permissions(self.project,q,self.settings)
        q=copy.deepcopy(self.candidate.project);q.route.waypoints.reverse()
        with self.assertRaises(ProjectError):assert_permissions(self.project,q,self.settings)

    def test_defaults_modes_and_inapplicable_parameters_remain_locked(self):
        with self.assertRaisesRegex(ProjectError,'锁定'):TrialSettings().validate(self.project)
        with self.assertRaisesRegex(ProjectError,'模式'):TrialSettings([Adjustment('cam.base_radius',25,60)]).validate(self.project)
        ball=copy.deepcopy(self.project);ball.mechanism.kind='ball'
        with self.assertRaisesRegex(ProjectError,'机构'):TrialSettings([Adjustment('mechanism.offset',-5,5)],mode='structure',allow_installation_change=True).validate(ball)
        for key in ('mechanism.link','cam.ratio','vehicle.drive_diameter','vehicle.passive_diameter','cam.pressure_limit'):
            with self.assertRaisesRegex(ProjectError,'未支持'):TrialSettings([Adjustment(key,1,100)],mode='structure',allow_installation_change=True).validate(self.project)

    def test_invalid_ranges_and_budgets_cannot_start(self):
        cases=[TrialSettings([Adjustment('route.tension',float('nan'),1)]),
               TrialSettings([Adjustment('route.tension',1,1.3)]),
               TrialSettings([Adjustment('route.tension',.2,2)]),
               TrialSettings([Adjustment('route.tension',.2,1),Adjustment('route.tension',.2,1)]),
               TrialSettings([Adjustment('route.tension',.75,.75)]),
               TrialSettings([Adjustment('route.tension',.2,1)],evaluations=1.5),
               TrialSettings([Adjustment('route.tension',.2,1)],seconds=float('inf'))]
        for settings in cases:
            with self.assertRaises(ProjectError):settings.validate(self.project)

    def test_installation_coupling_agrees_with_flat_neutral_geometry(self):
        before=physical_cost(self.outcome.baseline);after=physical_cost(self.candidate.result)
        # Analytic flat neutral: pin X = -25+25=0; link rises 80 mm.
        # Contact Y = base+15+2; pivot Y = contact Y-80.
        self.assertAlmostEqual(before['neutral_contact_y_mm'],42)
        self.assertAlmostEqual(before['pivot_y_mm'],-38)
        self.assertAlmostEqual(after['pivot_y_mm'],-20.5)
        self.assertAlmostEqual(after['pivot_y_mm']-before['pivot_y_mm'],17.5)
        self.assertIn('联动安装位置',self.outcome.report(self.candidate))

    def test_arm_length_does_not_reduce_fixed_route_required_front_angle(self):
        a=demo_project();b=copy.deepcopy(a);b.mechanism.arm=27
        r1=calculate(a,precision=False);r2=calculate(b,precision=False)
        np.testing.assert_array_equal(r1.delta,r2.delta)
        np.testing.assert_array_equal(r1.target_path,r2.target_path)

    def test_full_cycle_pose_preserves_return_scope_without_fake_route_location(self):
        r=copy.deepcopy(self.outcome.baseline);r.pressure[-1]=100
        data=compare(r,r)
        row=next(x['after'] for x in data['checks'] if x['name']=='压力角')
        self.assertIn('回程',row['scope']);self.assertIsNone(row['point']);self.assertIsNone(row['route_index'])
        self.assertIsNotNone(row['pose']);self.assertEqual(row['angle_deg'],360.)

    def test_new_failures_and_worse_metrics_are_not_hidden(self):
        after=copy.deepcopy(self.candidate.result)
        from camtool.engine import Check
        after.checks.append(Check('新增测试失败','fail','构造边界测试'))
        after.stats['max_steer']=self.outcome.baseline.stats['max_steer']+1
        data=compare(self.outcome.baseline,after)
        self.assertIn('压力角',data['resolved']);self.assertIn('新增测试失败',data['new_failures'])
        self.assertTrue(any(row['metric']=='max_steer' for row in data['worsened_metrics']))

    def test_single_probes_precede_limited_promising_combinations(self):
        settings=TrialSettings([Adjustment('cam.base_radius',25,60),Adjustment('route.tension',.75,1.3)],
                               mode='structure',allow_installation_change=True,evaluations=8,seconds=30,audit_top=1)
        with patch('camtool.improvement.audit_candidate',side_effect=PermissionError(13,'测试审计文件不可读','C:/Users/private-person/cam.dxf')):
            outcome=run_improvement(self.project,settings)
        phases=[r['phase'] for r in outcome.trials]
        self.assertIn('有局部改善参数的有限组合',phases)
        first=phases.index('有局部改善参数的有限组合')
        self.assertTrue(all(p=='单参数试算' for p in phases[:first]))
        self.assertTrue(all(len(r['modifications'])==1 for r in outcome.trials[:first]))
        self.assertFalse(outcome.candidates[0].independent_pass)
        self.assertTrue(outcome.candidates[0].validation_error)
        self.assertNotIn('private-person',outcome.report())
        self.assertIn('读写失败',outcome.candidates[0].validation_error)

    def test_invalid_geometry_is_recorded_and_remaining_probes_continue(self):
        settings=TrialSettings([Adjustment('mechanism.pivot_x',-150,-25)],mode='structure',allow_installation_change=True,evaluations=3,seconds=20,audit_top=1)
        with patch('camtool.improvement.audit_candidate',side_effect=ProjectError('测试明确跳过审计')):
            outcome=run_improvement(self.project,settings)
        self.assertEqual(len(outcome.trials),2)
        self.assertTrue(any('error' in t for t in outcome.trials))
        self.assertEqual(self.project.mechanism.pivot_x,-25)

    def test_uncomputable_baseline_and_no_candidate_are_truthful(self):
        p=copy.deepcopy(self.project);p.mechanism.pivot_x=-200
        settings=TrialSettings([Adjustment('mechanism.pivot_x',-210,-190)],mode='structure',allow_installation_change=True,evaluations=3,seconds=10,audit_top=1)
        outcome=run_improvement(p,settings)
        self.assertIsNone(outcome.baseline);self.assertTrue(outcome.baseline_error)
        self.assertFalse(outcome.candidates);self.assertIn('不代表不存在',outcome.stop_reason)
        self.assertTrue(all('error' in t for t in outcome.trials))

    def test_no_improvement_remains_explicit(self):
        p=demo_project();p.route.waypoints=[[300.,400.],[1300.,400.]]
        settings=TrialSettings([Adjustment('route.tension',.75,1.0)],evaluations=3,seconds=20,audit_top=1)
        with patch('camtool.improvement.audit_candidate',side_effect=ProjectError('测试明确跳过审计')):
            outcome=run_improvement(p,settings)
        self.assertIn('未确认指标改善',outcome.stop_reason)
        self.assertFalse(outcome.candidates[0].comparison['resolved'])

    def test_cancel_at_start_and_during_trial_never_returns_a_candidate(self):
        with self.assertRaisesRegex(ProjectError,'取消'):run_improvement(self.project,self.settings,lambda:True)
        stop=threading.Event()
        def progress(text):
            if '阶段2' in text:stop.set()
        before=self.project.to_dict()
        with self.assertRaisesRegex(ProjectError,'取消'):run_improvement(self.project,self.settings,stop.is_set,progress)
        self.assertEqual(self.project.to_dict(),before)

    def test_time_budget_can_end_with_coarse_only_and_no_final_pass(self):
        now=[0.];real_calculate=calculate
        def compute(project,cancelled,**kwargs):
            result=real_calculate(project,cancelled,**kwargs)
            if kwargs.get('precision') is False:now[0]=2.
            return result
        settings=TrialSettings([Adjustment('cam.base_radius',25,60)],mode='structure',allow_installation_change=True,seconds=1,audit_top=1)
        with patch('camtool.improvement.time.monotonic',side_effect=lambda:now[0]),patch('camtool.improvement.calculate',side_effect=compute):
            outcome=run_improvement(self.project,settings)
        self.assertEqual(len(outcome.trials),1)
        self.assertIsNone(outcome.candidates[0].result)
        self.assertIn('未完成验证',outcome.candidates[0].status)
        self.assertIn('时间预算用尽',outcome.stop_reason)

    def test_export_keeps_exact_audited_bytes_and_rejects_forged_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            folder=Path(directory)/'plan';self.outcome.export(folder)
            files=list(folder.glob('candidate-*/design/cam.dxf'));self.assertEqual(len(files),1)
            self.assertEqual(hashlib.sha256(files[0].read_bytes()).hexdigest(),self.candidate.audit.sha256)
            report=(folder/'修改方案.md').read_text(encoding='utf-8')
            self.assertNotIn(directory,report);self.assertIn('原案保留',report)
            record=json.loads((folder/'方案与试算记录.json').read_text(encoding='utf-8'))
            self.assertEqual(record['settings']['adjustments'][0]['key'],'cam.base_radius')
            self.assertIsNone(record['random_seed'])
            with self.assertRaises(ProjectError):self.outcome.export(folder)
            broken=copy.deepcopy(self.outcome);broken.candidates[0].dxf=b'not the audited file'
            with self.assertRaisesRegex(ProjectError,'哈希'):broken.export(Path(directory)/'broken')
            self.assertFalse((Path(directory)/'broken').exists())


if __name__=='__main__':unittest.main()
