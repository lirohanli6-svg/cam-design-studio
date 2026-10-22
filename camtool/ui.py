from __future__ import annotations

import copy
import ctypes
import hashlib
import math
import json
import queue
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any, Callable
from functools import partial

import numpy as np

from . import plotting
from .engine import Result, calculate, optimize
from .export import export_design, report_text
from .project import Project, ProjectError
from .version import APP_NAME, VERSION, changelog_text
from .verification_ui import VerificationPage
from .workbench import apply_parameter_profile, index_at_time, parameter_profile, time_axis
from .waypoints import WaypointEditor, PointTableEditor
from .workbench_ui import WorkbenchPages, prepare_views
from .dimensions import draw_dimensions
from .auxiliary_ui import AuxiliaryControls
from .plot_views import FigurePanel,RetainedCanvas
from .workflow import workflow_state
from .improvement_ui import ImprovementPage


def prepare_display() -> None:
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except (AttributeError, OSError):
        pass


class PageNotebook(ttk.Notebook):
    """Synchronize programmatic selection before queued Tk tab-change events run."""
    on_select: Callable[[int],Any] | None = None

    def select(self,tab_id: Any = None) -> str:
        value=super().select(tab_id)
        if tab_id is not None and self.on_select is not None:
            self.on_select(self.index(super().select()))
        return value


