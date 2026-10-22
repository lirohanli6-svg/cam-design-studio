"""Soft-control document and Tk editor; no reference to required-point history."""
from __future__ import annotations

import math
import tkinter as tk
from tkinter import ttk
from typing import Any, Callable

from .project import Project, ProjectError
from .route_shape import required_nodes
from .waypoints import PointTableEditor, WaypointDocument, coordinate


class AuxiliaryDocument(WaypointDocument):
    """Editor rows are X,Y,one-based segment; persistence stays [segment0,X,Y]."""
    def validate_points(self,points: list[list[float]]) -> None:
        if len(points)>40 or any(len(p)!=3 or not all(math.isfinite(v) for v in p)
                                or p[2]!=int(p[2]) or p[2]<1 for p in points):
            raise ProjectError("最多 40 个辅助点；X/Y 必须有限，所属区间必须是正整数。")

    def load_controls(self,points: list[list[float]]) -> None:
        self.load({"points":[[x,y,s+1] for s,x,y in points],"tangents":[]})

    def controls(self) -> list[list[float]]:
        return [[s-1,x,y] for x,y,s in self.points]


class AuxiliaryEditor(PointTableEditor):
    columns=(("x","X / mm",115),("y","Y / mm",115),("segment","所属区间",75))
    captions=("X","Y","所属区间")
    noun="辅助点"
    document: AuxiliaryDocument

    def __init__(self,parent: Any,changed: Callable[[],None],selected: Callable[[],None],
                 map_edit: tk.BooleanVar,project: Callable[[],Project]) -> None:
        self.project_values=project
        super().__init__(parent,changed,selected,map_edit)

    def make_document(self) -> AuxiliaryDocument:
        return AuxiliaryDocument(self._changed)

    def notice(self,points: list[list[float]]) -> str:
        return "仅引导形状；同段取均值，排序不改所属区间。"

    def build_heading(self) -> None:
        ttk.Label(self,text="辅助仅引导 · 同段取均值 · 排序不改所属区间",
                  font=("Microsoft YaHei UI",8)).pack(fill="x")

    def display_value(self,col: int,value: float) -> str:
        return str(int(value)) if col==2 else str(value)

    def build_addition(self,footer: ttk.Frame) -> None:
        self.new_segment=tk.StringVar(value="1")
        self.segment_error=tk.StringVar()
        form=ttk.LabelFrame(footer,text="新增辅助点（独立输入）",padding=3)
        form.pack(fill="x",pady=(2,0))
        # One input row and one shared validation row keep the point table usable
        # at 1000x640 even when all three input errors are visible.
        fields=(("X",self.new_x,self.new_errors[0]),("Y",self.new_y,self.new_errors[1]),
                ("所属区间",self.new_segment,self.segment_error))
        for i,(axis,var,error) in enumerate(fields):
            ttk.Label(form,text=axis).grid(row=0,column=i*2,sticky="w")
            entry=ttk.Entry(form,textvariable=var,width=7 if i<2 else 4)
            entry.grid(row=0,column=i*2+1,sticky="ew",padx=3)
            entry.bind("<Return>",self._add_key)
            if i<2:self.new_entries.append(entry)
            else:self.segment_entry=entry
            label=tk.Label(form,textvariable=error,fg="#b3263d",bg="#edf2f8",
                           font=("Microsoft YaHei UI",8),wraplength=135,height=1,anchor="nw")
            label.grid(row=1,column=i*2,columnspan=2,sticky="ew")
            def show_error(*args: Any,var: tk.StringVar=error,label: Any=label) -> None:
                if var.get():label.grid()
                else:label.grid_remove()
            error.trace_add("write",show_error);show_error()
            form.columnconfigure(i*2+1,weight=1)
        self.add_button=ttk.Button(form,text="添加",command=self.add,width=5,padding=(5,3))
        self.add_button.grid(row=0,column=6,sticky="e")

    def build_other(self,footer: ttk.Frame,map_edit: tk.BooleanVar) -> None:
        frame=ttk.Frame(footer);frame.pack(fill="x",pady=2)
        ttk.Checkbutton(frame,text="地图点击添加 / 选择 / 拖动辅助点",variable=map_edit).pack(anchor="w")

    def load_controls(self,points: list[list[float]]) -> None:
        self.load([[x,y,s+1] for s,x,y in points],[])
        self.new_segment.set("1");self.segment_error.set("")
        self.segment_entry.configure(style="TEntry")

    def segment_value(self,text: str) -> float:
        value=coordinate(text)
        project=self.project_values()
        if not project.route.waypoints:raise ProjectError("请先添加比赛必过点，建立路线区间")
        count=len(required_nodes(project))-1
        if value!=int(value) or not 1<=value<=count:
            raise ProjectError(f"所属区间须为 1～{max(0,count)} 的整数")
        return value

    def cell_value(self,row: int,col: int,text: str) -> float:
        if col==2:return self.segment_value(text)
        value=coordinate(text)
        bounds=self.project_values().route.field
        lo,hi=bounds[:2] if col==0 else bounds[2:]
        if not math.isfinite(lo+hi) or lo>=hi:raise ProjectError("请先填写有效场地边界")
        if not lo<=value<=hi:raise ProjectError(f"须在场地 {lo:g}～{hi:g} mm 内")
        return value

    def add(self) -> None:
        if not self.ensure_idle():return
        values=[];valid=True
        for i,var in enumerate((self.new_x,self.new_y,self.new_segment)):
            error=self.new_errors[i] if i<2 else self.segment_error
            entry=self.new_entries[i] if i<2 else self.segment_entry
            try:
                values.append(self.cell_value(-1,i,var.get()))
                error.set("");entry.configure(style="TEntry")
            except ProjectError as exc:
                valid=False;error.set(str(exc));entry.configure(style="Invalid.TEntry")
        if not valid:return
        try:changed=self.apply([*self.document.points,values],(len(self.document.points),))
        except ProjectError as exc:self.segment_error.set(str(exc));return
        if changed:
            self.new_x.set("");self.new_y.set("")
            self.feedback.set("辅助点已添加；必过点未改变，请重新计算。")

    def map_add(self,x: float,y: float) -> bool:
        try:
            segment=self.segment_value(self.new_segment.get())
            self.cell_value(-1,0,str(x));self.cell_value(-1,1,str(y))
            changed=self.apply([*self.document.points,[x,y,segment]],(len(self.document.points),),preview=True)
            self.segment_error.set("")
            return changed
        except ProjectError as exc:
            self.segment_error.set(str(exc));self.feedback.set(f"地图未添加：{exc}；坐标未改变。")
            return False
