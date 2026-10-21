import json,sys,tkinter as tk
from pathlib import Path
from PIL import ImageGrab
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from camtool.ui import App,prepare_display
from camtool.version import VERSION
from camtool.view_smoke import check_views
folder=ROOT/'build/visual-qa'/f'views-v{VERSION}';folder.mkdir(parents=True,exist_ok=True)
prepare_display();root=tk.Tk();app=App(root)
def capture(name):
    window=root
    if name in ('02-popout-route','03-maximized-route'):window=app.figure_windows['scene'][0]
    elif name=='04-cam-contact-detail':window=app.figure_windows['cam'][0]
    window.lift();root.update();x,y=window.winfo_rootx(),window.winfo_rooty()
    ImageGrab.grab(bbox=(x,y,x+window.winfo_width(),y+window.winfo_height())).save(folder/(name+'.png'))
try:print(json.dumps(check_views(app,folder,capture),ensure_ascii=False))
finally:app.cancel.set();root.destroy()
