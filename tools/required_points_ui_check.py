"""Actual user file, async calculation, checkpoint table and visible miss marks."""
import copy
import hashlib
import json
import sys
import time
import tkinter as tk
from pathlib import Path
from PIL import ImageGrab
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from camtool import Project
from camtool.ui import App,prepare_display
from camtool.version import VERSION
source=Path(sys.argv[1]).resolve()
output=ROOT/'build/visual-qa'/f'required-points-v{VERSION}';output.mkdir(parents=True,exist_ok=True)
digest=hashlib.sha256(source.read_bytes()).hexdigest();project=Project.load(source)
prepare_display();root=tk.Tk();app=App(root);errors=[];checks={}
root.report_callback_exception=lambda *args:errors.append(str(args))

def pump(predicate=lambda:False):
    end=time.monotonic()+120
    while True:
        root.update();time.sleep(.01)
        if not predicate():break
        if time.monotonic()>end:raise AssertionError('UI timeout')
    assert not errors,errors

def shot(name):
    root.lift();pump();app.render();pump()
    x,y=root.winfo_rootx(),root.winfo_rooty()
    ImageGrab.grab(bbox=(x,y,x+root.winfo_width(),y+root.winfo_height())).save(output/(name+'.png'))

try:
    root.geometry('1360x780+20+20');pump();app.load(project);app.file=source;app.run(False);pump(lambda:app.busy)
    r=app.result;assert r is not None,app.status.get()
    assert r.sampling['used']==4000 and all(row['passed'] for row in r.checkpoint_rows)
    assert app.collect().to_dict()==project.to_dict()
    assert not r.valid and '不满足' in app.status.get()
    assert len(app.checkpoint_tree.get_children())==10
    assert all(app.checkpoint_tree.item(i,'values')[-1]=='通过' for i in app.checkpoint_tree.get_children())
    app.views.select(4);app.report_tabs.select(1);pump();shot('01-user-checkpoint-table')
    app.checkpoint_tree.selection_set('5');app.checkpoint_tree.event_generate('<<TreeviewSelect>>');pump();app.render();pump()
    assert app.views.index(app.views.select())==0
    assert app.canvases['scene'].find_withtag('selected_waypoint_6')
    banner=app.canvases['scene'].find_withtag('checkpoint_banner');assert banner
    assert '不满足' in app.canvases['scene'].itemcget(banner[0],'text')
    shot('02-user-loop-after-adaptive-sampling')
    checks['overall_failures_visible_even_when_all_visits_pass']=True
    checks['actual_user_file_all_ten_nominal_visits_within_twenty_mm']=True
    checks['hardware_points_tolerance_and_input_file_not_modified']=True
    checks['table_row_locates_original_required_point']=True
    # A genuinely stricter requirement must stay failed at the cap and show red
    # misses, rather than moving targets or loosening the tolerance.
    strict=copy.deepcopy(project);strict.route.tolerance=.1
    app.load(strict);app.run(False);pump(lambda:app.busy)
    assert app.result is not None and not app.result.sampling['passed']
    app.views.select(0);pump();app.render();pump()
    canvas=app.canvases['scene']
    assert any(canvas.type(i)=='text' and str(canvas.itemcget(i,'text')).startswith('漏') for i in canvas.find_all())
    shot('03-real-misses-red-and-numbered')
    app.views.select(4);app.report_tabs.select(1);root.geometry('1000x640+20+20');pump();shot('04-small-window-miss-table')
    assert app.checkpoint_tree.winfo_ismapped() and app.checkpoint_tree.winfo_height()>100
    assert any(app.checkpoint_tree.item(i,'values')[-1]=='不通过' for i in app.checkpoint_tree.get_children())
    checks['cap_failure_red_marks_table_and_small_window']=True
    app.values['route.tension'].set('.8');pump()
    assert app.result is None and not app.checkpoint_tree.get_children()
    assert str(app.export_button['state'])=='disabled'
    checks['editing_invalidates_checkpoint_table_and_exports']=True
    assert hashlib.sha256(source.read_bytes()).hexdigest()==digest
    checks['source_sha256']=digest;checks['callback_errors']=errors
    (output/'required-points-ui.json').write_text(json.dumps(checks,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(checks,ensure_ascii=False))
finally:
    app.cancel.set();root.destroy()
