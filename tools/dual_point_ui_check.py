"""Run native editor acceptance with desktop screenshots (no user files edited)."""
import json
import sys
import tkinter as tk
from pathlib import Path
from PIL import ImageGrab

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from camtool.ui import App,prepare_display
from camtool.version import VERSION
from camtool.editor_smoke import check_point_editors

output=ROOT/'build/visual-qa'/f'dual-point-v{VERSION}';output.mkdir(parents=True,exist_ok=True)
prepare_display();root=tk.Tk();app=App(root)
def capture(name):
    root.lift();root.update()
    x,y=root.winfo_rootx(),root.winfo_rooty()
    ImageGrab.grab(bbox=(x,y,x+root.winfo_width(),y+root.winfo_height())).save(output/f'{name}.png')
try:print(json.dumps(check_point_editors(app,output,capture),ensure_ascii=False))
finally:app.cancel.set();app.auxiliary.cancel.set();root.destroy()
