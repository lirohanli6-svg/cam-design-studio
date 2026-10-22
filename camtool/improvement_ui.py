"""Main-thread Tk adapter for bounded trials; preview never edits the project."""
from __future__ import annotations

import copy
import json
import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import TYPE_CHECKING, Any

from .engine import Result, calculate, validate
from .improvement import (PARAMETERS, UNSUPPORTED, Adjustment, Candidate, ImprovementOutcome,
                          TrialSettings, assert_permissions, clean, modifications, rule_directions,
                          run_improvement, value, COMPONENTS, diagnose, diagnosis_text, exploration_range)
from .project import Project, ProjectError
from .workbench import RouteHistory

if TYPE_CHECKING:
    from .ui import App


class ImprovementPage:
    def __init__(self, app: App) -> None:
        self.app=app
        self.frame=ttk.Frame(app.views); app.views.add(self.frame,text="改进助手")
        self.mode=tk.StringVar(value="仅调整路线")
        self.status=tk.StringVar(value="先读取并诊断当前项目；未授权也可查看问题。所有部件默认固定。")
        self.cancel=threading.Event();self.events: queue.Queue[tuple[int,int,str,Any]]=queue.Queue()
        self.generation=0;self.running_generation=-1;self.busy=False;self.adopting=False;self.form_loading=False
        self.form_project: Project | None=None;self.needs_form=True
        self.outcome: ImprovementOutcome | None=None
        self.selected: Candidate | None=None
        self.history=RouteHistory();self.cache: dict[str,Result]={};self.anchor_revision=-1
        self.enabled: dict[str,tk.BooleanVar]={};self.lower: dict[str,tk.StringVar]={};self.upper: dict[str,tk.StringVar]={}
        self.current: dict[str,tk.StringVar]={};self.checks: dict[str,ttk.Checkbutton]={};self.entries: dict[str,tuple[ttk.Entry,ttk.Entry]]={}
        self.diagnosis: dict[str,Any] | None=None;self.diagnostic_result: Result | None=None
        self.invalid_fields: tuple[str,...]=();self.phase='';self.analysis_dirty=True
        self.installation_ack=tk.BooleanVar(value=False)
        self.range_sources: dict[str,str]={};self.component_permissions: dict[str,tk.StringVar]={}
        self.permission_labels: dict[str,tk.StringVar]={}
        self.budgets={k:tk.StringVar(value=v) for k,v in [('evaluations','12'),('seconds','60'),('audit_top','2')]}
        row=ttk.Frame(self.frame,padding=4);row.pack(fill="x")
        self.start_button=ttk.Button(row,text="开始实际试算",command=self.start,style="Accent.TButton",width=16)
        self.start_button.pack(side="left",padx=2)
        ttk.Button(row,text="停止",command=self.cancel.set,width=7).pack(side="left",padx=2)
        ttk.Button(row,text="采用所选候选",command=self.adopt,width=16).pack(side="left",padx=2)
        ttk.Button(row,text="保留原案",command=self.keep_original,width=12).pack(side="left",padx=2)
        ttk.Button(row,text="撤销采用",command=self.undo,width=11).pack(side="left",padx=2)
        ttk.Button(row,text="重做采用",command=lambda:self.undo(True),width=11).pack(side="left",padx=2)
        ttk.Button(row,text="导出方案与证据",command=self.export,width=18).pack(side="left",padx=2)
        ttk.Label(self.frame,textvariable=self.status,wraplength=960,padding=5).pack(side="bottom",fill="x")
        self.book=ttk.Notebook(self.frame);self.book.pack(fill="both",expand=True)
        self.build_diagnosis_pages()
        config_page=ttk.Frame(self.book);self.config_page=config_page;self.book.add(config_page,text="高级：参数范围与预算")
        canvas=tk.Canvas(config_page,highlightthickness=0,bg="#edf2f8")
        scroll=ttk.Scrollbar(config_page,command=canvas.yview);canvas.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right",fill="y");canvas.pack(fill="both",expand=True)
        form=ttk.Frame(canvas,padding=6);window=canvas.create_window((0,0),window=form,anchor="nw")
        form.bind("<Configure>",lambda e:canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>",lambda e:canvas.itemconfigure(window,width=e.width))
        canvas.bind("<MouseWheel>",lambda e:canvas.yview_scroll(-int(e.delta/120),"units"))
        top=ttk.Frame(form);top.pack(fill="x")
        ttk.Label(top,text="调整模式").pack(side="left")
        ttk.Combobox(top,textvariable=self.mode,values=("仅调整路线","允许结构改型"),state="readonly",width=16).pack(side="left",padx=5)
        ttk.Button(top,text="重新读取当前输入并锁定全部",command=self.refresh_form,width=29).pack(side="left",padx=6)
        for key,label in [('evaluations','候选次数'),('seconds','整体秒预算'),('audit_top','复算/审计前N个')]:
            ttk.Label(top,text=label).pack(side="left",padx=(5,1))
            ttk.Entry(top,textvariable=self.budgets[key],width=5).pack(side="left")
        ttk.Label(form,text="勾选才授权；模式切换不会解锁硬件。范围需包含当前值以进行单参数对照。所有门槛、车辆尺寸、目标点和采样保持锁定。",
                  wraplength=940,padding=(0,5)).pack(fill="x")
        table=ttk.Frame(form);table.pack(fill="x")
        for col,title in enumerate(('允许','参数 / 单位','当前值','下限','上限','用途 / 机构限制')):
            ttk.Label(table,text=title).grid(row=0,column=col,sticky="w",padx=4)
        table.columnconfigure(5,weight=1)
        for i,p in enumerate(PARAMETERS,1):
            self.enabled[p.key]=tk.BooleanVar(value=False);self.current[p.key]=tk.StringVar(value="待读取")
            self.lower[p.key]=tk.StringVar();self.upper[p.key]=tk.StringVar()
            check=ttk.Checkbutton(table,variable=self.enabled[p.key],command=self.parameter_toggled)
            check.grid(row=i,column=0,padx=4);self.checks[p.key]=check
            ttk.Label(table,text=f"{p.name} / {p.unit}").grid(row=i,column=1,sticky="w",padx=4,pady=5)
            ttk.Label(table,textvariable=self.current[p.key],width=10).grid(row=i,column=2,sticky="w")
            lo=ttk.Entry(table,textvariable=self.lower[p.key],width=9,state="disabled");lo.grid(row=i,column=3,padx=3)
            hi=ttk.Entry(table,textvariable=self.upper[p.key],width=9,state="disabled");hi.grid(row=i,column=4,padx=3)
            self.entries[p.key]=(lo,hi)
            ttk.Label(table,text=p.purpose,wraplength=390).grid(row=i,column=5,sticky="w",padx=5,pady=5)
            self.lower[p.key].trace_add('write',lambda *args,key=p.key:self.range_edited(key));self.upper[p.key].trace_add('write',lambda *args,key=p.key:self.range_edited(key))
        ttk.Label(form,text=UNSUPPORTED,wraplength=950,padding=(0,5)).pack(fill="x")
        self.baseline_text=self.text_box(form,height=8)
        self.baseline_text.configure(state="normal");self.baseline_text.insert('1.0',"打开助手后读取当前输入；后台会重新完整计算基线，不把过期结果当作基线。")
        self.baseline_text.configure(state="disabled")
        self.results=ttk.Frame(self.book);self.book.add(self.results,text="③ 具体修改与验证")
        area=ttk.Frame(self.results);area.pack(fill="x")
        self.tree=ttk.Treeview(area,columns=('id','status','changes','fails','audit'),show='headings',height=3,selectmode='browse')
        for key,label,width in [('id','候选',50),('status','验证层级',250),('changes','实际改动',330),('fails','剩余主失败项',210),('audit','独立审计',110)]:
            self.tree.heading(key,text=label);self.tree.column(key,width=width,minwidth=45)
        bar=ttk.Scrollbar(area,command=self.tree.yview);bar.pack(side='right',fill='y');self.tree.configure(yscrollcommand=bar.set);self.tree.pack(fill='x',expand=True)
        self.tree.bind('<<TreeviewSelect>>',self.preview)
        details=ttk.Notebook(self.results);details.pack(fill='both',expand=True)
        plan=ttk.Frame(details);details.add(plan,text='修改方案 / 依据');self.detail=self.text_box(plan)
        checks=ttk.Frame(details);details.add(checks,text='全部检查前后对照')
        self.check_tree=ttk.Treeview(checks,columns=('name','before','after','limit','state','where'),show='headings')
        for key,title,width in [('name','检查',140),('before','原值（含单位）',230),('after','候选值（含单位）',230),('limit','限制/要求',200),('state','原状态→候选状态',160),('where','候选最坏位置',260)]:
            self.check_tree.heading(key,text=title);self.check_tree.column(key,width=width,minwidth=70)
        sb=ttk.Scrollbar(checks,command=self.check_tree.yview);sb.pack(side='right',fill='y');self.check_tree.configure(yscrollcommand=sb.set)
        horizontal=ttk.Scrollbar(checks,orient='horizontal',command=self.check_tree.xview);horizontal.pack(side='bottom',fill='x');self.check_tree.configure(xscrollcommand=horizontal.set)
        self.check_tree.pack(fill='both',expand=True)
        records=ttk.Frame(details);details.add(records,text='安装代价 / 采样 / 试算记录');self.records=self.text_box(records)
        self.installation_ack.trace_add('write',self.settings_changed)
        self.mode.trace_add('write',self.mode_changed)
        for var in self.budgets.values():var.trace_add('write',self.settings_changed)
        app.root.after(100,self.poll)

    @staticmethod
    def text_box(parent: Any, height: int = 8) -> tk.Text:
        frame=ttk.Frame(parent);frame.pack(fill='both',expand=True)
        text=tk.Text(frame,wrap='word',height=height,state='disabled',padx=8,pady=5,font=('Microsoft YaHei UI',9))
        bar=ttk.Scrollbar(frame,command=text.yview);text.configure(yscrollcommand=bar.set)
        bar.pack(side='right',fill='y');text.pack(fill='both',expand=True)
        return text

    @staticmethod
    def put(widget: tk.Text, text: str) -> None:
        widget.configure(state='normal');widget.delete('1.0','end');widget.insert('1.0',text);widget.configure(state='disabled')

    def build_diagnosis_pages(self) -> None:
        self.home=ttk.Frame(self.book);self.book.add(self.home,text='① 当前问题与可能原因')
        actions=ttk.Frame(self.home);actions.pack(fill='x',padx=4,pady=4)
        ttk.Button(actions,text='重新读取并分析',width=17,command=self.analyze).pack(side='left',padx=2)
        ttk.Button(actions,text='定位所选问题',width=14,command=self.locate_problem).pack(side='left',padx=2)
        ttk.Button(actions,text='定位无效输入',width=14,command=self.focus_invalid).pack(side='left',padx=2)
        ttk.Button(actions,text='查看完整检查报告',width=19,command=lambda:self.app.views.select(4)).pack(side='left',padx=2)
        ttk.Button(actions,text='下一步：确认可改部件',width=24,command=lambda:self.book.select(self.permissions_page)).pack(side='right',padx=2)
        area=ttk.Frame(self.home);area.pack(fill='x',padx=4)
        self.problem_tree=ttk.Treeview(area,columns=('problem','actual','limit','scope','angle'),show='headings',height=3,selectmode='browse')
        for key,title,width in [('problem','当前失败项',130),('actual','实际值',280),('limit','限制',180),('scope','发生区间',230),('angle','凸轮角/°',90)]:
            self.problem_tree.heading(key,text=title);self.problem_tree.column(key,width=width,minwidth=60)
        bar=ttk.Scrollbar(area,command=self.problem_tree.yview);bar.pack(side='right',fill='y');self.problem_tree.configure(yscrollcommand=bar.set)
        self.problem_tree.pack(fill='x',expand=True);self.problem_tree.bind('<<TreeviewSelect>>',self.show_problem)
        self.diagnosis_text=self.text_box(self.home,height=12)
        self.put(self.diagnosis_text,'选择助手后自动读取当前输入并计算；不需要先勾选参数。')
        self.permissions_page=ttk.Frame(self.book);self.book.add(self.permissions_page,text='② 确认可改部件')
        ttk.Label(self.permissions_page,text='依据当前失败项推荐比较方向；所有部件默认固定。范围只是探索暂定值，没有实测机械依据，不是安全范围。',wraplength=940,padding=6).pack(fill='x')
        permissions_area=ttk.Frame(self.permissions_page);permissions_area.pack(fill='both',expand=True)
        permission_canvas=tk.Canvas(permissions_area,highlightthickness=0,bg='#edf2f8')
        scroll=ttk.Scrollbar(permissions_area,command=permission_canvas.yview);scroll.pack(side='right',fill='y')
        permission_canvas.configure(yscrollcommand=scroll.set);permission_canvas.pack(fill='both',expand=True)
        permission_body=ttk.Frame(permission_canvas);window=permission_canvas.create_window((0,0),window=permission_body,anchor='nw')
        permission_body.bind('<Configure>',lambda e:permission_canvas.configure(scrollregion=permission_canvas.bbox('all')))
        permission_canvas.bind('<Configure>',lambda e:permission_canvas.itemconfigure(window,width=e.width))
        permission_canvas.bind('<MouseWheel>',lambda e:permission_canvas.yview_scroll(-int(e.delta/120),'units'))
        for name,keys,choice in COMPONENTS:
            row=ttk.Frame(permission_body,padding=5);row.pack(fill='x')
            ttk.Label(row,text=('安装角度/横距' if name=='机构安装' else name),width=13).pack(side='left')
            var=tk.StringVar(value='固定，不可修改');self.component_permissions[name]=var
            ttk.Combobox(row,textvariable=var,values=('固定，不可修改',choice),state='readonly',width=24).pack(side='left',padx=5)
            text=tk.StringVar(value='先分析当前问题');self.permission_labels[name]=text
            ttk.Label(row,textvariable=text,wraplength=600).pack(side='left',fill='x',expand=True)
            var.trace_add('write',self.permission_changed)
        ttk.Checkbutton(permission_body,text='明确允许结构试算所需的中位高度/安装坐标联动；其余未勾选的尺寸仍锁定',variable=self.installation_ack).pack(anchor='w',padx=6,pady=4)
        self.permission_note=tk.StringVar(value='未授权时仍可返回首页查看全部诊断。')
        ttk.Label(permission_body,textvariable=self.permission_note,wraplength=940,padding=6).pack(fill='x')
        row=ttk.Frame(self.permissions_page,padding=6);row.pack(side='bottom',fill='x',before=permissions_area)
        self.confirm_button=ttk.Button(row,text='确认上述授权，准备对比试算',width=31,command=self.confirm_permissions,style='Accent.TButton');self.confirm_button.pack(side='left',padx=3)
        ttk.Button(row,text='高级：查看/细调范围与预算',width=30,command=lambda:self.book.select(self.config_page)).pack(side='left',padx=3)

    def on_visible(self) -> None:
        if self.busy:return
        if self.needs_form:self.refresh_form()
        elif self.analysis_dirty:self.analyze()

    def open(self) -> None:
        if not self.app.ensure_editors_idle():return
        self.app.views.select(self.frame);self.on_visible()

    def refresh_form(self) -> None:
        if self.busy or not self.app.ensure_editors_idle():return
        try:project=self.app.collect();validate(project)
        except (ProjectError,ValueError) as exc:self.show_input_error(exc);return
        self.settings_changed();self.form_loading=True;self.form_project=project
        for p in PARAMETERS:
            data=exploration_range(project,p.key)
            self.current[p.key].set(f"{data['current']:g}");self.lower[p.key].set(f"{data['lower']:g}");self.upper[p.key].set(f"{data['upper']:g}")
            self.enabled[p.key].set(False);self.range_sources[p.key]=data['source']
        self.installation_ack.set(False)
        for var in self.component_permissions.values():var.set('固定，不可修改')
        self.form_loading=False;self.needs_form=False;self.update_controls();self.book.select(self.home)
        self.analyze()

    def show_input_error(self,exc: Exception) -> None:
        self.diagnosis=None;self.diagnostic_result=None;self.invalid_fields=getattr(exc,'fields',())
        self.problem_tree.delete(*self.problem_tree.get_children());self.book.select(self.home)
        fields='、'.join(self.invalid_fields) or '见下方原始计算错误；暂不能归因单个尺寸'
        text=f'输入/计算未完成：{exc}\n需要核对的字段：{fields}。\n修正方法：输入有限有效数字并核对单位、范围和机构闭合；点击“定位无效输入”返回对应参数，再点“重新读取并分析”。不使用旧诊断。'
        self.put(self.diagnosis_text,text);self.put(self.baseline_text,text);self.status.set(text.split('\n')[0]);self.analysis_dirty=False

    def focus_invalid(self) -> None:
        if not self.invalid_fields:return
        key=self.invalid_fields[0];self.app.views.select(0)
        widget=self.app.controls.get(key)
        if widget is not None:
            for tab in self.app.inputs.tabs():
                if str(widget).startswith(str(tab)+'.'):self.app.inputs.select(tab);break
            self.app.root.update_idletasks();widget.focus_set()
        elif key in ('route.waypoints','route.tangent_offsets'):self.app.inputs.select(self.app.waypoints)
        elif key=='route.auxiliary_points':self.app.inputs.select(self.app.auxiliary.editor)
        elif key=='route.obstacles':
            for tab in self.app.inputs.tabs():
                if str(self.app.obstacles).startswith(str(tab)+'.'):self.app.inputs.select(tab);break
            self.app.obstacles.focus_set()
        self.app.status.set('请修正 '+key+'：'+self.status.get())

    def analyze(self) -> None:
        if self.busy or self.app.busy or self.app.auxiliary.busy or not self.app.ensure_editors_idle():return
        if self.needs_form:self.refresh_form();return
        try:project=self.app.collect();validate(project)
        except (ValueError,ProjectError) as exc:self.show_input_error(exc);return
        self.generation+=1;generation=self.generation;revision=self.app.revision
        self.cancel.clear();self.diagnosis=None;self.diagnostic_result=None;self.phase='analysis';self.invalid_fields=()
        self.outcome=None;self.selected=None;self.tree.delete(*self.tree.get_children());self.check_tree.delete(*self.check_tree.get_children())
        self.problem_tree.delete(*self.problem_tree.get_children());self.put(self.diagnosis_text,'正在读取当前输入并执行完整检查与采样复核；无需授权，原案不变，可停止。')
        self.book.select(self.home)
        cached=self.app.result
        reuse=cached is not None and cached.project.to_dict()==project.to_dict() and bool(cached.precision)
        self.running_generation=generation;self.busy=True;self.start_button.configure(state='disabled')
        self.status.set('正在后台诊断：读取当前项目、完整计算与采样复核；可以停止。')
        def worker() -> None:
            try:
                from .workbench_ui import prepare_views
                self.events.put((generation,revision,'progress',f'已读取{len(project.route.waypoints)}个必过点；'+('快照匹配，复用完整结果并整理诊断。' if reuse else '正在计算完整设计及采样复核。')))
                result=cached if reuse and cached is not None else calculate(project,self.cancel.is_set)
                if self.cancel.is_set():raise ProjectError('诊断已取消，原案不变。')
                prepared=prepare_views(result);data=diagnose(result,prepared['diagnostics'])
                if self.cancel.is_set():raise ProjectError('诊断已取消，原案不变。')
                self.events.put((generation,revision,'diagnosis_ready',(result,reuse,data,prepared)))
            except Exception as exc:self.events.put((generation,revision,'analysis_error',exc))
            finally:self.events.put((generation,revision,'finished',None))
        threading.Thread(target=worker,daemon=True).start()

    def install_diagnosis(self,result: Result,reused: bool = False,data: dict[str,Any] | None = None,prepared: dict[str,Any] | None = None) -> None:
        if result.project.to_dict()!=self.app.collect().to_dict():
            self.status.set('已拒绝项目快照不匹配的诊断，请重新读取；原案不变。');return
        self.diagnostic_result=result;self.diagnosis=diagnose(result) if data is None else data;self.analysis_dirty=False;self.form_project=copy.deepcopy(result.project)
        self.app.show_result(result,prepared);self.problem_tree.delete(*self.problem_tree.get_children())
        for i,item in enumerate(self.diagnosis['problems']):
            d=item['diagnostic'];self.problem_tree.insert('','end',iid=str(i),values=(item['name'],d['actual'],d['limit'],d['scope'],d['angle_deg']))
        text=diagnosis_text(self.diagnosis);self.put(self.diagnosis_text,text);self.put(self.baseline_text,text)
        for name,keys,choice in COMPONENTS:
            used=[k for k in keys if k in self.diagnosis['recommended'] and result.project.mechanism.kind in next(p for p in PARAMETERS if p.key==k).kinds]
            reasons={'路线':'比较路线曲率与转向变化；必过点、顺序和容差保持。',
                     '凸轮尺寸':'尺度影响接触法线与安装高度；增大不保证改善。',
                     '转向臂尺寸':'改变位移与传力几何；不能降低固定路线所需前轮角。',
                     '机构安装':'比较中位/横距与接触几何、正负行程分配；先核对实物可调性。'}
            self.permission_labels[name].set(reasons[name]+' 推荐对比：'+('、'.join(next(p for p in PARAMETERS if p.key==k).name for k in used) if used else '当前失败无推荐，保持固定。'))
        self.status.set(('已核对快照并复用当前完整结果。' if reused else '当前项目诊断完成。')+f" {len(self.diagnosis['problems'])}项主失败；先看事实/待验证原因，再确认可改部件。")

    def show_problem(self,event: Any = None) -> None:
        if self.diagnosis is None or not self.problem_tree.selection():return
        item=self.diagnosis['problems'][int(self.problem_tree.selection()[0])]
        self.put(self.diagnosis_text,diagnosis_text(dict(self.diagnosis,problems=[item])))

    def locate_problem(self) -> None:
        if self.diagnosis is None or self.diagnostic_result is None or not self.problem_tree.selection():return
        if self.diagnostic_result.project.to_dict()!=self.app.collect().to_dict():self.invalidate();return
        name=self.diagnosis['problems'][int(self.problem_tree.selection()[0])]['name']
        page=self.app.workbench
        for i,d in enumerate(page.diags):
            if d.name==name:
                self.app.views.select(page.frames['issues']);page.issue_tree.selection_set(str(i));page.choose_issue();break

    def permission_changed(self,*args: Any) -> None:
        if self.form_loading:return
        self.form_loading=True
        for enabled in self.enabled.values():enabled.set(False)
        self.form_loading=False;self.update_controls();self.settings_changed()
        self.permission_note.set('部件选择已变化，旧授权/候选失效；请点击确认，再开始试算。')

    def confirm_permissions(self) -> None:
        if self.busy or self.diagnosis is None or self.form_project is None:
            self.status.set('先完成当前项目诊断；不需要先勾选参数。');return
        self.mode.set('允许结构改型' if any(self.component_permissions[n].get()!='固定，不可修改' for n in ('凸轮尺寸','转向臂尺寸','机构安装')) else '仅调整路线')
        self.form_loading=True
        for p in PARAMETERS:self.enabled[p.key].set(False)
        allowed=[]
        for name,keys,choice in COMPONENTS:
            if self.component_permissions[name].get()!=choice:continue
            for key in keys:
                if key in self.diagnosis['recommended'] and self.form_project.mechanism.kind in next(p for p in PARAMETERS if p.key==key).kinds:
                    self.enabled[key].set(True);allowed.append(key)
        if any(next(p for p in PARAMETERS if p.key==k).hardware for k in allowed) and not self.installation_ack.get():
            for enabled in self.enabled.values():enabled.set(False)
            self.form_loading=False;self.update_controls();self.settings_changed()
            self.permission_note.set('尚未授权：基圆/结构与中位高度、安装坐标存在联动。请先核对并勾选允许安装联动，再确认；只改凸轮而固定全部安装不在本模型承诺范围内。');return
        self.form_loading=False;self.update_controls();self.settings_changed()
        self.permission_note.set('已授权：'+('；'.join(f"{next(p for p in PARAMETERS if p.key==k).name} [{self.lower[k].get()}～{self.upper[k].get()}]，{self.range_sources[k]}" for k in allowed) or '无推荐因素被授权，保持全部固定。'))
        self.status.set('授权已确认；可直接开始对比试算，或进入高级设置细调。' if allowed else '所有部件固定；仍可查看完整诊断，暂不试算。')

    def range_edited(self,key: str) -> None:
        if self.form_loading:return
        self.range_sources[key]='用户给定范围（未核实实物限制）';self.settings_changed()

    def update_controls(self) -> None:
        kind=None if self.form_project is None else self.form_project.mechanism.kind
        for p in PARAMETERS:
            allowed=kind in p.kinds and (not p.hardware or self.mode.get()=='允许结构改型')
            if not allowed:self.enabled[p.key].set(False)
            self.checks[p.key].configure(state='normal' if allowed else 'disabled')
            for entry in self.entries[p.key]:entry.configure(state='normal' if allowed and self.enabled[p.key].get() else 'disabled')

    def mode_changed(self,*args: Any) -> None:
        for p in PARAMETERS:
            if p.hardware:self.enabled[p.key].set(False)
        self.update_controls();self.settings_changed()

    def parameter_toggled(self) -> None:
        self.form_loading=True
        for name,keys,choice in COMPONENTS:
            self.component_permissions[name].set(choice if any(self.enabled[k].get() for k in keys) else '固定，不可修改')
        self.form_loading=False
        self.update_controls();self.settings_changed()
        self.permission_note.set('高级设置已逐项授权；仅勾选因素会变化，以参数表中的实际勾选/范围为准，其他部件仍固定。')

    def settings_changed(self,*args: Any) -> None:
        if self.form_loading:return
        if not (self.busy and self.phase=='analysis'):self.generation+=1;self.cancel.set()
        self.outcome=None;self.selected=None
        self.tree.delete(*self.tree.get_children());self.check_tree.delete(*self.check_tree.get_children())
        self.put(self.detail,'尚无当前授权范围对应的候选；查看不会修改原案。')
        self.put(self.records,'试算结束后显示实际记录。')
        self.status.set('授权范围/预算已变化，旧候选失效；诊断仍可查看，确认可改部件后试算。')

    def invalidate(self) -> None:
        if self.adopting:return
        self.generation+=1;self.cancel.set();self.settings_changed();self.needs_form=True;self.form_project=None
        self.history=RouteHistory();self.cache.clear();self.anchor_revision=-1
        self.diagnosis=None;self.diagnostic_result=None;self.analysis_dirty=True
        self.problem_tree.delete(*self.problem_tree.get_children());self.put(self.diagnosis_text,'当前输入已改变，旧诊断失效；重新读取后再分析。')
        self.form_loading=True
        self.installation_ack.set(False)
        for var in self.component_permissions.values():var.set('固定，不可修改')
        for enabled in self.enabled.values():enabled.set(False)
        self.form_loading=False
        if self.app.views.select()==str(self.frame):self.app.root.after(150,self.on_visible)
        self.status.set('当前项目或输入已更改，旧任务/候选/助手采用历史失效；重新读取并授权。点表自己的历史保持独立。')

    def settings(self, project: Project) -> TrialSettings:
        out=TrialSettings([Adjustment(p.key,float(self.lower[p.key].get()),float(self.upper[p.key].get())) for p in PARAMETERS if self.enabled[p.key].get()],
                          mode='structure' if self.mode.get()=='允许结构改型' else 'route',
                          evaluations=int(self.budgets['evaluations'].get()),seconds=float(self.budgets['seconds'].get()),audit_top=int(self.budgets['audit_top'].get()),allow_installation_change=self.installation_ack.get(),range_sources={p.key:self.range_sources.get(p.key,'用户给定范围（未核实实物限制）') for p in PARAMETERS if self.enabled[p.key].get()})
        out.validate(project);return out

    def start(self) -> None:
        if self.busy or self.app.busy or self.app.auxiliary.busy or not self.app.ensure_editors_idle():return
        if self.needs_form:
            self.refresh_form();return
        try:
            project=self.app.collect();settings=self.settings(project)
        except (ValueError,ProjectError) as exc:
            self.status.set(f'未开始：{exc}');return
        if self.diagnosis is None:self.analyze();return
        self.generation+=1;generation=self.generation;revision=self.app.revision
        self.phase='trial';self.running_generation=generation;self.busy=True;self.cancel.clear();self.outcome=None;self.selected=None
        self.tree.delete(*self.tree.get_children());self.anchor_revision=revision
        self.start_button.configure(state='disabled');self.status.set('正在后台计算基线；原案不变，可停止。')
        def worker() -> None:
            try:
                result=run_improvement(project,settings,self.cancel.is_set,lambda text:self.events.put((generation,revision,'progress',text)))
                self.events.put((generation,revision,'done',result))
            except Exception as exc:self.events.put((generation,revision,'error',str(exc)))
            finally:self.events.put((generation,revision,'finished',None))
        threading.Thread(target=worker,daemon=True).start()

    def poll(self) -> None:
        try:
            while True:
                generation,revision,kind,data=self.events.get_nowait()
                if kind=='finished':
                    if generation==self.running_generation:
                        self.busy=False;self.start_button.configure(state='normal')
                        if self.needs_form and self.app.views.select()==str(self.frame):self.app.root.after(150,self.on_visible)
                    continue
                if generation!=self.generation or revision!=self.app.revision:continue
                if self.cancel.is_set():
                    self.status.set('助手任务已取消；未采用迟到结果，原案不变。')
                    if self.phase=='analysis':
                        self.put(self.diagnosis_text,'诊断已取消，未得到当前输入的完整结论；点击重新读取并分析可恢复。');self.analysis_dirty=False
                    continue
                if kind=='diagnosis_ready':self.install_diagnosis(*data)
                elif kind=='analysis_error':self.show_input_error(data)
                elif kind=='done':self.install(data)
                elif kind=='exported':self.status.set('修改方案、输入快照、候选与实际DXF审计证据已完整保存。')
                else:self.status.set(data)
        except queue.Empty:pass
        self.app.root.after(100,self.poll)

    def install(self, outcome: ImprovementOutcome) -> None:
        self.outcome=outcome;self.anchor_revision=self.app.revision
        self.tree.delete(*self.tree.get_children())
        if outcome.baseline is not None and self.app.collect().to_dict()==outcome.original_project.to_dict():
            self.app.show_result(copy.deepcopy(outcome.baseline))
        if outcome.baseline is not None:self.install_diagnosis(outcome.baseline)
        else:self.put(self.baseline_text,rule_directions(None))
        for c in outcome.candidates:
            changes='；'.join(f"{d['name']} {d['before']:g}→{d['after']:g}{d['unit']}" for d in modifications(outcome.original_project,c.project))
            failed='未完成验证' if c.result is None else '、'.join(c.comparison.get('remaining',[])) or '无主失败项'
            self.tree.insert('','end',iid=str(c.number),values=(c.number,c.status,changes,failed,'通过' if c.independent_pass else '不通过/未完成'))
        self.status.set(f'实际试算{len(outcome.trials)}次，耗时{outcome.elapsed:.2f}s；{outcome.stop_reason}。预览不改原案。')
        self.book.select(self.results)
        if outcome.candidates:
            self.tree.selection_set(str(outcome.candidates[0].number));self.preview()
        else:self.put(self.detail,outcome.report())

    def preview(self,event: Any = None) -> None:
        chosen=self.tree.selection()
        if not chosen or self.outcome is None:return
        c=next(x for x in self.outcome.candidates if str(x.number)==chosen[0]);self.selected=c
        changes='；'.join(f"{d['name']} {d['before']:g} → {d['after']:g} {d['unit']}" for d in modifications(self.outcome.original_project,c.project))
        summary=f"候选{c.number}：{c.status}\n建议修改的部件 / 具体参数 / 原值 → 建议值：{changes}\n"
        reasons=diagnose(self.outcome.baseline)['recommended'] if self.outcome.baseline is not None else {}
        for d in modifications(self.outcome.original_project,c.project):
            summary+=f"为什么比较{d['name']}：关联基线{reasons.get(d['parameter'],['用户自行授权探索'])}（不是根因结论）。\n"
        summary+=f"支持证据：实际试算{c.number}及相同门槛/采样的完整候选复算；详细单参数记录见第三页签。\n"
        if c.result is not None:
            before=self.outcome.baseline
            pressure_before='未计算' if before is None or 'max_pressure' not in before.stats else f"{before.stats['max_pressure']:.4f}"
            summary+=f"最大压力角：{pressure_before} → {c.result.stats.get('max_pressure','未计算')}°；原门槛保持 {c.project.cam.pressure_limit:g}°。\n"
            a=c.comparison.get('baseline_cost',{});b=c.comparison.get('candidate_cost',{})
            summary+=f"安装代价：转轴(X,Y) {a.get('pivot_x_mm','未知')},{a.get('pivot_y_mm','未知')} → {b.get('pivot_x_mm','未知')},{b.get('pivot_y_mm','未知')} mm；必须重新核对装配。\n"
            summary+=f"解决{c.comparison.get('resolved')}；新增失败{c.comparison.get('new_failures')}；剩余失败{c.comparison.get('remaining')}。\n"
            summary+='其他指标恶化：'+json.dumps(c.comparison.get('worsened_metrics',[]),ensure_ascii=False)+'\n'
            summary+=f"主检查：{'通过' if c.result.valid else '未通过'}；采样复核：{c.result.precision.get('stable','未完成')}；实际DXF独立审计：{'通过' if c.independent_pass else '未通过/未完成'}。\n"
        self.put(self.detail,summary+'\n'+self.outcome.report(c));self.check_tree.delete(*self.check_tree.get_children())
        for row in c.comparison.get('checks',[]):
            before,after=row['before'],row['after'];a={} if before is None else before;b={} if after is None else after
            self.check_tree.insert('','end',values=(row['name'],a.get('actual','未计算'),b.get('actual','未计算'),b.get('limit',a.get('limit','—')),
                f"{a.get('status','未计算')}→{b.get('status','未计算')}",f"{b.get('scope','未提供')} / {b.get('angle_deg')}° / {b.get('point')} mm"))
        trials=[{k:v for k,v in t.items() if k not in ('project','checks')} for t in self.outcome.trials]
        independent=None if c.audit is None else dict(checks=[vars(check) for check in c.audit.checks],issues=c.audit.issues,sha256=c.audit.sha256)
        self.put(self.records,json.dumps(clean(dict(comparison=c.comparison,independent=independent,local_influence=self.outcome.influence(),trials=trials,validation_error=c.validation_error)),ensure_ascii=False,indent=2))

    def snapshot(self) -> dict[str,Any]:
        return dict(project=self.app.collect().to_dict(),file=self.app.file,dirty=self.app.dirty,source=self.app.parameter_source.get(),
                    waypoint_history=copy.deepcopy(self.app.waypoints.document.history),auxiliary_history=copy.deepcopy(self.app.auxiliary.editor.document.history))

    @staticmethod
    def token(project: Project) -> str:
        return json.dumps(project.to_dict(),sort_keys=True)

    def restore(self, snapshot: dict[str,Any]) -> None:
        project=Project.from_dict(snapshot['project']);self.adopting=True
        try:
            self.app.load(project);self.app.file=snapshot['file'];self.app.dirty=snapshot['dirty']
            self.app.parameter_source.set(snapshot['source'])
            self.app.waypoints.document.history=copy.deepcopy(snapshot['waypoint_history'])
            self.app.auxiliary.editor.document.history=copy.deepcopy(snapshot['auxiliary_history'])
            result=self.cache.get(self.token(project))
            if result is not None and result.project.to_dict()==project.to_dict():self.app.show_result(copy.deepcopy(result))
            self.anchor_revision=self.app.revision
            self.form_project=copy.deepcopy(project);self.needs_form=False
            if result is not None:self.install_diagnosis(result,True)
            else:self.diagnosis=None;self.diagnostic_result=None;self.analysis_dirty=True
            for p in PARAMETERS:self.current[p.key].set(f'{value(project,p.key):g}')
        finally:self.adopting=False
        self.app.views.select(self.frame)

    def adopt(self) -> None:
        c=self.selected
        if self.busy or self.app.busy or self.outcome is None or c is None or not self.app.ensure_editors_idle():return
        if self.app.revision!=self.anchor_revision:
            self.invalidate();return
        if c.result is None or c.result.project.to_dict()!=c.project.to_dict():
            self.status.set('只有粗筛/结果归属不符，不能采用；需完成候选复算。');return
        try:assert_permissions(self.outcome.original_project,c.project,self.outcome.settings)
        except ProjectError as exc:self.status.set(str(exc));return
        if not c.independent_pass or not c.result.valid or not c.result.precision.get('stable'):
            if not messagebox.askyesno('候选未全部通过','此候选存在失败、采样未确认或独立验证未完成。仅采用为继续分析的草稿？不会保存或加工放行。',parent=self.app.root):return
        previous=self.snapshot()
        if self.app.result is not None:self.cache[self.token(self.app.result.project)]=copy.deepcopy(self.app.result)
        if self.outcome.baseline is not None:self.cache[self.token(self.outcome.baseline.project)]=copy.deepcopy(self.outcome.baseline)
        self.cache[self.token(c.project)]=copy.deepcopy(c.result)
        current=copy.deepcopy(previous);current.update(project=c.project.to_dict(),file=None,dirty=True,source='助手试算结构/路线草稿，未实测')
        self.history.commit(previous,current);self.restore(current)
        self.status.set('已显式采用为未保存的新草稿，原磁盘项目未改；可撤销采用。'+c.status+'；实车未验证。')

    def undo(self, redo: bool = False) -> None:
        if self.busy or self.app.busy or self.app.revision!=self.anchor_revision or not self.app.ensure_editors_idle():return
        state=(self.history.redo if redo else self.history.undo)(self.snapshot())
        if state is not None:self.restore(state);self.status.set('已重做采用。' if redo else '已撤销采用并恢复原快照，磁盘文件未修改。')

    def keep_original(self) -> None:
        if self.busy or self.app.busy or self.app.revision!=self.anchor_revision:return
        while self.history.undo_stack:
            length=len(self.history.undo_stack);self.undo()
            if len(self.history.undo_stack)>=length:return
        self.status.set('已保留原案；查看候选未写入磁盘。')

    def export(self) -> None:
        if self.busy or self.outcome is None:return
        selected=filedialog.askdirectory(parent=self.app.root,title='选择新空目录保存修改方案与精确DXF证据')
        if not selected:return
        outcome=self.outcome;self.generation+=1;generation=self.generation;revision=self.app.revision
        self.phase='export';self.running_generation=generation;self.busy=True;self.cancel.clear();self.start_button.configure(state='disabled')
        self.status.set('正在后台准备完整方案和证据；可停止，不覆盖已有文件。')
        def worker() -> None:
            try:outcome.export(Path(selected),self.cancel.is_set);self.events.put((generation,revision,'exported',None))
            except Exception as exc:self.events.put((generation,revision,'error',str(exc)))
            finally:self.events.put((generation,revision,'finished',None))
        threading.Thread(target=worker,daemon=True).start()
