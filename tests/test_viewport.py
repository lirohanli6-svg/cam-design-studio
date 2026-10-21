import copy
import unittest
from camtool.viewport import Viewport,grid_step
from camtool.project import Project


class ViewportTests(unittest.TestCase):
    def setUp(self):
        self.view=Viewport();self.view.prepare((-100,6100,-100,2100),900,600)

    def test_roundtrip_and_equal_scale(self):
        v=self.view
        for point in [(410.,420.),(-2000.,9900.),(0.,0.)]:
            actual=v.unxy(*v.xy(*point))
            for a,b in zip(actual,point):self.assertAlmostEqual(a,b,places=9)
        x,y=v.xy(10,10);x0,y0=v.xy(0,0)
        self.assertAlmostEqual(x-x0,y0-y)

    def test_cursor_anchor_survives_continuous_zoom_and_limits(self):
        v=self.view;anchor=v.unxy(221,313)
        for factor in [1.2]*80+[1/1.2]*160:
            v.zoom_at(factor,221,313)
            for a,b in zip(v.unxy(221,313),anchor):self.assertAlmostEqual(a,b,places=7)
        self.assertGreaterEqual(v.zoom,.05);self.assertLessEqual(v.zoom,512)

    def test_pan_is_pixel_delta_and_preserves_scale(self):
        v=self.view;before=v.xy(410,420);s=v.scale
        v.pan(70,-23);after=v.xy(410,420)
        self.assertAlmostEqual(after[0]-before[0],70);self.assertAlmostEqual(after[1]-before[1],-23)
        self.assertEqual(s,v.scale)

    def test_resize_and_new_frame_bounds_do_not_refit_existing_camera(self):
        v=self.view;v.zoom_at(3,100,200);v.pan(20,30);old=(v.cx,v.cy,v.zoom,v.home)
        v.prepare((-10000,10000,-10000,10000),1100,700)
        self.assertEqual((v.cx,v.cy,v.zoom,v.home),old)
        self.assertEqual(v.xy(v.cx,v.cy),(550,350))

    def test_restore_fit_and_focus_have_explicit_distinct_effects(self):
        v=self.view;home=v.home;v.focus(410,420,30)
        self.assertAlmostEqual(v.cx,410);self.assertGreater(v.zoom,1)
        v.restore();self.assertEqual(v.home,home);self.assertEqual(v.zoom,1)
        v.fit((0,100,0,100));self.assertEqual((v.cx,v.cy,v.zoom),(50,50,1))

    def test_camera_never_mutates_project_or_dimension_units(self):
        p=Project.demo();before=copy.deepcopy(p.to_dict())
        self.view.zoom_at(10,100,100);self.view.pan(1000,-1000);self.view.restore()
        self.assertEqual(p.to_dict(),before)

    def test_grid_tracks_nice_world_unit_scale(self):
        for scale in [.001,.01,1,10,10000]:
            step=grid_step(scale)
            self.assertGreaterEqual(step*scale,90);self.assertLessEqual(step*scale,450)

    def test_invalid_zoom_does_not_change_view(self):
        v=self.view;before=copy.deepcopy(v)
        for factor in [float('nan'),float('inf'),-1,0]:v.zoom_at(factor,10,10)
        self.assertEqual(v,before)
