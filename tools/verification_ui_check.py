"""Exercise the real independent GUI workflow and save visual QA evidence."""
import json
import sys
import tempfile
import time
import tkinter as tk
from pathlib import Path
from unittest.mock import patch

from PIL import ImageGrab

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from camtool import calculate, export_design
from camtool.ui import App, prepare_display
from camtool.version import VERSION

output=Path(sys.argv[1]) if len(sys.argv)>1 else ROOT/'build'/'visual-qa'/f'verification-v{VERSION}'
output.mkdir(parents=True,exist_ok=True)
prepare_display()
root=tk.Tk()
app=App(root)
root.geometry('1360x780+20+20')
errors=[]


def pump_until_idle(page,timeout=60):
    end=time.monotonic()+timeout
    while page.busy:
        root.update()
        if time.monotonic()>end:
            raise AssertionError('verification GUI timeout')
        time.sleep(0.01)
    root.update()


def screenshot(name):
    root.lift()
    root.update()
    x,y=root.winfo_rootx(),root.winfo_rooty()
    ImageGrab.grab(bbox=(x,y,x+root.winfo_width(),y+root.winfo_height())).save(output/f'{name}.png')


try:
    with patch('tkinter.messagebox.showerror',side_effect=lambda *a,**k: errors.append(a)):
        project=app.collect()
        design=calculate(project)
        app.show_result(design)
        files=export_design(design,output/'design-export',overwrite=True)
        original=app.collect().to_dict()
        app.views.select(app.verification_frame)
        root.update()
        page=app.verifier
        page.copy_design()
        page.file.set(str(files['dxf']))
        root.update()
        assert app.verification_frame.winfo_width()>1200
        page.run()
        pump_until_idle(page)
        assert page.result is not None and page.result.kinematic_pass,errors
        result=page.result
        for index,name in enumerate(['route','angles','checkpoints','checks','issues','parameters']):
            page.outputs.select(index)
            page.render()
            root.update()
            screenshot(name)
        report_dir=Path(tempfile.mkdtemp(prefix='gui-report-',dir=output))
        with patch('tkinter.filedialog.askdirectory',return_value=str(report_dir)):
            page.export()
        assert (report_dir/'二次检验报告.md').is_file()
        root.geometry('1000x640+20+20')
        page.outputs.select(0)
        root.update()
        assert page.status_label.winfo_ismapped()
        assert page.status_label.winfo_y()+page.status_label.winfo_height()<=app.verification_frame.winfo_height()
        assert not app.status_label.winfo_ismapped()
        screenshot('small-window')
        page.vars['vehicle.wheelbase'].set('135')
        assert page.result is None and str(page.export_button['state'])=='disabled'
        assert app.collect().to_dict()==original
        page.copy_design()
        page.run()
        page.cancel.set()
        pump_until_idle(page)
        assert page.result is None
        assert not errors and '停止' in page.message.get(),(errors,page.message.get())
        errors.clear()
        app.views.select(0)
        root.update()
        assert app.inputs.winfo_ismapped() and app.slider.winfo_ismapped()
        app.show_version()
        root.update()
        windows=[w for w in root.winfo_children() if isinstance(w,tk.Toplevel)]
        assert any(VERSION in w.title() for w in windows)
        assert not errors,errors
        (output/'ui-check.json').write_text(json.dumps({'software_version':VERSION,'async_verification':True,
            'full_width_page':True,'independent_editing':True,'stale_export_disabled':True,'cancel_checked':True,
            'report_export':True,'version_dialog':True,'small_window':True,'restores_design':True,
            'path_points':len(result.path),'metrics':result.metrics},ensure_ascii=False,indent=2),encoding='utf-8')
        print('PASS: real async verifier, all result tabs, report export, independent editing, stale results, cancellation, small window, version dialog and design restoration')
finally:
    app.verifier.cancel.set()
    root.destroy()
