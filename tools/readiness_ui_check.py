"""Explicit real-Tk check of examples, delivery, verification and late cancel.

Run manually: python tools/readiness_ui_check.py [output-directory]
Never imported or executed by normal application startup.
"""
from __future__ import annotations

import hashlib
import json
import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from unittest.mock import patch

from PIL import ImageGrab

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from camtool import ProjectError, export_design
from camtool.ui import App, prepare_display
from camtool.workbench_ui import prepare_views
from camtool.version import VERSION


def main() -> None:
    output = Path(sys.argv[1]) if len(sys.argv)>1 else ROOT/'build/hackathon-readiness-20261022/gui'
    output.mkdir(parents=True, exist_ok=True)
    prepare_display()
    root = tk.Tk()
    app = App(root)
    root.geometry('1360x780+20+20')
    errors: list[str] = []
    root.report_callback_exception = lambda *args: errors.append(str(args))
    checks: dict = {}
    beats: list[float] = []
    def heartbeat() -> None:
        beats.append(time.monotonic())
        root.after(20, heartbeat)
    root.after(20, heartbeat)
    def wait(predicate=lambda:False, timeout=120) -> None:
        deadline = time.monotonic()+timeout
        root.update()
        while predicate():
            if time.monotonic()>deadline:
                raise AssertionError('GUI timeout: '+app.status.get())
            time.sleep(.01)
            root.update()
        root.update()
        assert not errors, errors
    def screenshot(name: str) -> None:
        # Flush delayed plots and raise the test window so desktop captures are
        # not black/occluded. This test-only action never runs on app startup.
        root.attributes('-topmost',True); root.lift(); app.render(); root.update()
        time.sleep(.15); root.update()
        x,y=root.winfo_rootx(),root.winfo_rooty()
        shot=ImageGrab.grab(bbox=(x,y,x+root.winfo_width(),y+root.winfo_height()))
        assert shot.getpixel((2,2))[:3]==(23,39,64), 'Screenshot is not the application header'
        shot.save(output/f'{name}.png')
        root.attributes('-topmost',False)
    try:
        with patch('tkinter.messagebox.showerror', side_effect=lambda *a,**k:errors.append(str(a))):
            wait()
            assert not app.busy and app.result is None
            checks['normal_start_has_no_demo_or_acceptance_task'] = True
            original=app.collect()
            user_file=output/'preserved-project.cam.json'
            original.save(user_file)
            digest=hashlib.sha256(user_file.read_bytes()).hexdigest()
            app.file=user_file; app.dirty=True
            app.parameter_source.set('GUI测试来源，非实测')
            page=app.workbench
            app.show_workbench('demo')
            page.load_case(False)
            wait(lambda:app.busy)
            normal=app.result
            assert normal is not None and normal.valid and normal.precision.get('stable')
            assert '来源' in page.case_info.get() and '未实测' in page.case_info.get()
            app.refresh_workflow()
            assert '最终 DXF：未检验' in app.workflow_status.get()
            checks['normal_case'] = dict(source='camtool.workbench.demo_project(False)',
                input=normal.project.to_dict(),checks=[vars(c) for c in normal.checks],precision=normal.precision,
                nominal_passed=sum(bool(r['passed']) for r in normal.checkpoint_rows))
            app.play(); wait(); app.pause()
            app.render()
            assert all(len(page.canvases[key].find_all())>10 for key in ('demo_route','demo_cam','demo_mechanism'))
            screenshot('normal-synchronized')
            export_folder=output/'normal-export'
            with patch('tkinter.filedialog.askdirectory',return_value=str(export_folder)), patch('tkinter.messagebox.askyesno',return_value=True):
                app.export()
                assert app.busy
                wait(lambda:app.busy)
            assert app.export_receipt is not None
            actual_hash=hashlib.sha256((export_folder/'cam.dxf').read_bytes()).hexdigest()
            assert actual_hash==app.export_receipt[2]
            app.next_step()
            wait()
            assert app.views.select()==str(app.verification_frame)
            verifier=app.verifier
            assert Path(verifier.file.get())==export_folder/'cam.dxf'
            with patch('tkinter.filedialog.askopenfilename',return_value=str(export_folder/'motion.csv')):
                verifier.choose_nominal()
            assert 'motion.csv' in verifier.nominal_source and str(export_folder) not in verifier.nominal_source
            verifier.run()
            beat_start=len(beats)
            wait(lambda:verifier.busy)
            assert len(beats)>beat_start
            audit=verifier.result
            assert audit is not None and audit.kinematic_pass
            assert audit.sha256==actual_hash
            app.refresh_workflow()
            assert '最终 DXF：独立运动学通过' in app.workflow_status.get()
            checks['normal_dxf'] = dict(sha256=audit.sha256,kinematic_pass=audit.kinematic_pass,
                checks=[vars(c) for c in audit.checks],metrics=audit.metrics,
                input_sources=audit.inputs.sources)
            app.views.select(app.verification_frame); wait()
            screenshot('independent-verification')
            # A fully computed result cancelled before UI delivery is rejected.
            verifier.result=None
            verifier.busy=True
            verifier.cancel.set()
            verifier.events.put(('result',(verifier.revision,audit)))
            wait(lambda:verifier.busy)
            assert verifier.result is None and '停止' in verifier.message.get()
            app.result=None; app.busy=True; app.cancel.set()
            app.events.put(('done',(app.revision,normal,prepare_views(normal))))
            wait(lambda:app.busy)
            assert app.result is None and str(app.export_button['state'])=='disabled'
            page.analysis_busy=True; page.analysis_cancel.set(); page.perturbations=[]
            page.events.put((page.analysis_generation,'perturb',(app.revision,[{'stale':True}])))
            page.events.put((page.analysis_generation,'finished',None))
            wait(lambda:page.analysis_busy)
            assert not page.perturbations and '停止' in page.analysis_status.get()
            checks['queued_calculation_and_verification_results_rejected_after_cancel'] = True
            # Existing valid bytes survive an in-progress cancelled export.
            app.show_result(normal)
            before={p.name:p.read_bytes() for p in export_folder.iterdir() if p.is_file()}
            started=threading.Event(); release=threading.Event()
            def slow_export(result, folder, **kwargs):
                started.set()
                while not release.wait(.01):
                    if kwargs['cancelled']():
                        raise ProjectError('导出已停止；已有文件未替换。')
                return export_design(result,folder,**kwargs)
            with patch('camtool.ui.export_design',side_effect=slow_export) as mocked, patch('tkinter.filedialog.askdirectory',return_value=str(export_folder)), patch('tkinter.messagebox.askyesno',return_value=True):
                app.export(); wait(lambda:not started.is_set())
                app.export()
                count=len(beats)
                for _ in range(15):
                    time.sleep(.01); root.update()
                assert len(beats)>count and mocked.call_count==1
                app.cancel.set(); release.set(); wait(lambda:app.busy)
            assert before=={p.name:p.read_bytes() for p in export_folder.iterdir() if p.is_file()}
            assert app.export_receipt is None
            checks['export_responsive_cancellable_and_duplicate_click_guarded'] = True
            page.load_case(True); wait(lambda:app.busy)
            problem=app.result
            assert problem is not None and not problem.valid
            failed=[(i,d) for i,d in enumerate(page.diags) if d.status=='fail']
            assert failed
            app.show_workbench('issues')
            page.issue_tree.selection_set(str(failed[0][0])); page.choose_issue()
            assert '超限影响' in page.issue_detail.get('1.0','end')
            assert page.focus is not None and page.focus.route_index is not None
            checks['problem_case'] = dict(source='camtool.workbench.demo_project(True)',
                input=problem.project.to_dict(),failures=[c.name for c in problem.checks if c.status=='fail'])
            screenshot('problem-diagnosis')
            root.geometry('1000x640+20+20'); wait()
            assert app.next_button.winfo_ismapped()
            assert page.issue_tree.winfo_height()>40
            assert page.canvases['issue_cam'].winfo_height()>100
            screenshot('small-window')
            # A real planning edit must invalidate and actually recompute. Keep
            # hard limits/obstacle so a retry cannot manufacture a green result.
            app.values['route.tension'].set('0.8')
            assert app.result is None
            app.run(False); wait(lambda:app.busy)
            assert app.result is not None and not app.result.valid
            assert app.result.project.vehicle.max_steer==problem.project.vehicle.max_steer
            assert app.result.project.cam.pressure_limit==problem.project.cam.pressure_limit
            assert app.result.project.route.obstacles==problem.project.route.obstacles
            checks['adjust_recompute_retains_actual_failures_and_constraints']=True
            app.values['vehicle.wheelbase'].set(str(problem.project.vehicle.wheelbase+1))
            app.refresh_workflow()
            assert app.result is None and app.export_receipt is None
            assert '已失效' in app.workflow_status.get()
            assert str(app.export_button['state'])=='disabled'
            page.restore_project()
            assert app.collect().to_dict()==original.to_dict() and app.file==user_file and app.dirty
            assert app.parameter_source.get()=='GUI测试来源，非实测'
            assert hashlib.sha256(user_file.read_bytes()).hexdigest()==digest
            checks['modified_inputs_invalidate_and_original_project_restores_unchanged'] = True
            checks['callback_errors']=errors
            checks['heartbeat_count']=len(beats)
            checks['software_version']=VERSION
            (output/'readiness-ui.json').write_text(json.dumps(checks,ensure_ascii=False,indent=2),encoding='utf-8')
            print('PASS: real examples, background export, independent DXF, late cancellation, invalidation, restore and small window')
    finally:
        app.cancel.set();app.verifier.cancel.set();app.auxiliary.cancel.set();app.workbench.analysis_cancel.set()
        root.destroy()


if __name__=='__main__':
    main()
