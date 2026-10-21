import copy
import hashlib
import tempfile
import unittest
from pathlib import Path
from camtool import Project,calculate,export_design
from camtool.route_metrics import ordered_deviations

FIXTURE=Path(__file__).parent/'fixtures/required_loop.cam.json'


class RequiredPointsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.project=Project.load(FIXTURE)
        cls.result=calculate(cls.project)

    def test_user_loop_all_ten_visits_fit_original_twenty_mm_tolerance(self):
        r=self.result
        self.assertEqual(len(r.checkpoint_rows),10)
        self.assertTrue(all(row['passed'] for row in r.checkpoint_rows))
        self.assertLess(r.stats['checkpoint_error'],20.)
        self.assertEqual(r.sampling['requested'],1200)
        self.assertEqual(r.sampling['used'],4000)
        self.assertGreater(r.sampling['attempts'][0]['checkpoint_error_mm'],100.)
        self.assertEqual(r.project.to_dict(),self.project.to_dict())
        self.assertEqual(r.project.route.waypoints[0],r.project.route.waypoints[-1])

    def test_auto_density_is_explicit_and_does_not_imply_hardware_pass(self):
        r=self.result
        self.assertTrue(r.sampling['passed'])
        self.assertFalse(r.valid)
        for name in ('前轮转角','压力角','局部过切','全局接触干涉'):
            self.assertTrue(any(c.name==name and c.status=='fail' for c in r.checks))
        self.assertTrue(any(c.name=='顺序打卡' and c.status=='pass' for c in r.checks))
        self.assertEqual(r.precision['cam_points'],[4000,4000])
        self.assertFalse(r.precision['refined'])
        self.assertFalse(r.precision['stable'])

    def test_tighter_tolerance_stays_failed_at_density_cap(self):
        p=copy.deepcopy(self.project);p.route.tolerance=.1
        r=calculate(p,precision=False)
        self.assertEqual(r.sampling['used'],4000)
        self.assertFalse(r.sampling['passed'])
        self.assertTrue(any(not row['passed'] for row in r.checkpoint_rows))
        self.assertTrue(any(c.name=='顺序打卡' and c.status=='fail' for c in r.checks))

    def test_normal_design_keeps_requested_density(self):
        r=calculate(Project.demo(),precision=False)
        self.assertEqual(r.sampling['used'],1200)
        self.assertFalse(r.sampling['automatic'])

    def test_distance_uses_ordered_segments_instead_of_only_vertices(self):
        import numpy as np
        rows=ordered_deviations(np.array([[0.,0.],[10.,0.],[0.,0.]]),[[0.,0.],[5.,0.],[0.,0.]],.001)
        self.assertTrue(all(row['passed'] for row in rows))
        self.assertGreater(rows[-1]['position'],rows[0]['position'])

    def test_export_uses_actual_outline_density_and_records_each_visit(self):
        import ezdxf
        original=hashlib.sha256(FIXTURE.read_bytes()).hexdigest()
        with tempfile.TemporaryDirectory() as folder:
            files=export_design(self.result,Path(folder))
            outline=list(ezdxf.readfile(files['dxf']).modelspace().query('LWPOLYLINE[layer=="CAM_OUTLINE"]'))[0]
            self.assertEqual(len(outline),4000)
            self.assertEqual(Project.load(files['project']).to_dict(),self.project.to_dict())
            text=files['report'].read_text(encoding='utf-8')
            self.assertIn('1200',text);self.assertIn('4000',text)
            self.assertIn('采样加密与逐点打卡',text)
        self.assertEqual(hashlib.sha256(FIXTURE.read_bytes()).hexdigest(),original)
