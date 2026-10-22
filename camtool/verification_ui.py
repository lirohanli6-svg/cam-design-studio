"""Independent verifier UI; its editable snapshot never mutates the design."""
from __future__ import annotations

import copy
import json
import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any, Callable

import numpy as np

from .plotting import Plot
from .project import Project, ProjectError
from .verification import (FAIL, PASS, VerificationInput, VerificationResult,
                           export_verification, load_nominal_csv, verification_report, verify_dxf)


class VerificationPage:
    def __init__(self, parent: ttk.Frame, context: Callable[[], tuple[Project, Any, Any, float]]) -> None:
        self.parent, self.context = parent, context
        self.file = tk.StringVar()
        self.message = tk.StringVar(value="选择最终 cam.dxf；核对独立参数、来源及计划运行区间后开始检验。")
        self.vars: dict[str, tk.StringVar] = {}
        self.sources: dict[str, tk.StringVar] = {}
        self.result: VerificationResult | None = None
        self.reference: Any = None
        self.nominal: Any = None
        self.reference_source = "未提供设计曲线"
        self.nominal_source = "未提供名义转角"
        self.cancel = threading.Event()
        self.events: queue.Queue[tuple[str, Any]] = queue.Queue()
        self.busy = False
        self.loading = True
        self.revision = 0
        top = ttk.Frame(parent, padding=5)
        top.pack(fill="x")
        ttk.Button(top, text="选择最终 DXF", command=self.choose_dxf, width=13).pack(side="left")
        ttk.Entry(top, textvariable=self.file).pack(side="left", fill="x", expand=True, padx=5)
        self.run_button = ttk.Button(top, text="开始二次检验", command=self.run, width=13, style="Accent.TButton")
        self.run_button.pack(side="left", padx=2)
        ttk.Button(top, text="停止", command=self.cancel.set, width=5).pack(side="left")
        self.export_button = ttk.Button(top, text="导出检验报告", command=self.export, width=13, state="disabled")
        self.export_button.pack(side="left", padx=2)
        actions = ttk.Frame(parent, padding=(5, 0, 5, 4))
        actions.pack(fill="x")
        for text, callback, width in [("复制设计参数", self.copy_design, 15), ("读取项目", self.load_project, 11),
                                      ("读取配置", self.load_config, 11), ("保存配置", self.save_config, 11),
                                      ("名义 CSV（仅比较）", self.choose_nominal, 22)]:
            ttk.Button(actions, text=text, command=callback, width=width).pack(side="left", padx=2)
        notice = ttk.Label(parent, text="独立反算只读取最终轮廓和本页参数；复制的设计值不等于实测。整圈检查机构，仅计划段回算比赛路线；动力学未验证。",
                           foreground="#8c5a17", wraplength=1100, padding=(8, 3))
        notice.pack(fill="x")
        self.status_label = ttk.Label(parent, textvariable=self.message, padding=6, wraplength=1100)
        self.status_label.pack(side="bottom", fill="x")
        def resize(event: Any) -> None:
            if event.widget == parent:
                notice.configure(wraplength=max(300,event.width-24))
                self.status_label.configure(wraplength=max(300,event.width-24))
        parent.bind("<Configure>",resize,add="+")
        pane = ttk.Panedwindow(parent, orient="horizontal")
        pane.pack(fill="both", expand=True)
        forms = ttk.Notebook(pane, width=340)
        pane.add(forms, weight=0)
        self.outputs = ttk.Notebook(pane)
        pane.add(self.outputs, weight=1)
        vehicle = self.scroll_form(forms, "车辆与机构")
        self.note(vehicle, "以下值只用于二次检验，可独立填写实测值；不会改动左侧设计项目。长度 mm，角度 °。")
        for key, label in [("vehicle.wheelbase", "前后轴距 mm"), ("vehicle.track", "两后轮轮距 mm"),
                           ("vehicle.drive_diameter", "驱动轮直径 mm"), ("vehicle.sensor_x", "传感器前向距离 X mm"),
                           ("vehicle.sensor_y", "传感器左向偏移 Y mm"), ("vehicle.max_steer", "前轮转角限位 ±°")]:
            self.field(vehicle, key, label)
        self.field(vehicle, "vehicle.drive_side", "驱动轮侧", ["left", "right"])
        self.note(vehicle, "left=车体左侧，right=右侧；传感器 X/Y 从后轴中点测量，与场地起点不同。")
        self.field(vehicle, "mechanism.kind", "机构：flat 平推 / ball 顶球", ["flat", "ball"])
        self.field(vehicle, "mechanism.side", "平推安装侧 left/right", ["left", "right"])
        for key, label in [("arm", "转向臂 / 球臂长 mm"), ("arm_angle", "中位摆臂角 °"),
                           ("link", "平推连杆长 mm"), ("roller", "滚子 / 球半径 mm"),
                           ("steer_zero", "中位前轮角 °"), ("min_stroke", "允许最小行程 mm"),
                           ("max_stroke", "允许最大行程 mm")]:
            self.field(vehicle, "mechanism."+key, label)
        self.field(vehicle, "mechanism.branch", "平推闭合分支 +1 / -1", ["1", "-1"])
        mount = self.scroll_form(forms, "装配与精度")
        self.note(mount, "固定机构坐标：凸轮轴心为 (0,0)，X 向右、Y 向上。转轴和导向位置建议独立实测，不依赖设计基圆推算。")
        for key, label in [("pivot_x", "实测转向轴 X mm"), ("pivot_y", "实测转向轴 Y mm"),
                           ("guide_x", "平推导向线 X mm"), ("install_angle", "DXF 安装转角 °"),
                           ("cam.ratio", "驱动轮圈数 / 凸轮圈数")]:
            self.field(mount, key, label)
        self.note(mount, "安装转角：驱动角为 0° 时，将 DXF 图整体旋转多少度装到固定机构上；逆时针为正，设计初值为负的廓线相位。")
        self.field(mount, "direction", "实体旋向：+1 逆 / -1 顺", ["1", "-1"])
        for key, label in [("run_start", "计划段起始驱动角 °"), ("run_end", "计划段结束驱动角 °"),
                           ("angle_step", "粗网格角步长 °"), ("contact_tolerance", "接触残差容差 mm"),
                           ("solve_min", "转角搜索下限 °"), ("solve_max", "转角搜索上限 °"),
                           ("contact_jump", "接触突跳基础阈值 mm"), ("stability_mm", "粗细轨迹稳定阈值 mm"),
                           ("stability_deg", "粗细转角稳定阈值 °"), ("nominal_tolerance_deg", "名义比较阈值 °")]:
            self.field(mount, key, label)
        self.note(mount, "自动以角步长 1/2、接触容差 1/5 再求解一遍。整圈检查 0～360°，只有计划段用于比赛轨迹。搜索区间外不保证无其他解。")
        route = self.scroll_form(forms, "路线与来源")
        for key, label in [("route.start_x", "场地传感器起点 X mm"), ("route.start_y", "场地传感器起点 Y mm"),
                           ("start_heading", "计划段起跑车头角 °"), ("route.tolerance", "打卡 / 路线容差 mm")]:
            self.field(route, key, label)
        for i, label in enumerate(["场地最小 X", "场地最大 X", "场地最小 Y", "场地最大 Y"]):
            self.field(route, f"bounds.{i}", label+" mm")
        self.note(route, "打卡点按访问顺序填写，每行 x,y（mm）：")
        self.waypoints = tk.Text(route, height=6, width=28, undo=True)
        self.waypoints.pack(fill="x")
        self.note(route, "圆障碍物每行 x,y,半径；没有则留空：")
        self.obstacles = tk.Text(route, height=3, width=28, undo=True)
        self.obstacles.pack(fill="x")
        self.note(route, "逐组记录来源、测量方法或记录编号；继承项目的值应保留“未独立实测”说明。软件不能核实来源声明。")
        for group in VerificationInput.from_project(Project.demo()).sources:
            ttk.Label(route, text=group+"数据来源").pack(anchor="w", pady=(6, 0))
            var = tk.StringVar()
            self.sources[group] = var
            ttk.Entry(route, textvariable=var).pack(fill="x")
            var.trace_add("write", self.changed)
        self.canvases: dict[str, tk.Canvas] = {}
        for key, title in [("route", "路线叠加"), ("angles", "转角对比")]:
            page = ttk.Frame(self.outputs)
            self.outputs.add(page, text=title)
            canvas = tk.Canvas(page, bg="white", highlightthickness=0)
            canvas.pack(fill="both", expand=True)
            canvas.bind("<Configure>", lambda e: self.render())
            self.canvases[key] = canvas
        self.checkpoint_tree = self.table("打卡偏差", ("点", "目标 X/Y mm", "偏差 mm", "凸轮角 °", "状态"), (40, 130, 90, 90, 70))
        self.check_tree = self.table("逐项检查", ("状态", "检查项目", "判定依据"), (70, 150, 420))
        self.issue_tree = self.table("角度与问题", ("凸轮角 °", "位置 mm", "原因"), (90, 170, 400))
        details = ttk.Frame(self.outputs)
        self.outputs.add(details, text="参数与范围")
        self.details = tk.Text(details, wrap="word", bg="white", padx=10, pady=10, state="disabled")
        self.details.pack(fill="both", expand=True)
        self.inputs = VerificationInput.from_project(Project.demo())
        self.load(self.inputs)
        self.file.trace_add("write", self.changed)
        for widget in (self.waypoints, self.obstacles):
            widget.bind("<<Modified>>", self.text_changed)
        self.parent.after(100, self.poll)

    def scroll_form(self, book: ttk.Notebook, title: str) -> ttk.Frame:
        frame = ttk.Frame(book)
        book.add(frame, text=title)
        canvas = tk.Canvas(frame, width=335, bg="#edf2f8", highlightthickness=0)
        bar = ttk.Scrollbar(frame, command=canvas.yview)
        canvas.configure(yscrollcommand=bar.set)
        bar.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        inner = ttk.Frame(canvas, padding=8)
        window = canvas.create_window((0, 0), window=inner, anchor="nw")
        inner.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure(window, width=e.width))
        def wheel(event: Any) -> None:
            if str(event.widget).startswith(str(frame)+"."):
                canvas.yview_scroll(-int(event.delta/120), "units")
        self.parent.winfo_toplevel().bind("<MouseWheel>", wheel, add="+")
        return inner

    @staticmethod
    def note(page: ttk.Frame, text: str) -> None:
        ttk.Label(page, text=text, wraplength=300, foreground="#6b7180").pack(fill="x", pady=5)

    def field(self, page: ttk.Frame, key: str, label: str, choices: list[str] | None = None) -> None:
        ttk.Label(page, text=label).pack(anchor="w", pady=(4, 0))
        var = tk.StringVar()
        self.vars[key] = var
        if choices:
            ttk.Combobox(page, textvariable=var, values=choices, state="readonly", width=18).pack(fill="x")
        else:
            ttk.Entry(page, textvariable=var).pack(fill="x")
        var.trace_add("write", self.changed)

    def table(self, title: str, names: tuple[str, ...], widths: tuple[int, ...]) -> ttk.Treeview:
        frame = ttk.Frame(self.outputs)
        self.outputs.add(frame, text=title)
        tree = ttk.Treeview(frame, columns=names, show="headings", selectmode="browse")
        for name, width in zip(names, widths):
            tree.heading(name, text=name)
            tree.column(name, width=width, minwidth=40)
        bar = ttk.Scrollbar(frame, command=tree.yview)
        tree.configure(yscrollcommand=bar.set)
        bar.pack(side="right", fill="y")
        tree.pack(fill="both", expand=True)
        detail = tk.Text(frame, height=4, wrap="word", bg="white", state="disabled", padx=8, pady=5)
        detail.pack(side="bottom", fill="x", before=tree)
        def select(event: Any) -> None:
            if tree.selection():
                detail.configure(state="normal")
                detail.delete("1.0", "end")
                detail.insert("1.0", "\n".join(map(str, tree.item(tree.selection()[0], "values"))))
                detail.configure(state="disabled")
        tree.bind("<<TreeviewSelect>>", select)
        tree.tag_configure(PASS, foreground="#16724d")
        tree.tag_configure(FAIL, foreground="#bc283b")
        tree.tag_configure("未验证", foreground="#8c6519")
        return tree

    def text_changed(self, event: Any) -> None:
        if event.widget.edit_modified():
            event.widget.edit_modified(False)
            self.changed()

    def changed(self, *args: Any) -> None:
        if self.loading:
            return
        self.revision += 1
        self.cancel.set()
        self.result = None
        self.export_button.configure(state="disabled")
        self.message.set("输入已改变；旧二次检验结果失效，请重新检验。")
        self.clear_outputs()

    def clear_outputs(self) -> None:
        for tree in (self.check_tree, self.checkpoint_tree, self.issue_tree):
            tree.delete(*tree.get_children())
        for canvas in self.canvases.values():
            canvas.delete("all")
            canvas.create_text(15, 25, text="选择 DXF 并核对独立参数后，点击「开始二次检验」。", anchor="nw", fill="#718096")
        self.details.configure(state="normal")
        self.details.delete("1.0", "end")
        self.details.configure(state="disabled")

    def load(self, inputs: VerificationInput) -> None:
        self.loading = True
        self.inputs = copy.deepcopy(inputs)
        for key, var in self.vars.items():
            if key.startswith("bounds."):
                value = inputs.project.route.field[int(key.split(".")[1])]
            elif "." in key:
                group, name = key.split(".")
                value = getattr(getattr(inputs.project, group), name)
            else:
                value = getattr(inputs, key)
            var.set(str(value))
        for group, var in self.sources.items():
            var.set(inputs.sources.get(group, "来源未记录"))
        for widget, rows in ((self.waypoints, inputs.project.route.waypoints), (self.obstacles, inputs.project.route.obstacles)):
            widget.delete("1.0", "end")
            widget.insert("1.0", "\n".join(",".join(map(str, row)) for row in rows))
            widget.edit_modified(False)
        self.loading = False
        self.changed()

    def collect(self) -> VerificationInput:
        q = copy.deepcopy(self.inputs)
        try:
            for key, var in self.vars.items():
                text = var.get().strip()
                if key in ("mechanism.kind", "mechanism.side", "vehicle.drive_side"):
                    value: Any = text
                elif key in ("mechanism.branch", "direction"):
                    value = int(text)
                else:
                    value = float(text)
                if key.startswith("bounds."):
                    q.project.route.field[int(key.split(".")[1])] = value
                elif "." in key:
                    group, name = key.split(".")
                    setattr(getattr(q.project, group), name, value)
                else:
                    setattr(q, key, value)
            for widget, attr, columns in ((self.waypoints, "waypoints", 2), (self.obstacles, "obstacles", 3)):
                rows = [[float(v.strip()) for v in line.split(",")] for line in widget.get("1.0", "end").splitlines() if line.strip()]
                if any(len(row) != columns for row in rows):
                    raise ProjectError(f"{attr} 每行应为 {columns} 列。")
                setattr(q.project.route, attr, rows)
            q.sources = {key: var.get().strip() or "来源未记录" for key, var in self.sources.items()}
            q.validate()
        except (ValueError, TypeError) as exc:
            raise ProjectError(f"二次检验输入有误：{exc}") from exc
        return q

    def choose_dxf(self) -> None:
        path = filedialog.askopenfilename(parent=self.parent, title="选择最终加工轮廓 DXF", filetypes=[("DXF", "*.dxf")])
        if path:
            self.file.set(path)

    def copy_design(self) -> None:
        try:
            project, reference, nominal, end = self.context()
            self.reference, self.nominal = copy.deepcopy(reference), copy.deepcopy(nominal)
            self.reference_source = "当前设计曲线，仅用于比较" if reference is not None else "未提供设计曲线"
            self.nominal_source = "当前设计名义转角，仅用于比较" if nominal is not None else "未提供名义转角"
            q = VerificationInput.from_project(project, end)
            q.sources["运行区间"] = "当前设计计划里程换算，须独立核定；仅定义比赛运行范围"
            self.load(q)
            self.message.set("已复制到独立检验快照；请在三个参数页核对实测安装位置、来源及计划区间。")
        except (ProjectError, ValueError) as exc:
            messagebox.showerror("参数无法复制", str(exc), parent=self.parent)

    def load_project(self) -> None:
        path = filedialog.askopenfilename(parent=self.parent, filetypes=[("项目", "*.json")])
        if path:
            try:
                self.reference = self.nominal = None
                self.reference_source, self.nominal_source = "未提供设计曲线", "未提供名义转角"
                self.load(VerificationInput.from_project(Project.load(path)))
                self.message.set("项目参数已载入；运行区间默认 0～240°，请核定；装配值为设计推算初值，须独立测量。")
            except (ProjectError, OSError) as exc:
                messagebox.showerror("读取失败", str(exc), parent=self.parent)

    def load_config(self) -> None:
        path = filedialog.askopenfilename(parent=self.parent, filetypes=[("二次检验配置", "*.json")])
        if path:
            try:
                self.reference = self.nominal = None
                self.reference_source, self.nominal_source = "未提供设计曲线", "未提供名义转角"
                self.load(VerificationInput.from_dict(json.loads(Path(path).read_text(encoding="utf-8-sig"))))
            except (ProjectError, OSError, ValueError, TypeError, KeyError) as exc:
                messagebox.showerror("读取失败", str(exc), parent=self.parent)

    def save_config(self) -> None:
        try:
            q = self.collect()
            path = filedialog.asksaveasfilename(parent=self.parent, defaultextension=".json", initialfile="二次检验参数.json")
            if path:
                Path(path).write_text(json.dumps(q.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        except (ProjectError, OSError) as exc:
            messagebox.showerror("保存失败", str(exc), parent=self.parent)

    def choose_nominal(self) -> None:
        path = filedialog.askopenfilename(parent=self.parent, filetypes=[("名义比较 CSV", "*.csv")])
        if path:
            try:
                self.nominal = load_nominal_csv(Path(path))
                self.nominal_source = Path(path).name+"（仅比较，数据将写入报告快照）"
                self.changed()
                self.message.set("已读入名义 CSV，仅用于转角比较，不驱动接触和轨迹。")
            except (ProjectError, OSError, ValueError) as exc:
                messagebox.showerror("比较数据读取失败", str(exc), parent=self.parent)

    def run(self) -> None:
        if self.busy:
            return
        try:
            q = self.collect()
            path = Path(self.file.get())
            if not path.is_file():
                raise ProjectError("请先选择最终 cam.dxf 文件。")
        except (ProjectError, OSError) as exc:
            messagebox.showerror("不能开始二次检验", str(exc), parent=self.parent)
            return
        self.cancel.clear()
        self.busy = True
        self.result = None
        self.clear_outputs()
        self.run_button.configure(state="disabled")
        self.export_button.configure(state="disabled")
        self.message.set("正在从 DXF 求接触；将执行粗细两次整圈检查，随后仅积分计划运行段。")
        revision = self.revision
        reference, nominal = copy.deepcopy(self.reference), copy.deepcopy(self.nominal)
        reference_source, nominal_source = self.reference_source, self.nominal_source
        def worker() -> None:
            try:
                result = verify_dxf(path, q, reference=reference, nominal=nominal, cancelled=self.cancel.is_set,
                                    progress=lambda text: self.events.put(("progress", text)))
                result.metrics.update(reference_source=reference_source, nominal_source=nominal_source)
                self.events.put(("result", (revision, result)))
            except Exception as exc:
                self.events.put(("error", str(exc)))
        threading.Thread(target=worker, daemon=True).start()

    def poll(self) -> None:
        try:
            while True:
                event, value = self.events.get_nowait()
                if event == "progress":
                    if not self.cancel.is_set():
                        self.message.set(value)
                else:
                    self.busy = False
                    self.run_button.configure(state="normal")
                    if event == "error":
                        self.message.set(value)
                        if not self.cancel.is_set():
                            messagebox.showerror("二次检验提示", value, parent=self.parent)
                    else:
                        revision, result = value
                        if self.cancel.is_set():
                            self.message.set("二次检验已停止；未采用后台结果，请重新检验。")
                        elif revision != self.revision:
                            self.message.set("检验过程中参数已修改，结果已丢弃，请重新检验。")
                        else:
                            self.show_result(result)
        except queue.Empty:
            pass
        self.parent.after(100, self.poll)

    def show_result(self, result: VerificationResult) -> None:
        self.result = result
        self.clear_outputs()
        for c in result.checks:
            self.check_tree.insert("", "end", values=(c.status, c.name, c.detail), tags=(c.status,))
        for p in result.checkpoints:
            self.checkpoint_tree.insert("", "end", values=(p["number"], p["target"],
                    "无有效访问" if p["deviation_mm"] is None else f"{p['deviation_mm']:.4f}", p["angle_deg"], p["status"]), tags=(p["status"],))
        for issue in result.issues:
            self.issue_tree.insert("", "end", values=(issue["angle_deg"], issue["position_mm"], issue["reason"]))
        self.details.configure(state="normal")
        self.details.insert("1.0", verification_report(result))
        self.details.configure(state="disabled")
        self.export_button.configure(state="normal")
        fails = sum(c.status == FAIL for c in result.checks)
        unknown = sum(c.status == "未验证" for c in result.checks)
        self.message.set(f"二次检验完成：{fails} 项不通过、{unknown} 项未验证。请逐项查看依据；几何运动学结果不保证实车性能。")
        self.render()

    def render(self) -> None:
        result = self.result
        if result is None:
            return
        q = result.inputs
        r = q.project.route
        reference = result.reference if len(result.reference) >= 2 else np.array([[r.start_x, r.start_y], *r.waypoints])
        points = np.vstack([reference, result.path, [[r.field[0], r.field[2]], [r.field[1], r.field[3]]]])
        lo, hi = points.min(axis=0)-80, points.max(axis=0)+80
        c = self.canvases["route"]
        plot = Plot(c, [lo[0], hi[0], lo[1], hi[1]])
        xmin,xmax,ymin,ymax = r.field
        plot.line(np.array([[xmin,ymin],[xmax,ymin],[xmax,ymax],[xmin,ymax],[xmin,ymin]]), "#b2bfce", 1)
        plot.line(reference, "#8495a8", 1.5, (4, 3))
        plot.line(result.path, "#cc354a", 2)
        for x, y, radius in r.obstacles:
            plot.circle(x, y, radius, "#c68536", "#fff0dc")
        for i, (x, y) in enumerate(r.waypoints, 1):
            plot.circle(x, y, r.tolerance, "#97b2db")
            plot.label(x, y, str(i))
        c.create_text(12, 8, anchor="nw", text="红：独立回算；灰虚线："+("原设计曲线" if len(result.reference) >= 2 else "打卡点连线（非设计曲线）"), fill="#25334c", width=max(200, c.winfo_width()-20))
        if not len(result.path):
            c.create_text(20, 50, anchor="nw", text="计划段缺少唯一有效接触解，未绘制猜测轨迹。", fill="#bc283b")
        c = self.canvases["angles"]
        valid = np.isfinite(result.steering)
        if not valid.any():
            c.delete("all")
            c.create_text(20, 30, anchor="nw", text="没有唯一有效的轮廓反算转角；请查看角度与问题。", fill="#bc283b")
            return
        low, high = float(result.steering[valid].min()), float(result.steering[valid].max())
        if len(result.nominal):
            low, high = min(low, float(result.nominal[:, 1].min())), max(high, float(result.nominal[:, 1].max()))
        span = max(1, high-low)
        plot = Plot(c, [0, 360, low-span*.15, high+span*.15], equal=False)
        if len(result.nominal):
            plot.line(result.nominal, "#8495a8", 1.5, (4, 3))
        indices = np.flatnonzero(valid)
        for section in np.split(indices, np.flatnonzero(np.diff(indices) != 1)+1):
            plot.line(np.column_stack([result.angles[section], result.steering[section]]), "#cc354a", 2)
        for a in (q.run_start, q.run_end):
            plot.line(np.array([[a, low-span*.15], [a, high+span*.15]]), "#c68536", 1, (4, 3))
        c.create_text(12, 8, anchor="nw", text="凸轮角° → 前轮角°；红：反算 灰：名义 竖线：计划段", fill="#25334c", width=max(200, c.winfo_width()-20))

    def export(self) -> None:
        if self.result is None:
            return
        folder = filedialog.askdirectory(parent=self.parent, title="选择新的二次检验报告目录")
        if folder:
            try:
                files = export_verification(self.result, Path(folder))
                self.message.set(f"已导出二次检验报告、JSON 参数快照、独立接触与轨迹 CSV：{files['report']}")
            except (ProjectError, OSError) as exc:
                messagebox.showerror("导出失败", str(exc), parent=self.parent)
