"""Diagnosis-first real Tk workflow; deterministic built-in theory, no user file."""
from __future__ import annotations
import copy,hashlib,json,sys,threading,time,tkinter as tk
from pathlib import Path
from unittest.mock import patch
from PIL import ImageGrab
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from camtool.ui import App,prepare_display
from camtool.workbench import demo_project
from camtool.engine import calculate
from camtool.improvement import software_pass,assert_permissions

out=Path(sys.argv[1]) if len(sys.argv)>1 else ROOT/'build/diagnostic-assistant-20261022/gui-diagnosis'
out.mkdir(parents=True,exist_ok=True)
prepare_display();root=tk.Tk();app=App(root);page=app.improvement
root.geometry('1360x780+20+20');errors=[];beats=[];checks={}
root.report_callback_exception=lambda *args:errors.append(str(args))
def tick():beats.append(time.monotonic());root.after(20,tick)
root.after(20,tick)
def wait(predicate=lambda:False,timeout=160):
    deadline=time.monotonic()+timeout
    while True:
        root.update();time.sleep(.01)
        if not predicate():break
        if time.monotonic()>deadline:raise AssertionError('GUI timeout '+page.status.get())
    assert not errors,errors

def shot(name):
    root.attributes('-topmost',True);root.lift();root.update();time.sleep(.15);root.update()
    x,y=root.winfo_rootx(),root.winfo_rooty();im=ImageGrab.grab(bbox=(x,y,x+root.winfo_width(),y+root.winfo_height()))
    assert im.getpixel((2,2))[:3]==(23,39,64);im.save(out/(name+'.png'));root.attributes('-topmost',False)

