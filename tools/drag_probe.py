"""Measure the actual native drag path; red on clear/rebuild flicker pattern."""
import json,sys,time,tkinter as tk
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from camtool.ui import App,prepare_display
prepare_display();root=tk.Tk();app=App(root);root.update();app.render();root.update()
canvas=app.canvases['scene'];counts={'canvas_all_clears':0,'table_deletes':0,'motions':80,'calculation_calls':0}
old_delete=canvas.delete;old_tree_delete=app.tree.delete
def delete(*args):
    if 'all' in args:counts['canvas_all_clears']+=1
    return old_delete(*args)
def tree_delete(*args):counts['table_deletes']+=1;return old_tree_delete(*args)
def calculate(*args,**kwargs):counts['calculation_calls']+=1;raise AssertionError('拖动触发计算')
try:
    x,y=app.scene_plot.xy(*app.route_snapshot()['points'][1]);start=time.perf_counter()
    with patch.object(canvas,'delete',side_effect=delete),patch.object(app.tree,'delete',side_effect=tree_delete),patch('camtool.ui.calculate',side_effect=calculate):
        canvas.event_generate('<ButtonPress-1>',x=round(x),y=round(y));root.update()
        for i in range(80):
            canvas.event_generate('<B1-Motion>',x=round(x+1+i),y=round(y-10));root.update()
        canvas.event_generate('<ButtonRelease-1>',x=round(x+80),y=round(y-10));root.update()
    counts['elapsed_ms']=(time.perf_counter()-start)*1000
    target=Path(sys.argv[1]) if len(sys.argv)>1 else ROOT/'build/drag-probe.json'
    target.parent.mkdir(parents=True,exist_ok=True);target.write_text(json.dumps(counts,indent=2),encoding='utf-8');print(counts)
    assert counts['canvas_all_clears']==0 and counts['table_deletes']==0 and counts['calculation_calls']==0,counts
finally:app.cancel.set();root.destroy()