class App:
    views: ttk.Notebook
    verification_frame: ttk.Frame
    workbench: WorkbenchPages
    tree: ttk.Treeview
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.project = Project.demo()
        self.file: Path | None = None
        self.result: Result | None = None
        self.revision = 0
        self.dirty = False
        self.loading = True
        self.busy = False
        self.task_kind = ""
        self.export_receipt: tuple[int, Path, str, tuple[int, int]] | None = None
        self.workflow_signature: tuple = ()
        self.workflow_check_at = 0.0
        self.workflow_status = tk.StringVar()
        self.workflow_step = tk.StringVar()
        self.playing = False
        self.playback_rate = tk.StringVar(value="1")
        self.play_generation = 0
        self.play_clock = 0.0
        self.play_start = 0.0
        self.scene_plot: plotting.Plot | None = None
        self.drag_plot: plotting.Plot | None = None
        self.drag_index: int | None = None
        self.drag_snapshot: dict[str, Any] | None = None
        self.drag_editor: PointTableEditor | None = None
        self.map_edit = tk.BooleanVar(value=True)
        self.view_mode=tk.StringVar(value='编辑点位模式')
        self.detail_angle: float | None=None
        self.figure_panels: dict[str,FigurePanel]={}
        self.figure_windows: dict[str,tuple[tk.Toplevel,FigurePanel]]={}
        self.drag_canvas: tk.Canvas | None=None
        self.drag_pending: tuple[float,float] | None=None
        self.drag_timer: str | None=None
        self.drag_invalidated=False
        self.drag_motions=0;self.drag_flushes=0
        self.drag_frame_ms: list[float]=[]
        self.map_mode = tk.StringVar(value="打卡点")
        self.map_hint = tk.StringVar()
        self.dimension_canvases: dict[str, tk.Canvas] = {}
        self.active_parameter = "vehicle.wheelbase"
        self.parameter_source = tk.StringVar(value="设计项目继承，尚未独立实测")
        self.cancel = threading.Event()
        self.events: queue.Queue[tuple[str, Any]] = queue.Queue()
        self.values: dict[str, tk.StringVar] = {}
        self.conversions: dict[str, Callable[[str], Any]] = {}
        self.controls: dict[str, ttk.Entry | ttk.Combobox] = {}
        self.name = tk.StringVar()
        self.is_demo = tk.BooleanVar(value=True)
        self.status = tk.StringVar(value="演示参数仅供学习。填写实车尺寸后，点击计算。")
        self.frame = tk.IntVar(value=0)
        self.frame.trace_add('write',self.frame_changed)
        root.title(f"{APP_NAME} {VERSION}")
        width, height = min(1360, root.winfo_screenwidth()-100), min(780, root.winfo_screenheight()-140)
        root.geometry(f"{width}x{height}+20+20")
        root.minsize(1000, 640)
        root.configure(bg="#edf2f8")
        root.option_add("*Font", ("Microsoft YaHei UI", 9))
        style = ttk.Style(root)
        style.theme_use("clam")
        style.configure("TFrame", background="#edf2f8")
        style.configure("TLabel", background="#edf2f8", foreground="#25334c")
        style.configure("TButton", padding=(8, 5), width=7)
        style.configure("Compact.TButton", padding=(5,2), font=("Microsoft YaHei UI",9))
        style.configure("Accent.TButton", background="#2464df", foreground="white")
        style.configure("TNotebook.Tab", padding=(12, 7))
        style.layout("Content.TNotebook.Tab", [])
        style.configure("Navigation.TNotebook.Tab", padding=(10, 7))
        style.configure("Treeview", rowheight=26)
        self._build()
        self.playback_rate.trace_add("write", lambda *args: self.pause())
        self.name.trace_add("write", self.changed)
        self.is_demo.trace_add("write", self.changed)
        self.load(self.project)
        self.root.bind("<Control-z>", self.undo_key)
        self.root.bind("<Control-y>", self.redo_key)
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.root.after(80, self.poll)

    def _build(self) -> None:
        heading = tk.Frame(self.root, bg="#172740", padx=18, pady=3)
        heading.pack(fill="x")
        tk.Label(heading, text="凸轮设计工作台", bg="#172740", fg="white", font=("Microsoft YaHei UI", 17, "bold")).pack(side="left")
        tk.Label(heading, text="路线 → 车辆 → 转向机构 → 凸轮", bg="#172740", fg="#a7bddc").pack(side="right")
        toolbar = ttk.Frame(self.root, padding=(12, 3))
        toolbar.pack(fill="x")
        actions: list[tuple[str, Callable[[], Any]]] = [("新建", self.new), ("示例与路演", lambda:self.show_workbench("demo")), ("打开", self.open),
                                                       ("保存", self.save), ("另存为", lambda: self.save(True))]
        for text, action in actions:
            ttk.Button(toolbar, text=text, command=action, width=11 if text=="示例与路演" else 7).pack(side="left", padx=2)
        self.run_button = ttk.Button(toolbar, text="计算设计", command=lambda: self.run(False), style="Accent.TButton")
        self.run_button.pack(side="left", padx=(18, 3))
        self.optimize_button = ttk.Button(toolbar, text="本地优化", command=lambda: self.run(True))
        self.optimize_button.pack(side="left", padx=3)
        ttk.Button(toolbar, text="停止计算", command=self.cancel.set).pack(side="left", padx=3)
        ttk.Button(toolbar, text="二次检验", command=lambda: self.views.select(self.verification_frame)).pack(side="left", padx=3)
        self.export_button = ttk.Button(toolbar, text="导出结果", command=self.export, state="disabled")
        self.export_button.pack(side="right", padx=3)
        details = ttk.Frame(self.root, padding=(16, 0, 16, 4))
        details.pack(fill="x")
        ttk.Label(details, text="项目名称").pack(side="left")
        ttk.Entry(details, textvariable=self.name, width=35).pack(side="left", padx=8)
        ttk.Checkbutton(details, text="演示项目（须替换实车尺寸）", variable=self.is_demo).pack(side="left", padx=10)
        ttk.Button(details, text=f"版本与更新日志 {VERSION}", command=self.show_version, width=24).pack(side="right")
        workflow = ttk.Frame(self.root, padding=(16, 2))
        workflow.pack(fill="x")
        labels = ttk.Frame(workflow)
        state_label = ttk.Label(labels, textvariable=self.workflow_status, wraplength=1000)
        state_label.pack(anchor="w")
        step_label = ttk.Label(labels, textvariable=self.workflow_step, foreground="#546784", wraplength=1000)
        step_label.pack(anchor="w")
        self.next_button = ttk.Button(workflow, text="下一步", command=self.next_step, width=18)
        self.next_button.pack(side="right", padx=4)
        labels.pack(side="left", fill="x", expand=True)
        labels.bind("<Configure>", lambda e:[label.configure(wraplength=max(320,e.width-2)) for label in (state_label,step_label)])
        self.navigation = ttk.Notebook(self.root, height=1, style="Navigation.TNotebook")
        self.navigation.pack(fill="x", padx=12, pady=(2,0))
        pane = ttk.Panedwindow(self.root, orient="horizontal")
        pane.pack(fill="both", expand=True, padx=12)
        self.inputs = ttk.Notebook(pane)
        pane.add(self.inputs, weight=0)
        content_views=PageNotebook(pane, style="Content.TNotebook")
        self.views = content_views
        pane.add(self.views, weight=1)
        self._waypoint_page()
        self._parameter_pages()
        self.canvases: dict[str, tk.Canvas] = {}
        canvas: tk.Canvas
        for key, title in [("scene", "场地与试跑"), ("cam", "凸轮轮廓"), ("mechanism", "机构运动")]:
            page = ttk.Frame(self.views)
            self.views.add(page, text=title)
            if key=="scene":
                mode=ttk.Frame(page,padding=3);mode.pack(fill="x")
                ttk.Label(mode,text="点位类型").pack(side="left")
                selector=ttk.Combobox(mode,textvariable=self.map_mode,values=("打卡点","辅助点"),state="readonly",width=7)
                selector.pack(side="left",padx=4)
                selector.bind("<<ComboboxSelected>>",self.choose_map_mode)
                ttk.Label(mode,textvariable=self.map_hint,wraplength=450,font=("Microsoft YaHei UI",8)).pack(side="left",fill="x",expand=True)
            if key in ('scene','cam'):
                panel=FigurePanel(page,self,key);panel.pack(fill='both',expand=True)
                self.figure_panels[key]=panel;canvas=panel.canvas
            else:
                canvas = tk.Canvas(page, bg="white", highlightthickness=0)
                canvas.pack(fill="both", expand=True)
                canvas.bind("<Configure>", lambda _e: self.schedule_render())
            self.canvases[key] = canvas
        curve_page = ttk.Frame(self.views)
        self.views.add(curve_page, text="计算曲线")
        for key in ("steer", "stroke", "pressure"):
            canvas = tk.Canvas(curve_page, bg="white", highlightthickness=1, highlightbackground="#e7edf5")
            canvas.pack(fill="both", expand=True, pady=1)
            canvas.bind("<Configure>", lambda _e: self.schedule_render())
            self.canvases[key] = canvas
        report_page = ttk.Frame(self.views)
        self.views.add(report_page, text="检查报告")
        ttk.Button(report_page,text="设计改进助手：先看问题 → 确认可改部件 → 试算验证",command=lambda:self.improvement.open(),width=58).pack(anchor="w",padx=5,pady=3)
        self.report_tabs=ttk.Notebook(report_page)
        self.report_tabs.pack(fill="both",expand=True)
        summary_page=ttk.Frame(self.report_tabs)
        points_page=ttk.Frame(self.report_tabs)
        self.report_tabs.add(summary_page,text="总体检查报告")
        self.report_tabs.add(points_page,text="逐点打卡偏差")
        self.checkpoint_status=tk.StringVar(value="尚未计算。")
        ttk.Label(points_page,textvariable=self.checkpoint_status,wraplength=470,padding=6).pack(fill="x")
        self.checkpoint_tree=ttk.Treeview(points_page,columns=("number","target","error","limit","state"),show="headings",selectmode="browse")
        for key,title,width in [("number","点号",45),("target","必过点 X,Y mm",135),("error","名义偏差 mm",100),("limit","容差 mm",75),("state","状态",70)]:
            self.checkpoint_tree.heading(key,text=title);self.checkpoint_tree.column(key,width=width,minwidth=35)
        bar=ttk.Scrollbar(points_page,command=self.checkpoint_tree.yview)
        self.checkpoint_tree.configure(yscrollcommand=bar.set)
        bar.pack(side="right",fill="y");self.checkpoint_tree.pack(fill="both",expand=True)
        self.checkpoint_tree.tag_configure("fail",foreground="#b3263d")
        self.checkpoint_tree.bind("<<TreeviewSelect>>",self.locate_checkpoint)
        self.report = tk.Text(summary_page, wrap="word", bg="white", fg="#25334c", padx=18, pady=16,
                              relief="flat", font=("Microsoft YaHei UI", 10))
        self.report.pack(fill="both", expand=True)
        self.report.configure(state="disabled")
        help_page = ttk.Frame(self.views)
        self.views.add(help_page, text="使用说明")
        help_text = tk.Text(help_page, wrap="word", bg="white", padx=20, pady=20, relief="flat")
        help_text.insert("1.0", HELP)
        help_text.configure(state="disabled")
        help_text.pack(fill="both", expand=True)
        self.verification_frame = ttk.Frame(self.views)
        self.views.add(self.verification_frame, text="二次检验")
        self.verifier = VerificationPage(self.verification_frame, self.verification_context)
        self.workbench = WorkbenchPages(self)
        self.auxiliary = AuxiliaryControls(self, self.workbench.frames["compare"])
        self.improvement = ImprovementPage(self)
        self.inputs.bind("<<NotebookTabChanged>>",self.point_page_changed)
        self.map_edit.trace_add("write",self.map_mode_changed)
        self.auxiliary.editor.new_segment.trace_add("write",lambda *args:self.update_map_hint())
        self.update_map_hint()
        for tab in self.views.tabs():
            self.navigation.add(ttk.Frame(self.navigation, height=1), text=self.views.tab(tab,"text"))
        content_views.on_select=self.navigation.select
        self.navigation.bind("<<NotebookTabChanged>>", self.navigate_view)
        playback = ttk.Frame(self.root, padding=(16, 2))
        playback.pack(side="bottom", fill="x", before=pane)
        ttk.Button(playback, text="播放 / 暂停", command=self.play).pack(side="left")
        self.slider = ttk.Scale(playback, from_=0, to=1, variable=self.frame, command=lambda _value: self.schedule_render())
        self.slider.pack(side="left", fill="x", expand=True, padx=12)
        self.slider.bind("<ButtonPress-1>",lambda event:self.pause())
        self.progress_label = ttk.Label(playback, text="等待计算", width=30)
        self.progress_label.pack(side="right")
        self.status_label = ttk.Label(self.root, textvariable=self.status, padding=(16, 2), foreground="#546784")
        self.status_label.pack(side="bottom", fill="x", before=playback)
        def tab_changed(_event: Any) -> None:
            selected = self.views.select()
            expanded = selected in [str(self.verification_frame), str(self.improvement.frame), *[str(frame) for frame in self.workbench.frames.values()]]
            if expanded:
                if str(self.inputs) in pane.panes():
                    pane.forget(self.inputs)
                playback.pack_forget()
                self.status_label.pack_forget()
                if selected == str(self.verification_frame):
                    self.pause()
                    self.verifier.render()
            else:
                if str(self.inputs) not in pane.panes():
                    pane.insert(0, self.inputs, weight=0)
                if not playback.winfo_manager():
                    playback.pack(side="bottom", fill="x", before=pane)
                if not self.status_label.winfo_manager():
                    self.status_label.pack(side="bottom", fill="x", before=playback)
            self.schedule_render()
        self.views.bind("<<NotebookTabChanged>>", tab_changed)
        self.root.bind_all('<ButtonRelease-1>',self.outside_release,add='+')
        for canvas in self.canvases.values():
            if isinstance(canvas,RetainedCanvas):continue
            canvas.create_text(30, 40, text="填写左侧参数后，点击「计算设计」。", fill="#718096", anchor="nw")

    def verification_context(self) -> tuple[Project, Any, Any, float]:
        project = self.collect()
        if self.result is None or not len(self.result.profile):
            return project, None, None, 240.0
        result = self.result
        reference = result.target_path.copy()
        nominal = np.column_stack([np.degrees(result.cam_angle), np.degrees(result.cam_delta)])
        return project, reference, nominal, float(result.stats["cam_usage"]*360)

    def refresh_workflow(self) -> None:
        receipt = self.export_receipt
        if receipt is not None and time.monotonic() >= self.workflow_check_at:
            self.workflow_check_at = time.monotonic()+1
            try:
                stat = receipt[1].stat()
                if (stat.st_mtime_ns,stat.st_size) != receipt[3]:
                    self.export_receipt = None
            except OSError:
                self.export_receipt = None
        receipt = self.export_receipt
        digest = receipt[2] if receipt is not None and receipt[0] == self.revision else ""
        signature = (self.revision,id(self.result),digest,id(self.verifier.result),self.busy,
                     self.auxiliary.busy,self.verifier.busy,self.workbench.analysis_busy,self.improvement.busy,self.task_kind)
        if signature == self.workflow_signature:
            return
        self.workflow_signature = signature
        state = workflow_state(self.result,digest,self.verifier.result)
        self.workflow_status.set(state.stages)
        running = self.task_kind if self.busy else ("设计改进助手（本页可停止）" if self.improvement.busy else ("辅助点搜索" if self.auxiliary.busy else (
            "最终 DXF 二次检验" if self.verifier.busy else ("实测/扰动分析" if self.workbench.analysis_busy else "")))
        )
        self.workflow_step.set(f"正在运行：{running}；可在对应任务处停止。完成前不是本次通过结论。" if running else state.next_step)
        titles = {"calculate":"计算设计", "issues":"查看问题定位", "export":"导出结果", "verify":"检验本次导出", "limits":"查看落地边界"}
        self.next_button.configure(text=titles[state.action],state="disabled" if running else "normal")

    def next_step(self) -> None:
        if self.busy or self.auxiliary.busy or self.verifier.busy or self.workbench.analysis_busy or self.improvement.busy:
            return
        receipt = self.export_receipt
        digest = receipt[2] if receipt is not None and receipt[0] == self.revision else ""
        action = workflow_state(self.result,digest,self.verifier.result).action
        if action == "calculate":
            self.run(False)
        elif action == "issues":
            self.show_workbench("issues")
        elif action == "export":
            self.export()
        elif action == "verify" and receipt is not None:
            self.verifier.file.set(str(receipt[1]))
            self.verifier.copy_design()
            self.views.select(self.verification_frame)
        else:
            messagebox.showinfo("理论到实物", "理论设计与最终 DXF 检验均不能代替实物验证。\n下一步：核对实际机构和测量参数 → 制造装配 → 低速试跑采集 → 导入实测轨迹比较。\n弹簧、摩擦、驱动力矩、打滑及空间接触尚未验证。", parent=self.root)

    def show_version(self) -> None:
        window = tk.Toplevel(self.root)
        window.title(f"版本与更新日志 · {VERSION}")
        window.geometry("820x620")
        text = tk.Text(window, wrap="word", padx=16, pady=16, font=("Microsoft YaHei UI", 10))
        bar = ttk.Scrollbar(window, command=text.yview)
        text.configure(yscrollcommand=bar.set)
        bar.pack(side="right", fill="y")
        text.pack(fill="both", expand=True)
        text.insert("1.0", changelog_text())
        text.configure(state="disabled")

    def show_workbench(self,key: str) -> None:
        self.views.select(self.workbench.frames[key])

    def navigate_view(self,_event: Any = None) -> None:
        index=self.navigation.index(self.navigation.select())
        if self.views.index(self.views.select())!=index:
            self.views.select(index)
        if self.views.select()==str(self.improvement.frame):self.improvement.on_visible()

    def parameter_focus(self,key: str,_event: Any = None) -> None:
        self.active_parameter=key
        self.draw_sketches()

    def draw_sketches(self) -> None:
        try:
            project=self.collect()
            project.check_structure()
        except (ProjectError,ValueError,AttributeError,tk.TclError):
            project=self.project
        for kind,canvas in self.dimension_canvases.items():
            draw_dimensions(canvas,project,self.active_parameter,kind)
            canvas.scale("all",0,0,min(1,max(canvas.winfo_width(),340)/340),.84)

    def save_parameters(self) -> None:
        try:
            snapshot=parameter_profile(self.collect(),self.parameter_source.get())
            path=filedialog.asksaveasfilename(parent=self.root,defaultextension=".json",initialfile="实测参数配置.json")
            if path:
                Path(path).write_text(json.dumps(snapshot,ensure_ascii=False,indent=2,allow_nan=False),encoding="utf-8")
                self.status.set("参数配置与来源已保存；来源声明不等于软件核实。")
        except (ProjectError,ValueError,OSError) as exc:
            messagebox.showerror("参数配置",str(exc),parent=self.root)

    def load_parameters(self) -> None:
        if not self.ensure_editors_idle(): return
        path=filedialog.askopenfilename(parent=self.root,filetypes=[("参数配置","*.json")])
        if path:
            try:
                data=json.loads(Path(path).read_text(encoding="utf-8-sig"))
                project=apply_parameter_profile(self.collect(),data)
                previous_file=self.file
                self.load(project); self.file=previous_file
                self.parameter_source.set(data["source"])
                self.changed()
            except (ProjectError,ValueError,TypeError,OSError) as exc:
                messagebox.showerror("参数配置",str(exc),parent=self.root)

    def route_snapshot(self) -> dict[str,Any]:
        return self.waypoints.snapshot()

    def undo_route(self) -> None:
        self.waypoints.undo()

    def redo_route(self) -> None:
        self.waypoints.undo(True)

    def undo_key(self,event: Any) -> str | None:
        if isinstance(event.widget,(tk.Text,tk.Entry,ttk.Entry,ttk.Combobox)): return None
        if self.views.select()==str(self.improvement.frame):self.improvement.undo();return "break"
        editor=self.keyboard_editor(event.widget)
        if editor is not None: editor.undo(); return "break"
        return None

    def redo_key(self,event: Any) -> str | None:
        if isinstance(event.widget,(tk.Text,tk.Entry,ttk.Entry,ttk.Combobox)): return None
        if self.views.select()==str(self.improvement.frame):self.improvement.undo(True);return "break"
        editor=self.keyboard_editor(event.widget)
        if editor is not None: editor.undo(True); return "break"
        return None

    def keyboard_editor(self,widget: Any) -> PointTableEditor | None:
        panel=self.figure_for(widget)
        if panel is not None and panel.kind=='scene':return self.map_editor()
        selected=self.inputs.select()
        for editor in (self.waypoints,self.auxiliary.editor):
            if selected==str(editor):return editor
        return None

    def ensure_editors_idle(self) -> bool:
        """Global project operations require explicit resolution of both drafts."""
        if self.drag_canvas is not None:self.finish_map_gesture()
        for editor in (self.waypoints,self.auxiliary.editor):
            if editor.draft is not None and editor._pending():
                self.views.select(0);self.inputs.select(editor)
                editor.ensure_idle();return False
        return all(editor.ensure_idle() for editor in (self.waypoints,self.auxiliary.editor))

    def map_editor(self) -> PointTableEditor:
        return self.auxiliary.editor if self.map_mode.get()=="辅助点" else self.waypoints

    def frame_changed(self,*args: Any) -> None:
        self.detail_angle=None
        if hasattr(self,'workbench'):self.schedule_render()

    def map_mode_changed(self,*args: Any) -> None:
        self.finish_map_gesture()
        self.view_mode.set('编辑点位模式' if self.map_edit.get() else '浏览模式')
        self.update_map_hint()

    def open_figure(self,kind: str) -> None:
        if kind in self.figure_windows:
            window,panel=self.figure_windows[kind];window.deiconify();window.lift();panel.draw();return
        window=tk.Toplevel(self.root);window.title(('场地路线图' if kind=='scene' else '凸轮轮廓图')+' · 放大查看')
        window.geometry('1040x740+40+40');window.minsize(640,480)
        panel=FigurePanel(window,self,kind,True);panel.pack(fill='both',expand=True)
        self.figure_windows[kind]=(window,panel)
        def close() -> None:
            if self.drag_canvas==panel.canvas:self.finish_map_gesture()
            self.figure_windows.pop(kind,None);window.destroy()
        window.protocol('WM_DELETE_WINDOW',close);window.bind('<Control-z>',self.undo_key);window.bind('<Control-y>',self.redo_key);window.after_idle(panel.draw)

    def figure_for(self,canvas: Any) -> FigurePanel | None:
        return next((panel for panel in [*self.figure_panels.values(),*[p for _,p in self.figure_windows.values()]] if panel.canvas==canvas),None)

    def show_figure_angle(self,angle: float | None) -> None:
        self.detail_angle=angle;self.schedule_render()

    def focus_problem(self) -> None:
        d=self.workbench.focus;r=self.result
        if d is None or r is None:return
        self.show_figure_angle(d.angle_deg)
        for panel in [*self.figure_panels.values(),*[p for _,p in self.figure_windows.values()]]:
            if panel.camera.home is None:
                if panel.kind=='scene':
                    a,b,c,e=r.project.route.field;bounds=(a-100,b+100,c-100,e+100)
                else:
                    radius=r.stats['max_profile_radius']*1.4;bounds=(-radius,radius,-radius,radius)
                panel.camera.prepare(bounds,panel.canvas.winfo_width(),panel.canvas.winfo_height())
            if panel.kind=='scene' and d.point is not None:
                panel.camera.focus(d.point[0],d.point[1],max(100,r.project.route.tolerance*5))
            elif panel.kind=='cam' and d.angle_deg is not None:
                i=int(np.argmin(abs(np.degrees(r.cam_angle)-d.angle_deg)))
                panel.camera.focus(float(r.pitch[i,0]),float(r.pitch[i,1]),max(5,r.project.mechanism.roller*5))
            panel.draw()
        self.views.select(0 if d.point is not None else 1)

    def point_page_changed(self,event: Any=None) -> None:
        selected=self.inputs.select()
        mode="辅助点" if selected==str(self.auxiliary.editor) else "打卡点" if selected==str(self.waypoints) else None
        if mode is not None:
            self.finish_map_gesture()
            self.map_mode.set(mode)
        self.update_map_hint();self.schedule_render()

    def choose_map_mode(self,event: Any=None) -> None:
        self.finish_map_gesture()
        self.inputs.select(self.map_editor())
        self.update_map_hint();self.schedule_render()

    def update_map_hint(self) -> None:
        mode=self.map_mode.get()
        action=f"区间 {self.auxiliary.editor.new_segment.get()} · " if mode=="辅助点" else "按比赛顺序 · "
        self.map_hint.set((action+"空白添加，拖动所选；蓝圆=打卡，紫菱=辅助") if self.map_edit.get() else "地图编辑已关闭（在当前点页勾选开启）")

    def draw_route_editor(self) -> None:
        panel=self.figure_panels['scene'];panel.draw();self.scene_plot=panel.plot

    def map_press(self,event: Any) -> None:
        editor=self.map_editor()
        if not editor.ensure_idle() or not self.map_edit.get():return
        panel=self.figure_for(getattr(event,'widget',self.canvases['scene'])) or self.figure_panels['scene']
        if panel.plot is None:return
        self.pause();self.drag_plot=panel.plot;self.drag_canvas=panel.canvas
        self.drag_snapshot=editor.snapshot();self.drag_editor=editor;self.drag_index=None
        self.drag_origin=(event.x,event.y);self.drag_pending=None;self.drag_invalidated=False
        self.drag_frame_ms=[]
        panel.canvas.focus_set()
        points=self.drag_snapshot['points']
        distance=[math.dist(self.drag_plot.xy(*point[:2]),(event.x,event.y)) for point in points]
        if distance:
            nearby=[i for i in editor.selected() if distance[i]<=12]
            nearest=min(nearby,key=lambda i:distance[i]) if nearby else int(np.argmin(distance))
            if distance[nearest]<=12:
                self.drag_index=nearest;editor.tree.selection_set(str(nearest));editor.tree.focus(str(nearest));editor.refresh_selection()
        other=self.waypoints if editor is self.auxiliary.editor else self.auxiliary.editor
        if self.drag_index is None and any(math.dist(self.drag_plot.xy(*point[:2]),(event.x,event.y))<=12 for point in other.document.points):
            editor.feedback.set('这是另一类点，请先切换地图编辑模式；本次未添加或修改。')
            self.drag_plot=None;self.drag_snapshot=None;self.drag_editor=None;self.drag_canvas=None;return
        if panel.canvas.winfo_viewable():panel.canvas.grab_set_global()

    def map_drag(self,event: Any) -> None:
        if self.drag_plot is None or self.drag_index is None:return
        self.drag_motions+=1;self.drag_pending=(event.x,event.y)
        if self.drag_timer is None:self.drag_timer=self.root.after(16,self.flush_drag)

    def flush_drag(self) -> None:
        started=time.perf_counter()
        if self.drag_timer is not None:self.root.after_cancel(self.drag_timer);self.drag_timer=None
        if self.drag_pending is None or self.drag_plot is None or self.drag_editor is None or self.drag_index is None:return
        px,py=self.drag_pending;self.drag_pending=None
        editor=self.drag_editor;i=self.drag_index
        # Returning to the exact press pixel restores original precision.
        if math.dist((px,py),self.drag_origin)<.5:
            point=self.drag_snapshot['points'][i][:2] if self.drag_snapshot is not None else editor.document.points[i][:2]
        else:point=[round(v,3) for v in self.drag_plot.unxy(px,py)]
        if editor is self.auxiliary.editor:
            try:editor.cell_value(i,0,str(point[0]));editor.cell_value(i,1,str(point[1]))
            except ProjectError as exc:editor.feedback.set(f'地图未移动：{exc}；未自动纠正。');return
        if editor.document.points[i][:2]==point:return
        editor.document.points[i][:2]=point
        if editor is self.auxiliary.editor:self.project.route.auxiliary_points=self.auxiliary.editor.document.controls()
        if not self.drag_invalidated:
            self.drag_invalidated=True;self.changed()
        editor.tree.item(str(i),values=tuple(editor.display_value(col,v) for col,v in enumerate(editor.document.points[i])))
        self.drag_flushes+=1
        self.render()
        self.drag_frame_ms.append((time.perf_counter()-started)*1000)

    def map_release(self,event: Any) -> None:
        if self.drag_plot is None or self.drag_snapshot is None or self.drag_editor is None:return
        editor=self.drag_editor
        if self.drag_index is not None:
            if math.dist((event.x,event.y),self.drag_origin)>.5 or self.drag_invalidated:
                self.drag_pending=(event.x,event.y);self.flush_drag()
        elif math.dist((event.x,event.y),self.drag_origin)<5:
            points=editor.snapshot()['points'];x,y=self.drag_plot.unxy(event.x,event.y);bounds=self.collect().route.field
            if editor is self.auxiliary.editor:self.auxiliary.editor.map_add(round(x,3),round(y,3))
            elif bounds[0]<=x<=bounds[1] and bounds[2]<=y<=bounds[3] and len(points)<200:
                point=[round(x,3),round(y,3)]
                if not points or np.linalg.norm(np.array(points[-1])-point)>.01:editor.apply([*points,point],(len(points),),preview=True)
        self.finish_map_gesture()

    def outside_release(self,event: Any) -> None:
        if self.drag_canvas is None:return
        # Capture keeps releases outside both main/popout canvas in this gesture.
        if getattr(event,'widget',None)==self.drag_canvas:return
        from types import SimpleNamespace
        self.map_release(SimpleNamespace(x=event.x_root-self.drag_canvas.winfo_rootx(),y=event.y_root-self.drag_canvas.winfo_rooty()))

    def finish_map_gesture(self) -> None:
        self.flush_drag()
        if self.drag_editor is not None and self.drag_snapshot is not None:
            editor=self.drag_editor
            if editor is self.waypoints and editor.document.points!=self.drag_snapshot['points']:
                editor.document.tangents=[];self.project.route.tangent_offsets=[]
            editor.finish_gesture(self.drag_snapshot)
        if self.drag_canvas is not None and self.drag_canvas.grab_current()==self.drag_canvas:self.drag_canvas.grab_release()
        self.drag_plot=None;self.drag_index=None;self.drag_snapshot=None;self.drag_editor=None;self.drag_canvas=None;self.drag_pending=None
        self.schedule_render()

    def _scroll_page(self, title: str) -> ttk.Frame:
        frame = ttk.Frame(self.inputs, width=420)
        self.inputs.add(frame, text=title)
        if title in ("车辆","机构"):
            kind="vehicle" if title=="车辆" else "mechanism"
            sketch=tk.Canvas(frame,height=175,bg="white",highlightthickness=0)
            sketch.pack(side="top",fill="x")
            self.dimension_canvases[kind]=sketch
            sketch.bind("<Configure>",lambda event:self.draw_sketches())
        canvas = tk.Canvas(frame, width=405, bg="#edf2f8", highlightthickness=0)
        bar = ttk.Scrollbar(frame, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=bar.set)
        bar.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        inner = ttk.Frame(canvas, padding=12)
        window = canvas.create_window((0, 0), window=inner, anchor="nw")
        inner.bind("<Configure>", lambda _e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure(window, width=e.width))
        def scroll(event: tk.Event[Any]) -> None:
            if str(event.widget).startswith(f"{frame}."):
                canvas.yview_scroll(-int(event.delta/120), "units")
        self.root.bind("<MouseWheel>", scroll, add="+")
        return inner

    def _field(self, page: tk.Misc, key: str, label: str, hint: str = "", choices: dict[str, Any] | None = None) -> None:
        row = ttk.Frame(page)
        row.pack(fill="x", pady=(5, 0))
        ttk.Label(row, text=label).pack(side="left")
        variable = tk.StringVar()
        self.values[key] = variable
        if choices:
            widget: ttk.Entry | ttk.Combobox = ttk.Combobox(row, textvariable=variable, values=list(choices), width=16, state="readonly")
            mapping = choices
            self.conversions[key] = lambda text: mapping[text]
        else:
            widget = ttk.Entry(row, textvariable=variable, width=19)
            self.conversions[key] = lambda text: float(text)
        widget.pack(side="right")
        self.controls[key] = widget
        widget.bind("<FocusIn>",partial(self.parameter_focus,key))
        variable.trace_add("write", self.changed)
        if hint:
            ttk.Label(page, text=hint, foreground="#718096", wraplength=350).pack(fill="x", pady=(1, 3))

    def _parameter_pages(self) -> None:
        page = self._scroll_page("车辆")
        profiles=ttk.Frame(page); profiles.pack(fill="x")
        ttk.Button(profiles,text="保存参数配置",width=17,command=self.save_parameters).pack(side="left",padx=2)
        ttk.Button(profiles,text="加载参数配置",width=17,command=self.load_parameters).pack(side="left",padx=2)
        ttk.Label(page,text="配置来源（实测请记录仪器/时间/记录号）",wraplength=350).pack(fill="x")
        ttk.Entry(page,textvariable=self.parameter_source).pack(fill="x",pady=4)
        ttk.Label(page, text="这里填车上实测尺寸\n以两后轮轴心连线的中点为原点：X 向车头，Y 向车体左侧。传感器相对位置与场地起点坐标是两组不同的数据。",
                  foreground="#2455a0", background="#e3ecfb", padding=8, wraplength=350).pack(fill="x", pady=(0, 6))
        basic=ttk.LabelFrame(page,text="基础参数 · 安装位置与尺寸",padding=5); basic.pack(fill="x",pady=4)
        advanced=ttk.LabelFrame(page,text="高级参数 · 速度与转向能力",padding=5)
        for key, label, hint in [
            ("sensor_x", "传感器前向距离 X mm", "从后轴中点朝车头量到传感器检测中心，须大于 0。例：前方 60 mm 填 60；此处不是场地起点 X。"),
            ("sensor_y", "传感器左向偏移 Y mm", "检测中心在车体中线上填 0；偏左 20 mm 填 20，偏右 20 mm 填 -20。此处不是场地起点 Y。"),
            ("wheelbase", "前后轴距 mm", "前轮转向轴到后轴中点的前后距离。"),
            ("track", "两后轮轮距 mm", "左右后轮中心之间的距离。"),
            ("front_diameter", "小前轮直径 mm", "小前轮外径，填写直径。"),
            ("drive_diameter", "后驱动轮直径 mm", "由传动机构带动的后轮外径。"),
            ("passive_diameter", "后从动轮直径 mm", "另一只自由转动的后轮外径。"),
            ("speed", "后轴中点地速 mm/s", "小车后轴中点每秒前进的毫米数，用于估算行驶时间。"),
            ("max_steer", "前轮转角上限 °", "前轮相对车身直行方向允许的最大偏转角，按实车能力填写；左转为正，右转为负。")]:
            self._field(advanced if key in ("speed","max_steer") else basic, f"vehicle.{key}", label, hint)
        self._field(basic, "vehicle.drive_side", "驱动轮位置", "面朝车头判断左右，选择实际由传动机构驱动的后轮。",
                    choices={"车体左侧": "left", "车体右侧": "right"})
        advanced.pack(fill="x",pady=4)
        ttk.Label(page, text="填写示例：传感器在后轴前方 60 mm、车体中线上，则 X=60、Y=0；示例不能代替实测尺寸。\n两后轮各自独立转动。", foreground="#718096", wraplength=350).pack(fill="x", pady=12)
        page = self._scroll_page("机构")
        basic=ttk.LabelFrame(page,text="基础参数 · 机构与接触尺寸",padding=5); basic.pack(fill="x",pady=4)
        advanced=ttk.LabelFrame(page,text="高级参数 · 装配、中位、分支与行程",padding=5)
        self._field(basic, "mechanism.kind", "机构模型", choices={"平推微调": "flat", "凸轮直接顶球": "ball"})
        self._field(basic, "mechanism.side", "J13 平推安装", choices={"左置": "left", "右置": "right"})
        for key, label, hint in [
            ("arm", "转向臂/球臂长 mm", "转向轴到连杆销或球心的距离。"),
            ("arm_angle", "中位摆臂角 °", "从机构平面 +X 轴逆时针量角。"),
            ("pivot_x", "转向轴横向位置 mm", "平推：相对导向线；顶球：相对凸轮轴。"),
            ("link", "平推连杆长度 mm", "两销中心距；顶球模式不使用。"),
            ("offset", "平推导向偏置 mm", "导向线到凸轮轴横距；顶球模式不使用。"),
            ("roller", "滚子/球半径 mm", "请输入半径，不能填直径。"),
            ("steer_zero", "机构中位对应前轮角 °", "机械零位微调；左转为正。"),
            ("min_stroke", "允许最小纵向位移 mm", "相对机构中位；整圈含回程均检查。"),
            ("max_stroke", "允许最大纵向位移 mm", "")]:
            self._field(basic if key in ("arm","link","roller") else advanced, f"mechanism.{key}", label, hint)
        self._field(advanced, "mechanism.branch", "平推连杆闭合分支", choices={"正支 +1": 1, "负支 -1": -1})
        advanced.pack(fill="x",pady=4)
        ttk.Label(page, text="这是可配置的理想平面机构。STEP 仅作尺寸参考，未自动识别 YDI 装配；请核对实际销、槽和球心运动。", foreground="#9c6c2d", wraplength=350).pack(fill="x", pady=12)
        page = self._scroll_page("凸轮")
        for key, label, hint in [
            ("base_radius", "设计保底基圆半径 mm", "实际最小半径会另行报告。"),
            ("neutral_lift", "中位抬升量 mm", "为双向位移预留空间；同时确定安装高度。"),
            ("hole_diameter", "中心孔直径 mm", "0 表示不输出中心孔。"),
            ("thickness", "凸轮厚度 mm", "用于记录；DXF 为二维轮廓。"),
            ("ratio", "轮 → 凸轮减速比", "驱动轮转多少圈，凸轮转 1 圈。"),
            ("phase", "廓线起始相位 °", "几何相位；装车时须按标记对齐。"),
            ("points", "整圈轮廓起始点数", "360～4000；漏点或回算误差过大时自动加密，实际点数见报告；达到上限仍不满足会报错。"),
            ("pressure_limit", "压力角上限 °", "属于用户设定的设计门槛。")]:
            self._field(page, f"cam.{key}", label, hint)
        self._field(page, "cam.direction", "凸轮旋向", choices={"逆时针 +1": 1, "顺时针 -1": -1})
        self._field(page, "cam.reference_layers", "导出参考图层", "保留时包含出发点 S0 和凸轮旋向箭头；选择仅轮廓与孔则不导出这些参考标记。",
                    choices={"保留参考图层": True, "仅轮廓与孔": False})
        page = self._scroll_page("场地")
        ttk.Label(page, text="这里填场地上的位置和起跑方向\n与打卡点共用场地坐标系：图中 X 向右、Y 向上。起点是传感器检测中心的位置，车辆尺寸请到「车辆」页填写。",
                  foreground="#2455a0", background="#e3ecfb", padding=8, wraplength=350).pack(fill="x", pady=(0, 6))
        for key, label, hint in [
            ("start_x", "传感器场地起点 X mm", "传感器起跑时在场地中的 X 坐标，与打卡点使用同一个原点；不是车上传感器的安装距离。"),
            ("start_y", "传感器场地起点 Y mm", "传感器起跑时在场地中的 Y 坐标。例：起点 (410,420)，上面填 410、这里填 420。"),
            ("heading", "车头方向角 °（可留空）", "直接输入角度：向右 0°、向上 90°、向左 180°、向下 -90°；留空时朝向表格第 2 个打卡点。"),
            ("start_steer", "起跑前轮角 °", "用于确定起跑传感器切向；须与实车起跑前轮姿态一致，左正右负。不是机构中位角。"),
            ("tolerance", "统一打卡容差半径 mm", "传感器经过各打卡中心时允许的最大偏差；所有点共用，按比赛要求填写。"),
            ("tension", "路线张力 0.2～1.5", "控制打卡点之间曲线的形状；修改后重新计算并检查弯道，数值越大不一定越好。"),
            ("steer_rate_limit", "转向变化速度上限 °/s", "理论恒速下的规划门槛；不代表已验证执行器动态能力。"),
            ("step", "路线采样步长 mm", "沿路线每隔多少毫米计算一次；数值越小，采样越密、计算量越大。")]:
            self._field(page, f"route.{key}", label, hint)
        ttk.Label(page, text="场地边界：填写场地坐标的左右、下上限。它限制可用空间，不会自动把弯道变宽。", foreground="#718096", wraplength=350).pack(fill="x", pady=(10, 0))
        for i, label in enumerate(["场地最小 X mm", "场地最大 X mm", "场地最小 Y mm", "场地最大 Y mm"]):
            self._field(page, f"bounds.{i}", label)
        ttk.Label(page, text="圆形障碍物：每行 x,y,半径（mm）", foreground="#546784").pack(fill="x", pady=(12, 4))
        self.obstacles = tk.Text(page, height=5, width=30, wrap="none", undo=True)
        self.obstacles.pack(fill="x")
        self.obstacles.bind("<<Modified>>", self.obstacle_changed)
        ttk.Label(page, text="障碍物示例：1000,800,50 表示圆心 (1000,800)、半径 50 mm；没有则留空。\n边界检查含传感器与轮心，不含车壳和轮宽。", foreground="#718096", wraplength=350).pack(fill="x", pady=8)
        page = self._scroll_page("优化")
        for key, label in [("iterations", "搜索轮数 1～6"), ("smooth_weight", "转向平顺权重"),
                           ("length_weight", "路线长度权重"), ("pressure_weight", "压力角改善权重")]:
            self._field(page, f"optimization.{key}", label)
        ttk.Label(page, text="先满足约束，再比较权重目标。\n本版搜索全路线张力和局部切线；自动辅助点搜索在「优化对比」页，属于局部优化，不保证找到全局最优。计算过程中可停止。", wraplength=350, foreground="#718096").pack(fill="x", pady=12)

    def _waypoint_page(self) -> None:
        self.waypoints=WaypointEditor(self.inputs,self.waypoints_changed,self.waypoint_selected,self.map_edit)
        self.inputs.add(self.waypoints,text="打卡点")
        self.tree=self.waypoints.tree

    def waypoints_changed(self) -> None:
        self.project.route.tangent_offsets=self.waypoints.snapshot()["tangents"]
        self.changed()

    def waypoint_selected(self) -> None:
        if not self.loading and self.inputs.select()==str(self.waypoints) and (self.waypoints.selected() or self.waypoints.draft is not None):
            self.pause()
            self.views.select(0)
        self.schedule_render()

    def changed(self, *_args: Any) -> None:
        if self.loading:
            return
        self.dirty = True
        self.revision += 1
        self.result = None
        self.export_receipt = None
        self.progress_label.configure(text="输入已更改 · 等待重算")
        self.playing = False
        self.play_generation+=1
        self.cancel.set()
        self.workbench.invalidate()
        self.checkpoint_tree.delete(*self.checkpoint_tree.get_children())
        self.checkpoint_status.set("输入已更改或项目已载入，旧逐点结果失效，请重新计算。")
        self.auxiliary.invalidate()
        self.improvement.invalidate()
        self.scene_plot=None
        self.export_button.configure(state="disabled")
        self.status.set("参数已修改，预览结果已失效；请重新计算后导出。")
        self._mechanism_controls()
        for canvas in self.canvases.values():
            if isinstance(canvas,RetainedCanvas):continue
            canvas.delete("all")
            canvas.create_text(30, 40, text="输入已更改，请重新计算以更新预览。", anchor="nw", fill="#718096")
        self.report.configure(state="normal")
        self.report.delete("1.0", "end")
        self.report.insert("1.0", "输入已更改，旧报告已失效。请重新计算。")
        self.report.configure(state="disabled")
        if self.drag_plot is None:self.draw_sketches()
        self.schedule_render()

    def obstacle_changed(self, _event: Any) -> None:
        if self.obstacles.edit_modified():
            self.changed()
            self.obstacles.edit_modified(False)

    def load(self, project: Project) -> None:
        self.export_receipt = None
        self.finish_map_gesture()
        for panel in [*self.figure_panels.values(),*[p for _,p in self.figure_windows.values()]]:panel.camera.home=None
        self.pause()
        self.cancel.set()
        self.revision+=1
        self.drag_plot=None; self.drag_index=None; self.drag_snapshot=None;self.drag_editor=None
        self.workbench.invalidate()
        self.checkpoint_tree.delete(*self.checkpoint_tree.get_children())
        self.checkpoint_status.set("输入已更改或项目已载入，旧逐点结果失效，请重新计算。")
        self.auxiliary.invalidate()
        self.improvement.invalidate()
        self.loading = True
        self.project = copy.deepcopy(project)
        self.name.set(project.name)
        self.is_demo.set(project.demo_mode)
        for key, var in self.values.items():
            group, attr = key.split(".")
            value = project.route.field[int(attr)] if group == "bounds" else getattr(getattr(project, group), attr)
            control = self.controls[key]
            if isinstance(control, ttk.Combobox):
                options = list(control["values"])
                matches = [option for option in options if self.conversions[key](option) == value]
                if not matches:
                    raise ProjectError(f"不支持的选项：{key}。")
                var.set(matches[0])
            else:
                var.set("" if value is None else str(value))
        self.waypoints.load(project.route.waypoints,project.route.tangent_offsets)
        self.auxiliary.load()
        self.obstacles.delete("1.0", "end")
        self.obstacles.insert("1.0", "\n".join(",".join(str(x) for x in row) for row in project.route.obstacles))
        self.obstacles.edit_modified(False)
        self.loading = False
        self.dirty = False
        self.result = None
        self.progress_label.configure(text="项目已载入 · 等待计算")
        self.export_button.configure(state="disabled")
        self._mechanism_controls()
        for canvas in self.canvases.values():
            if isinstance(canvas,RetainedCanvas):continue
            canvas.delete("all")
            canvas.create_text(30, 40, text="项目已载入，请点击「计算设计」。", fill="#718096", anchor="nw")
        self.report.configure(state="normal")
        self.report.delete("1.0", "end")
        self.report.insert("1.0", "项目已载入，尚无本项目的计算报告。")
        self.report.configure(state="disabled")
        self.parameter_source.set("项目参数继承，未记录独立测量来源")
        self.draw_sketches()
        self.scene_plot=None
        self.schedule_render()

    def _mechanism_controls(self) -> None:
        flat = self.values["mechanism.kind"].get() == "平推微调"
        for key in ("mechanism.side", "mechanism.link", "mechanism.branch", "mechanism.offset"):
            widget = self.controls[key]
            widget.configure(state=("readonly" if isinstance(widget, ttk.Combobox) else "normal") if flat else "disabled")

    def collect(self) -> Project:
        p = copy.deepcopy(self.project)
        p.name, p.demo_mode = self.name.get(), self.is_demo.get()
        for key, variable in self.values.items():
            group, attr = key.split(".")
            text = variable.get().strip()
            try:
                value = None if key == "route.heading" and not text else self.conversions[key](text)
            except (ValueError, KeyError) as exc:
                raise ProjectError(f"请检查参数 {key}，输入有效数字或选项。",fields=(key,)) from exc
            if group == "bounds":
                if value is None:
                    raise ProjectError("场地边界不能留空。")
                p.route.field[int(attr)] = float(value)
            else:
                setattr(getattr(p, group), attr, value)
        snapshot=self.waypoints.snapshot()
        p.route.waypoints=snapshot["points"]
        p.route.tangent_offsets=snapshot["tangents"]
        p.route.auxiliary_points=self.auxiliary.editor.document.controls()
        try:
            p.route.obstacles = [[float(x.strip()) for x in line.split(",")] for line in
                                 self.obstacles.get("1.0", "end").splitlines() if line.strip()]
        except ValueError as exc:
            raise ProjectError("障碍物应每行填写 x,y,半径。") from exc
        return p

    def table_changed(self,points: list[list[float]]) -> None:
        self.waypoints.apply(points)

    def may_discard(self) -> bool:
        if not self.ensure_editors_idle(): return False
        if not self.dirty:
            return True
        answer = messagebox.askyesnocancel("保存项目", "当前项目有未保存的修改。是否先保存？", parent=self.root)
        return self.save() if answer else answer is not None

    def new(self) -> None:
        if self.may_discard():
            p = Project.demo()
            p.name = "新项目 · 请填写实车尺寸"
            p.demo_mode = False
            p.route.waypoints = []
            self.load(p)
            self.file = None
            self.changed()

    def demo(self) -> None:
        if self.may_discard():
            self.load(Project.demo())
            self.file = None
            self.revision += 1
            self.status.set("演示项目已载入。尺寸为示例，请先计算查看。")

    def open(self) -> None:
        if not self.may_discard():
            return
        path = filedialog.askopenfilename(parent=self.root, filetypes=[("小车项目", "*.cam.json"), ("JSON", "*.json")])
        if path:
            try:
                project = Project.load(path)
                self.load(project)
                self.file = Path(path)
                self.revision += 1
                self.status.set("项目已打开，请计算以刷新结果。")
            except (OSError, ProjectError, ValueError, TypeError) as exc:
                messagebox.showerror("打开失败", str(exc), parent=self.root)

    def save(self, save_as: bool = False) -> bool:
        if not self.ensure_editors_idle(): return False
        try:
            project = self.collect()
            path = self.file
            if path is None or save_as:
                selected = filedialog.asksaveasfilename(parent=self.root, defaultextension=".cam.json", filetypes=[("小车项目", "*.cam.json")])
                if not selected:
                    return False
                path = Path(selected)
            project.save(path)
            self.project, self.file, self.dirty = project, path, False
            self.status.set(f"已保存：{path.name}")
            return True
        except (OSError, ValueError) as exc:
            messagebox.showerror("保存失败", str(exc), parent=self.root)
            return False

    def run(self, improve: bool) -> None:
        if not self.ensure_editors_idle(): return
        if self.busy or self.auxiliary.busy or self.improvement.busy:
            return
        try:
            project = self.collect()
        except ProjectError as exc:
            messagebox.showerror("参数有误", str(exc), parent=self.root)
            return
        self._mechanism_controls()
        self.pause()
        self.busy, self.playing = True, False
        self.task_kind = "本地优化" if improve else "计算设计"
        self.cancel.clear()
        self.run_button.configure(state="disabled")
        self.optimize_button.configure(state="disabled")
        self.export_button.configure(state="disabled")
        revision = self.revision
        original_snapshot = copy.deepcopy(self.result) if self.result is not None and self.result.project.to_dict()==project.to_dict() else None
        self.status.set("正在本地计算…" if not improve else "正在比较路线候选，可点击停止优化。")
        def worker() -> None:
            try:
                if improve:
                    original=original_snapshot
                    original_error=""
                    if original is None:
                        try: original=calculate(project,self.cancel.is_set)
                        except ProjectError as exc:
                            if self.cancel.is_set(): raise
                            original_error=str(exc)
                    result=optimize(project,lambda text:self.events.put(("status",(revision,text))),self.cancel.is_set)
                    ready=(None if original is None else prepare_views(original),prepare_views(result))
                    if self.cancel.is_set(): raise ProjectError("优化已停止；原方案未替换。")
                    self.events.put(("comparison",(revision,original,result,ready,project,original_error)))
                else:
                    result=calculate(project,self.cancel.is_set)
                    ready_result=prepare_views(result)
                    if self.cancel.is_set(): raise ProjectError("计算已停止。")
                    self.events.put(("done", (revision, result, ready_result)))
            except Exception as exc:
                self.events.put(("error", (revision,str(exc))))
        threading.Thread(target=worker, daemon=True).start()

    def poll(self) -> None:
        try:
            while True:
                event, value = self.events.get_nowait()
                if event == "status":
                    revision,text=value
                    if revision==self.revision: self.status.set(text)
                else:
                    self.busy = False
                    self.run_button.configure(state="normal")
                    self.optimize_button.configure(state="normal")
                    if event == "error":
                        revision,text=value
                        self.export_button.configure(state="normal" if self.result is not None and len(self.result.profile) else "disabled")
                        if revision!=self.revision: continue
                        self.status.set(text)
                        if "停止" not in text: messagebox.showerror("计算提示", text, parent=self.root)
                    else:
                        revision=value[0]
                        if revision != self.revision:
                            self.status.set("计算期间输入发生变化；结果已丢弃，请重新计算。")
                            continue
                        if event == "exported":
                            _,path,digest,stamp=value
                            if self.cancel.is_set():
                                self.status.set("导出文件已写入，但任务随后被停止；请核对目录。未将其关联为当前检验对象。")
                            else:
                                self.export_receipt=(revision,path,digest,stamp)
                                self.status.set("五个结果文件已完整导出。点击「检验本次导出」，核对参数与来源后开始独立检验。")
                            self.export_button.configure(state="normal" if self.result is not None else "disabled")
                            continue
                        if self.cancel.is_set():
                            self.status.set("任务已停止；未采用后台结果，原方案保留。")
                            self.export_button.configure(state="normal" if self.result is not None and len(self.result.profile) else "disabled")
                            continue
                        if event=="comparison":
                            _,original,result,ready,project,original_error=value
                            if original is not None: self.show_result(original,ready[0])
                            self.workbench.set_comparison(original,result,ready,project,original_error)
                            self.status.set("优化候选已生成；原方案保留，请在优化对比页选择。")
                        else:
                            _,result,prepared=value
                            self.show_result(result,prepared)
        except queue.Empty:
            pass
        self.refresh_workflow()
        self.root.after(80, self.poll)

    def show_result(self, result: Result, prepared: dict[str,Any] | None = None) -> None:
        self.pause()
        self.result = result
        self.workbench.install_result(result,prepared)
        self.slider.configure(to=len(result.path)-1)
        self.frame.set(0)
        self.export_button.configure(state="normal" if len(result.profile) else "disabled")
        self.report.configure(state="normal")
        self.report.delete("1.0", "end")
        self.report.insert("1.0", report_text(result))
        self.report.configure(state="disabled")
        self.checkpoint_tree.delete(*self.checkpoint_tree.get_children())
        verified=bool(len(result.profile))
        for row in result.checkpoint_rows:
            error=row["error_mm"]
            state="未验证" if not verified else ("通过" if row["passed"] else "不通过")
            self.checkpoint_tree.insert("","end",iid=str(row["number"]-1),values=(row["number"],
                f"{row['target'][0]:g}, {row['target'][1]:g}","—" if not verified or error is None else f"{error:.3f}",
                result.project.route.tolerance,state),tags=("fail",) if state=="不通过" else ())
        passed=sum(bool(row["passed"]) for row in result.checkpoint_rows) if verified else 0
        sampling=result.sampling
        self.checkpoint_status.set(f"凸轮名义回算：{passed}/{len(result.checkpoint_rows)} 个必过点在容差内；采样 {sampling.get('requested','—')}→{sampling.get('used','—')} 点。点击行定位。\n这是名义回算，最终DXF接触及实车需另验；有其他失败项时不能据此认定整车通过。")
        fail = sum(c.status == "fail" for c in result.checks)
        self.status_label.configure(foreground="#b3263d" if fail else "#546784")
        self.status.set(f"计算完成 · 名义打卡 {passed}/{len(result.checkpoint_rows)} · 采样 {sampling.get('used','—')} 点 · 驱动轮 {result.stats['drive_distance']:.0f} mm · {result.stats['time']:.1f} s · "
                        + (f"{fail} 项不满足，请查看检查报告。" if fail else ("数值检查通过；采样稳定性尚未确认，请查看报告。" if not result.precision.get("stable") else "数值检查及采样复算通过；实车仍须核对和试跑。")))
        self.schedule_render()

    def locate_checkpoint(self,event: Any=None) -> None:
        selected=self.checkpoint_tree.selection()
        if not selected or self.result is None:return
        i=int(selected[0])
        if i>=len(self.result.checkpoint_rows):return
        self.pause()
        row=self.result.checkpoint_rows[i]
        self.frame.set(min(len(self.result.path)-1,int(round(row["position"]))))
        self.waypoints.tree.selection_set(str(i));self.waypoints.refresh_selection()
        self.inputs.select(self.waypoints)
        self.views.select(0);self.schedule_render()

    def schedule_render(self) -> None:
        if getattr(self, "render_pending", False):
            return
        self.render_pending = True
        self.root.after(50, self.render)

    def render(self) -> None:
        self.render_pending=False
        selected=self.views.index(self.views.select());result=self.result
        index=0 if result is None else min(int(self.frame.get()),len(result.path)-1)
        if selected in (0,1):
            panel=self.figure_panels['scene' if selected==0 else 'cam'];panel.draw()
            if selected==0:self.scene_plot=panel.plot
        elif result is not None and selected==2:plotting.mechanism(self.canvases['mechanism'],result,index)
        elif result is not None and selected==3:
            for kind in ('steer','stroke','pressure'):plotting.curves(self.canvases[kind],result,kind)
        self.workbench.render(index)
        for _,panel in list(self.figure_windows.values()):panel.draw()
        if result is not None:self.progress_label.configure(text=f'里程 {result.drive_s[index]:.0f} mm · 前轮 {np.degrees(result.delta[index]):.1f}°')

    def pause(self) -> None:
        self.playing=False
        self.play_generation+=1

    def play(self) -> None:
        if self.result is None:
            return
        if self.playing:
            self.pause(); return
        if int(self.frame.get()) >= len(self.result.path)-1: self.frame.set(0)
        self.playing=True
        self.play_generation+=1
        self.play_clock=time.monotonic()
        self.play_start=float(time_axis(self.result)[int(self.frame.get())])
        self.tick(self.play_generation)

    def tick(self, generation: int | None = None) -> None:
        generation=self.play_generation if generation is None else generation
        if not self.playing or self.result is None or generation!=self.play_generation:
            return
        seconds=self.play_start+(time.monotonic()-self.play_clock)*float(self.playback_rate.get())
        self.frame.set(index_at_time(self.result,seconds))
        self.schedule_render()
        if seconds>=time_axis(self.result)[-1]:
            self.playing = False
        else:
            self.root.after(40,lambda:self.tick(generation))

    def export(self) -> None:
        if not self.ensure_editors_idle(): return
        if self.busy or self.auxiliary.busy or self.improvement.busy: return
        result = self.result
        if result is None:
            return
        if not result.valid and not messagebox.askyesno("检查未通过", "存在不满足的设计检查。仍导出供分析？请勿直接作为制造结果。", parent=self.root):
            return
        selected = filedialog.askdirectory(parent=self.root, title="选择结果目录（建议新建空文件夹）")
        if selected:
            try:
                folder = Path(selected)
                names = ("cam.dxf", "motion.csv", "cam.csv", "检查报告.md", "project.cam.json")
                overwrite = any((folder/name).exists() for name in names)
                if overwrite and not messagebox.askyesno("同名文件", "此目录已有设计结果。允许覆盖这五个结果文件？", parent=self.root):
                    return
                revision=self.revision
                self.busy=True; self.task_kind="导出结果"; self.cancel.clear()
                self.export_receipt=None
                self.run_button.configure(state="disabled"); self.optimize_button.configure(state="disabled")
                self.export_button.configure(state="disabled")
                self.status.set("正在后台生成五个结果文件，完成后替换；可点「停止计算」取消。")
                def worker() -> None:
                    try:
                        files=export_design(result,folder,overwrite=overwrite,cancelled=self.cancel.is_set)
                        path=files["dxf"]; digest=hashlib.sha256(path.read_bytes()).hexdigest(); stat=path.stat()
                        self.events.put(("exported",(revision,path,digest,(stat.st_mtime_ns,stat.st_size))))
                    except Exception as exc:
                        self.events.put(("error",(revision,str(exc))))
                threading.Thread(target=worker,daemon=True).start()
            except (OSError, ProjectError) as exc:
                messagebox.showerror("导出失败", str(exc), parent=self.root)

    def close(self) -> None:
        if self.may_discard():
            self.cancel.set()
            self.verifier.cancel.set()
            self.auxiliary.cancel.set()
            self.improvement.cancel.set()
            self.workbench.analysis_cancel.set()
            self.root.destroy()


