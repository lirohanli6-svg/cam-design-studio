"""Real Tk editing, background search, cancellation, contact evidence and export."""
import copy
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
from camtool import Project,calculate
from camtool.ui import App,prepare_display
from camtool.auxiliary_search import search_auxiliaries,SearchSettings
from camtool.version import VERSION
out=Path(sys.argv[1]) if len(sys.argv)>1 else ROOT/'build/visual-qa'/f'auxiliary-v{VERSION}';out.mkdir(parents=True,exist_ok=True)
prepare_display();root=tk.Tk();app=App(root);aux=app.auxiliary
errors=[];checks={};root.report_callback_exception=lambda *args:errors.append(str(args))

def pump(until=lambda:False):
    deadline=time.monotonic()+120
    root.update();time.sleep(.01)
    while True:
        root.update();time.sleep(.01)
        if not until():break
        if time.monotonic()>deadline:raise AssertionError('GUI timeout')
    assert not errors,errors

def shot(name):
    root.lift();pump();app.render();pump()
    x,y=root.winfo_rootx(),root.winfo_rooty()
    ImageGrab.grab(bbox=(x,y,x+root.winfo_width(),y+root.winfo_height())).save(out/f'{name}.png')

try:
    root.geometry('1360x780+20+20');pump();p=app.collect();original=p.to_dict()
    app.inputs.select(aux.page.master.master) if False else None
    app.inputs.select(app.inputs.tabs()[-1]);pump()
    aux.values['segment'].set('2');aux.values['x'].set('1600');aux.values['y'].set('1100')
    aux.edit(False);pump()
    assert app.project.route.auxiliary_points==[[1.,1600.,1100.]]
    assert app.collect().route.waypoints==p.route.waypoints and app.result is None
    aux.travel(False);assert app.project.route.auxiliary_points==[]
    aux.travel(True);assert len(app.project.route.auxiliary_points)==1
    aux.values['x'].set('NaN');aux.edit(False);assert len(app.project.route.auxiliary_points)==1
    assert aux.editor.new_errors[0].get() and aux.values['x'].get()=='NaN';shot('auxiliary-editor-invalid')
    checks['separate_soft_edit_invalid_undo_redo']=True
    app.load(p);app.show_result(calculate(p));app.views.select(app.workbench.frames['issues']);pump()
    diags=app.workbench.diags
    i=next(i for i,d in enumerate(diags) if d.name=='全局接触干涉')
    app.workbench.issue_tree.selection_set(str(i));app.workbench.choose_issue();pump();app.render()
    assert app.workbench.canvases['issue_cam'].find_withtag('contact_evidence')
    shot('contact-roller-nearest-segment');checks['contact_evidence_visible']=True
    # Real background search: retain current input and keep all actual reports.
    aux.settings['evaluations'].set('4');aux.settings['seconds'].set('15');aux.start();pump(lambda:aux.busy)
    assert aux.outcome is not None,aux.summary.get()
    result=aux.outcome;assert app.collect().to_dict()==original
    assert result.verification is not None and result.verification.kinematic_pass
    assert len(app.workbench.compare_tree.get_children())==8
    export_folder=out/f'exported-search-{time.time_ns()}'
    with patch('tkinter.filedialog.askdirectory',return_value=str(export_folder)):aux.export()
    dxf=export_folder/'候选方案/cam.dxf'
    assert hashlib.sha256(dxf.read_bytes()).hexdigest()==result.verification.sha256
    checks['actual_background_search_final_dxf_and_export']=True
    shot('comparison-actual-metrics')
    app.workbench.adopt(True);assert app.collect().to_dict()==result.candidate.project.to_dict()
    app.workbench.adopt(False);assert app.collect().to_dict()==original
    checks['candidate_adopt_and_original_restore']=True
    root.geometry('1000x640+20+20');pump();app.views.select(app.workbench.frames['compare']);pump();shot('small-search-comparison')
    assert app.workbench.canvases['compare'].winfo_height()>80
    checks['small_window_search_controls_and_map_visible']=True
    aux.settings['evaluations'].set('36');aux.start();pump();aux.cancel.set();pump(lambda:aux.busy)
    assert aux.outcome is None and app.collect().to_dict()==original
    checks['cancel_real_search_no_adoption']=True
    # Force a worker to ignore cancellation; changing actual input must still
    # reject the late value via generation+revision, not merely the cancel flag.
    entered=threading.Event();release=threading.Event()
    def late(*args,**kwargs):
        entered.set();release.wait(10);return copy.deepcopy(result)
    with patch('camtool.auxiliary_ui.search_auxiliaries',side_effect=late):
        aux.start();pump(lambda:not entered.is_set())
        app.views.select(0);app.inputs.select(aux.editor);pump()
        aux.editor.new_x.set('700');aux.editor.new_y.set('550');aux.editor.new_segment.set('1')
        aux.editor.add_button.invoke();pump()
        box=aux.tree.bbox('0','#1');assert box
        x,y,w,h=box
        aux.tree.event_generate('<ButtonPress-1>',x=x+w//2,y=y+h//2)
        aux.tree.event_generate('<ButtonRelease-1>',x=x+w//2,y=y+h//2);pump()
        assert aux.editor.entry is not None
        aux.editor.draft_text.set('725');aux.editor.entry.focus_force();pump()
        aux.editor.entry.event_generate('<Return>');pump()
        release.set();pump(lambda:aux.busy)
    assert aux.outcome is None and app.result is None and app.collect().route.auxiliary_points==[[0.,725.,550.]], (aux.outcome is None,app.result is None,app.collect().route.auxiliary_points,aux.editor.feedback.get(),aux.editor.draft)
    checks['stale_worker_cannot_overwrite_new_input']=True
    checks['callback_errors']=errors
    (out/'auxiliary-ui.json').write_text(json.dumps(checks,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(checks,ensure_ascii=False))
finally:
    aux.cancel.set();app.cancel.set();root.destroy()
