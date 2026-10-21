"""Explicit native UI acceptance for cameras, captured drags and popout sync."""
from __future__ import annotations
import copy
import ctypes
import json
import time
import threading
from pathlib import Path
from typing import TYPE_CHECKING,Any,Callable
from unittest.mock import patch
import numpy as np
from .engine import calculate
from .project import Project
if TYPE_CHECKING:
    from .ui import App
    from .plot_views import FigurePanel


def check_views(app: App,folder: Path,capture: Callable[[str],None] | None=None) -> dict[str,Any]:
    folder.mkdir(parents=True,exist_ok=True);root=app.root
    saved=(app.collect(),app.result,app.file,app.dirty,app.map_edit.get())
    errors=[];old_handler=root.report_callback_exception
    root.report_callback_exception=lambda *args:errors.append(str(args))  # type: ignore[method-assign]
    checks: dict[str,Any]={}
    def pump(delay: float=.01) -> None:
        root.update();time.sleep(delay);root.update();assert not errors,errors
    def draw() -> None:app.render();pump()
    def shot(name: str) -> None:
        draw()
        if capture is not None:capture(name)
    def point_pixels(panel: FigurePanel,index: int) -> tuple[int,int]:
        panel.draw();assert panel.plot is not None
        return tuple(round(v) for v in panel.plot.xy(*app.map_editor().document.points[index][:2]))  # type: ignore[return-value]
    def drag(panel: FigurePanel,index: int,dx: int,dy: int,steps: int=12) -> tuple[float,float]:
        c=panel.canvas;x,y=point_pixels(panel,index);assert panel.plot is not None
        expected=panel.plot.unxy(x+dx,y+dy)
        c.event_generate('<ButtonPress-1>',x=x,y=y);pump()
        for i in range(1,steps+1):
            c.event_generate('<B1-Motion>',x=round(x+dx*i/steps),y=round(y+dy*i/steps));pump(.018)
        c.event_generate('<ButtonRelease-1>',x=x+dx,y=y+dy);pump()
        return expected
    def outside_drag(panel: FigurePanel,index: int) -> None:
        if not hasattr(ctypes,'windll'):raise AssertionError('真实窗口外释放需要Windows，未验证')
        panel.fit();x,y=point_pixels(panel,index);c=panel.canvas;assert panel.plot is not None
        sx,sy=c.winfo_rootx(),c.winfo_rooty();end_x=sx+c.winfo_width()+24;end_y=sy+c.winfo_height()//2
        expected=panel.plot.unxy(end_x-sx,end_y-sy);editor=app.map_editor();before_drag=editor.snapshot()
        class Cursor(ctypes.Structure):_fields_=[('x',ctypes.c_long),('y',ctypes.c_long)]
        old_cursor=Cursor();ctypes.windll.user32.GetCursorPos(ctypes.byref(old_cursor))
        try:
            c.winfo_toplevel().lift();c.focus_force();pump()
            ctypes.windll.user32.SetCursorPos(sx+x,sy+y);ctypes.windll.user32.mouse_event(2,0,0,0,0);pump(.03)
            assert app.drag_canvas==c
            ctypes.windll.user32.SetCursorPos(end_x,end_y);pump(.05)
            ctypes.windll.user32.mouse_event(4,0,0,0,0);pump(.04)
        finally:
            ctypes.windll.user32.mouse_event(4,0,0,0,0);ctypes.windll.user32.SetCursorPos(old_cursor.x,old_cursor.y)
        assert app.drag_canvas is None and c.grab_current() is None
        np.testing.assert_allclose(editor.document.points[index][:2],np.round(expected,3),atol=1e-9)
        editor.undo();assert editor.snapshot()==before_drag

    def camera(panel: FigurePanel) -> tuple[Any,...]:
        v=panel.camera;return v.home,v.cx,v.cy,v.zoom
    try:
        root.geometry('1360x780+20+20');root.deiconify();root.lift()
        p=Project.demo();app.load(p);app.inputs.select(app.waypoints);app.show_result(calculate(p));app.views.select(0);pump();draw()
        scene=app.figure_panels['scene'];canvas=scene.canvas
        before=app.collect().to_dict();revision=app.revision;app.map_edit.set(False);pump()
        x,y=230,170;assert scene.plot is not None;anchor=scene.plot.unxy(x,y)
        for delta in [120]*8+[-120]*6:
            canvas.event_generate('<MouseWheel>',x=x,y=y,delta=delta);pump(.002)
            assert scene.plot is not None
            np.testing.assert_allclose(scene.plot.unxy(x,y),anchor,atol=1e-8,rtol=0)
            assert scene.plot.sx==scene.plot.sy
        old=camera(scene)
        canvas.event_generate('<ButtonPress-2>',x=200,y=180)
        canvas.event_generate('<B2-Motion>',x=240,y=200)
        canvas.event_generate('<ButtonRelease-2>',x=240,y=200);pump()
        assert camera(scene)!=old and app.collect().to_dict()==before and app.revision==revision
        canvas.event_generate('<ButtonPress-1>',x=100,y=100)
        canvas.event_generate('<B1-Motion>',x=120,y=110)
        canvas.event_generate('<ButtonRelease-1>',x=120,y=110);pump()
        assert app.collect().to_dict()==before and not app.dirty
        scene.restore();assert scene.camera.zoom==1
        scene.fit();assert scene.camera.zoom==1
        canvas.event_generate('<Motion>',x=160,y=170);pump();assert 'mm' in scene.hover.get()
        checks['cursor_anchor_equal_scale_pan_browse_safe_hover_and_restore']=True
        # Start with a current calculation: first motion invalidates once.
        app.map_edit.set(True);pump();scene.fit()
        x,y=point_pixels(scene,1);scene.zoom(2,x,y);pump()
        editor=app.waypoints;original=editor.snapshot();view=camera(scene)
        grids=set(canvas.find_withtag('view_grid'));clears=[];rebuild=[];calculations=[]
        old_delete=canvas.delete;old_tree=editor.tree.delete
        def delete(*args: Any) -> Any:
            if 'all' in args:clears.append(args)
            return old_delete(*args)
        def tree_delete(*args: Any) -> Any:rebuild.append(args);return old_tree(*args)
        start=time.perf_counter();frames=app.drag_flushes;motions=app.drag_motions
        with patch.object(canvas,'delete',side_effect=delete),patch.object(editor.tree,'delete',side_effect=tree_delete),patch('camtool.ui.calculate',side_effect=lambda *a,**k:calculations.append(1)):
            expected=drag(scene,1,60,-26)
        elapsed=(time.perf_counter()-start)*1000
        np.testing.assert_allclose(editor.document.points[1],np.round(expected,3),atol=1e-9)
        assert not clears and not rebuild and not calculations
        assert grids==set(canvas.find_withtag('view_grid')) and camera(scene)==view
        assert len(canvas.find_all())==len(canvas.items),'存在未管理的残影'
        assert len(editor.document.history.undo_stack)==1
        assert app.result is None and app.dirty and str(app.export_button['state'])=='disabled'
        assert [float(v) for v in editor.tree.item('1','values')]==editor.document.points[1]
        checks['retained_drag_no_clear_no_table_rebuild_no_calculation']=True
        checks['drag_measurement']={'motions':app.drag_motions-motions,'flushes':app.drag_flushes-frames,
            'elapsed_ms_including_12_scheduled_waits':elapsed,'all_clears':len(clears),'table_rebuilds':len(rebuild),
            'flush_median_ms':float(np.median(app.drag_frame_ms)),'flush_max_ms':max(app.drag_frame_ms)}
        app.undo_route();assert editor.snapshot()==original
        app.redo_route();app.undo_route();assert editor.snapshot()==original
        # No movement or an exact round trip adds no history and loses no precision.
        for roundtrip in (False,True):
            x,y=point_pixels(scene,1);count=len(editor.document.history.undo_stack)
            canvas.event_generate('<ButtonPress-1>',x=x,y=y);pump()
            if roundtrip:
                canvas.event_generate('<B1-Motion>',x=x+20,y=y-10);pump(.03)
            canvas.event_generate('<ButtonRelease-1>',x=x,y=y);pump()
            assert editor.snapshot()==original and len(editor.document.history.undo_stack)==count
        checks['one_drag_one_undo_and_zero_displacement_no_history']=True
        entered=threading.Event();release=threading.Event();stale=calculate(p)
        def delayed(*args: Any,**kwargs: Any) -> Any:
            entered.set();release.wait(10);return stale
        with patch('camtool.ui.calculate',side_effect=delayed),patch.object(app.cancel,'is_set',return_value=False):
            app.run(False);deadline=time.monotonic()+10
            while not entered.is_set():pump();assert time.monotonic()<deadline
            drag(scene,1,25,-10,3);edited=editor.snapshot();release.set()
            while app.busy:pump();assert time.monotonic()<deadline
        assert app.result is None and editor.snapshot()==edited
        editor.undo();assert editor.snapshot()==original
        checks['stale_worker_rejected_after_coalesced_drag']=True
        shot('01-main-zoomed-edit')
        # Real popout with independent camera, shared data and active point type.
        p.route.waypoints.append(copy.deepcopy(p.route.waypoints[0]));p.route.auxiliary_points=[[0.,650.,650.]]
        app.load(p);app.views.select(0);app.inputs.select(editor);pump();app.open_figure('scene');pump()
        window,pop=app.figure_windows['scene'];window.lift();pump();pop.fit()
        last=len(editor.document.points)-1
        editor.tree.selection_set(str(last));editor.refresh_selection();pump();pop.fit()
        closed=editor.snapshot();x,y=point_pixels(pop,last);pop.zoom(2,x,y)
        drag(pop,last,24,-12)
        assert editor.document.points[0]==closed['points'][0] and editor.document.points[-1]!=closed['points'][-1]
        editor.undo();assert editor.snapshot()==closed
        checks['popout_zoomed_drag_and_separate_first_last_same_point']=True
        outside_drag(pop,1)
        checks['native_outside_window_release_retains_exact_last_position']=True
        window.withdraw();root.lift();app.views.select(0);pump();scene.fit()
        editor.tree.selection_set(str(last));editor.refresh_selection();pump()
        x,y=point_pixels(scene,last);scene.zoom(2,x,y)
        drag(scene,last,22,-10);assert editor.document.points[0]==closed['points'][0]
        assert editor.document.points[-1]!=closed['points'][-1]
        editor.undo();assert editor.snapshot()==closed
        outside_drag(scene,1)
        checks['main_native_outside_release_and_closed_endpoint_drag']=True
        window.deiconify();window.lift();pump()
        app.inputs.select(app.auxiliary.editor);pump();pop.fit()
        aux=app.auxiliary.editor;aux_before=aux.snapshot();required_before=editor.snapshot()
        drag(pop,0,18,-8)
        assert aux.snapshot()!=aux_before and aux.document.points[0][2]==1 and editor.snapshot()==required_before
        aux.undo();assert aux.snapshot()==aux_before
        checks['popout_auxiliary_drag_history_and_required_isolation']=True
        shot('02-popout-route')
        window.state('zoomed');pump(.04);pop.draw()
        assert pop.plot is not None and pop.plot.sx==pop.plot.sy
        shot('03-maximized-route');window.state('normal');pump()
        # Main and both windows track frame/selection; cameras are independent.
        app.load(Project.demo());r=calculate(app.collect());app.show_result(r)
        app.views.select(1);app.open_figure('cam');pump();app.render();pump()
        cam=app.figure_panels['cam'];cam_window,cam_pop=app.figure_windows['cam']
        for view in (cam,cam_pop):
            view.draw();assert view.plot is not None
            anchor=view.plot.unxy(160,140)
            for delta in [120]*5+[-120]*3:
                view.canvas.event_generate('<MouseWheel>',x=160,y=140,delta=delta);pump(.002)
                assert view.plot is not None and view.plot.sx==view.plot.sy
                np.testing.assert_allclose(view.plot.unxy(160,140),anchor,atol=1e-8,rtol=0)
            before=camera(view)
            view.canvas.event_generate('<ButtonPress-2>',x=150,y=150)
            view.canvas.event_generate('<B2-Motion>',x=180,y=165)
            view.canvas.event_generate('<ButtonRelease-2>',x=180,y=165);pump()
            assert camera(view)!=before
        cam_window.state('zoomed');pump();cam_pop.draw()
        assert cam_pop.plot is not None and cam_pop.plot.sx==cam_pop.plot.sy
        cam_window.state('normal');pump()
        checks['main_and_popout_cam_cursor_zoom_pan_and_maximize']=True
        cam.zoom(2);cam_pop.zoom(3);pop.zoom(2);states=[camera(v) for v in (scene,cam,pop,cam_pop)]
        app.frame.set(len(r.path)//2);draw()
        assert states==[camera(v) for v in (scene,cam,pop,cam_pop)]
        assert cam_pop.canvas.find_withtag('cam_contact') and cam_pop.canvas.find_withtag('rotation_center')
        assert '压力角' in cam_pop.detail.get() and '主端采样' in cam_pop.detail.get()
        app.play();generation=app.play_generation
        with patch('camtool.ui.time.monotonic',return_value=app.play_clock+1):app.tick(generation)
        app.pause();draw();assert states==[camera(v) for v in (scene,cam,pop,cam_pop)]
        checks['play_pause_timeline_and_cam_contact_popouts_keep_cameras']=True
        app.views.select(app.workbench.frames['issues']);pump()
        issue=next(i for i,d in enumerate(app.workbench.diags) if d.name=='全局接触干涉')
        app.workbench.issue_tree.selection_set(str(issue));app.workbench.choose_issue();draw()
        assert states==[camera(v) for v in (scene,cam,pop,cam_pop)]
        app.focus_problem();draw();assert states!=[camera(v) for v in (scene,cam,pop,cam_pop)]
        checks['issue_selection_keeps_view_and_explicit_focus_zoom']=True
        cam_window.geometry('800x600+50+30');cam_window.lift();pump();shot('04-cam-contact-detail')
        window.withdraw();cam_window.withdraw();root.geometry('1000x640+20+20');app.views.select(0);pump()
        scene.fit();shot('05-small-main')
        assert scene.canvas.winfo_width()>200 and scene.canvas.winfo_height()>80
        assert app.collect().to_dict()==r.project.to_dict() and not app.dirty
        checks['small_window_maximize_and_display_only_project_invariance']=True
        old_bound=app.values['bounds.0'].get()
        for bad in ('NaN','inf',str(r.project.route.field[1]+1)):
            app.values['bounds.0'].set(bad);draw()
            assert scene.plot is None and '未验证' in scene.detail.get()
            assert app.values['bounds.0'].get()==bad
        app.values['bounds.0'].set(old_bound);draw();assert scene.plot is not None
        checks['invalid_bounds_rejected_before_canvas_without_silent_correction']=True
        checks['callback_errors']=errors
        (folder/'视图与拖动验证.json').write_text(json.dumps(checks,ensure_ascii=False,indent=2),encoding='utf-8')
        return checks
    finally:
        app.finish_map_gesture()
        for window,_ in list(app.figure_windows.values()):window.destroy()
        app.figure_windows.clear();app.map_edit.set(saved[4]);app.load(saved[0]);app.file=saved[2];app.dirty=saved[3]
        if saved[1] is not None:app.show_result(saved[1])
        root.report_callback_exception=old_handler  # type: ignore[method-assign]
