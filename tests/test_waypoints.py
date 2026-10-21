import copy
import unittest

from camtool.project import ProjectError
from camtool.waypoints import WaypointDocument, coordinate, proximity_notice


class WaypointDocumentTests(unittest.TestCase):
    def setUp(self):
        self.notifications=[]
        self.doc=WaypointDocument(lambda:self.notifications.append(self.doc.snapshot()))
        self.original={'points':[[1.123456789,2.],[30.,40.],[50.,60.]],'tangents':[.123456789,0.,0.]}
        self.doc.load(self.original)

    def test_finite_coordinate_text_rejects_empty_words_nan_and_infinity(self):
        for text in ('','  ','no','NaN','inf','-Infinity','1e9999'):
            with self.subTest(text=text),self.assertRaises(ProjectError): coordinate(text)
        self.assertEqual(coordinate(' 1.2e3 '),1200.)

    def test_one_commit_one_undo_preserves_coordinate_and_tangent_precision(self):
        points=copy.deepcopy(self.original['points']); points[1][0]=31.123456789
        self.assertTrue(self.doc.apply(points)); self.assertEqual(len(self.notifications),1)
        self.assertEqual(self.doc.tangents,[])
        self.assertTrue(self.doc.travel()); self.assertEqual(self.doc.snapshot(),self.original)
        self.assertFalse(self.doc.travel())
        self.assertTrue(self.doc.travel(True)); self.assertEqual(self.doc.points,points)

    def test_no_numeric_change_preserves_history_redo_tangents_and_notifications(self):
        self.assertFalse(self.doc.apply(copy.deepcopy(self.original['points'])))
        self.assertEqual(self.doc.snapshot(),self.original); self.assertFalse(self.doc.travel())
        self.assertFalse(self.notifications)

    def test_invalid_committed_geometry_cannot_change_snapshot(self):
        for points in ([[float('nan'),0]],[[float('inf'),0]],[[1.]],[[1.,2.]]*201):
            with self.assertRaises(ProjectError): self.doc.apply(points)
            self.assertEqual(self.doc.snapshot(),self.original)
        self.assertFalse(self.notifications)

    def test_multiple_map_previews_form_only_one_gesture_record(self):
        for value in (32.,33.,34.):
            points=copy.deepcopy(self.doc.points); points[1][0]=value
            self.doc.apply(points,remember=False)
        self.doc.finish_gesture(self.original)
        self.assertTrue(self.doc.travel()); self.assertEqual(self.doc.snapshot(),self.original)
        self.assertFalse(self.doc.travel())

    def test_independent_append_never_overwrites_an_existing_point(self):
        points=copy.deepcopy(self.doc.points)+[[90.,100.]]
        self.doc.apply(points); self.assertEqual(self.doc.points[:-1],self.original['points'])
        self.doc.travel(); self.assertEqual(self.doc.snapshot(),self.original)

    def test_adjacent_duplicates_warn_but_return_to_start_is_retained(self):
        adjacent=[[1.,2.],[1.,2.],[3.,4.]]
        self.doc.apply(adjacent); self.assertIn('相邻',proximity_notice(self.doc.points))
        closed=[[1.,2.],[3.,4.],[1.,2.]]
        self.doc.apply(closed); self.assertIn('首尾',proximity_notice(self.doc.points))
        self.assertEqual(len(self.doc.points),3)

    def test_snapshots_are_detached_and_project_load_resets_history(self):
        snapshot=self.doc.snapshot(); snapshot['points'][0][0]=999
        self.assertNotEqual(self.doc.points[0][0],999)
        self.doc.apply([[1.,2.],[3.,4.]])
        self.doc.load(self.original); self.assertFalse(self.doc.travel())


if __name__=='__main__': unittest.main()