HELP = """使用顺序

1. 首次使用先打开演示项目，点击计算，查看场地、曲线、机构和检查报告。
2. 新建自己的项目，分别填写车辆、机构与凸轮的实际尺寸。
3. 在表格中按比赛顺序输入坐标，或从两列 x,y 的 CSV 导入。
4. 设置场地、容差、障碍物、传感器起点和车头方向。方向留空时朝向第二个点。
5. 点击计算；若有不满足项，先读检查报告。可以尝试本地优化，再复核尺寸和结果。
6. 用滑块或播放按钮查看名义行进过程。同步演示页按理论后轴行驶时间播放，倍率可调；界面刷新会跳过部分采样点，不是实车录像。
7. 保存项目。导出结果后，在 CAD 中核对轮廓、孔、比例和安装关系，再制作与试跑。

重要约定

长度统一用毫米；转角单位为度。后轴中点为车体原点，车头为 +X，左侧为 +Y。场地车头角以 +X 为 0°，逆时针为正。前轮左转为正。

两组坐标怎样填写

「车辆」中的传感器前向距离 X / 左向偏移 Y：量车上的安装位置，以后轴中点为原点。传感器在后轴前方 60 mm、车体中线上，填 X=60、Y=0；偏左为正，偏右为负。须按实车测量，本版 X 必须大于 0。

「场地」中的传感器场地起点 X/Y：传感器起跑时在场地上的坐标，与打卡点共用场地原点。起点为 (410,420) 时，仅在「场地」填写 X=410、Y=420，不要将这组数当成车上的安装距离。

车头方向角：在输入框直接填数字，向右 0°、向上 90°、向左 180°、向下 -90°；留空时朝向打卡表格的第 2 点。方向描述以场地路线图为准。路线所需的前轮转角由计算得到；「车辆」的转角上限只是按实车能力设置的检查门槛。

后面两轮独立转动，一只驱动一只从动。轮→凸轮减速比 = 驱动轮圈数 / 凸轮圈数；它不是电机减速比。

平推模型采用导向滑块、转向臂销和固定长度连杆的平面闭合关系；顶球模型采用绕转向轴摆动的球心圆弧。真实机构若为槽副、带额外摆杆或有空间倾角，不能直接套用这里的理想模型，需按实物运动校准后使用。

基圆输入是设计保底半径；中位抬升量为双向行程预留空间。安装高度由中位半径与机构几何计算，需按机构预览和报告中的转轴坐标安装。微调后应重新计算。

凸轮从哪里出发、朝哪个方向转

「凸轮轮廓」中的红色出发点 S0，是小车起跑、驱动里程为 0 时滚子/球与凸轮接触的轮廓点；橙色箭头表示凸轮实体旋转方向。顺逆时针均按正对软件图中这一面观察，从另一面看视觉方向相反。

装车时打开「机构运动」，将下方进度条拖到最左，按初始机构姿态将 S0 对准滚子/球接触位置，再固定凸轮与传动轴。初始前轮角在图下方和检查报告中显示，不应一律摆成直行。起始相位已经计入图中姿态，无需再次加相位。

导出 DXF 时保留参考图层，即包含 START_REFERENCE（S0 出发点）和 ROTATION_REFERENCE（CW 顺时针 / CCW 逆时针）。这是装配参考标记，加工外形使用 CAM_OUTLINE；如需刻印，单独安排标记工序。选择「仅轮廓与孔」时不导出参考标记。检查报告记录出发点坐标、旋向和初始前轮角。

本版不读取 STEP 尺寸。演示值不是 YDI 标准件的已确认尺寸，必须人工测量并核对连杆、摆臂及球心位置。

检查范围

名义转向回算检查离散凸轮转角采样对打卡路线的影响，尚不是弹簧/接触力或制造误差仿真。边界和障碍物检查只覆盖传感器与三个轮心，须另核对轮宽、外壳包络及比赛规则。

输出

cam.dxf：毫米、模型空间 1:1；CAM_OUTLINE 是加工外轮廓。BASE_CIRCLE、ROLLER_PATH、STEERING_AXIS 是参考层，切割前关闭；CENTER_HOLE 为中心孔。
motion.csv：驱动里程、后轴里程、时间、目标前轮角、位移、凸轮角与回算传感器坐标。
cam.csv：整圈凸轮角、理论/实际坐标、名义转角与压力角，包含闭合终点。
检查报告.md 与 project.cam.json：记录结果、假设和全部输入。

独立程序离线运行。理论检查通过后仍要先试制、低速试跑和校准，不能仅凭一张轮廓图判断实车必然成功。

新版操作

凸轮点数是起始精度，名义回算漏点/偏差过大时自动加密至最多4000点，实际点数与逐次偏差见报告；达到上限仍失败会明确标红。“检查报告 → 逐点打卡偏差”可查看、点击定位各点。设计路线经过点中心不能代替凸轮回算；名义回算通过也不能代替最终DXF和实车检验。

「同步演示」同屏显示路线、凸轮旋转、转向机构。正常/问题案例可复现，均是理论数据；「返回原项目」恢复载入案例前的输入与结果，不改磁盘文件。
「问题定位」点击检查行，查看实际值、限制、依据和建议，定位路线/凸轮/曲线。回程问题只定位凸轮，不伪造比赛位置。
「优化对比」保留原方案，叠加候选路线并比较八项实际指标（指标表可滚动）。必须自行选择原方案或候选；也可保存两套项目，未改善会如实显示。
「辅助」页单击 X/Y/所属区间编辑；Enter 提交、Esc 取消、Tab/Shift+Tab 换格。新增使用独立“新增辅助点”区域。蓝色选中行与黄色编辑格分开；非法输入保留原值/草稿并提示。Ctrl/Shift 多选、全选、批量删除、排序、撤销/重做各在本页生效；切页保留草稿和新增输入，不会隐式提交。两类点分别维护历史；Ctrl+Z/Ctrl+Y 按当前点页或地图当前模式选择历史。整项目保存/计算/搜索/采用方案前须明确处理未提交格。
辅助点仅引导所属区间形状，不要求穿过、不计比赛打卡；同区间取均值，上/下移仅改变显示顺序、不改变区间。区间从起跑位置连接各必过点，首点与起跑同坐标时不重复计段；改变必过点数量/顺序或起点后须核对辅助段号。
「优化对比」可设置半径、段范围、点上限及预算，搜索整条比赛路线的辅助点。最终候选从真实导出 DXF 重新求接触、转角与轨迹；主设计粗细采样稳定性单独显示，未确认不等于通过。可停止搜索，改输入后过期结果会丢弃；原方案须显式选择才能替换。
「场地与试跑」地图上方显示“打卡点/辅助点”模式，切左侧点页也会同步。蓝圆=打卡，紫菱“辅N·段N”=辅助；表格与地图选择/编号高亮同步。勾选地图编辑后，空白点击添加当前类型、拖动当前类型；点击另一类点不会误添加。地图添加辅助点使用独立新增区里的所属区间，不覆盖 X/Y 输入。一次拖动一条历史，修改任一类点后须重新计算，旧结果和导出失效。
已有点位：单击 X/Y 格直接编辑，黄色格与蓝色选中行区分。Enter 提交，Esc 取消当前草稿；Tab/Shift+Tab 校验并保存当前格后切换，非法输入阻止换格。空值、非数字、NaN、无穷值在对应位置提示，原坐标和草稿保留。取消或数值等价不记历史。
新增：只使用独立“新增点位”区的 X、Y 和“添加”。选行/编辑已有格不会填入新增框。相邻同点提醒但保留；首尾同点可以返回起点，不能自动删掉最后一点。
点击编号列选行，地图同步红圈及编号。Ctrl 多选、Shift 连选、全选/Ctrl+A、删除/Delete、上移/下移、撤销/重做集中在表格附近；CSV/地图另成组。每次有效提交、新增及一次拖点都有独立历史，修改后须重新计算。
所有结果页统一使用顶部一排页签，“同步演示 / 问题定位 / 优化对比 / 实测与扰动”各一处。点“场地与试跑”返回设计；小窗口仍保留新增、批量操作及导入入口。
车辆/机构页点击参数输入框可高亮示意位置。基础和高级参数分组；独立参数配置可保存来源并加载，来源声明未经软件核实。
「实测与扰动」先保存格式模板，按场地坐标导入传感器实测 CSV。模板没有任何实测点；测试/理论数据须声明 kind=synthetic。预测始终标为理论。
扰动分析固定原设计的最终 DXF，按用户范围分别扫描驱动轮径与安装相位。仅是单因素几何运动学预测；多因素耦合、动力学和打滑未计算。
"""
