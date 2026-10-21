"""Waypoint editing: committed coordinates, drafts, history and the Tk adapter."""
from __future__ import annotations

import copy
import math
import tkinter as tk
from tkinter import filedialog, ttk
from typing import Any, Callable

from .project import ProjectError, read_waypoints
from .workbench import RouteHistory


def coordinate(text: str) -> float:
    if not text.strip(): raise ProjectError("坐标不能为空")
    try: value=float(text)
    except ValueError as exc: raise ProjectError("请输入有效数字") from exc
    if not math.isfinite(value): raise ProjectError("不接受 NaN 或无穷值")
    return value


def proximity_notice(points: list[list[float]]) -> str:
    adjacent=[str(i+1)+"/"+str(i+2) for i in range(len(points)-1)
              if math.dist(points[i],points[i+1])<.01]
    if adjacent: return "相邻点 "+"、".join(adjacent[:3])+" 重合/过近（< 0.01 mm），已保留；计算前核对。"
    if len(points)>2 and math.dist(points[0],points[-1])<.01:
        return "首尾同点：按返回起点保留，未删除。"
    return ""


class WaypointDocument:
    """One mutation seam for cell/form commits and map gesture previews."""
    def __init__(self, changed: Callable[[],None]) -> None:
        self.changed=changed
        self.points: list[list[float]]=[]
        self.tangents: list[float]=[]
        self.history=RouteHistory()

    def snapshot(self) -> dict[str,Any]:
        return copy.deepcopy({"points":self.points,"tangents":self.tangents})

    def load(self,snapshot: dict[str,Any]) -> None:
        self.points=copy.deepcopy(snapshot["points"]); self.tangents=copy.deepcopy(snapshot["tangents"])
        self.history=RouteHistory()

    def validate_points(self,points: list[list[float]]) -> None:
        if len(points)>200 or any(len(p)!=2 or not all(math.isfinite(v) for v in p) for p in points):
            raise ProjectError("最多 200 个点，每点须为两个有限坐标。")

    def apply(self,points: list[list[float]],remember: bool=True) -> bool:
        self.validate_points(points)
        if points==self.points: return False
        previous=self.snapshot()
        self.points=copy.deepcopy(points); self.tangents=[]
        if remember: self.history.commit(previous,self.snapshot())
        self.changed()
        return True

    def finish_gesture(self,previous: dict[str,Any]) -> None:
        self.history.commit(previous,self.snapshot())

    def travel(self,redo: bool=False) -> bool:
        value=(self.history.redo if redo else self.history.undo)(self.snapshot())
        if value is None: return False
        self.points=value["points"]; self.tangents=value["tangents"]
        self.changed()
        return True


