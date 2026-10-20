"""Exercise the application's own UI and capture its window for visual QA."""
import json
import sys
import tkinter as tk
from unittest.mock import patch
from pathlib import Path

from PIL import ImageGrab

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from camtool import Project, calculate
from camtool.ui import App, prepare_display

output = Path(__file__).resolve().parents[1]/"build"/"visual-qa"
output.mkdir(parents=True, exist_ok=True)
prepare_display()
root = tk.Tk()
app = App(root)
root.update()
assert app.status_label.winfo_y()+app.status_label.winfo_height() <= root.winfo_height()
assert app.slider.winfo_ismapped()
app.show_result(calculate(app.collect()))
root.update()
for tab, name in [(0, "scene"), (1, "cam"), (2, "mechanism"), (3, "curves"), (4, "report")]:
    app.views.select(tab)
    root.update()
    app.render()
    root.update()
    x, y = root.winfo_rootx(), root.winfo_rooty()
    ImageGrab.grab(bbox=(x, y, x+root.winfo_width(), y+root.winfo_height())).save(output/f"{name}.png")
app.values["vehicle.wheelbase"].set("140")
assert app.result is None
assert str(app.export_button["state"]) == "disabled"
assert app.collect().vehicle.wheelbase == 140
app.load(Project.demo())
app.tree.selection_set("1")
app.select_row(None)
assert float(app.point_x.get()) == 1100
precise = Project.demo()
precise.vehicle.wheelbase = 120.123456789
precise.route.waypoints[1][0] = 1100.123456789
app.load(precise)
assert app.collect().to_dict() == precise.to_dict()
app.load(Project.demo())
app.run(False)
retained = None
def done():
    global retained
    if app.busy:
        root.after(100, done)
    else:
        assert app.result is not None
        assert app.result.valid
        retained = app.result
        app.run(True)
        app.cancel.set()
        root.after(100, cancelled_done)
def cancelled_done():
    if app.busy:
        root.after(100, cancelled_done)
    else:
        assert app.result is retained
        assert str(app.export_button["state"]) == "normal"
        (output/"ui-check.json").write_text(json.dumps({"ui_ok": True, "precision_ok": True,
                                                     "cancel_retains_export": True}), encoding="utf-8")
        root.destroy()
root.after(200, done)
root.after(20000, root.destroy)
with patch("tkinter.messagebox.showerror"):
    root.mainloop()
