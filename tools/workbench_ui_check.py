"""Drive actual Tk widgets and background queues; synthetic fixtures are not measurements."""
import hashlib
import json
import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from unittest.mock import patch

from PIL import ImageGrab

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from camtool import Project, calculate
from camtool.ui import App, prepare_display
from camtool.workbench import time_axis
from camtool.version import VERSION

output=Path(sys.argv[1]) if len(sys.argv)>1 else ROOT/'build'/'visual-qa'/f'workbench-v{VERSION}'
output.mkdir(parents=True,exist_ok=True)
prepare_display()
root=tk.Tk(); app=App(root); page=app.workbench
root.geometry('1360x780+20+20')
errors=[]
root.report_callback_exception=lambda *a:errors.append(str(a))
checks={}


def pump(predicate=lambda:False,timeout=90):
    end=time.monotonic()+timeout
    root.update()
    while predicate():
        root.update()
        if time.monotonic()>end: raise AssertionError('GUI timeout')
        time.sleep(.01)
    root.update()
    assert not errors,errors


def screenshot(name):
    root.lift(); root.update(); app.render(); root.update()
    x,y=root.winfo_rootx(),root.winfo_rooty()
    ImageGrab.grab(bbox=(x,y,x+root.winfo_width(),y+root.winfo_height())).save(output/f'{name}.png')


def design():
    app.run(False); pump(lambda:app.busy)
    assert app.result is not None


