"""Native Tk editor acceptance checks, callable in source and frozen release.

Only the explicit --smoke-dir diagnostic entry point calls this module. Tests
operate on in-memory theory data and new files in the supplied QA directory.
"""
from __future__ import annotations

import copy
import json
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable
from unittest.mock import patch

from .engine import calculate
from .project import Project

if TYPE_CHECKING:
    from .ui import App
    from .waypoints import PointTableEditor


def check_point_editors(app: App,directory: Path,capture: Callable[[str],None] | None=None) -> dict[str,Any]:
    directory.mkdir(parents=True,exist_ok=True)
    root=app.root;required=app.waypoints;aux=app.auxiliary.editor
    checks: dict[str,Any]={};errors=[]
    previous_handler=root.report_callback_exception
    root.report_callback_exception=lambda *args:errors.append(str(args))  # type: ignore[method-assign]
    saved=(app.collect(),app.result,app.file,app.dirty)

    def pump(wait: Callable[[],bool]=lambda:False) -> None:
        deadline=time.monotonic()+60
        while True:
            root.update();time.sleep(.005)
            if not wait():break
            if time.monotonic()>deadline:raise AssertionError("双点编辑GUI超时")
        assert not errors,errors

    def page(editor: PointTableEditor) -> None:
        app.views.select(0);app.inputs.select(editor);pump();app.render();pump()

    def click(editor: PointTableEditor,row: int,col: int | None=None,state: int=0) -> None:
        editor.tree.see(str(row));pump()
        box=editor.tree.bbox(str(row),"#0" if col is None else f"#{col+1}")
        assert box,(row,col,editor.tree.winfo_height())
        x,y,w,h=box
        editor.tree.event_generate("<ButtonPress-1>",x=x+w//2,y=y+h//2,state=state)
        editor.tree.event_generate("<ButtonRelease-1>",x=x+w//2,y=y+h//2,state=state);pump()
        app.render();pump()

    def key(widget: Any,event: str) -> None:
        root.update_idletasks();deadline=time.monotonic()+3
        while not widget.winfo_viewable():
            pump()
            assert time.monotonic()<deadline,('key target not mapped',event,str(widget),aux.draft,required.draft)
        # Windows may finish an older FocusIn after the new Tab cell is mapped.
        # Request focus after pumping it; never bypass the actual key binding.
        widget.focus_force();pump()
        while root.focus_get()!=widget:
            widget.focus_force();pump()
            assert time.monotonic()<deadline,('key target focus',event,str(root.focus_get()),str(widget),aux.draft,required.draft)
        widget.event_generate(event,when="tail");pump()

    def cell(editor: PointTableEditor,row: int,col: int,text: str,event: str="<Return>") -> None:
        click(editor,row,col);assert editor.entry is not None and editor.draft==(row,col)
        editor.entry.delete(0,"end");editor.entry.insert(0,text);key(editor.entry,event)

    def shot(name: str) -> None:
        app.render();pump()
        if capture is not None:capture(name)

    def map_click(x: float,y: float,drag: bool=False) -> None:
        canvas=app.canvases['scene'];app.render();pump()
        assert app.scene_plot is not None
        px,py=app.scene_plot.xy(x,y)
        canvas.event_generate('<ButtonPress-1>',x=round(px),y=round(py));pump()
        if drag:
            for dx in (8,16,24):
                canvas.event_generate('<B1-Motion>',x=round(px+dx),y=round(py-12));pump()
        canvas.event_generate('<ButtonRelease-1>',x=round(px+24 if drag else px),y=round(py-12 if drag else py));pump()

    try:
        root.geometry('1360x780+20+20')
        root.deiconify();root.lift();root.focus_force();pump()
        project=Project.demo()
        project.route.auxiliary_points=[[0.,600.123456789,500.],[1.,1600.,1100.],[2.,2700.,850.],
                                        [3.,3900.,900.],[1.,1800.,1000.]]
        app.load(project);page(aux)
        original=aux.snapshot();required_original=required.snapshot()
        # Real result first, so invalidation checks an existing exportable design.
        app.show_result(calculate(app.collect()));pump()
        revision=app.revision
        click(aux,0);assert aux.new_x.get()==aux.new_y.get()==''
        assert app.canvases['scene'].find_withtag('selected_auxiliary_1')
        cell(aux,0,0,'611.25')
        assert aux.document.points[0][0]==611.25 and app.revision==revision+1
        assert app.dirty and app.result is None and str(app.export_button['state'])=='disabled'
        assert required.snapshot()==required_original
        aux.undo();assert aux.snapshot()==original
        aux.undo(True);assert aux.document.points[0][0]==611.25
        aux.undo();assert aux.snapshot()==original
        checks['auxiliary_single_click_enter_invalidation_and_map']=True
        revision=app.revision
        cell(aux,0,0,'999','<Escape>')
        cell(aux,0,0,str(original['points'][0][0]))
        assert aux.snapshot()==original and app.revision==revision
        assert len(aux.document.history.redo_stack)==1
        checks['escape_and_noop_do_not_add_history_or_clear_redo']=True
        for bad in ('','abc','NaN','inf','-Infinity','1e9999','-1'):
            cell(aux,0,0,bad)
            assert aux.entry is not None and aux.entry.get()==bad and aux.snapshot()==original
            assert app.revision==revision and '原坐标未改变' in aux.feedback.get()
            if bad=='NaN':shot('01-auxiliary-invalid-cell')
            key(aux.entry,'<Escape>')
        for bad in ('','NaN','0','1.5','99'):
            cell(aux,0,2,bad,'<Tab>');assert aux.draft==(0,2) and aux.snapshot()==original
            key(aux.entry,'<Escape>')
        checks['invalid_xy_and_segment_keep_committed_values_and_draft']=True
        cell(aux,0,0,'620','<Tab>');assert aux.draft==(0,1)
        aux.draft_text.set('510');key(aux.entry,'<Tab>');assert aux.draft==(0,2)
        aux.draft_text.set('2');key(aux.entry,'<Tab>');assert aux.draft==(1,0)
        key(aux.entry,'<Shift-Tab>');assert aux.draft==(0,2)
        key(aux.entry,'<Escape>')
        for _ in range(3):aux.undo()
        assert aux.snapshot()==original
        checks['tab_shift_tab_xy_segment_navigation_and_separate_commits']=True
        aux.new_x.set('1000');aux.new_y.set('600');aux.new_segment.set('3')
        click(aux,1);cell(aux,1,1,'1110');aux.undo()
        assert (aux.new_x.get(),aux.new_y.get(),aux.new_segment.get())==('1000','600','3')
        aux.add_button.invoke();pump()
        assert aux.document.controls()[-1]==[2.,1000.,600.] and len(aux.document.points)==6
        assert required.snapshot()==required_original
        aux.undo();assert aux.snapshot()==original
        for bad_form in (('','3','2'),('NaN','inf','2'),('1','2','1.5')):
            for var,value in zip((aux.new_x,aux.new_y,aux.new_segment),bad_form):var.set(value)
            revision=app.revision;aux.add_button.invoke();pump()
            assert aux.snapshot()==original and app.revision==revision
            assert tuple(v.get() for v in (aux.new_x,aux.new_y,aux.new_segment))==bad_form
            assert any(v.get() for v in [*aux.new_errors,aux.segment_error])
        checks['independent_auxiliary_add_and_local_validation']=True
        aux.new_x.set('');aux.new_y.set('');aux.new_segment.set('1')
        for var in [*aux.new_errors,aux.segment_error]:var.set('')
        click(aux,1);click(aux,3,state=4)
        assert set(aux.tree.selection())=={'1','3'}
        aux.buttons['删除'].invoke();pump()
        assert aux.document.points==[p for i,p in enumerate(original['points']) if i not in (1,3)]
        aux.undo();assert aux.snapshot()==original
        click(aux,1);click(aux,3,state=1);assert set(aux.tree.selection())=={'1','2','3'}
        aux.buttons['上移'].invoke();pump()
        assert aux.document.points==[original['points'][i] for i in (1,2,3,0,4)]
        aux.buttons['下移'].invoke();pump();assert aux.snapshot()==original
        aux.undo();aux.undo();assert aux.snapshot()==original
        aux.clear_selection();assert not aux.selected()
        key(aux.tree,'<Control-a>');assert len(aux.selected())==5
        key(aux.tree,'<Delete>');assert not aux.document.points
        aux.undo();assert aux.snapshot()==original
        assert required.snapshot()==required_original
        checks['native_multiselect_delete_sort_all_clear_and_undo_keep_segments']=True
        # Both drafts may coexist. Page switches do not resolve either one.
        cell(aux,0,0,'aux draft')
        page(required);required.new_x.set('99');required.new_y.set('88')
        cell(required,1,0,'required draft')
        page(aux)
        assert aux.draft_text.get()=='aux draft' and required.draft_text.get()=='required draft'
        assert aux.snapshot()==original and required.snapshot()==required_original
        assert (required.new_x.get(),required.new_y.get())==('99','88')
        key(aux.entry,'<Escape>');cell(aux,0,0,'625')
        required_history=copy.deepcopy(required.document.history)
        key(aux.tree,'<Control-z>');assert aux.snapshot()==original
        key(aux.tree,'<Control-y>');assert aux.document.points[0][0]==625
        assert required.document.history==required_history and required.draft_text.get()=='required draft'
        assert not app.save() and required.draft is not None
        page(required);key(required.entry,'<Escape>');cell(required,1,0,'1122')
        aux_before=aux.snapshot();aux_history=copy.deepcopy(aux.document.history)
        key(required.tree,'<Control-z>');assert required.snapshot()==required_original
        key(required.tree,'<Control-y>');assert required.document.points[1][0]==1122
        assert aux.snapshot()==aux_before and aux.document.history==aux_history
        checks['separate_drafts_additions_selection_shortcuts_histories_and_page_switch']=True
        # Reload resets both documents; map gestures touch only the active type.
        app.load(project);page(aux);app.render();pump()
        required_before=required.snapshot()
        click(aux,1);app.render();pump()
        assert app.canvases['scene'].find_withtag('selected_auxiliary_2')
        assert app.canvases['scene'].find_withtag('auxiliary_2')
        map_click(1600,1100)
        assert aux.selected()==(1,) and not aux.document.history.undo_stack
        shot('02-auxiliary-map-selection')
        map_click(1600,1100,True)
        assert aux.document.points[1][:2]!=[1600.,1100.]
        assert aux.document.points[1][2]==2 and len(aux.document.history.undo_stack)==1
        assert required.snapshot()==required_before
        aux.undo();assert aux.snapshot()==original
        aux.undo(True);aux.undo();assert aux.snapshot()==original
        aux.new_x.set('41');aux.new_y.set('42');aux.new_segment.set('3')
        map_click(3100,1200)
        assert len(aux.document.points)==6 and aux.document.points[-1][2]==3
        assert (aux.new_x.get(),aux.new_y.get())==('41','42')
        aux.undo();assert aux.snapshot()==original
        map_click(required.document.points[1][0],required.document.points[1][1]);assert aux.snapshot()==original
        assert '另一类点' in aux.feedback.get()
        aux.new_segment.set('99');map_click(3100,1200)
        assert aux.snapshot()==original and aux.segment_error.get()
        aux.new_segment.set('1');aux.segment_error.set('')
        aux_snapshot=aux.snapshot();aux_history=copy.deepcopy(aux.document.history)
        page(required);map_click(required.document.points[1][0],required.document.points[1][1],drag=True)
        assert required.snapshot()!=required_before and aux.snapshot()==aux_snapshot
        assert aux.document.history==aux_history
        key(app.canvases['scene'],'<Control-z>')
        assert required.snapshot()==required_before,{
            'mode':app.map_mode.get(),'tab':app.inputs.select(),'focus':str(root.focus_get()),
            'undo_count':len(required.document.history.undo_stack),'current':required.snapshot(),'before':required_before}
        checks['map_select_highlight_add_drag_single_undo_mode_and_type_isolation']=True
        closed=[*required.document.points,copy.deepcopy(required.document.points[0])]
        required.apply(closed)
        copy_file=directory/'双列表编辑保存加载.cam.json';app.file=copy_file
        assert app.save();loaded=Project.load(copy_file)
        assert loaded.route.waypoints[0]==loaded.route.waypoints[-1]
        assert loaded.route.auxiliary_points==aux.document.controls()
        app.load(loaded);page(aux)
        assert not required.document.history.undo_stack and not aux.document.history.undo_stack
        assert aux.document.controls()==project.route.auxiliary_points
        checks['both_lists_save_load_and_required_return_to_start_preserved']=True
        # A late computation deliberately ignores cancellation; revision must win.
        app.load(project);page(aux);stale=calculate(project)
        entered=threading.Event();release=threading.Event()
        def delayed(*args: Any,**kwargs: Any) -> Any:
            entered.set();release.wait(10);return stale
        with patch('camtool.ui.calculate',side_effect=delayed),patch.object(app.cancel,'is_set',return_value=False):
            app.run(False);pump(lambda:not entered.is_set())
            cell(aux,0,1,'555');release.set();pump(lambda:app.busy)
        assert app.result is None and aux.document.points[0][1]==555
        assert str(app.export_button['state'])=='disabled'
        checks['late_calculation_cannot_replace_auxiliary_edit']=True
        root.geometry('1000x640+20+20');page(aux)
        for var,value in zip((aux.new_x,aux.new_y,aux.new_segment),('NaN','','1.5')):var.set(value)
        aux.add_button.invoke();pump();cell(aux,0,0,'NaN')
        shot('03-small-window-invalid-auxiliary')
        for widget in (aux.add_button,aux.segment_entry,*aux.new_entries,*aux.buttons.values()):
            assert widget.winfo_ismapped() and widget.winfo_rooty()+widget.winfo_height()<=aux.winfo_rooty()+aux.winfo_height(),str(widget)
        assert aux.entry is not None and aux.entry.winfo_ismapped()
        assert aux.tree.winfo_height()>=70,aux.tree.winfo_height()
        key(aux.entry,'<Escape>')
        checks['small_window_edit_table_and_primary_actions_visible']=True
        checks['callback_errors']=errors
        (directory/'双列表编辑验证.json').write_text(json.dumps(checks,ensure_ascii=False,indent=2),encoding='utf-8')
        return checks
    finally:
        aux.cancel_edit();required.cancel_edit()
        app.load(saved[0]);app.file=saved[2];app.dirty=saved[3]
        if saved[1] is not None:app.show_result(saved[1])
        root.report_callback_exception=previous_handler  # type: ignore[method-assign]
