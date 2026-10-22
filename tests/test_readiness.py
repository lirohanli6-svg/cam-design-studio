"""Workflow claims and interrupted deliveries must remain trustworthy."""
import copy
import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from camtool import calculate, export_design, ProjectError
from camtool.verification import VerificationInput, VerificationResult, export_verification
from camtool.workbench import demo_project, diagnostics
from camtool.workflow import candidate_explanation, workflow_state
from camtool.auxiliary_search import SearchSettings, search_auxiliaries


class ReadinessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.normal = calculate(demo_project())
        cls.problem = calculate(demo_project(True))

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)

    def test_four_claims_do_not_promote_nominal_or_missing_precision_to_pass(self):
        self.assertEqual(workflow_state(None).action, 'calculate')
        state = workflow_state(self.problem)
        self.assertIn('项不满足', state.stages)
        self.assertIn('实车：未验证', state.stages)
        self.assertEqual(state.action, 'issues')
        result = copy.deepcopy(self.normal)
        result.precision = {}
        self.assertIn('采样未确认', workflow_state(result).stages)
        self.assertIn('最终 DXF：未检验', workflow_state(result).stages)

    def test_dxf_badge_requires_matching_bytes_project_and_run_interval(self):
        q = VerificationInput.from_project(self.normal.project, self.normal.stats['cam_usage']*360)
        audit = VerificationResult(q, 'cam.dxf', 'actual-dxf-hash')
        self.assertIn('未检验', workflow_state(self.normal, '', audit).stages)
        self.assertIn('另份文件', workflow_state(self.normal, 'other-hash', audit).stages)
        self.assertIn('不通过 / 有未验证项', workflow_state(self.normal, audit.sha256, audit).stages)
        audit.inputs.run_end -= 1
        self.assertIn('另组参数/区间', workflow_state(self.normal, audit.sha256, audit).stages)
        audit.inputs.run_end += 1
        audit.inputs.project.route.tolerance += 1
        self.assertIn('另组参数/区间', workflow_state(self.normal, audit.sha256, audit).stages)

    def test_diagnostics_explain_consequences_and_candidate_keeps_failures(self):
        row = next(d for d in diagnostics(self.problem) if d.name == '前轮转角')
        self.assertIn('机械限位', row.impact)
        self.assertIn('°', row.actual)
        text = candidate_explanation(self.normal.project, self.problem)
        self.assertIn('前轮转角', text)
        self.assertIn('实车未验证', text)
        self.assertIn('比较条件发生变化', text)
        self.assertIn('规划参数未改变', candidate_explanation(self.normal.project, self.normal))

    def test_discoverable_case_has_refined_evidence_without_changing_legacy_demo(self):
        from camtool import Project
        self.assertTrue(self.normal.valid and self.normal.precision['stable'])
        self.assertEqual(Project.demo().route.step, 5.)
        self.assertEqual(self.normal.project.vehicle, Project.demo().vehicle)
        self.assertEqual(self.normal.project.mechanism, Project.demo().mechanism)
        self.assertEqual(self.normal.project.cam.pressure_limit, Project.demo().cam.pressure_limit)
        self.assertEqual(self.normal.project.route.waypoints, Project.demo().route.waypoints)

    def test_failed_staging_does_not_damage_existing_files(self):
        files = export_design(self.normal, self.folder)
        before = {key: p.read_bytes() for key, p in files.items()}
        with patch('camtool.export.report_text', side_effect=OSError('模拟报告写失败')):
            with self.assertRaises(OSError):
                export_design(self.problem, self.folder, overwrite=True)
        self.assertEqual(before, {key: p.read_bytes() for key, p in files.items()})
        self.assertFalse(list(self.folder.glob('.solarcam-export-*')))

    def test_partial_replace_error_rolls_back_original_set_and_extra_file(self):
        files = export_design(self.normal, self.folder)
        before = {key: p.read_bytes() for key, p in files.items()}
        extra = self.folder/'notes.txt'
        extra.write_text('keep', encoding='utf-8')
        replace = os.replace
        def fail_report(source, target):
            if Path(source).name == '检查报告.md' and Path(source).parent.name != 'backup':
                raise PermissionError('模拟目标报告被占用')
            return replace(source, target)
        with patch('camtool.export.os.replace', side_effect=fail_report):
            with self.assertRaises(PermissionError):
                export_design(self.problem, self.folder, overwrite=True)
        self.assertEqual(before, {key: p.read_bytes() for key, p in files.items()})
        self.assertEqual(extra.read_text(encoding='utf-8'), 'keep')

    def test_cancel_during_replacement_removes_new_files_and_restores_originals(self):
        existing = self.folder/'cam.dxf'
        existing.write_bytes(b'previous dxf')
        replace = os.replace
        cancelled = False
        def cancel_after_first(source, target):
            nonlocal cancelled
            value = replace(source, target)
            if Path(target) == existing:
                cancelled = True
            return value
        with patch('camtool.export.os.replace', side_effect=cancel_after_first):
            with self.assertRaisesRegex(ProjectError, '停止'):
                export_design(self.normal, self.folder, overwrite=True, cancelled=lambda:cancelled)
        self.assertEqual(existing.read_bytes(), b'previous dxf')
        self.assertEqual([p.name for p in self.folder.iterdir()], ['cam.dxf'])

    def test_report_exports_basename_and_hash_without_machine_directory(self):
        q = VerificationInput.from_project(self.normal.project)
        audit = VerificationResult(q, str(self.folder/'private-user'/'cam.dxf'), hashlib.sha256(b'checked bytes').hexdigest())
        files = export_verification(audit, self.folder/'report')
        report = files['report'].read_text(encoding='utf-8')
        data = json.loads(files['result'].read_text(encoding='utf-8'))
        self.assertEqual(data['filename'], 'cam.dxf')
        self.assertIn(audit.sha256, report)
        self.assertNotIn(str(self.folder), report)

    def test_search_records_same_precision_fixed_inputs_and_budget_reason(self):
        project = copy.deepcopy(self.normal.project)
        before = project.to_dict()
        outcome = search_auxiliaries(project, SearchSettings(evaluations=1, seconds=10, maximum_points=0))
        self.assertEqual(project.to_dict(), before)
        self.assertTrue(outcome.original.precision['refined'])
        self.assertEqual(outcome.original.project.to_dict(), before)
        self.assertEqual(outcome.candidate.project.vehicle, project.vehicle)
        self.assertEqual(outcome.candidate.project.cam, project.cam)
        self.assertEqual(outcome.candidate.project.route.tolerance, project.route.tolerance)
        self.assertEqual(outcome.stop_reason, '候选预算用尽')
        self.assertGreaterEqual(outcome.elapsed, outcome.search_elapsed)
        outcome.export(self.folder/'search')
        record = json.loads((self.folder/'search/辅助点搜索记录.json').read_text(encoding='utf-8'))
        self.assertIsNone(record['random_seed'])
        self.assertIn('无大模型调用', record['method'])
        self.assertEqual(record['remaining_failures'], [c.name for c in outcome.candidate.checks if c.status=='fail'])
        self.assertIn('original_sampling', record)
        self.assertIn('粗采样筛选', record['trials'][0]['validation_scope'])
        self.assertEqual(record['trials'][0]['project']['vehicle'], before['vehicle'])
        self.assertIn('remaining_failures', record['trials'][0])
        self.assertIn('metric_changes_from_original', record['trials'][0])
        self.assertEqual(hashlib.sha256((self.folder/'search/候选方案/cam.dxf').read_bytes()).hexdigest(), outcome.verification.sha256)

    def test_failed_rollback_retains_original_backup_for_recovery(self):
        files = export_design(self.normal, self.folder)
        before = files['dxf'].read_bytes()
        replace = os.replace
        def fail_and_block_rollback(source, target):
            source = Path(source)
            if source.name == '检查报告.md' or (source.parent.name=='backup' and source.name=='cam.dxf'):
                raise PermissionError('模拟写入及恢复被文件锁阻止')
            return replace(source, target)
        with patch('camtool.export.os.replace', side_effect=fail_and_block_rollback):
            with self.assertRaisesRegex(ProjectError, '回滚失败'):
                export_design(self.problem, self.folder, overwrite=True)
        backups = list(self.folder.glob('.solarcam-export-*/backup/cam.dxf'))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_bytes(), before)


if __name__ == '__main__':
    unittest.main()
