"""Explicit frozen/source acceptance; never runs during normal startup."""
from __future__ import annotations
import copy
import hashlib
import json
import threading
import time
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable
from unittest.mock import patch
from .workbench import demo_project
from .improvement import software_pass
if TYPE_CHECKING:
    from .ui import App


def check_improvement(app: App,out: Path,capture: Callable[[str],None] | None=None) -> dict[str,Any]:
    out.mkdir(parents=True,exist_ok=True)
    root=app.root;page=app.improvement
    previous_handler=root.report_callback_exception
    root.geometry('1360x780+20+20')
    errors=[];checks: dict[str,Any]={};beats=[]
    root.report_callback_exception=lambda *args:errors.append(str(args))
    def heartbeat():beats.append(time.monotonic());root.after(20,heartbeat)
    root.after(20,heartbeat)
    def wait(predicate=lambda:False,timeout=150):
        end=time.monotonic()+timeout;root.update()
        while predicate():
            if time.monotonic()>end:raise AssertionError('GUI timeout '+page.status.get())
            time.sleep(.01);root.update()
        root.update();assert not errors,errors
    def screenshot(name):
        if capture is not None:capture(name)
    try:
        with patch('tkinter.messagebox.showerror',side_effect=lambda *a,**k:errors.append(str(a))):
            p=demo_project();p.cam.base_radius=25
            if getattr(sys,'frozen',False):
                from .project import Project
                packaged=Project.load(Path(sys.executable).parent/'可复现理论案例/改进助手_压力角理论案例.cam.json')
                assert packaged.to_dict()==p.to_dict()
                p=packaged
            app.load(p)
            file=out/'untouched-user-project.cam.json';p.save(file)
            digest=hashlib.sha256(file.read_bytes()).hexdigest();app.file=file
            # Existing point history is separate and must survive structural adoption.
            modified=copy.deepcopy(p.route.waypoints);modified[1][0]+=1
            app.waypoints.apply(modified);app.undo_route();app.dirty=False
            original=app.collect().to_dict()
            point_history=copy.deepcopy(app.waypoints.document.history)
            page.open();wait(lambda:page.busy)
            assert not any(v.get() for v in page.enabled.values())
            page.start();assert not page.busy and '锁定' in page.status.get()
            assert page.book.select()==str(page.home) and page.diagnosis is not None
            assert '已确认事实' in page.diagnosis_text.get('1.0','end')
            assert '待验证原因' in page.diagnosis_text.get('1.0','end')
            checks['automatic_diagnosis_before_authorization']=True
            page.component_permissions['凸轮尺寸'].set('尚未制造，允许改尺寸')
            page.confirm_permissions();assert not any(v.get() for v in page.enabled.values())
            page.installation_ack.set(True);page.confirm_permissions()
            assert [k for k,v in page.enabled.items() if v.get()]==['cam.base_radius']
            assert '探索暂定' in page.permission_note.get()
            checks['component_authorization_requires_explicit_installation_consent']=True
            page.budgets['evaluations'].set('4');page.budgets['seconds'].set('90');page.budgets['audit_top'].set('1')
            screenshot('authorization-default-locks')
            page.start();assert page.busy
            n=len(beats);page.start();wait(lambda:page.busy)
            assert len(beats)>n and page.outcome is not None,page.status.get()
            outcome=page.outcome;c=outcome.candidates[0]
            assert c.independent_pass and software_pass(c.result)
            assert app.collect().to_dict()==original
            assert len(outcome.trials)>0
            checks['actual_bounded_search_responsive_and_original_untouched']=True
            before=app.collect().to_dict()
            page.tree.selection_set(str(c.number));page.preview();wait()
            assert app.collect().to_dict()==before
            assert '转轴' in page.detail.get('1.0','end')
            assert len(page.check_tree.get_children())>=len(c.result.checks)
            screenshot('candidate-with-calculated-changes')
            root.geometry('1000x640+20+20');wait();screenshot('small-window-results')
            assert page.tree.winfo_height()>40 and page.detail.winfo_height()>80
            page.adopt();wait()
            assert app.collect().to_dict()==c.project.to_dict() and app.file is None and app.dirty
            assert app.result is not None and app.result.project.to_dict()==app.collect().to_dict()
            assert app.waypoints.document.history==point_history
            assert hashlib.sha256(file.read_bytes()).hexdigest()==digest
            assert page.current['cam.base_radius'].get()==f'{c.project.cam.base_radius:g}'
            page.undo();wait()
            assert app.collect().to_dict()==original and app.file==file and not app.dirty
            assert app.waypoints.document.history==point_history
            page.undo(True);assert app.collect().to_dict()==c.project.to_dict()
            page.keep_original();assert app.collect().to_dict()==original
            checks['preview_adopt_undo_redo_point_history_and_disk_preservation']=True
            export_folder=out/f'exported-plan-{time.time_ns()}'
            with patch('tkinter.filedialog.askdirectory',return_value=str(export_folder)):
                page.export();wait(lambda:page.busy)
            dxf=export_folder/f'candidate-{c.number}/design/cam.dxf'
            assert hashlib.sha256(dxf.read_bytes()).hexdigest()==c.audit.sha256
            record=json.loads((export_folder/'方案与试算记录.json').read_text(encoding='utf-8'))
            assert record['candidates'][0]['independent_pass']
            checks['export_exact_dxf_hash_and_readable_plan']=True
            # A late completed outcome must not be accepted after cancellation.
            page.outcome=None;page.busy=True;page.running_generation=page.generation
            page.cancel.set();page.events.put((page.generation,app.revision,'done',outcome))
            page.events.put((page.generation,app.revision,'finished',None));wait(lambda:page.busy)
            assert page.outcome is None and app.collect().to_dict()==original
            checks['queued_result_after_cancel_is_rejected']=True
            # Real cancellation in numerical trial; it must not adopt or save.
            page.cancel.clear();page.start();root.update();page.cancel.set();wait(lambda:page.busy)
            assert page.outcome is None and app.collect().to_dict()==original
            checks['real_search_cancel_no_project_mutation']=True
            # Deliberately ignore cancellation to exercise project revision guard.
            entered=threading.Event();release=threading.Event()
            def delayed(*args,**kwargs):entered.set();release.wait(5);return outcome
            with patch('camtool.improvement_ui.run_improvement',side_effect=delayed):
                page.start();wait(lambda:not entered.is_set())
                other=copy.deepcopy(p);other.name='切换项目测试'
                app.load(other);release.set();wait(lambda:page.busy)
            assert page.outcome is None and app.collect().name==other.name
            checks['project_switch_rejects_uncancellable_late_worker']=True
            ball=demo_project();ball.mechanism.kind='ball';app.load(ball);page.open()
            page.mode.set('允许结构改型');wait()
            assert str(page.checks['mechanism.offset']['state'])=='disabled'
            assert not page.enabled['mechanism.offset'].get()
            assert not any(v.get() for v in page.enabled.values())
            page.enabled['mechanism.arm'].set(True);page.parameter_toggled()
            page.mode.set('仅调整路线');page.mode.set('允许结构改型')
            assert not page.enabled['mechanism.arm'].get()
            checks['mechanism_specific_controls_and_mode_never_unlocks_hardware']=True
            assert hashlib.sha256(file.read_bytes()).hexdigest()==digest
            checks['callback_errors']=errors;checks['heartbeat_count']=len(beats)
            checks['export_directory']=export_folder.name
            checks['real_case']=dict(before_pressure=outcome.baseline.stats['max_pressure'],after_pressure=c.result.stats['max_pressure'],
                changes=record['candidates'][0]['modifications'],comparison=c.comparison,status=c.status,
                sha256=c.audit.sha256,trials=len(outcome.trials),elapsed=outcome.elapsed,stop_reason=outcome.stop_reason)
            (out/'improvement-ui.json').write_text(json.dumps(checks,ensure_ascii=False,indent=2),encoding='utf-8')
            return checks
    finally:
        page.cancel.set();app.cancel.set();app.auxiliary.cancel.set();app.verifier.cancel.set();app.workbench.analysis_cancel.set();root.report_callback_exception=previous_handler  # type: ignore[method-assign]

