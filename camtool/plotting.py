from __future__ import annotations

import math
import tkinter as tk
from typing import Iterable, Literal

import numpy as np

from .engine import Array, Result, follower_positions
from .markings import cam_markings

BLUE = "#2464df"
TEAL = "#159c8c"
INK = "#25334c"
MUTED = "#718096"


class Plot:
    def __init__(self, canvas: tk.Canvas, bounds: Iterable[float], *, equal: bool = True, footer: int = 0) -> None:
        self.canvas = canvas
        canvas.delete("all")
        xmin, xmax, ymin, ymax = bounds
        if xmax-xmin < 1e-6:
            xmin, xmax = xmin-1, xmax+1
        if ymax-ymin < 1e-6:
            ymin, ymax = ymin-1, ymax+1
        width, height = max(canvas.winfo_width(), 200), max(canvas.winfo_height()-footer, 100)
        self.sx = (width-100)/(xmax-xmin)
        self.sy = (height-65)/(ymax-ymin)
        if equal:
            self.sx = self.sy = min(self.sx, self.sy)
        self.x0 = 65+(width-100-self.sx*(xmax-xmin))/2
        self.y0 = 25+(height-65-self.sy*(ymax-ymin))/2
        self.xmin, self.ymax = xmin, ymax
        self.xmax, self.ymin = xmax, ymin
        for fraction in np.linspace(0, 1, 6):
            x, y = xmin+(xmax-xmin)*fraction, ymin+(ymax-ymin)*fraction
            a, b = self.xy(x, ymin), self.xy(x, ymax)
            canvas.create_line(*a, *b, fill="#e7edf5")
            canvas.create_text(a[0], a[1]+14, text=f"{x:.0f}", fill=MUTED, font=("Segoe UI", 8))
            a, b = self.xy(xmin, y), self.xy(xmax, y)
            canvas.create_line(*a, *b, fill="#e7edf5")
            canvas.create_text(a[0]-8, a[1], text=f"{y:.1f}", anchor="e", fill=MUTED, font=("Segoe UI", 8))

    def xy(self, x: float, y: float) -> tuple[float, float]:
        return self.x0+(x-self.xmin)*self.sx, self.y0+(self.ymax-y)*self.sy

    def unxy(self, x: float, y: float) -> tuple[float, float]:
        return self.xmin+(x-self.x0)/self.sx, self.ymax-(y-self.y0)/self.sy

    def line(self, points: Array, color: str = BLUE, width: float = 2, dash: tuple[int, int] | None = None) -> None:
        if len(points) < 2:
            return
        stride = max(1, len(points)//2500)
        points = np.vstack([points[::stride], points[-1]])
        coords = [coord for x, y in points for coord in self.xy(float(x), float(y))]
        self.canvas.create_line(*coords, fill=color, width=width, dash=dash or ())

    def circle(self, x: float, y: float, radius: float, color: str, fill: str = "") -> None:
        a, b = self.xy(x-radius, y+radius), self.xy(x+radius, y-radius)
        self.canvas.create_oval(*a, *b, outline=color, fill=fill, width=1.2)

    def label(self, x: float, y: float, text: str, color: str = INK) -> None:
        px, py = self.xy(x, y)
        self.canvas.create_text(px+6, py-8, text=text, fill=color, anchor="sw", font=("Microsoft YaHei UI", 9))


def scene(canvas: tk.Canvas, result: Result, index: int) -> Plot:
    p, v = result.project, result.project.vehicle
    xmin, xmax, ymin, ymax = p.route.field
    extent = np.vstack([result.path, result.rear, [xmin, ymin], [xmax, ymax]])
    lo, hi = extent.min(axis=0)-100, extent.max(axis=0)+100
    plot = Plot(canvas, [lo[0], hi[0], lo[1], hi[1]])
    plot.line(np.array([[xmin, ymin], [xmax, ymin], [xmax, ymax], [xmin, ymax], [xmin, ymin]]), "#afbed1", 1.5)
    for x, y, radius in p.route.obstacles:
        plot.circle(x, y, radius, "#df9546", "#fff0dc")
    plot.line(result.target_path, TEAL, 1, (4, 3))
    plot.line(result.path)
    for number, (x, y) in enumerate(p.route.waypoints, 1):
        plot.circle(x, y, p.route.tolerance, "#b6c8e5")
        plot.circle(x, y, max(4, 4/plot.sx), BLUE, "#ffffff")
        plot.label(x, y, str(number))
    index = min(index, len(result.path)-1)
    rear, angle = result.rear[index], result.heading[index]
    tangent, normal = np.array([math.cos(angle), math.sin(angle)]), np.array([-math.sin(angle), math.cos(angle)])
    left, right, front = rear+v.track/2*normal, rear-v.track/2*normal, rear+v.wheelbase*tangent
    plot.line(np.array([left, front, right, left]), "#9aa9be", 1.5)
    for center, diameter, turn in [(left, v.drive_diameter if v.drive_side == "left" else v.passive_diameter, angle),
                                   (right, v.drive_diameter if v.drive_side == "right" else v.passive_diameter, angle),
                                   (front, v.front_diameter, angle+result.delta[index])]:
        vector = np.array([math.cos(turn), math.sin(turn)])*diameter/2
        plot.line(np.array([center-vector, center+vector]), INK, 5)
    drive = left if v.drive_side == "left" else right
    plot.label(float(drive[0]), float(drive[1]), "驱动", "#d58c30")
    sensor = result.path[index]
    plot.circle(float(sensor[0]), float(sensor[1]), 6/plot.sx, TEAL, TEAL)
    canvas.create_text(14, 12, text="X / Y：mm   蓝：名义回算轨迹   虚线：目标轨迹   绿点：传感器", anchor="nw", fill=MUTED,
                       width=max(150,canvas.winfo_width()-28))
    return plot


def cam(canvas: tk.Canvas, result: Result, *, compact: bool = False) -> Plot | None:
    if not len(result.profile):
        canvas.delete("all")
        canvas.create_text(30, 30, text="容量不足，尚无可用凸轮轮廓。", anchor="nw", fill="#c14d48")
        return None
    radius = result.stats["max_profile_radius"]*1.4
    plot = Plot(canvas, [-radius, radius, -radius, radius], footer=0 if compact else 64)
    plot.circle(0, 0, result.project.cam.base_radius, "#a5b3c7")
    plot.circle(0, 0, result.project.cam.hole_diameter/2, "#c26a68")
    plot.line(result.pitch, TEAL, 1, (4, 3))
    plot.line(result.profile, BLUE, 2.2)
    markings = cam_markings(result)
    _start_mark(plot, markings.start, compact=compact)
    _rotation_mark(plot, markings.arrow, markings.arrowhead)
    if compact:
        canvas.create_text(10,8,anchor="nw",text="凸轮定位 · S0 与实体旋向",fill=INK)
        return plot
    canvas.create_text(14, 12, text="毫米 · 蓝：实际轮廓  虚线：接触中心轨迹  红色 S0：出发点", anchor="nw", fill=MUTED)
    canvas.create_text(14, canvas.winfo_height()-62,
                       text=f"凸轮实体旋转：{markings.direction}（正对本图这一面观察）",
                       anchor="nw", fill="#bd5521", font=("Microsoft YaHei UI", 10, "bold"))
    canvas.create_text(14, canvas.winfo_height()-38,
                       text="起跑装配：在「机构运动」把进度条拖到最左，让 S0 对准滚子/球的接触位置。",
                       width=max(200, canvas.winfo_width()-28), anchor="nw", fill=INK)
    return plot


def _start_mark(plot: Plot, point: Array, *, compact: bool = False) -> None:
    color = "#cd3e48"
    plot.line(np.array([[0.0, 0.0], point]), color, 1, (4, 3))
    x, y = plot.xy(float(point[0]), float(point[1]))
    plot.canvas.create_oval(x-5, y-5, x+5, y+5, fill=color, outline="white", width=1.5)
    right = point[0] >= 0
    below = y-12 < 50
    anchor: Literal["nw", "ne", "sw", "se"] = ("nw" if right else "ne") if below else ("sw" if right else "se")
    plot.canvas.create_text(x+(12 if right else -12), y+(12 if below else -12), text="S0" if compact else "出发点 S0 · 里程 0",
                            anchor=anchor, fill=color,
                            font=("Microsoft YaHei UI", 10, "bold"))


def _rotation_mark(plot: Plot, arrow: Array, arrowhead: Array) -> None:
    plot.line(arrow, "#bd5521", 2)
    plot.line(arrowhead, "#bd5521", 2)


def curves(canvas: tk.Canvas, result: Result, kind: str) -> Plot | None:
    if kind == "steer":
        x, y, label = result.drive_s, np.degrees(result.delta), "驱动轮里程 mm → 前轮转角 °"
    elif kind == "stroke":
        x, y, label = result.drive_s, result.stroke, "驱动轮里程 mm → 推杆/球纵向位移 mm"
    else:
        x, y, label = np.degrees(result.cam_angle), result.pressure, "凸轮转角 ° → 压力角 °（含回程）"
    if not len(y):
        canvas.delete("all")
        return None
    span = max(float(np.ptp(y)), 1.0)
    plot = Plot(canvas, [float(x[0]), float(x[-1]), float(y.min()-span*.15), float(y.max()+span*.15)], equal=False)
    plot.line(np.column_stack([x, y]))
    canvas.create_text(14, 8, text=label, anchor="nw", fill=INK)
    return plot


def mechanism(canvas: tk.Canvas, result: Result, index: int, *, compact: bool = False) -> Plot | None:
    if not len(result.profile):
        return None
    p = result.project
    m = p.mechanism
    index = min(index, len(result.delta)-1)
    delta = np.array([result.delta[index]])
    center = follower_positions(delta, p)[0][0]
    angle = p.cam.direction*result.drive_s[index]/(math.pi*p.vehicle.drive_diameter*p.cam.ratio)*2*math.pi-math.radians(p.cam.phase)
    matrix = np.array([[math.cos(angle), -math.sin(angle)], [math.sin(angle), math.cos(angle)]])
    outline = result.profile @ matrix.T
    extent = np.vstack([outline, result.pivot, center])
    markings = cam_markings(result)
    extent = np.vstack([extent, markings.arrow])
    lo, hi = extent.min(axis=0)-20, extent.max(axis=0)+20
    plot = Plot(canvas, [lo[0], hi[0], lo[1], hi[1]], footer=0 if compact else 64)
    plot.line(outline, BLUE)
    plot.circle(float(center[0]), float(center[1]), m.roller, TEAL, "#d8f4ee")
    if m.kind == "flat":
        sign = -1 if m.side == "right" else 1
        beta = math.radians(m.arm_angle)+sign*(delta[0]-math.radians(m.steer_zero))
        pin = result.pivot+m.arm*np.array([math.cos(beta), math.sin(beta)])
        plot.line(np.array([result.pivot, pin]), "#d77e39", 4)
        plot.line(np.array([pin, center]), INK, 2)
        plot.line(np.array([[m.offset, lo[1]], [m.offset, hi[1]]]), "#aebcce", 1, (4, 3))
    else:
        plot.line(np.array([result.pivot, center]), "#d77e39", 4)
    plot.circle(float(result.pivot[0]), float(result.pivot[1]), 2, INK)
    plot.label(float(result.pivot[0]), float(result.pivot[1]), "转向轴")
    plot.label(0, 0, "凸轮轴")
    _start_mark(plot, markings.start @ matrix.T, compact=compact)
    _rotation_mark(plot, markings.arrow, markings.arrowhead)
    if compact:
        canvas.create_text(10,8,anchor="nw",text=f"{'平推' if m.kind=='flat' else '顶球'}机构 · 前轮 {math.degrees(delta[0]):.2f}°",fill=INK)
        return plot
    canvas.create_text(14, 12, text="理想平面机构 · 安装高度由基圆与中位抬升确定", anchor="nw", fill=MUTED)
    canvas.create_text(14, canvas.winfo_height()-62,
                       text=f"凸轮实体旋转：{markings.direction}（正对本图这一面观察）",
                       anchor="nw", fill="#bd5521", font=("Microsoft YaHei UI", 10, "bold"))
    canvas.create_text(14, canvas.winfo_height()-38,
                       text=f"{'当前为起跑姿态' if index == 0 else '拖到进度条最左查看起跑姿态'} · 前轮角 {math.degrees(delta[0]):.2f}°（左正右负）",
                       width=max(200, canvas.winfo_width()-28), anchor="nw", fill=INK)
    return plot


def route_edit(canvas: tk.Canvas, project: object, selected: int | None = None,
               bounds: tuple[float,float,float,float] | None = None) -> Plot:
    from .project import Project
    assert isinstance(project,Project)
    r=project.route
    plot=Plot(canvas,bounds or (r.field[0]-80,r.field[1]+80,r.field[2]-80,r.field[3]+80))
    xmin,xmax,ymin,ymax=r.field
    plot.line(np.array([[xmin,ymin],[xmax,ymin],[xmax,ymax],[xmin,ymax],[xmin,ymin]]),"#9eb0c5",1)
    points=np.array(r.waypoints,dtype=float).reshape((-1,2))
    plot.line(points,"#aab6c7",1,(4,3))
    for i,(x,y) in enumerate(points):
        color="#cf3d4a" if i==selected else BLUE
        plot.circle(x,y,5/plot.sx,color,color)
        plot.label(x,y,str(i+1),color)
    for x,y,radius in r.obstacles: plot.circle(x,y,radius,"#c58d42")
    canvas.create_text(12,10,anchor="nw",text="路线编辑 · 点击空白添加 / 拖动打卡点 · 折线为编辑参考，尚未计算",fill=INK)
    return plot
