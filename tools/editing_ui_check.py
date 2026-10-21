"""Real Tk input checks for inline cells, isolated additions and map transactions."""
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
from camtool.version import VERSION
output=ROOT/'build'/'visual-qa'/f'editing-v{VERSION}'
output.mkdir(parents=True,exist_ok=True)
prepare_display(); root=tk.Tk(); app=App(root); editor=app.waypoints
root.geometry('1360x780+20+20')
errors=[]; checks={}
root.report_callback_exception=lambda *a:errors.append(str(a))
def pump(wait=lambda:False):
    end=time.monotonic()+90
    root.update()
    while wait():
        root.update(); time.sleep(.01)
        if time.monotonic()>end: raise AssertionError('GUI timeout')
    root.update(); assert not errors,errors
def shot(name):
    root.lift(); pump(); app.render(); pump()
    x,y=root.winfo_rootx(),root.winfo_rooty()
    ImageGrab.grab(bbox=(x,y,x+root.winfo_width(),y+root.winfo_height())).save(output/f'{name}.png')
def click(row,col='#0',state=0):
    editor.tree.see(str(row)); pump()
    box=editor.tree.bbox(str(row),col); assert box,(row,col)
    x,y,w,h=box
    editor.tree.event_generate('<ButtonPress-1>',x=round(x+w/2),y=y+h//2,state=state)
    editor.tree.event_generate('<ButtonRelease-1>',x=round(x+w/2),y=y+h//2,state=state); pump()
def key(widget,event):
    widget.focus_force(); pump(); widget.event_generate(event); pump()
def text(widget,value):
    widget.delete(0,'end'); widget.insert(0,value)
def cell(row,col,value,event='<Return>'):
    click(row,f'#{col+1}'); assert editor.draft==(row,col) and editor.entry is not None
    text(editor.entry,value); key(editor.entry,event)
try:
    with patch('tkinter.messagebox.showerror',side_effect=lambda *a,**k:errors.append(str(a))):
        project=Project.demo(); project.route.waypoints[1][0]=1100.123456789
        project.route.tangent_offsets=[.123456789,0.,0.,0.,0.]
        saved=output/'preserved-user-project.cam.json'; project.save(saved)
        digest=hashlib.sha256(saved.read_bytes()).hexdigest()
        app.load(project); app.file=saved; pump()
        original=app.route_snapshot(); design=calculate(app.collect()); app.show_result(design); pump()
        revision=app.revision
        click(1); app.render()
        assert editor.draft is None and app.revision==revision
        assert app.canvases['scene'].find_withtag('selected_waypoint_2')
        assert editor.new_x.get()==editor.new_y.get()==''
        click(1,'#1'); assert editor.entry is not None and editor.entry.cget('background')=='#fff5ce'
        shot('active-cell')
        text(editor.entry,'1110.25'); key(editor.entry,'<Return>')
        assert app.route_snapshot()['points'][1][0]==1110.25 and app.revision==revision+1
        assert app.dirty and app.result is None and str(app.export_button['state'])=='disabled'
        app.render(); assert app.canvases['scene'].find_withtag('selected_waypoint_2')
        px,py=app.scene_plot.xy(1110.25,400)
        marker=app.canvases['scene'].find_withtag('selected_waypoint_2')[0]
        coords=app.canvases['scene'].coords(marker)
        assert abs((coords[0]+coords[2])/2-px)<.01 and abs((coords[1]+coords[3])/2-py)<.01
        app.undo_route(); assert app.route_snapshot()==original
        app.redo_route(); assert app.route_snapshot()['points'][1][0]==1110.25
        app.undo_route(); assert app.route_snapshot()==original
        checks['single_click_enter_transaction_and_map_before_after_calculation']=True
        revision=app.revision
        cell(1,0,'9999','<Escape>'); assert app.route_snapshot()==original and app.revision==revision
        cell(1,0,str(original['points'][1][0])+'0')
        assert app.route_snapshot()==original and app.revision==revision
        app.undo_route(); assert app.route_snapshot()==original and app.revision==revision
        app.redo_route(); assert app.route_snapshot()['points'][1][0]==1110.25
        app.undo_route(); assert app.route_snapshot()==original
        checks['escape_and_numeric_noop_preserve_undo_redo_and_precision']=True
        for bad in ('','abc','NaN','inf','-Infinity','1e9999'):
            revision=app.revision; cell(1,0,bad)
            assert editor.entry is not None and editor.entry.get()==bad
            assert app.route_snapshot()==original and app.revision==revision
            assert '原坐标未改变' in editor.feedback.get()
            key(editor.entry,'<Escape>')
        cell(1,0,'bad')
        text(editor.entry,'1130.125'); key(editor.entry,'<Return>')
        assert app.route_snapshot()['points'][1][0]==1130.125
        app.undo_route(); assert app.route_snapshot()==original
        checks['invalid_cells_keep_original_and_draft_then_allow_correction']=True
        cell(1,0,'1200','<Tab>'); assert editor.draft==(1,1)
        text(editor.entry,'450'); key(editor.entry,'<Tab>'); assert editor.draft==(2,0)
        key(editor.entry,'<Escape>')
        app.undo_route(); assert app.route_snapshot()['points'][1]==[1200.,400.]
        app.undo_route(); assert app.route_snapshot()==original
        cell(1,0,'bad','<Tab>'); assert editor.draft==(1,0) and app.route_snapshot()==original
        key(editor.entry,'<Escape>')
        click(1,'#1'); key(editor.entry,'<Shift-Tab>'); assert editor.draft==(0,1)
        key(editor.entry,'<Escape>')
        checks['tab_shift_tab_commit_history_and_invalid_navigation_blocked']=True
        for values in (('',''),('word','5'),('NaN','inf')):
            revision=app.revision
            for entry,value in zip(editor.new_entries,values): text(entry,value)
            editor.add_button.invoke(); pump()
            assert app.route_snapshot()==original and app.revision==revision
            assert [entry.get() for entry in editor.new_entries]==list(values)
            assert any(v.get() for v in editor.new_errors)
        text(editor.new_entries[0],'4700'); text(editor.new_entries[1],'800')
        click(0); click(2)
        assert [entry.get() for entry in editor.new_entries]==['4700','800']
        cell(2,1,'860'); assert len(app.route_snapshot()['points'])==5
        assert editor.new_x.get()=='4700' and editor.new_y.get()=='800'
        app.undo_route(); assert app.route_snapshot()==original
        editor.add_button.invoke(); pump()
        assert app.route_snapshot()['points']==original['points']+[[4700.,800.]]
        app.undo_route(); assert app.route_snapshot()==original
        app.redo_route(); assert len(app.route_snapshot()['points'])==6
        app.undo_route(); assert app.route_snapshot()==original
        checks['isolated_new_form_validation_and_add_cannot_overwrite_selection']=True
        editor.new_x.set('4400'); editor.new_y.set('700'); editor.add_button.invoke(); pump()
        assert len(app.route_snapshot()['points'])==6 and '相邻' in editor.feedback.get()
        app.undo_route(); assert app.route_snapshot()==original
        editor.new_x.set('300'); editor.new_y.set('400'); editor.add_button.invoke(); pump()
        closed=app.route_snapshot(); assert len(closed['points'])==6 and closed['points'][0]==closed['points'][-1]
        assert '首尾' in editor.feedback.get()
        app.render(); assert app.canvases['scene'].find_withtag('selected_waypoint_6')
        closed_file=output/'closed-project.cam.json'; app.file=closed_file; assert app.save()
        assert len(Project.load(closed_file).route.waypoints)==6
        shot('closed-route')
        checks['adjacent_duplicate_notice_and_closed_route_preserved_and_saved']=True
        click(1); click(3,state=4); assert set(editor.tree.selection())=={'1','3'}
        editor.buttons['删除'].invoke(); pump()
        assert app.route_snapshot()['points']==[p for i,p in enumerate(closed['points']) if i not in (1,3)]
        app.undo_route(); assert app.route_snapshot()==closed
        click(1); click(3,state=1); assert set(editor.tree.selection())=={'1','2','3'}
        editor.clear_selection(); editor.tree.focus_force(); key(editor.tree,'<Control-a>')
        app.render()
        marked=app.canvases['scene'].find_withtag('selected_waypoint_6')
        assert any('1/6' in app.canvases['scene'].itemcget(item,'text') for item in marked if app.canvases['scene'].type(item)=='text')
        key(editor.tree,'<Delete>'); assert not app.route_snapshot()['points']
        app.undo_route(); assert app.route_snapshot()==closed
        checks['native_ctrl_shift_select_all_batch_delete_and_undo_retained']=True
        app.load(project); app.file=saved; pump(); app.render()
        editor.new_x.set('12'); editor.new_y.set('34')
        original=app.route_snapshot(); canvas=app.canvases['scene']
        x,y=app.scene_plot.xy(*original['points'][1]); x2,y2=x+35,y-15
        canvas.event_generate('<ButtonPress-1>',x=round(x),y=round(y))
        for dx in (10,20,35): canvas.event_generate('<B1-Motion>',x=round(x+dx),y=round(y-15)); pump()
        canvas.event_generate('<ButtonRelease-1>',x=round(x2),y=round(y2)); pump()
        assert app.route_snapshot()['points'][1]!=original['points'][1]
        assert editor.new_x.get()=='12' and editor.new_y.get()=='34'
        assert [float(v) for v in editor.tree.item('1','values')]==app.route_snapshot()['points'][1]
        app.undo_route(); assert app.route_snapshot()==original
        revision=app.revision; app.undo_route(); assert app.revision==revision
        checks['map_drag_sync_independent_new_fields_and_one_undo_per_gesture']=True
        app.load(project); pump(); started=threading.Event(); release=threading.Event()
        def delayed(p,cancelled=None):
            started.set(); release.wait(8); return calculate(p)
        with patch('camtool.ui.calculate',side_effect=delayed),patch.object(app.cancel,'is_set',return_value=False):
            app.run(False); pump(lambda:not started.is_set())
            cell(1,0,'1122.5'); release.set(); pump(lambda:app.busy)
        assert app.result is None and app.route_snapshot()['points'][1][0]==1122.5
        assert str(app.export_button['state'])=='disabled'
        checks['stale_background_result_rejected_after_real_cell_enter']=True
        app.load(project); root.geometry('1000x640+20+20'); pump()
        editor.new_x.set('NaN'); editor.new_y.set(''); editor.add_button.invoke(); pump()
        cell(1,0,'NaN'); shot('small-invalid-input')
        for widget in (editor.add_button,editor.buttons['删除'],editor.buttons['撤销'],*editor.new_entries):
            assert widget.winfo_ismapped()
            assert widget.winfo_rooty()+widget.winfo_height()<=editor.winfo_rooty()+editor.winfo_height()
        assert editor.tree.winfo_height()>=70 and editor.entry is not None and editor.entry.winfo_ismapped()
        key(editor.entry,'<Escape>'); shot('small-editor')
        checks['small_window_main_actions_inline_errors_and_active_cell_visible']=True
        assert hashlib.sha256(saved.read_bytes()).hexdigest()==digest
        checks['user_project_unchanged']=True; checks['callback_errors']=errors
        (output/'editing-ui.json').write_text(json.dumps(checks,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(checks,ensure_ascii=False))
finally:
    app.cancel.set(); editor.cancel_edit(); app.workbench.analysis_cancel.set(); root.destroy()