class PointTableEditor(ttk.Frame):
    """Shared Tk gestures; every instance owns its document, draft and selection.

    Subclasses supply row validation and the independent addition form. There is
    no global point history or selection, and focus loss never commits a draft.
    """
    columns: tuple[tuple[str,str,int],...] = (("x","X / mm",150),("y","Y / mm",150))
    captions: tuple[str,...] = ("X","Y")
    noun = "点"

    def make_document(self) -> WaypointDocument:
        return WaypointDocument(self._changed)

    def notice(self,points: list[list[float]]) -> str:
        return proximity_notice(points)

    def cell_value(self,row: int,col: int,text: str) -> float:
        return coordinate(text)

    def display_value(self,col: int,value: float) -> str:
        return str(value)

    def __init__(self,parent: Any,changed: Callable[[],None],selected: Callable[[],None],map_edit: tk.BooleanVar) -> None:
        super().__init__(parent,padding=6,width=420)
        self.changed_callback=changed; self.selected_callback=selected
        self.document=self.make_document()
        self.selection_after: tuple[int,...]=()
        self.entry: tk.Entry | None=None
        self.draft: tuple[int,int] | None=None
        self.draft_text=tk.StringVar()
        self.feedback=tk.StringVar(value="单击 X/Y 编辑；Enter 提交，Esc 取消，Tab 提交并换格。")
        self.selection_text=tk.StringVar(value="选 0/0")
        self.new_x=tk.StringVar(); self.new_y=tk.StringVar()
        self.new_errors=[tk.StringVar(),tk.StringVar()]
        self.new_entries: list[ttk.Entry]=[]
        style=ttk.Style(self)
        style.configure("Invalid.TEntry",fieldbackground="#fff0ed",foreground="#a52b30")
        self.build_heading()
        footer=ttk.Frame(self); footer.pack(side="bottom",fill="x")
        table=ttk.Frame(self); table.pack(fill="both",expand=True)
        self.tree=ttk.Treeview(table,columns=tuple(c[0] for c in self.columns),show="tree headings",height=7,selectmode="extended")
        for key,title,width in [("#0","编号",44),*self.columns]:
            self.tree.heading(key,text=title); self.tree.column(key,width=width,stretch=key!="#0")
        self.bar=ttk.Scrollbar(table,command=self.tree.yview)
        self.tree.configure(yscrollcommand=self._scroll)
        self.bar.pack(side="right",fill="y"); self.tree.pack(fill="both",expand=True)
        self.tree.bind("<Button-1>",self._click)
        self.tree.bind("<<TreeviewSelect>>",self.refresh_selection)
        self.tree.bind("<Return>",self._edit_focused)
        self.tree.bind("<Control-a>",self.select_all); self.tree.bind("<Control-A>",self.select_all)
        self.tree.bind("<Delete>",self._delete_key)
        self.tree.bind("<Configure>",lambda event:self._place())
        self.feedback_label=tk.Label(footer,textvariable=self.feedback,wraplength=395,font=("Microsoft YaHei UI",8),height=1,anchor="nw",justify="left",bg="#edf2f8",fg="#546784")
        self.feedback_label.pack(fill="x",pady=2)
        controls=ttk.Frame(footer); controls.pack(fill="x",pady=2)
        ttk.Label(controls,textvariable=self.selection_text,font=("Microsoft YaHei UI",8)).pack(side="left",padx=(0,3))
        self.buttons: dict[str,ttk.Button]={}
        commands: list[tuple[str,Callable[[],Any]]]=[("全选",self.select_all),("取消",self.clear_selection),("删除",self.delete),
            ("上移",lambda:self.move(-1)),("下移",lambda:self.move(1)),("撤销",self.undo),("重做",lambda:self.undo(True))]
        for title,command in commands:
            button=ttk.Button(controls,text=title,command=command,width=4,padding=(4,3))
            button.pack(side="left",padx=1); self.buttons[title]=button
        self.build_addition(footer)
        self.build_other(footer,map_edit)

    def build_heading(self) -> None:
        ttk.Label(self,text="单击 X/Y 编辑 · 编号选行 · Ctrl/Shift 多选",font=("Microsoft YaHei UI",8)).pack(fill="x")

    def build_addition(self,footer: ttk.Frame) -> None:
        add=ttk.Frame(footer,padding=(4,2),relief="solid",borderwidth=1); add.pack(fill="x",pady=(2,0))
        ttk.Label(add,text="新增点位",font=("Microsoft YaHei UI",8)).grid(row=0,column=0,rowspan=2,padx=(0,3))
        add.columnconfigure(2,weight=1); add.columnconfigure(4,weight=1)
        for i,(axis,var) in enumerate(zip(("X","Y"),(self.new_x,self.new_y))):
            ttk.Label(add,text=axis).grid(row=0,column=i*2+1,sticky="w")
            entry=ttk.Entry(add,textvariable=var,width=8); entry.grid(row=0,column=i*2+2,sticky="ew",padx=3)
            entry.bind("<Return>",self._add_key)
            self.new_entries.append(entry)
            tk.Label(add,textvariable=self.new_errors[i],fg="#b3263d",bg="#edf2f8",font=("Microsoft YaHei UI",8),wraplength=140,height=2,anchor="nw",justify="left").grid(row=1,column=i*2+1,columnspan=2,sticky="nw")
        self.add_button=ttk.Button(add,text="添加",command=self.add,width=5,padding=(5,3))
        self.add_button.grid(row=0,column=5,padx=3)
    def build_other(self,footer: ttk.Frame,map_edit: tk.BooleanVar) -> None:
        other=ttk.Frame(footer,padding=(4,2),relief="solid",borderwidth=1); other.pack(fill="x",pady=(2,0))
        ttk.Label(other,text="导入/地图",font=("Microsoft YaHei UI",8)).pack(side="left",padx=(0,6))
        self.csv_button=ttk.Button(other,text="导入 CSV",command=self.import_csv,width=9,padding=(5,3))
        self.csv_button.pack(side="left")
        ttk.Checkbutton(other,text="地图点击 / 拖点",variable=map_edit).pack(side="left",padx=8)

    def snapshot(self) -> dict[str,Any]:
        return self.document.snapshot()

    def load(self,points: list[list[float]],tangents: list[float]) -> None:
        self.cancel_edit(); self.document.load({"points":points,"tangents":tangents})
        self.new_x.set(""); self.new_y.set("")
        for var in self.new_errors: var.set("")
        for entry in self.new_entries: entry.configure(style="TEntry")
        self.selection_after=(); self._render()
        self.feedback.set(self.notice(points) or "单击 X/Y 编辑；Enter 提交，Esc 取消，Tab 提交并换格。")

    def apply(self,points: list[list[float]],selected: tuple[int,...]=(),preview: bool=False) -> bool:
        if not self.ensure_idle(): return False
        self.selection_after=selected
        return self.document.apply(points,remember=not preview)

    def finish_gesture(self,previous: dict[str,Any]) -> None:
        self.document.finish_gesture(previous)

    def _changed(self) -> None:
        self._render(); self.changed_callback()

    def _render(self) -> None:
        self.tree.delete(*self.tree.get_children())
        for i,p in enumerate(self.document.points): self.tree.insert("","end",iid=str(i),text=str(i+1),values=tuple(self.display_value(col,v) for col,v in enumerate(p)))
        selected=[str(i) for i in self.selection_after if i<len(self.document.points)]
        self.tree.selection_set(selected)
        if selected: self.tree.see(selected[-1]); self.tree.focus(selected[-1])
        self.refresh_selection()

    def selected(self) -> tuple[int,...]:
        return tuple(int(row) for row in self.tree.selection())

    def refresh_selection(self,_event: Any=None) -> None:
        count=len(self.tree.selection()); self.selection_text.set(f"选 {count}/{len(self.document.points)}")
        for name in ("上移","下移"): self.buttons[name].configure(state="normal" if count else "disabled")
        self.buttons["删除"].configure(state="normal" if count else "disabled")
        self.selected_callback()

    def _pending(self) -> bool:
        if self.draft is None: return False
        row,col=self.draft
        try: return coordinate(self.draft_text.get())!=self.document.points[row][col]
        except ProjectError: return True

    def ensure_idle(self) -> bool:
        if self.draft is None: return True
        if self._pending():
            self.feedback.set(f"第 {self.draft[0]+1} {self.noun} {self.captions[self.draft[1]]} 草稿未提交；Enter 提交 / Esc 取消后再操作。")
            self.selected_callback()
            self.tree.see(str(self.draft[0])); self._place()
            if self.entry is not None: self.entry.focus_set()
            return False
        self.cancel_edit(); return True

    def _click(self,event: Any) -> str | None:
        row=self.tree.identify_row(event.y); col=self.tree.identify_column(event.x)
        if self.draft is not None and row==str(self.draft[0]) and col==f"#{self.draft[1]+1}": return "break"
        if not self.ensure_idle(): return "break"
        if event.state & 5: return None  # Leave Ctrl/Shift selection to the native tree adapter.
        if row and col in tuple(f"#{i+1}" for i in range(len(self.columns))):
            self.tree.selection_set(row); self.tree.focus(row)
            self.begin_edit(int(row),int(col[1:])-1); return "break"
        return None

    def _edit_focused(self,_event: Any=None) -> str:
        selected=self.tree.selection()
        if len(selected)==1: self.begin_edit(int(selected[0]),0)
        return "break"

    def begin_edit(self,row: int,col: int) -> None:
        if not self.ensure_idle(): return
        self.draft=(row,col); self.draft_text.set(str(self.document.points[row][col]))
        self.tree.see(str(row)); self.tree.selection_set(str(row)); self.tree.focus(str(row))
        self.entry=tk.Entry(self.tree,textvariable=self.draft_text,bg="#fff5ce",fg="#172740",insertbackground="#172740",
                            relief="solid",bd=2,highlightthickness=2,highlightbackground="#d08a13",highlightcolor="#d08a13")
        self.entry.bind("<Return>",self._commit_key)
        self.entry.bind("<KP_Enter>",self._commit_key)
        self.entry.bind("<Escape>",self._cancel_key)
        self.entry.bind("<Tab>",lambda event:self._tab(1))
        self.entry.bind("<Shift-Tab>",lambda event:self._tab(-1))
        self.feedback.set(f"编辑第 {row+1} {self.noun} {self.captions[col]}（黄色格）· Enter 提交 / Esc 取消 / Tab 保存换格")
        self._place(); self.entry.focus_set(); self.entry.selection_range(0,"end")
        self.refresh_selection()

    def _place(self) -> None:
        if self.entry is None or self.draft is None: return
        box=self.tree.bbox(str(self.draft[0]),f"#{self.draft[1]+1}")
        if box: self.entry.place(x=box[0],y=box[1],width=box[2],height=box[3])
        else: self.entry.place_forget()

    def _scroll(self,first: float,last: float) -> None:
        self.bar.set(first,last); self._place()

    def _delete_key(self,_event: Any=None) -> str:
        self.delete(); return "break"

    def _add_key(self,_event: Any=None) -> str:
        self.add(); return "break"

    def _cancel_key(self,_event: Any=None) -> str:
        self.cancel_edit(); return "break"

    def _commit_key(self,_event: Any=None) -> str:
        self.commit(); return "break"

    def cancel_edit(self) -> None:
        active=self.draft is not None
        if self.entry is not None: self.entry.destroy()
        self.entry=None; self.draft=None
        if active: self.feedback.set("已取消单元格草稿，原坐标未改变。")

    def commit(self) -> bool:
        if self.draft is None: return True
        row,col=self.draft
        try: value=self.cell_value(row,col,self.draft_text.get())
        except ProjectError as exc:
            self.feedback.set(f"第 {row+1} {self.noun} {self.captions[col]}：{exc}；原坐标未改变。")
            if self.entry is not None: self.entry.configure(highlightbackground="#c42b3e",highlightcolor="#c42b3e"); self.entry.focus_set()
            return False
        points=copy.deepcopy(self.document.points)
        if points[row][col]==value:
            self.cancel_edit(); self.feedback.set("数值未变化，没有生成修改记录。")
            return True
        points[row][col]=value; self.cancel_edit()
        self.apply(points,(row,))
        self.feedback.set(self.notice(points) or f"第 {row+1} {self.noun} {self.captions[col]} 已提交；需要重新计算。")
        return True

    def _tab(self,step: int) -> str:
        position=self.draft
        if position is not None and self.commit():
            width=len(self.columns)
            next_cell=(position[0]*width+position[1]+step)%(len(self.document.points)*width)
            self.begin_edit(next_cell//width,next_cell%width)
        return "break"

    def add(self) -> None:
        if not self.ensure_idle(): return
        values=[]; valid=True
        for i,var in enumerate((self.new_x,self.new_y)):
            try: values.append(coordinate(var.get())); self.new_errors[i].set(""); self.new_entries[i].configure(style="TEntry")
            except ProjectError as exc:
                valid=False; self.new_errors[i].set(str(exc)); self.new_entries[i].configure(style="Invalid.TEntry")
        if not valid: return
        points=copy.deepcopy(self.document.points)+[values]
        try: changed=self.apply(points,(len(points)-1,))
        except ProjectError as exc: self.new_errors[0].set(str(exc)); return
        if changed:
            self.new_x.set(""); self.new_y.set("")
            self.feedback.set(self.notice(points) or f"已新增第 {len(points)} {self.noun}；需要重新计算。")

    def select_all(self,_event: Any=None) -> str:
        if self.ensure_idle(): self.tree.selection_set(self.tree.get_children()); self.refresh_selection()
        return "break"

    def clear_selection(self) -> None:
        if self.ensure_idle(): self.tree.selection_remove(*self.tree.selection()); self.refresh_selection()

    def delete(self) -> None:
        if not self.ensure_idle(): return
        selected=set(self.selected())
        if not selected: return
        points=[p for i,p in enumerate(self.document.points) if i not in selected]
        self.apply(points)
        self.feedback.set(f"已删除 {len(selected)} 个点，可整批撤销；需要重新计算。")

    def move(self,step: int) -> None:
        if not self.ensure_idle(): return
        chosen=set(self.selected())
        points=copy.deepcopy(self.document.points)
        for index in sorted(chosen,reverse=step>0):
            target=index+step
            if 0<=target<len(points) and target not in chosen:
                points[index],points[target]=points[target],points[index]
                chosen.remove(index); chosen.add(target)
        if self.apply(points,tuple(sorted(chosen))):
            self.feedback.set(self.notice(points) or "顺序已调整，需要重新计算。")

    def undo(self,redo: bool=False) -> None:
        if not self.ensure_idle(): return
        self.selection_after=()
        if self.document.travel(redo): self.feedback.set(("已重做" if redo else "已撤销")+"；需要重新计算。")

    def import_csv(self) -> None:
        if not self.ensure_idle(): return
        path=filedialog.askopenfilename(parent=self.winfo_toplevel(),filetypes=[("打卡点 CSV","*.csv")])
        if path:
            try:
                points=read_waypoints(path); changed=self.apply(points)
                self.feedback.set(proximity_notice(points) or ("CSV 已导入，需要重新计算。" if changed else "CSV 坐标未变化。"))
            except (OSError,ProjectError) as exc: self.feedback.set(f"CSV 导入失败：{exc}")


class WaypointEditor(PointTableEditor):
    """Required visits, with their own coordinates, tangents and undo history."""
