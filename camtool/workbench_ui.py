"""Synchronized demonstration, diagnostics, opt-in comparison and analysis pages."""
from __future__ import annotations

import copy
import json
import math
import queue
import tempfile
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import TYPE_CHECKING, Any, Callable

import numpy as np

from . import plotting
from .engine import Result
from .export import export_design
from .plotting import Plot
from .project import ProjectError
from .project import Project
from .version import VERSION
from .workbench import (TRACK_TEMPLATE, Diagnostic, PlanComparison, cam_degrees, comparison_metrics, demo_project,
                        diagnostics, index_at_time, ordered_deviations, perturbation_analysis,
                        read_measured_track, time_axis)

if TYPE_CHECKING:
    from .ui import App


def prepare_views(result: Result) -> dict[str, Any]:
    return {"diagnostics":diagnostics(result),
            "visits":ordered_deviations(result.path,result.project.route.waypoints,result.project.route.tolerance)}


class WorkbenchPages:
    def __init__(self, app: App) -> None:
        self.app=app
        self.frames: dict[str,ttk.Frame]={}
        self.canvases: dict[str,tk.Canvas]={}
        self.diags: list[Diagnostic]=[]
        self.visits: list[dict[str,Any]]=[]
        self.focus: Diagnostic | None=None
        self.comparison: PlanComparison | None=None
        self.comparison_prepared: tuple[dict[str,Any] | None,dict[str,Any]] | None=None
        self.origin_file: Path | None=None
        self.origin_dirty=False
        self.backup: dict[str,Any] | None=None
        self.adopting=False
        self.updating=False
        self.time=tk.DoubleVar(value=0)
        self.demo_status=tk.StringVar(value="载入案例或计算项目后，使用统一理论时间轴。")
        self.compare_status=tk.StringVar(value="点击「本地优化」，原方案将被保留，不会自动采用优化方案。")
        self.analysis_status=tk.StringVar(value="用户导入轨迹与理论扰动分别显示；软件不生成实测数据。")
        self.analysis_cancel=threading.Event()
        self.events: queue.Queue[tuple[int,str,Any]]=queue.Queue()
        self.analysis_generation=0
        self.analysis_busy=False
        self.track: Any=None
        self.measured_rows: list[dict[str,Any]]=[]
        self.perturbations: list[dict[str,Any]]=[]
        self.analysis_revision=-1
        for key,title in [("demo","同步演示"),("issues","问题定位"),("compare","优化对比"),("analysis","实测与扰动")]:
            frame=ttk.Frame(app.views)
            app.views.add(frame,text=title)
            self.frames[key]=frame
        self._demo()
        self._issues()
        self._compare()
        self._analysis()
        for key in ("demo","issues","compare"):
            page=self.frames[key]
            ttk.Label(page,textvariable=app.status,padding=(6,2),wraplength=940).pack(fill="x",before=page.pack_slaves()[0])
        app.root.after(100,self.poll)

    def canvas(self,parent: Any,key: str) -> tk.Canvas:
        c=tk.Canvas(parent,bg="white",highlightthickness=1,highlightbackground="#d7e0ec")
        self.canvases[key]=c
        c.bind("<Configure>",lambda e:self.app.schedule_render())
        return c

    def _demo(self) -> None:
        page=self.frames["demo"]
        row=ttk.Frame(page,padding=5); row.pack(fill="x")
        for text,command in [("正常案例",lambda:self.load_case(False)),("问题案例",lambda:self.load_case(True)),
                             ("返回原项目",self.restore_project),("播放 / 暂停",self.app.play)]:
            ttk.Button(row,text=text,command=command,width=14).pack(side="left",padx=2)
        ttk.Label(row,text="倍率").pack(side="left",padx=(10,2))
        ttk.Combobox(row,textvariable=self.app.playback_rate,values=["0.5","1","2","5"],width=5,state="readonly").pack(side="left")
        ttk.Label(page,text="理论几何运动演示，非实车试验；三个视图共享同一时刻。案例是参数化设计示例，不是实测数据。",padding=(8,3)).pack(fill="x")
        ttk.Label(page,textvariable=self.demo_status,padding=6,wraplength=940).pack(side="bottom",fill="x")
        self.demo_slider=ttk.Scale(page,from_=0,to=1,variable=self.time,command=self.seek)
        self.demo_slider.pack(side="bottom",fill="x",padx=12,pady=6)
        self.demo_slider.bind("<ButtonPress-1>",lambda e:self.app.pause())
        grid=ttk.Frame(page); grid.pack(fill="both",expand=True)
        grid.rowconfigure(0,weight=1)
        grid.columnconfigure(0,weight=3); grid.columnconfigure(1,weight=2); grid.columnconfigure(2,weight=2)
        self.canvas(grid,"demo_route").grid(row=0,column=0,sticky="nsew",padx=2,pady=2)
        self.canvas(grid,"demo_cam").grid(row=0,column=1,sticky="nsew",padx=2,pady=2)
        self.canvas(grid,"demo_mechanism").grid(row=0,column=2,sticky="nsew",padx=2,pady=2)

    def _issues(self) -> None:
        page=self.frames["issues"]
        ttk.Label(page,text="点击检查行同步定位：路线位置 / 凸轮角 / 曲线。回程段不映射到比赛路线；建议依据当前模型，不保证调整后通过。",padding=6,wraplength=1200).pack(fill="x")
        panel=ttk.Frame(page); panel.pack(fill="x")
        self.issue_tree=ttk.Treeview(panel,columns=("status","name","actual","limit"),show="headings",height=5,selectmode="browse")
        for key,label,width in [("status","状态",70),("name","检查项目",150),("actual","实际值",260),("limit","限制 / 要求",230)]:
            self.issue_tree.heading(key,text=label); self.issue_tree.column(key,width=width,minwidth=50)
        bar=ttk.Scrollbar(panel,command=self.issue_tree.yview)
        self.issue_tree.configure(yscrollcommand=bar.set)
        bar.pack(side="right",fill="y"); self.issue_tree.pack(fill="both",expand=True)
        self.issue_tree.tag_configure("fail",foreground="#b3263d")
        self.issue_tree.bind("<<TreeviewSelect>>",self.choose_issue)
        self.issue_detail=tk.Text(page,height=4,wrap="word",bg="white",state="disabled",padx=8,pady=5)
        self.issue_detail.pack(fill="x")
        grid=ttk.Frame(page); grid.pack(fill="both",expand=True)
        for i,key in enumerate(["issue_route","issue_cam","issue_curve"]):
            grid.columnconfigure(i,weight=1)
            self.canvas(grid,key).grid(row=0,column=i,sticky="nsew",padx=2)
        grid.rowconfigure(0,weight=1)

    def _compare(self) -> None:
        page=self.frames["compare"]
        row=ttk.Frame(page,padding=5); row.pack(fill="x")
        for text,command in [("运行本地优化",lambda:self.app.run(True)),("保留原方案",lambda:self.adopt(False)),
                             ("采用优化方案",lambda:self.adopt(True)),("保存两套方案与对比",self.export_comparison)]:
            ttk.Button(row,text=text,command=command,width=22).pack(side="left",padx=2)
        ttk.Label(page,textvariable=self.compare_status,wraplength=1200,padding=6).pack(side="bottom",fill="x")
        self.compare_tree=ttk.Treeview(page,columns=("metric","before","after","change"),show="headings",height=5)
        for key,title in [("metric","指标（目标路线长度）"),("before","原方案"),("after","优化方案"),("change","优化减原方案")]:
            self.compare_tree.heading(key,text=title); self.compare_tree.column(key,width=180)
        self.compare_tree.pack(fill="x")
        self.canvas(page,"compare").pack(fill="both",expand=True)

    def _analysis(self) -> None:
        page=self.frames["analysis"]
        row=ttk.Frame(page,padding=4); row.pack(fill="x")
        for text,command in [("导入实测 CSV",self.import_track),("重新比较实测",self.compare_track),
                             ("保存格式模板",self.track_template),("导出分析结果",self.export_analysis),("停止分析",self.analysis_cancel.set)]:
            ttk.Button(row,text=text,command=command,width=18).pack(side="left",padx=2)
        controls=ttk.Frame(page,padding=4); controls.pack(fill="x")
        self.ranges: dict[str,tk.StringVar]={}
        for key,title,default,width in [("wheel_min","轮径下限 %","-1",5),("wheel_max","上限 %","1",5),
                                        ("phase_min","安装相位下限 °","-1",5),("phase_max","上限 °","1",5),
                                        ("count","每组点数","3",3),("step","角步长 °","2",4)]:
            ttk.Label(controls,text=title).pack(side="left",padx=(4,1))
            var=tk.StringVar(value=default); self.ranges[key]=var
            ttk.Entry(controls,textvariable=var,width=width).pack(side="left")
            var.trace_add("write",self.ranges_changed)
        ttk.Button(controls,text="理论扰动分析",command=self.run_perturbation,width=18).pack(side="left",padx=4)
        ttk.Label(page,text="实测：必须声明单位、场地坐标、轴向、传感器测量点及来源。扰动：固定已导出凸轮，单因素扫描，不含动力学与打滑。",padding=5,wraplength=1200).pack(fill="x")
        ttk.Label(page,textvariable=self.analysis_status,padding=6,wraplength=1200).pack(side="bottom",fill="x")
        table=ttk.Frame(page); table.pack(fill="x")
        self.analysis_tree=ttk.Treeview(table,columns=("type","value","error","fails"),show="headings",height=4)
        for key,label in [("type","数据 / 参数"),("value","声明来源 / 扰动量"),("error","打卡偏差 mm（扰动取最大值）"),("fails","判定 / 不通过与未验证项数")]:
            self.analysis_tree.heading(key,text=label); self.analysis_tree.column(key,width=200)
        bar=ttk.Scrollbar(table,command=self.analysis_tree.yview)
        self.analysis_tree.configure(yscrollcommand=bar.set)
        bar.pack(side="right",fill="y"); self.analysis_tree.pack(fill="x",expand=True)
        self.analysis_tree.bind("<<TreeviewSelect>>",lambda e:self.render_analysis())
        self.canvas(page,"analysis").pack(fill="both",expand=True)

    def invalidate(self) -> None:
        self.focus=None; self.diags=[]; self.visits=[]
        self.issue_tree.delete(*self.issue_tree.get_children())
        self.issue_detail.configure(state="normal"); self.issue_detail.delete("1.0","end"); self.issue_detail.configure(state="disabled")
        if not self.adopting:
            self.comparison=None; self.compare_tree.delete(*self.compare_tree.get_children())
            self.comparison_prepared=None
            self.compare_status.set("输入已改变，对比结果失效；原方案只在本次有效优化快照中保留。")
        self.analysis_generation+=1; self.analysis_cancel.set()
        self.measured_rows=[]; self.perturbations=[]; self.analysis_revision=-1
        self.analysis_tree.delete(*self.analysis_tree.get_children())
        self.analysis_status.set("设计或参数已改变，旧分析失效；重新计算后再比较 / 分析。已导入的数据未被改写。")
        for canvas in self.canvases.values():
            canvas.delete("all"); canvas.create_text(15,25,text="输入已改变 / 尚无计算结果，请重新计算。",anchor="nw",fill="#718096")

    def install_result(self,result: Result,prepared: dict[str,Any] | None=None) -> None:
        ready=prepared or prepare_views(result)
        self.diags=ready["diagnostics"]; self.visits=ready["visits"]; self.focus=None
        self.issue_tree.delete(*self.issue_tree.get_children())
        for i,d in enumerate(self.diags):
            self.issue_tree.insert("","end",iid=str(i),values=({"pass":"通过","fail":"不通过","warn":"注意"}.get(d.status,d.status),d.name,d.actual,d.limit),tags=(d.status,))
        self.demo_slider.configure(to=float(time_axis(result)[-1]))

    def seek(self,value: str) -> None:
        if self.updating or self.app.result is None: return
        self.app.frame.set(index_at_time(self.app.result,float(value)))
        self.app.schedule_render()

    def load_case(self,problem: bool) -> None:
        if self.app.busy: return
        try:
            if self.backup is None:
                self.backup={"project":self.app.collect(),"result":self.app.result,"file":self.app.file,"dirty":self.app.dirty,"source":self.app.parameter_source.get()}
            self.app.load(demo_project(problem)); self.app.file=None
            self.demo_status.set("正在后台计算案例；可点工具栏「停止计算」。")
            self.app.views.select(self.frames["demo"])
            self.app.run(False)
        except (ProjectError,ValueError) as exc:
            messagebox.showerror("案例加载失败",f"无法保留当前输入：{exc}",parent=self.app.root)

    def restore_project(self) -> None:
        if self.backup is None or self.app.busy: return
        saved=self.backup; self.backup=None
        self.app.load(saved["project"]); self.app.file=saved["file"]; self.app.dirty=saved["dirty"]
        self.app.parameter_source.set(saved["source"])
        if saved["result"] is not None: self.app.show_result(saved["result"])

    def choose_issue(self,event: Any=None) -> None:
        selected=self.issue_tree.selection()
        if not selected or self.app.result is None: return
        self.focus=self.diags[int(selected[0])]
        d=self.focus
        self.app.pause()
        if d.route_index is not None: self.app.frame.set(d.route_index)
        self.issue_detail.configure(state="normal"); self.issue_detail.delete("1.0","end")
        self.issue_detail.insert("1.0",f"{d.name}｜实际：{d.actual}｜限制：{d.limit}\n{d.scope}；凸轮角：{d.angle_deg}°；场地位置：{d.point}\n依据：{d.basis}\n建议：{d.advice}（修改后须重新检查，不保证通过。）")
        self.issue_detail.configure(state="disabled")
        self.render_issues()

    def set_comparison(self,original: Result | None,optimized: Result,prepared: tuple[dict[str,Any] | None,dict[str,Any]] | None=None,
                       original_project: Project | None=None,original_error: str="") -> None:
        self.comparison=PlanComparison.capture(original,optimized,original_project,original_error); self.origin_file=self.app.file
        self.origin_dirty=self.app.dirty
        self.comparison_prepared=prepared
        self.compare_tree.delete(*self.compare_tree.get_children())
        a,b=comparison_metrics(original),comparison_metrics(optimized)
        for key,value in a.items():
            after=b[key]
            delta=None if value is None or after is None else after-value
            self.compare_tree.insert("","end",values=(key,"未生成" if value is None else f"{value:.5f}","未生成" if after is None else f"{after:.5f}","不可比较" if delta is None else f"{delta:+.5f}"))
        self.compare_status.set(self.comparison.verdict()+" 原方案已保留在内存，可保存两套项目。")
        self.app.views.select(self.frames["compare"])
        self.render_compare()

    def adopt(self,optimized: bool) -> None:
        if self.comparison is None or self.app.busy: return
        result=copy.deepcopy(self.comparison.optimized if optimized else self.comparison.original)
        self.adopting=True
        try:
            self.app.load(self.comparison.original_project if result is None else result.project)
            self.app.file=None if optimized else self.origin_file
            self.app.dirty=True if optimized else self.origin_dirty
            prepared=None if self.comparison_prepared is None else self.comparison_prepared[1 if optimized else 0]
            if result is not None: self.app.show_result(result,prepared)
        finally: self.adopting=False
        self.compare_status.set(("已选择优化方案（另存为新项目）。" if optimized else "已保留原方案。")+self.comparison.verdict())
        self.app.views.select(self.frames["compare"])

    def export_comparison(self) -> None:
        if self.comparison is None: return
        folder=filedialog.askdirectory(parent=self.app.root,title="新建目录保存原方案、优化方案与对比")
        if folder:
            try: self.comparison.export(Path(folder)); self.compare_status.set("原方案、优化方案和实际指标对比已保存。")
            except (OSError,ProjectError) as exc: messagebox.showerror("对比保存失败",str(exc),parent=self.app.root)

    def overlay(self,canvas: tk.Canvas,paths: list[tuple[Any,str]],title: str,project: Project | None=None) -> Plot | None:
        result=self.app.result
        if project is None:
            if result is None: return None
            project=result.project
        r=project.route
        arrays=[np.asarray(path,dtype=float).reshape((-1,2)) for path,_ in paths if len(path)]
        points=np.vstack([*arrays,[[r.field[0],r.field[2]],[r.field[1],r.field[3]]]])
        lo,hi=points.min(axis=0)-80,points.max(axis=0)+80
        plot=Plot(canvas,[lo[0],hi[0],lo[1],hi[1]])
        xmin,xmax,ymin,ymax=r.field
        plot.line(np.array([[xmin,ymin],[xmax,ymin],[xmax,ymax],[xmin,ymax],[xmin,ymin]]),"#afbed1",1)
        for x,y,radius in r.obstacles: plot.circle(x,y,radius,"#df9546","#fff0dc")
        for path,color in paths: plot.line(np.asarray(path,dtype=float).reshape((-1,2)),color,2)
        for i,(x,y) in enumerate(r.waypoints,1): plot.circle(x,y,r.tolerance,"#a9bdd6"); plot.label(x,y,str(i))
        canvas.create_text(10,8,anchor="nw",text=title,width=max(160,canvas.winfo_width()-20),fill="#25334c")
        return plot

    def render_demo(self,index: int) -> None:
        result=self.app.result
        if result is None: return
        plotting.scene(self.canvases["demo_route"],result,index)
        plotting.mechanism(self.canvases["demo_mechanism"],result,index,compact=True)
        canvas=self.canvases["demo_cam"]
        if len(result.profile):
            degrees=float(cam_degrees(result)[index])
            angle=math.radians(result.project.cam.direction*degrees-result.project.cam.phase)
            rotation=np.array([[math.cos(angle),-math.sin(angle)],[math.sin(angle),math.cos(angle)]])
            outline=result.profile@rotation.T
            radius=float(np.linalg.norm(outline,axis=1).max())*1.4
            plot=Plot(canvas,[-radius,radius,-radius,radius])
            plot.line(outline,"#2464df",2)
            x,y=outline[0]; plot.circle(x,y,4/plot.sx,"#cb364a","#cb364a")
            plot.label(x,y,"S0","#cb364a")
            canvas.create_text(10,8,anchor="nw",text=f"凸轮实体同步旋转 · 驱动角 {degrees:.2f}°",fill="#25334c")
        else:
            canvas.delete("all"); canvas.create_text(10,20,anchor="nw",text="未生成凸轮；不播放虚构轮廓。")
        passed=0
        for visit in self.visits:
            if not visit["passed"] or visit["position"]>index+1e-9: break
            passed+=1
        target=result.project.route.waypoints[min(passed,len(self.visits)-1)]
        deviation=float(np.linalg.norm(result.path[index]-target))
        completed_error="尚未完成打卡" if passed==0 else f"已完成最大偏差 {max(v['error_mm'] for v in self.visits[:passed]):.2f} mm"
        seconds=float(time_axis(result)[index])
        self.updating=True; self.time.set(seconds); self.updating=False
        self.demo_status.set(f"{result.project.name}｜理论时间 {seconds:.2f}/{time_axis(result)[-1]:.2f} s｜前轮 {np.degrees(result.delta[index]):.2f}°｜打卡 {passed}/{len(self.visits)}｜{completed_error}｜{'终点距离' if passed==len(self.visits) else '当前目标距离'} {deviation:.2f} mm｜不满足 {sum(c.status=='fail' for c in result.checks)} 项")

    def render_issues(self) -> None:
        result=self.app.result
        if result is None: return
        d=self.focus
        paths=[(result.path,"#2464df")]
        routeplot=self.overlay(self.canvases["issue_route"],paths,"路线定位" if d is None else d.scope)
        if d and d.point is not None and routeplot:
            routeplot.circle(d.point[0],d.point[1],5/routeplot.sx,"#cc354a","#cc354a")
        camplot=plotting.cam(self.canvases["issue_cam"],result,compact=True)
        if d and d.angle_deg is not None and camplot and len(result.profile):
            i=int(np.argmin(abs(np.degrees(result.cam_angle)-d.angle_deg)))
            x,y=result.profile[i]
            camplot.circle(float(x),float(y),4/camplot.sx,"#cc354a","#cc354a")
        c=self.canvases["issue_curve"]
        kind=d.curve_kind if d else "steer"
        if kind=="pressure" and len(result.pressure): x,y,label=np.degrees(result.cam_angle),result.pressure,"整圈压力角 °"
        elif kind=="stroke" and len(result.contact_centers):
            x=np.degrees(result.cam_angle); y=result.contact_centers[:,1]-(result.contact_centers[0,1]-result.stroke[0]); label="整圈行程 mm"
        elif len(result.cam_delta): x,y,label=np.degrees(result.cam_angle),np.degrees(result.cam_delta),"整圈前轮转角 °"
        else: x,y,label=cam_degrees(result),np.degrees(result.delta),"运行前轮转角 °"
        span=max(1,float(np.ptp(y))); low,high=float(y.min()-span*.2),float(y.max()+span*.2)
        plot=Plot(c,[float(x[0]),float(x[-1]),low,high],equal=False)
        plot.line(np.column_stack([x,y]),"#2464df")
        if d and d.angle_deg is not None:
            a=min(float(x[-1]),max(float(x[0]),d.angle_deg)); value=float(np.interp(a,x,y))
            plot.line(np.array([[a,low],[a,high]]),"#cc354a",1,(3,3)); plot.circle(a,value,4/min(plot.sx,plot.sy),"#cc354a")
        c.create_text(10,8,anchor="nw",text=label+"；X：凸轮驱动角 °",width=max(160,c.winfo_width()-20))

    def render_compare(self) -> None:
        if self.comparison:
            before=np.empty((0,2)) if self.comparison.original is None else self.comparison.original.path
            title="原方案无法计算，无原路线叠加；红：候选；原输入已保留，无法判断改善" if self.comparison.original is None else "灰：保留的原方案；红：优化候选；请按实际指标自行选择"
            self.overlay(self.canvases["compare"],[(before,"#8293a7"),(self.comparison.optimized.path,"#cc354a")],title,self.comparison.original_project)

    def render(self,index: int) -> None:
        selected=self.app.views.select()
        if selected==str(self.frames["demo"]): self.render_demo(index)
        elif selected==str(self.frames["issues"]): self.render_issues()
        elif selected==str(self.frames["compare"]): self.render_compare()
        elif selected==str(self.frames["analysis"]): self.render_analysis()

    def ranges_changed(self,*args: Any) -> None:
        self.analysis_generation+=1; self.analysis_cancel.set(); self.perturbations=[]
        if hasattr(self,"analysis_tree"):
            self.fill_analysis_table()
            self.analysis_status.set("扰动范围已更改；旧扰动结果失效，须重新运行。")
            self.render_analysis()

    def start_job(self,kind: str,work: Callable[[Result,Callable[[],bool],Callable[[str],None]],Any]) -> None:
        if self.analysis_busy or self.app.result is None:
            self.analysis_status.set("请先计算当前设计；分析忙时可停止并等待本次退出。")
            return
        self.analysis_generation+=1; generation=self.analysis_generation
        revision=self.app.revision; result=copy.deepcopy(self.app.result)
        self.analysis_cancel.clear(); self.analysis_busy=True
        def worker() -> None:
            try:
                value=work(result,self.analysis_cancel.is_set,lambda text:self.events.put((generation,"progress",text)))
                if self.analysis_cancel.is_set(): raise ProjectError("分析已停止。")
                self.events.put((generation,kind,(revision,value)))
            except Exception as exc: self.events.put((generation,"error",str(exc)))
            finally: self.events.put((generation,"finished",None))
        threading.Thread(target=worker,daemon=True).start()
        self.analysis_status.set("正在后台分析，可停止；不会替换当前设计。")

    def import_track(self) -> None:
        path=filedialog.askopenfilename(parent=self.app.root,filetypes=[("实测轨迹 CSV","*.csv")])
        if path:
            self.start_job("measured",lambda result,cancel,progress:self.measure_data(read_measured_track(Path(path)),result))

    @staticmethod
    def measure_data(track: Any,result: Result) -> tuple[Any,list[dict[str,Any]]]:
        return track,ordered_deviations(track.path,result.project.route.waypoints,result.project.route.tolerance)

    def compare_track(self) -> None:
        if self.track is None: return
        track=copy.deepcopy(self.track)
        self.start_job("measured",lambda result,cancel,progress:self.measure_data(track,result))

    def run_perturbation(self) -> None:
        try:
            values={k:float(v.get()) for k,v in self.ranges.items()}
            if values["count"]!=int(values["count"]): raise ValueError("点数须为整数")
        except ValueError as exc:
            messagebox.showerror("扰动范围",str(exc),parent=self.app.root); return
        def work(result: Result,cancel: Callable[[],bool],progress: Callable[[str],None]) -> Any:
            with tempfile.TemporaryDirectory(prefix="solarcam-perturb-") as directory:
                dxf=export_design(result,Path(directory))["dxf"]
                return perturbation_analysis(result,dxf,values["wheel_min"],values["wheel_max"],values["phase_min"],values["phase_max"],
                                             int(values["count"]),values["step"],cancel,progress)
        self.start_job("perturb",work)

    def poll(self) -> None:
        try:
            while True:
                generation,kind,value=self.events.get_nowait()
                if kind=="finished": self.analysis_busy=False; continue
                if generation!=self.analysis_generation: continue
                if kind=="progress": self.analysis_status.set(value)
                elif kind=="error": self.analysis_status.set(value)
                else:
                    revision,data=value
                    if revision!=self.app.revision:
                        self.analysis_status.set("设计已更改，后台分析结果已丢弃。")
                        continue
                    self.analysis_revision=revision
                    if kind=="measured":
                        self.track,self.measured_rows=data
                        self.analysis_status.set(f"已比较{'用户声明实测（未经核实）' if self.track.metadata['kind']=='measured' else '测试/理论导入（非实测）'}：{self.track.metadata['source']}；单位 {self.track.metadata['unit']} 已换算 mm。")
                    else:
                        self.perturbations=data
                        self.analysis_status.set("理论单因素扰动完成；原凸轮固定，各方案失败/未验证项均保留，不是实测或性能保证。")
                    self.fill_analysis_table(); self.render_analysis()
        except queue.Empty: pass
        self.app.root.after(100,self.poll)

    def fill_analysis_table(self) -> None:
        self.analysis_tree.delete(*self.analysis_tree.get_children())
        for row in self.measured_rows:
            source="" if self.track is None else self.track.metadata['source']
            self.analysis_tree.insert("","end",values=(f"导入轨迹 · 打卡{row['number']}",source,"未到达" if row['error_mm'] is None else f"{row['error_mm']:.4f}","通过" if row['passed'] else "不通过"))
        base=next((r["max_checkpoint_error_mm"] for r in self.perturbations if r["kind"]=="基线"),None)
        for i,row in enumerate(self.perturbations):
            error=row["max_checkpoint_error_mm"]
            change="" if error is None or base is None else f"；Δ{error-base:+.3f} mm"
            self.analysis_tree.insert("","end",iid="p"+str(i),values=("理论 · "+row["kind"],str(row["value"])+change,"无有效轨迹" if error is None else f"{error:.4f}",f"{row['failures']} / {row['unknown']}"))

    def render_analysis(self) -> None:
        if self.app.result is None: return
        paths: list[tuple[Any,str]]=[(self.app.result.path,"#2464df")]
        if self.track is not None: paths.append((self.track.path,"#159c8c"))
        selected=self.analysis_tree.selection()
        rows=self.perturbations
        if selected and selected[0].startswith("p"): rows=[rows[int(selected[0][1:])]]
        for row in rows:
            if row["path"]: paths.append((row["path"],"#cd6473" if row["kind"]!="基线" else "#9a89bc"))
        imported="用户声明实测，未核实" if self.track is None or self.track.metadata['kind']=='measured' else "测试/理论，非实测"
        self.overlay(self.canvases["analysis"],paths,f"蓝：当前设计；绿：导入（{imported}）；红/紫：理论扰动预测")

    def track_template(self) -> None:
        path=filedialog.asksaveasfilename(parent=self.app.root,defaultextension=".csv",initialfile="实测轨迹格式模板.csv")
        if path:
            Path(path).write_text(TRACK_TEMPLATE,encoding="utf-8-sig")
            self.analysis_status.set("已保存只有格式与表头的模板；请填入真实采集数据，没有生成实测采样点。")

    def export_analysis(self) -> None:
        if self.app.result is None or self.analysis_revision!=self.app.revision:
            self.analysis_status.set("尚无当前设计的有效分析结果。")
            return
        path=filedialog.asksaveasfilename(parent=self.app.root,defaultextension=".json",initialfile="轨迹与扰动分析.json")
        if path:
            measured=None if self.track is None else {"declared_measured_not_authenticated":self.track.metadata['kind']=='measured',"metadata":self.track.metadata,
                 "filename":self.track.filename,"sha256":self.track.sha256,"path_mm":self.track.path.tolist(),"time_s":self.track.time_s.tolist(),"checkpoints":self.measured_rows}
            Path(path).write_text(json.dumps({"software_version":VERSION,"project":self.app.result.project.to_dict(),
                 "measured":measured,"theoretical_single_factor_perturbations":self.perturbations,
                 "limits":"几何运动学预测，不含动力学、打滑或实车性能保证；导入来源未经核实。"},ensure_ascii=False,indent=2,allow_nan=False),encoding="utf-8")
