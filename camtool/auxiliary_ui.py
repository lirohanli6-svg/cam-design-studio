"""Soft-guide editing and cancellable search, isolated from required visits."""
from __future__ import annotations
import queue
import threading
import tkinter as tk
from tkinter import ttk, filedialog
from pathlib import Path
from typing import TYPE_CHECKING, Any
from .auxiliary_search import SearchSettings, SearchOutcome, search_auxiliaries
from .project import ProjectError
from .auxiliary_editor import AuxiliaryEditor
from .route_shape import required_nodes
if TYPE_CHECKING:
    from .ui import App


class AuxiliaryControls:
    def __init__(self, app: App, compare: ttk.Frame) -> None:
        self.app=app; self.generation=0; self.busy=False
        self.cancel=threading.Event(); self.events: queue.Queue[tuple]=queue.Queue()
        self.outcome: SearchOutcome | None=None
        self.editor=AuxiliaryEditor(app.inputs,self.points_changed,self.selected,app.map_edit,app.collect)
        self.page=self.editor
        app.inputs.add(self.page,text="辅助")
        self.tree=self.editor.tree
        self.status=self.editor.feedback
        self.values={"x":self.editor.new_x,"y":self.editor.new_y,"segment":self.editor.new_segment}
        self.settings={}
        frame=ttk.LabelFrame(compare,text="自动辅助点搜索（候选搜索预算；最终精度复算与 DXF 独立检验另计）",padding=3)
        frame.pack(fill="x",before=compare.pack_slaves()[1])
        configs=[("radius","半径 mm","120"),("maximum_points","点上限","6"),("evaluations","候选预算","36"),
                 ("seconds","秒预算","60"),("first_segment","起始段","1"),("last_segment","末段 0=全部","0")]
        for i,(key,title,value) in enumerate(configs):
            ttk.Label(frame,text=title).grid(row=i//3,column=(i%3)*2,sticky="w")
            var=tk.StringVar(value=value); self.settings[key]=var
            ttk.Entry(frame,textvariable=var,width=7).grid(row=i//3,column=(i%3)*2+1,padx=(2,8))
            var.trace_add("write",self.settings_changed)
        ttk.Button(frame,text="搜索辅助点",command=self.start,width=13,style="Compact.TButton").grid(row=0,column=6,padx=3)
        ttk.Button(frame,text="停止搜索",command=self.cancel.set,width=11,style="Compact.TButton").grid(row=1,column=6,padx=3)
        ttk.Button(frame,text="保存搜索及检验",command=self.export,width=17,style="Compact.TButton").grid(row=0,column=7,rowspan=2,padx=3)
        self.summary=tk.StringVar(value="辅助候选坐标及最终 DXF 结论将在这里显示。")
        summary_frame=ttk.Frame(compare)
        summary_frame.pack(fill="x",before=compare.pack_slaves()[-1])
        summary_text=tk.Text(summary_frame,height=2,wrap="word",font=("Microsoft YaHei UI",9),relief="flat",background="#edf2f8")
        scroll=ttk.Scrollbar(summary_frame,command=summary_text.yview)
        summary_text.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right",fill="y"); summary_text.pack(fill="x",expand=True)
        def update_summary(*args: Any) -> None:
            summary_text.configure(state="normal");summary_text.delete("1.0","end")
            summary_text.insert("1.0",self.summary.get());summary_text.configure(state="disabled")
        self.summary.trace_add("write",update_summary);update_summary()
        app.root.after(100,self.poll)

    def load(self) -> None:
        self.editor.load_controls(self.app.project.route.auxiliary_points)

    def points_changed(self) -> None:
        self.app.project.route.auxiliary_points=self.editor.document.controls()
        self.app.changed()

    def selected(self) -> None:
        if not self.app.loading and self.app.inputs.select()==str(self.editor) and (self.editor.selected() or self.editor.draft is not None):
            self.app.pause(); self.app.views.select(0)
        self.app.schedule_render()

    def invalidate(self) -> None:
        self.generation+=1; self.cancel.set()
        if not self.app.workbench.adopting:
            self.outcome=None; self.summary.set("输入已更改；辅助搜索结果与检验失效，须重新计算。")

    def settings_changed(self,*args: Any) -> None:
        self.generation+=1; self.cancel.set(); self.outcome=None
        self.summary.set("搜索设置已更改，旧辅助搜索结果失效。")

    def edit(self,replace: bool=False) -> None:
        # Compatibility for existing automation: replacement is now a cell commit.
        if replace:self.editor.commit()
        else:self.editor.add()

    def travel(self,redo: bool) -> None:
        self.editor.undo(redo)

    def start(self) -> None:
        if self.busy or self.app.busy or not self.app.ensure_editors_idle(): return
        try:
            p=self.app.collect(); values: dict[str,Any]={k:float(v.get()) for k,v in self.settings.items()}
            settings=SearchSettings(**values); settings.validate(len(required_nodes(p))-1)
            # Keep integer settings integer after validating, never truncate input.
            for key in ("maximum_points","evaluations","first_segment","last_segment"): setattr(settings,key,int(getattr(settings,key)))
        except (ValueError,ProjectError) as exc: self.summary.set(f"未开始搜索：{exc}"); return
        self.generation+=1; generation=self.generation; revision=self.app.revision
        self.busy=True; self.cancel.clear(); self.outcome=None
        self.app.views.select(self.app.workbench.frames["compare"])
        self.summary.set("后台搜索已开始；原方案保持不变，可以停止或继续编辑。")
        def work() -> None:
            try:
                result=search_auxiliaries(p,settings,self.cancel.is_set,lambda text:self.events.put((generation,revision,"progress",text)))
                self.events.put((generation,revision,"done",result))
            except Exception as exc: self.events.put((generation,revision,"error",str(exc)))
            finally: self.events.put((generation,revision,"finished",None))
        threading.Thread(target=work,daemon=True).start()

    def poll(self) -> None:
        try:
            while True:
                generation,revision,kind,value=self.events.get_nowait()
                if kind=="finished": self.busy=False; continue
                if generation!=self.generation or revision!=self.app.revision: continue
                if kind=="done":
                    if self.cancel.is_set(): self.summary.set("搜索已取消，候选未采用。"); continue
                    self.outcome=value
                    self.app.workbench.set_comparison(value.original,value.candidate,original_project=value.original_project)
                    coords="；".join(f"段{int(s)+1} ({x:.1f},{y:.1f})" for s,x,y in value.candidate.project.route.auxiliary_points) or "无辅助点"
                    self.summary.set(value.status+" 辅助坐标："+coords)
                else: self.summary.set(value)
        except queue.Empty: pass
        self.app.root.after(100,self.poll)

    def export(self) -> None:
        if self.outcome is None: self.summary.set("尚无当前输入对应的辅助搜索结果。"); return
        folder=filedialog.askdirectory(parent=self.app.root,title="选择空目录保存原方案、候选及最终DXF检验")
        if folder:
            try: self.outcome.export(Path(folder)); self.summary.set(f"完整搜索、两套项目和最终 DXF 检验已保存到 {folder}")
            except (OSError,ProjectError) as exc: self.summary.set(str(exc))