try:
    p=demo_project();p.cam.base_radius=25
    app.load(p);file=out/'protected-original.cam.json';p.save(file);app.file=file;app.dirty=False
    digest=hashlib.sha256(file.read_bytes()).hexdigest();original=app.collect().to_dict();rev=app.revision
    n=len(beats);page.open();assert page.busy;wait(lambda:page.busy)
    assert len(beats)>n and page.book.select()==str(page.home)
    assert page.diagnosis is not None and page.diagnostic_result.project.to_dict()==original
    assert not any(v.get() for v in page.enabled.values())
    text=page.diagnosis_text.get('1.0','end');assert '压力角' in text and '已确认事实' in text and '待验证原因' in text
    assert abs(page.diagnosis['problems'][0]['diagnostic']['measurements']['actual_deg']-page.diagnostic_result.stats['max_pressure'])<1e-9
    assert app.collect().to_dict()==original and app.revision==rev and app.file==file and not app.dirty
    shot('01-actual-diagnosis-before-any-authorization')
    with patch('camtool.improvement_ui.calculate',side_effect=AssertionError('should reuse matching full result')):
        page.analyze();wait(lambda:page.busy);assert '复用' in page.status.get()
    checks['automatic_diagnosis_no_authorization_full_facts_and_snapshot_reuse']=True
    page.problem_tree.selection_set('0');page.locate_problem();wait()
    assert app.workbench.focus.name=='压力角' and app.workbench.focus.angle_deg is not None
    page.open();page.book.select(page.permissions_page)
    root.geometry('1000x640+20+20');wait()
    assert page.confirm_button.winfo_ismapped() and page.confirm_button.winfo_rooty()+page.confirm_button.winfo_height()<=root.winfo_rooty()+root.winfo_height()
    shot('02-component-permission-small-window')
    root.geometry('1360x780+20+20');wait()
    page.component_permissions['凸轮尺寸'].set('尚未制造，允许改尺寸')
    assert not any(v.get() for v in page.enabled.values())
    page.confirm_permissions()
    assert not any(v.get() for v in page.enabled.values()) and '安装' in page.permission_note.get()
    page.installation_ack.set(True);page.confirm_permissions()
    assert [k for k,v in page.enabled.items() if v.get()]==['cam.base_radius']
    assert '探索暂定' in page.permission_note.get() and '不是安全范围' in page.permission_note.get()
    # No numeric range or budget needs to be entered for the default workflow.
    before=len(beats);page.start();assert page.busy;wait(lambda:page.busy)
    assert len(beats)>before and page.outcome is not None
    outcome=page.outcome
    (out/'actual-default-plan.md').write_text(outcome.report(),encoding='utf-8')
    c=next((c for c in outcome.candidates if c.independent_pass and software_pass(c.result)),None)
    assert c is not None,outcome.report()
    assert_permissions(p,c.project,outcome.settings)
    assert app.collect().to_dict()==original and hashlib.sha256(file.read_bytes()).hexdigest()==digest
    page.tree.selection_set(str(c.number));page.preview();wait()
    detail=page.detail.get('1.0','end')
    for expected in ('为什么比较','支持证据','其他指标恶化','安装代价','采样复核','实际DXF独立审计'):assert expected in detail,expected
    assert f'{c.project.cam.base_radius:g}' in detail and c.comparison['resolved']==['压力角']
    assert outcome.settings.range_sources['cam.base_radius'].startswith('探索暂定')
    assert all('effect' in t for t in outcome.trials if t.get('stats') is not None)
    shot('03-calculated-modification-list')
    export_dir=out/('default-flow-export-'+str(time.time_ns()))
    with patch('tkinter.filedialog.askdirectory',return_value=str(export_dir)):
        page.export();wait(lambda:page.busy)
    assert hashlib.sha256((export_dir/f'candidate-{c.number}/design/cam.dxf').read_bytes()).hexdigest()==c.audit.sha256
    checks['default_flow_exports_exact_audited_candidate_bytes']=True
    page.adopt();wait();assert app.collect().to_dict()==c.project.to_dict() and app.file is None and app.dirty
    assert page.diagnostic_result.project.to_dict()==app.collect().to_dict()
    page.undo();wait();assert app.collect().to_dict()==original and app.file==file and not app.dirty
    assert hashlib.sha256(file.read_bytes()).hexdigest()==digest
    checks['component_confirmation_with_default_ranges_actual_trial_preview_adopt_undo_original_protection']=True
    # Cancelling analysis leaves an explicit recoverable state, never a fake reading placeholder.
    app.result=None;page.analyze();assert page.busy;page.cancel.set();wait(lambda:page.busy)
    assert page.diagnosis is None and '取消' in page.diagnosis_text.get('1.0','end')
    checks['real_analysis_cancel_no_stuck_reading_or_project_change']=True
    # An intentionally uncancellable old diagnosis cannot overwrite a switched project.
    entered=threading.Event();release=threading.Event();calls=[]
    old=copy.deepcopy(outcome.baseline)
    def delayed(project,cancelled):
        calls.append(project.name)
        if len(calls)==1:entered.set();release.wait(10);return old
        return calculate(project,cancelled)
    with patch('camtool.improvement_ui.calculate',side_effect=delayed):
        page.analyze();wait(lambda:not entered.is_set())
        other=copy.deepcopy(p);other.name='新项目的诊断';app.load(other);release.set()
        wait(lambda:page.busy or page.diagnosis is None)
    assert app.collect().name==other.name and page.diagnosis['project']['name']==other.name
    assert not any(v.get() for v in page.enabled.values()) and page.outcome is None
    checks['project_switch_late_analysis_rejected_and_new_project_auto_analyzed']=True
    app.values['vehicle.wheelbase'].set('not-a-number');page.open();wait(lambda:page.busy)
    assert page.diagnosis is None and page.invalid_fields==('vehicle.wheelbase',)
    assert 'vehicle.wheelbase' in page.diagnosis_text.get('1.0','end')
    page.focus_invalid();wait();assert app.views.index(app.views.select())==0
    assert str(app.controls['vehicle.wheelbase']).startswith(str(app.inputs.select())+'.')
    app.values['vehicle.wheelbase'].set('0');page.open();wait(lambda:page.busy)
    assert page.invalid_fields==('vehicle.wheelbase',)
    app.values['vehicle.wheelbase'].set(str(p.vehicle.wheelbase));page.open();wait(lambda:page.busy)
    assert page.diagnosis is not None and not any(v.get() for v in page.enabled.values())
    checks['invalid_input_field_and_recovery_without_blank_diagnosis']=True
    # Same revision is insufficient if a result has the wrong project snapshot.
    data=page.diagnosis;wrong=copy.deepcopy(page.diagnostic_result);wrong.project.name='wrong snapshot'
    page.install_diagnosis(wrong);assert page.diagnosis is data and '拒绝' in page.status.get()
    checks['wrong_snapshot_diagnosis_rejected']=True
    checks['callback_errors']=errors;checks['heartbeat_count']=len(beats)
    checks['source']='demo_project(False), only base radius=25 mm; no screenshot-specific project available'
    checks['real_case']={'before_pressure_deg':outcome.baseline.stats['max_pressure'],'after_pressure_deg':c.result.stats['max_pressure'],
        'original_radius_mm':p.cam.base_radius,'candidate_radius_mm':c.project.cam.base_radius,'trials':len(outcome.trials),
        'elapsed_seconds':outcome.elapsed,'stop_reason':outcome.stop_reason,'export_directory':export_dir.name,'status':c.status,'dxf_sha256':c.audit.sha256,'comparison':c.comparison,'settings':vars(outcome.settings)}
    # Serialize settings through the existing export rather than leaking dataclasses.
    checks['real_case']['settings']={'adjustments':[vars(a) for a in outcome.settings.adjustments],**{k:v for k,v in vars(outcome.settings).items() if k!='adjustments'}}
    (out/'diagnosis-ui.json').write_text(json.dumps(checks,ensure_ascii=False,indent=2),encoding='utf-8')
    print('PASS: automatic facts before authorization, component decisions/default trial, preview/adopt/undo, invalid fields, cancellation and stale diagnosis')
finally:
    page.cancel.set();app.cancel.set();app.auxiliary.cancel.set();app.workbench.analysis_cancel.set();app.verifier.cancel.set();root.destroy()
