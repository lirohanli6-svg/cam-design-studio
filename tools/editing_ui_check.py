"""Exercise native multiselection, batch deletion and the single visible page strip."""
import json
import sys
import time
import tkinter as tk
from pathlib import Path
from tkinter import ttk
from unittest.mock import patch

from PIL import ImageGrab

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from camtool import calculate
from camtool.ui import App, prepare_display
from camtool.version import VERSION

output=ROOT/'build'/'visual-qa'/f'editing-v{VERSION}'
output.mkdir(parents=True,exist_ok=True)
prepare_display(); root=tk.Tk(); app=App(root)
root.geometry('1360x780+20+20')
errors=[]; checks={}
root.report_callback_exception=lambda *a:errors.append(str(a))


def pump():
    root.update(); time.sleep(.02); root.update()
    assert not errors,errors


def shot(name):
    root.lift(); pump(); app.render(); pump()
    x,y=root.winfo_rootx(),root.winfo_rooty()
    ImageGrab.grab(bbox=(x,y,x+root.winfo_width(),y+root.winfo_height())).save(output/f'{name}.png')


def click_row(index,state=0):
    app.tree.see(str(index)); pump()
    box=app.tree.bbox(str(index))
    assert box,('not visible',index)
    x,y,w,h=box
    app.tree.event_generate('<ButtonPress-1>',x=x+40,y=y+h//2,state=state)
    app.tree.event_generate('<ButtonRelease-1>',x=x+40,y=y+h//2,state=state)
    pump()


try:
    with patch('tkinter.messagebox.showerror',side_effect=lambda *a,**k:errors.append(str(a))):
        pump(); app.show_result(calculate(app.collect())); pump()
        snapshot=app.route_snapshot(); snapshot['tangents']=[.123456789,0,1,0,0]
        app.project.route.tangent_offsets=snapshot['tangents'].copy()
        click_row(1); click_row(3,0x4)
        assert set(app.tree.selection())=={'1','3'}
        assert '已选 2 / 5' in app.point_selection_status.get()
        assert all(str(button['state'])=='disabled' for button in app.single_point_buttons)
        shot('batch-selection')
        app.delete_points_button.invoke(); pump()
        assert app.route_snapshot()['points']==[snapshot['points'][i] for i in (0,2,4)]
        assert [app.tree.item(i,'text') for i in app.tree.get_children()]==['1','2','3']
        assert app.result is None and str(app.export_button['state'])=='disabled'
        assert app.dirty and not app.project.route.tangent_offsets
        app.undo_route(); pump(); assert app.route_snapshot()==snapshot
        app.redo_route(); pump(); assert len(app.tree.get_children())==3
        app.undo_route(); pump(); assert app.route_snapshot()==snapshot
        checks['ctrl_nonadjacent_delete_preserves_order_undo_redo_and_invalidates']=True
        click_row(1); click_row(3,0x1)
        assert set(app.tree.selection())=={'1','2','3'}
        app.clear_point_selection(); pump()
        assert not app.tree.selection() and str(app.delete_points_button['state'])=='disabled'
        previous=app.route_snapshot(); revision=app.revision
        app.delete_point(); assert app.revision==revision and app.route_snapshot()==previous
        checks['shift_range_clear_selection_and_empty_delete_noop']=True
        app.tree.focus_set(); app.tree.event_generate('<Control-a>'); pump()
        assert len(app.tree.selection())==5
        app.tree.event_generate('<Delete>'); pump(); assert not app.tree.get_children()
        assert '已选 0 / 0' in app.point_selection_status.get()
        app.undo_route(); pump(); assert app.route_snapshot()==snapshot
        app.select_all_points(); app.delete_points_button.invoke(); pump(); assert not app.tree.get_children()
        app.undo_route(); pump(); assert app.route_snapshot()==snapshot
        checks['select_all_button_and_ctrl_a_delete_to_empty_undo']=True
        click_row(2)
        assert all(str(button['state'])=='normal' for button in app.single_point_buttons)
        assert float(app.point_x.get())==snapshot['points'][2][0]
        checks['single_row_edit_controls_restored']=True
        root.geometry('1000x640+20+20'); pump()
        assert app.tree.winfo_height()>=80 and not app.point_details_expanded.get()
        titles=[app.navigation.tab(tab,'text') for tab in app.navigation.tabs()]
        for label in ('同步演示','问题定位','优化对比','实测与扰动'): assert titles.count(label)==1
        assert ttk.Style(root).layout('Content.TNotebook.Tab')[0][0]=='null'
        positions={}
        for x in range(0,app.navigation.winfo_width(),2):
            try: index=app.navigation.index(f'@{x},15')
            except tk.TclError: continue
            positions.setdefault(index,[]).append(x)
        assert set(positions)==set(range(len(titles))),('page labels clipped',titles,positions.keys())
        for index,xs in positions.items():
            x=(xs[0]+xs[-1])//2
            app.navigation.event_generate('<ButtonPress-1>',x=x,y=15)
            app.navigation.event_generate('<ButtonRelease-1>',x=x,y=15); pump()
            assert app.views.index(app.views.select())==index
        app.show_workbench('demo'); pump(); assert app.navigation.index(app.navigation.select())==7
        app.views.select(app.workbench.frames['analysis'])
        app.navigation.select(0); pump()
        assert app.views.index(app.views.select())==0
        checks['queued_old_tab_event_never_overrides_new_navigation']=True
        app.show_workbench('demo'); pump()
        shot('single-navigation-small')
        app.navigation.select(0); pump()
        assert app.inputs.winfo_ismapped() and app.slider.winfo_ismapped()
        shot('waypoints-small')
        checks['all_eleven_pages_single_strip_visible_and_clickable_at_1000x640']=True
        checks['programmatic_navigation_and_return_to_design']=True
        checks['callback_errors']=errors
        (output/'editing-ui.json').write_text(json.dumps(checks,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(checks,ensure_ascii=False))
finally:
    app.cancel.set(); app.workbench.analysis_cancel.set(); root.destroy()
