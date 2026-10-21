import copy
import unittest

import numpy as np

from camtool.auxiliary_editor import AuxiliaryDocument
from camtool.project import Project, ProjectError
from camtool.route_shape import sample_route
from camtool.waypoints import WaypointDocument


class AuxiliaryDocumentTests(unittest.TestCase):
    def setUp(self):
        self.notices=[]
        self.doc=AuxiliaryDocument(lambda:self.notices.append(self.doc.snapshot()))
        self.rows=[[0.,600.123456789,500.],[2.,2000.,900.],[1.,1600.,800.]]
        self.doc.load_controls(self.rows)
        self.original=self.doc.snapshot()

    def test_storage_conversion_preserves_segments_and_precision(self):
        self.assertEqual(self.doc.controls(),self.rows)
        changed=self.doc.snapshot()['points'];changed[0][0]+=1
        self.doc.apply(changed)
        self.assertEqual(self.doc.controls()[0],[0.,601.123456789,500.])
        self.doc.travel();self.assertEqual(self.doc.controls(),self.rows)

    def test_sort_changes_only_display_order_and_route_is_identical(self):
        p=Project.demo();p.route.auxiliary_points=self.doc.controls()
        before=sample_route(p,0.)
        self.doc.apply(list(reversed(self.doc.points)))
        p.route.auxiliary_points=self.doc.controls()
        self.assertEqual(p.route.auxiliary_points,list(reversed(self.rows)))
        np.testing.assert_array_equal(sample_route(p,0.),before)
        self.doc.travel();self.assertEqual(self.doc.controls(),self.rows)

    def test_invalid_rows_cannot_change_committed_coordinates_or_history(self):
        for points in ([[1.,2.]],[[float('nan'),2.,1.]],[[1.,float('inf'),1.]],
                       [[1.,2.,1.5]],[[1.,2.,0.]],[[1.,2.,1.]]*41):
            with self.subTest(points=points),self.assertRaises(ProjectError):self.doc.apply(points)
            self.assertEqual(self.doc.snapshot(),self.original)
        self.assertFalse(self.doc.history.undo_stack)
        self.assertFalse(self.notices)

    def test_noop_preserves_redo_and_one_map_gesture_one_record(self):
        for dx in [1.,2.,3.]:
            points=copy.deepcopy(self.original['points']);points[0][0]+=dx
            self.doc.apply(points,remember=False)
        self.doc.finish_gesture(self.original)
        self.assertEqual(len(self.doc.history.undo_stack),1)
        self.doc.travel();self.assertEqual(self.doc.snapshot(),self.original)
        self.doc.apply(copy.deepcopy(self.doc.points))
        self.assertEqual(len(self.doc.history.redo_stack),1)
        self.doc.travel(True);self.assertAlmostEqual(self.doc.points[0][0],603.123456789)

    def test_required_and_soft_documents_never_share_lists_or_history(self):
        required=WaypointDocument(lambda:None)
        closed=[[410.,420.],[700.,1580.],[410.,420.]]
        required.load({'points':closed,'tangents':[.123456789]})
        required.apply([*closed, [1000.,1000.]])
        before=required.snapshot();history=copy.deepcopy(required.history)
        self.doc.apply(self.doc.points[:-1]);self.doc.travel();self.doc.travel(True)
        self.assertEqual(required.snapshot(),before)
        self.assertEqual(required.history,history)
        auxiliary=self.doc.snapshot()
        required.travel();self.assertEqual(required.points,closed)
        self.assertEqual(self.doc.snapshot(),auxiliary)

    def test_reload_resets_only_this_document_and_snapshots_are_detached(self):
        self.doc.apply(self.doc.points[:-1]);snapshot=self.doc.snapshot()
        snapshot['points'][0][0]=999
        self.assertNotEqual(self.doc.points[0][0],999)
        self.doc.load_controls(self.rows)
        self.assertFalse(self.doc.travel())
        self.assertEqual(self.doc.controls(),self.rows)


if __name__=='__main__':unittest.main()
