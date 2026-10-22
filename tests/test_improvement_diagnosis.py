"""Diagnostic rules: real theory result plus labelled synthetic interval fixtures."""
from __future__ import annotations
import copy
import math
import unittest
import numpy as np
from camtool.engine import calculate, validate, Check
from camtool.improvement import diagnose, diagnosis_text, exploration_range, PARAMETERS, TrialSettings, Adjustment, assert_permissions
from camtool.project import ProjectError
from camtool.workbench import demo_project,diagnostics


class DiagnosisTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Deterministic built-in theory input, not the user's screenshot/project.
        p=demo_project();p.cam.base_radius=25
        cls.result=calculate(p)

    def test_real_pressure_facts_precede_permission_and_do_not_claim_root_cause(self):
        r=self.result;original=r.project.to_dict();d=diagnose(r)
        issue=next(x for x in d['problems'] if x['name']=='压力角')
        self.assertAlmostEqual(issue['diagnostic']['measurements']['actual_deg'],r.pressure.max())
        self.assertEqual(issue['diagnostic']['measurements']['limit_deg'],45)
        self.assertIsNotNone(issue['diagnostic']['pose'])
        self.assertIn('cam.base_radius',issue['parameters'])
        text=diagnosis_text(d)
        self.assertIn('待验证原因',text);self.assertIn('增大基圆不保证改善',text)
        self.assertNotIn('建议值：',text)
        self.assertEqual(original,r.project.to_dict())

    def stroke_fixture(self,low,high):
        # Synthetic sampled stroke data only, with a known 180-degree run boundary.
        # This verifies interval arithmetic/location rules, not physical feasibility.
        r=copy.deepcopy(self.result);p=r.project;p.mechanism.min_stroke=-15;p.mechanism.max_stroke=15
        r.checks=[Check('完整周期行程','fail','synthetic interval fixture')]
        r.stroke=np.array([0.,low,high]);r.contact_centers=np.column_stack([np.zeros(3),r.stroke])
        r.cam_angle=np.radians([0.,120.,300.]);r.cam_delta=np.zeros(3)
        r.drive_s=np.array([0.,math.pi*p.vehicle.drive_diameter*p.cam.ratio*.5])
        return r

    def test_known_span_fits_but_positive_end_exceeds_and_return_has_no_route(self):
        r=self.stroke_fixture(-12.27,16.83);issue=diagnose(r)['problems'][0];m=issue['diagnostic']['measurements']
        self.assertAlmostEqual(m['span_mm'],29.10);self.assertEqual(m['available_span_mm'],30)
        self.assertAlmostEqual(m['positive_excess_mm'],1.83);self.assertEqual(m['negative_excess_mm'],0)
        self.assertEqual(m['classification'],'跨度可容纳但中位分配越界')
        self.assertIsNone(m['maximum_location']['point']);self.assertIn('回程',m['maximum_location']['scope'])
        self.assertIsNotNone(m['minimum_location']['point'])
        self.assertIn('总跨度可容纳',issue['hypotheses'][0])
        self.assertIn('不能仅修改报告零点',diagnosis_text(diagnose(r)))

    def test_known_span_insufficient_and_both_ends_have_excess(self):
        issue=diagnose(self.stroke_fixture(-20.,22.))['problems'][0];m=issue['diagnostic']['measurements']
        self.assertEqual(m['span_mm'],42);self.assertEqual(m['negative_excess_mm'],5);self.assertEqual(m['positive_excess_mm'],7)
        self.assertEqual(m['classification'],'总跨度不足');self.assertIn('单纯重分配中位不能容纳',issue['hypotheses'][0])

    def test_return_steer_recommends_route_not_arm_to_reduce_required_angle(self):
        r=self.stroke_fixture(-1,1);r.checks=[Check('完整周期转向','fail','synthetic return peak')]
        r.cam_delta=np.radians([0.,1.,50.]);issue=diagnose(r)['problems'][0]
        self.assertIn('回程',issue['diagnostic']['scope']);self.assertIsNone(issue['diagnostic']['point'])
        self.assertEqual(issue['parameters'],['route.tension'])
        self.assertIn('不能直接减少固定路线',diagnosis_text(diagnose(r)))

    def test_ball_rules_exclude_flat_offset_link_and_passive_wheel_phase(self):
        # Dependency-rule adaptation only; no claim this synthetic ball geometry passes.
        r=copy.deepcopy(self.result);r.project.mechanism.kind='ball'
        data=diagnose(r);self.assertNotIn('mechanism.offset',data['recommended'])
        self.assertNotIn('mechanism.link',data['recommended'])
        self.assertFalse(any('passive' in k for k in data['recommended']))
        self.assertIn('后从动轮直径在当前模型中不改变凸轮相位',data['transmission']['note'])
        self.assertIn('摆臂球心圆弧',diagnosis_text(data))

    def test_undercut_and_global_interference_are_separate_and_do_not_prove_common_cause(self):
        # Labelled diagnostic-record fixture: deliberately retain both statuses.
        r=copy.deepcopy(self.result)
        r.checks=[Check('局部过切','fail','synthetic local failure'),Check('全局接触干涉','fail','synthetic contact failure')]
        data=diagnose(r);text=diagnosis_text(data)
        self.assertEqual(len(data['problems']),2);self.assertIn('不同检查',text)
        self.assertIn('非邻近轮廓侵入',text);self.assertIn('相近不证明同源',text)
        self.assertIsInstance(data['correlation']['separation_deg'],float)
        self.assertIn('不能靠减小接触半径',text)

    def test_proposed_ranges_include_current_and_are_exploration_not_safe(self):
        p=self.result.project;before=p.to_dict()
        for spec in PARAMETERS:
            d=exploration_range(p,spec.key)
            self.assertLessEqual(d['lower'],d['current']);self.assertGreaterEqual(d['upper'],d['current'])
            self.assertIn('探索暂定',d['source']);self.assertIn('不是安全范围',d['source'])
        self.assertEqual(p.to_dict(),before)

    def test_invalid_fields_are_structured_and_do_not_require_report_parsing(self):
        for key,bad in [('vehicle.wheelbase',0),('cam.base_radius',float('nan')),('route.step',.1)]:
            p=copy.deepcopy(self.result.project);g,n=key.split('.');setattr(getattr(p,g),n,bad)
            with self.assertRaises(ProjectError) as caught:validate(p)
            self.assertIn(key,caught.exception.fields)

    def test_all_original_check_statuses_and_stroke_pose_survive_structured_extension(self):
        r=self.result;rows=diagnostics(r)
        self.assertEqual([(x.name,x.status) for x in rows],[(x.name,x.status) for x in r.checks])
        self.assertIn('span_mm',next(x for x in rows if x.name=='完整周期行程').measurements)

    def test_structure_must_explicitly_authorize_implicit_installation(self):
        p=self.result.project;s=TrialSettings([Adjustment('cam.base_radius',25,40)],mode='structure')
        with self.assertRaisesRegex(ProjectError,'安装'):s.validate(p)
        q=copy.deepcopy(p);q.cam.base_radius=40
        with self.assertRaisesRegex(ProjectError,'未授权安装'):assert_permissions(p,q,s)
        s.allow_installation_change=True;s.validate(p);assert_permissions(p,q,s)

    def test_route_only_never_grants_hardware_or_installation_by_implication(self):
        p=self.result.project;s=TrialSettings([Adjustment('route.tension',.5,1.)])
        s.validate(p);self.assertFalse(s.allow_installation_change)
        s.allow_installation_change='yes'
        with self.assertRaisesRegex(ProjectError,'明确'):s.validate(p)

if __name__=='__main__':unittest.main()