try:
    with patch('tkinter.messagebox.showerror',side_effect=lambda *a,**k:errors.append(str(a))):
        original=app.collect(); original.optimization.iterations=1
        user_file=output/'user-project-preservation.cam.json'; original.save(user_file)
        digest=hashlib.sha256(user_file.read_bytes()).hexdigest()
        app.load(original); app.file=user_file; app.dirty=False
        app.parameter_source.set('自动测试配置（非实测）'); design()
        page.load_case(False); pump(lambda:app.busy)
        assert app.result.valid and app.file is None
        r=app.result; i=len(r.path)//2
        page.seek(str(time_axis(r)[i])); app.render(); root.update()
        assert int(app.frame.get())==i
        assert abs(page.time.get()-time_axis(r)[i])<1e-6
        assert '已完成最大偏差' in page.demo_status.get()
        assert all(page.canvases[k].find_all() for k in ('demo_route','demo_cam','demo_mechanism'))
        app.play(); generation=app.play_generation
        with patch('camtool.ui.time.monotonic',return_value=app.play_clock+2): app.tick(generation)
        assert int(app.frame.get())>i
        app.pause(); paused=int(app.frame.get()); app.tick(generation)
        assert int(app.frame.get())==paused and not app.playing
        checks['shared_timeline_scrub_play_pause_and_old_timer']=True
        screenshot('normal-demo')
        root.geometry('1000x640+20+20'); pump(); screenshot('small-demo')
        assert all(page.canvases[k].winfo_width()>200 for k in ('demo_route','demo_cam','demo_mechanism'))
        assert page.frames['demo'].pack_slaves()[0].winfo_ismapped()
        root.geometry('1360x780+20+20')
        page.load_case(True); pump(lambda:app.busy)
        assert not app.result.valid
        app.views.select(page.frames['issues']); pump()
        j=next(j for j,d in enumerate(page.diags) if d.name=='前轮转角')
        page.issue_tree.selection_set(str(j)); page.issue_tree.event_generate('<<TreeviewSelect>>'); pump()
        assert int(app.frame.get())==page.diags[j].route_index
        assert all(word in page.issue_detail.get('1.0','end') for word in ['实际','限制','依据','建议','不保证'])
        screenshot('problem-location')
        checks['deterministic_problem_and_route_cam_curve_location']=True
        page.restore_project(); pump()
        assert app.collect().to_dict()==original.to_dict() and app.file==user_file
        assert not app.dirty and '非实测' in app.parameter_source.get()
        assert hashlib.sha256(user_file.read_bytes()).hexdigest()==digest
        app.run(True); pump(lambda:app.busy)
        assert page.comparison is not None and len(page.compare_tree.get_children())==8
        assert app.result.project.to_dict()==original.to_dict()
        pair_dir=output/'comparison'; pair_dir.mkdir(exist_ok=True)
        # Export function deliberately rejects existing artifacts. Use a fresh target per run.
        import tempfile
        pair_dir=Path(tempfile.mkdtemp(prefix='pair-',dir=pair_dir))
        with patch('tkinter.filedialog.askdirectory',return_value=str(pair_dir)): page.export_comparison()
        assert Project.load(pair_dir/'原方案.cam.json').to_dict()==original.to_dict()
        screenshot('optimization-comparison')
        page.adopt(True); assert app.file is None and app.dirty
        page.adopt(False); assert app.file==user_file and not app.dirty
        page.set_comparison(app.result,app.result)
        assert '未改善' in page.compare_status.get()
        checks['optimization_snapshots_five_metrics_explicit_choice_and_no_improvement']=True
        app.views.select(0); pump(); app.render(); pump()
        canvas=app.canvases['scene']; before=app.route_snapshot()
        x,y=app.scene_plot.xy(4600,1700)
        canvas.event_generate('<ButtonPress-1>',x=round(x),y=round(y))
        canvas.event_generate('<ButtonRelease-1>',x=round(x),y=round(y)); pump()
        assert len(app.route_snapshot()['points'])==len(before['points'])+1 and app.result is None
        app.undo_route(); assert app.route_snapshot()==before
        app.redo_route(); app.render(); pump()
        added=app.route_snapshot(); old=added['points'][-1]
        x,y=app.scene_plot.xy(*old); x2,y2=app.scene_plot.xy(4200,1650)
        canvas.event_generate('<ButtonPress-1>',x=round(x),y=round(y))
        canvas.event_generate('<B1-Motion>',x=round(x2),y=round(y2))
        canvas.event_generate('<ButtonRelease-1>',x=round(x2),y=round(y2)); pump()
        assert app.route_snapshot()['points'][-1]!=old
        assert float(app.tree.item(str(len(app.tree.get_children())-1),'values')[0])==app.route_snapshot()['points'][-1][0]
        app.undo_route(); assert app.route_snapshot()==added
        app.undo_route(); assert app.route_snapshot()==before
        checks['map_click_drag_table_sync_undo_redo_and_invalidation']=True
        app.parameter_source.set('GUI 测试配置（非实测）')
        profile=output/'parameter-profile.json'
        with patch('tkinter.filedialog.asksaveasfilename',return_value=str(profile)): app.save_parameters()
        app.values['vehicle.wheelbase'].set('131')
        with patch('tkinter.filedialog.askopenfilename',return_value=str(profile)): app.load_parameters()
        assert app.collect().vehicle.wheelbase==original.vehicle.wheelbase
        assert app.collect().route.waypoints==original.route.waypoints
        app.inputs.select(1); app.parameter_focus('vehicle.wheelbase'); pump()
        sketch=app.dimension_canvases['vehicle']
        assert any(sketch.itemcget(item,'fill')=='#cc354a' for item in sketch.find_all() if sketch.type(item) in ('line','text'))
        screenshot('parameter-sketch')
        checks['parameter_profile_route_preservation_and_focus_highlight']=True
        design(); r=app.result
        fixture=output/'synthetic-not-measured.csv'
        fixture.write_text('# unit=mm\n# frame=field_xy\n# axes=x_right_y_up\n# point=sensor\n# kind=synthetic\n# source=GUI自动测试构造（非实测）\nt_s,x,y\n'+
                           ''.join(f'{i},{p[0]},{p[1]}\n' for i,p in enumerate(r.path)),encoding='utf-8-sig')
        app.views.select(page.frames['analysis']); pump()
        with patch('tkinter.filedialog.askopenfilename',return_value=str(fixture)): page.import_track()
        pump(lambda:page.analysis_busy)
        assert page.measured_rows and '非实测' in page.analysis_status.get()
        for key in ('wheel_min','wheel_max','phase_min','phase_max'): page.ranges[key].set('0')
        page.ranges['count'].set('2'); page.ranges['step'].set('4')
        page.run_perturbation(); pump(lambda:page.analysis_busy)
        assert len(page.perturbations)==1 and page.perturbations[0]['path']
        report=output/'analysis.json'
        with patch('tkinter.filedialog.asksaveasfilename',return_value=str(report)): page.export_analysis()
        data=json.loads(report.read_text(encoding='utf-8'))
        assert not data['measured']['declared_measured_not_authenticated']
        assert data['theoretical_single_factor_perturbations'][0]['prediction']
        screenshot('import-and-prediction')
        checks['csv_source_classification_fixed_dxf_prediction_and_export']=True
        started=threading.Event(); release=threading.Event()
        def slow_analysis(result,cancel,progress):
            started.set(); release.wait(5); return [{'stale':True}]
        page.start_job('perturb',slow_analysis); pump(lambda:not started.is_set())
        page.analysis_cancel.set(); release.set(); pump(lambda:page.analysis_busy)
        assert '停止' in page.analysis_status.get()
        checks['background_analysis_cancellation']=True
        started.clear(); release.clear()
        def delayed(project,cancelled=None):
            started.set(); release.wait(5)
            return calculate(project)  # Deliberately ignores cancel to test revision guard too.
        with patch('camtool.ui.calculate',side_effect=delayed):
            app.run(False); pump(lambda:not started.is_set())
            app.waypoints.begin_edit(1,0)
            app.waypoints.draft_text.set(str(app.route_snapshot()['points'][1][0]+5))
            assert app.waypoints.commit()
            release.set(); pump(lambda:app.busy)
        assert app.result is None and page.comparison is None
        assert str(app.export_button['state'])=='disabled'
        checks['stale_background_result_never_replaces_new_input']=True
        assert hashlib.sha256(user_file.read_bytes()).hexdigest()==digest
        checks['user_file_unchanged']=True
        invalid=Project.demo(); invalid.mechanism.pivot_x=-104.7; invalid.cam.pressure_limit=60; invalid.optimization.iterations=1
        app.load(invalid); app.run(True); pump(lambda:app.busy)
        assert page.comparison is not None and page.comparison.original is None
        assert '无法判断改善' in page.compare_status.get()
        page.adopt(False); assert app.result is None and app.collect().to_dict()==invalid.to_dict()
        page.adopt(True); assert app.result is not None and len(app.result.profile)>0
        checks['optimization_can_recover_uncomputable_original_without_fake_metrics']=True
        screenshot('uncomputable-original-comparison')
        checks['callback_errors']=errors
        (output/'workbench-ui.json').write_text(json.dumps(checks,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(checks,ensure_ascii=False))
finally:
    app.cancel.set(); page.analysis_cancel.set(); root.destroy()
