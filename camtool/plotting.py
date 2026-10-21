from __future__ import annotations

import math
import tkinter as tk
from typing import Iterable, Literal

import numpy as np

from .engine import Array, Result, follower_positions
from .markings import cam_markings
from .project import Project
from .viewport import grid_step

BLUE = "#2464df"
TEAL = "#159c8c"
INK = "#25334c"
MUTED = "#718096"


class Plot:
    def __init__(self, canvas: tk.Canvas, bounds: Iterable[float], *, equal: bool = True, footer: int = 0) -> None:
        self.canvas = canvas
        self.footer = footer
        if not getattr(canvas,'retained_frame',False):canvas.delete("all")
        xmin, xmax, ymin, ymax = bounds
        state=getattr(canvas,'view_state',None)
        if state is not None:
            state.prepare((xmin,xmax,ymin,ymax),canvas.winfo_width(),canvas.winfo_height()-getattr(canvas,'view_footer',0))
            self.xmin,self.xmax,self.ymin,self.ymax=state.visible()
            self.sx=self.sy=state.scale;self.x0=self.y0=0.
            self._camera_grid()
            return
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

    def _camera_grid(self) -> None:
        c=self.canvas;step=grid_step(self.sx);width=c.winfo_width();height=c.winfo_height()-getattr(c,'view_footer',0)
        for x in np.arange(math.ceil(self.xmin/step)*step,self.xmax+step*.01,step)[:200]:
            px,_=self.xy(float(x),0)
            c.create_line(px,0,px,height,fill='#c2cfde' if abs(x)<step*.001 else '#e7edf5',tags='view_grid')
            c.create_text(px,height-18,text=f'{x:g}',fill=MUTED,font=('Segoe UI',8),tags='view_axis')
        for y in np.arange(math.ceil(self.ymin/step)*step,self.ymax+step*.01,step)[:200]:
            _,py=self.xy(0,float(y))
            c.create_line(0,py,width,py,fill='#c2cfde' if abs(y)<step*.001 else '#e7edf5',tags='view_grid')
            c.create_text(6,py,text=f'{y:g}',anchor='w',fill=MUTED,font=('Segoe UI',8),tags='view_axis')
        length=step*self.sx;right=max(140,width-25);bottom=max(65,height-82)
        c.create_line(right-length,bottom,right,bottom,fill=INK,width=2,tags='scale_bar')
        c.create_text(right-length/2,bottom-10,text=f'{step:g} mm',fill=INK,font=('Segoe UI',8),tags='scale_bar')

    def xy(self, x: float, y: float) -> tuple[float, float]:
        return self.x0+(x-self.xmin)*self.sx, self.y0+(self.ymax-y)*self.sy

    def unxy(self, x: float, y: float) -> tuple[float, float]:
        return self.xmin+(x-self.x0)/self.sx, self.ymax-(y-self.y0)/self.sy

    def line(self, points: Array, color: str = BLUE, width: float = 2, dash: tuple[int, int] | None = None) -> None:
        if len(points) < 2:
            return
        stride = 1 if hasattr(self.canvas,'view_state') else max(1, len(points)//2500)
        points = np.vstack([points[::stride], points[-1]])
        coords = [coord for x, y in points for coord in self.xy(float(x), float(y))]
        self.canvas.create_line(*coords, fill=color, width=width, dash=dash or ())

    def circle(self, x: float, y: float, radius: float, color: str, fill: str = "") -> None:
        a, b = self.xy(x-radius, y+radius), self.xy(x+radius, y-radius)
        self.canvas.create_oval(*a, *b, outline=color, fill=fill, width=1.2)

    def label(self, x: float, y: float, text: str, color: str = INK) -> None:
        px, py = self.xy(x, y)
        self.canvas.create_text(px+6, py-8, text=text, fill=color, anchor="sw", font=("Microsoft YaHei UI", 9))


def scene(canvas: tk.Canvas, result: Result, index: int, *, selected: Iterable[int] = (),
          auxiliary_selected: Iterable[int] = ()) -> Plot:
    p, v = result.project, result.project.vehicle
    xmin, xmax, ymin, ymax = p.route.field
    extent = np.vstack([result.path, result.rear, [xmin, ymin], [xmax, ymax]])
    lo, hi = extent.min(axis=0)-100, extent.max(axis=0)+100
    plot = Plot(canvas, [lo[0], hi[0], lo[1], hi[1]], footer=36)
    plot.line(np.array([[xmin, ymin], [xmax, ymin], [xmax, ymax], [xmin, ymax], [xmin, ymin]]), "#afbed1", 1.5)
    for x, y, radius in p.route.obstacles:
        plot.circle(x, y, radius, "#df9546", "#fff0dc")
    plot.line(result.target_path, TEAL, 1, (4, 3))
    plot.line(result.path)
    auxiliary_marks(plot, p,auxiliary_selected)
    for number, (x, y) in enumerate(p.route.waypoints, 1):
        row=result.checkpoint_rows[number-1] if number<=len(result.checkpoint_rows) else None
        missed=bool(len(result.profile) and row and not row["passed"])
        color="#bc2842" if missed else BLUE
        plot.circle(x,y,p.route.tolerance,color if missed else "#b6c8e5")
        plot.circle(x,y,max(4,4/plot.sx),color,"#fff0f1" if missed else "#ffffff")
        plot.label(x,y,str(number),color)
        if missed and row is not None:
            actual=np.asarray(row["point"])
            plot.line(np.array([[x,y],actual]),"#bc2842",1,(3,3))
            plot.label(float(actual[0]),float(actual[1]),f"漏{number} {row['error_mm']:.1f}mm","#bc2842")
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
    highlight_waypoints(plot,p.route.waypoints,selected)
    canvas.create_text(14, 12, text="X/Y：mm  蓝：凸轮名义回算（非实测）  绿虚线：设计必过路线  红：漏点  绿点：传感器", anchor="nw", fill=MUTED,
                       width=max(150,canvas.winfo_width()-28))
    passed=sum(bool(row['passed']) for row in result.checkpoint_rows)
    fails=sum(c.status=='fail' for c in result.checks)
    visits=f"名义打卡 {passed}/{len(result.checkpoint_rows)}" if len(result.profile) else "无凸轮，名义打卡未验证"
    overall=f"整体 {fails} 项不满足" if fails else "仍需核对采样稳定性及最终DXF"
    canvas.create_text(14,canvas.winfo_height()-6,anchor="sw",width=max(160,canvas.winfo_width()-28),
                       text=f"{visits} · {overall} · 实际 {int(result.stats.get('cam_points_used',0))} 点",
                       fill="#b3263d" if fails else "#875c18",tags="checkpoint_banner")
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


def highlight_waypoints(plot: Plot, points: list[list[float]], selected: Iterable[int]) -> None:
    groups: dict[tuple[float,float],list[int]]={}
    for i in selected:
        if not 0<=i<len(points): continue
        groups.setdefault((points[i][0],points[i][1]),[]).append(i+1)
    for point,numbers in groups.items():
        x,y=plot.xy(*point); tags=("selected_waypoint",*(f"selected_waypoint_{n}" for n in numbers))
        plot.canvas.create_oval(x-8,y-8,x+8,y+8,outline="#cf334a",width=3,tags=tags)
        plot.canvas.create_text(x+12,y-13,anchor="sw",text="选中 "+"/".join(str(n) for n in numbers),fill="#cf334a",font=("Microsoft YaHei UI",10,"bold"),tags=tags)


def route_edit(canvas: tk.Canvas, project: object, selected: Iterable[int] = (),
               bounds: tuple[float,float,float,float] | None = None, *,
               auxiliary_selected: Iterable[int] = (),mode: str="打卡点",footer: int=0) -> Plot:
    from .project import Project
    assert isinstance(project,Project)
    r=project.route
    plot=Plot(canvas,bounds or (r.field[0]-80,r.field[1]+80,r.field[2]-80,r.field[3]+80),footer=footer)
    xmin,xmax,ymin,ymax=r.field
    plot.line(np.array([[xmin,ymin],[xmax,ymin],[xmax,ymax],[xmin,ymax],[xmin,ymin]]),"#9eb0c5",1)
    points=np.array(r.waypoints,dtype=float).reshape((-1,2))
    plot.line(points,"#aab6c7",1,(4,3))
    for i,(x,y) in enumerate(points):
        color=BLUE
        plot.circle(x,y,5/plot.sx,color,color)
        plot.label(x,y,str(i+1),color)
    for x,y,radius in r.obstacles: plot.circle(x,y,radius,"#c58d42")
    highlight_waypoints(plot,r.waypoints,selected)
    canvas.create_text(12,10,anchor="nw",text=f"当前编辑：{mode} · 蓝圆=打卡 / 紫菱=辅助 · 折线仅参考，须重新计算",fill=INK,
                       width=max(160,canvas.winfo_width()-24),tags="edit_mode")
    auxiliary_marks(plot,project,auxiliary_selected)
    return plot


def auxiliary_marks(plot: Plot, p: Project, selected: Iterable[int] = ()) -> None:
    chosen=set(selected)
    for i,(segment,x,y) in enumerate(p.route.auxiliary_points):
        px,py=plot.xy(x,y);color="#9a4fb0";tag=f"auxiliary_{i+1}"
        plot.canvas.create_polygon(px,py-6,px+6,py,px,py+6,px-6,py,outline=color,fill="#f2e5f8",width=1.5,tags=tag)
        plot.canvas.create_text(px+8,py-8,text=f"辅{i+1}·段{int(segment)+1}",anchor="sw",fill=color,tags=tag)
        if i in chosen:
            tag=f"selected_auxiliary_{i+1}"
            plot.canvas.create_polygon(px,py-10,px+10,py,px,py+10,px-10,py,outline="#a12fbd",fill="",width=3,tags=tag)
            plot.canvas.create_text(px+10,py+10,text=f"选中辅{i+1}",anchor="nw",fill="#a12fbd",tags=tag)
