"""Retained Tk drawing, camera controls and synchronized independent windows."""
from __future__ import annotations
import math
import tkinter as tk
from tkinter import ttk
from typing import TYPE_CHECKING,Any
import numpy as np
from . import plotting
from .viewport import Viewport
from .project import ProjectError
if TYPE_CHECKING:
    from .ui import App


class RetainedCanvas(tk.Canvas):
    """Reuse stable items and skip unchanged Tcl calls; never clear a frame."""
    def __init__(self,*args: Any,**kwargs: Any) -> None:
        super().__init__(*args,**kwargs)
        self.view_state=Viewport();self.retained_frame=False
        self.view_footer=0
        self.items: list[tuple[int,str,Any,Any]]=[];self.cursor=0
        self.frames=0;self.updates=0;self.creations=0

    def begin(self) -> None:
        self.retained_frame=True;self.cursor=0;self.frames+=1

    def end(self) -> None:
        for item,*_ in self.items[self.cursor:]:super().delete(item)
        del self.items[self.cursor:];self.retained_frame=False

    def delete(self,*args: Any) -> None:
        if 'all' in args:self.items.clear()
        super().delete(*args)

    def _create(self,itemType: str,args: Any,kw: Any) -> int:
        base: Any=super()
        if not self.retained_frame:return base._create(itemType,args,kw)
        # Tk's public create_* pass nested sequences; normalize before comparing.
        def flat(values: Any) -> Any:
            for value in values:
                if isinstance(value,(list,tuple)):yield from flat(value)
                else:yield value
        coords=tuple(flat(args))
        options=dict(kw)
        i=self.cursor;self.cursor+=1
        if i<len(self.items) and self.items[i][1]==itemType:
            item,_,old_coords,old_options=self.items[i]
            if old_coords!=coords:self.coords(item,*coords);self.updates+=1
            if old_options!=options:
                configs=self.itemconfigure(item) or {}
                removed: dict[str,Any]={str(key):configs[str(key)][3] for key in old_options if key not in options and str(key) in configs}
                if removed:self.itemconfigure(item,**removed)
                self.itemconfigure(item,**options);self.updates+=1
        else:
            if i<len(self.items):super().delete(self.items[i][0])
            item=base._create(itemType,args,kw);self.creations+=1
        entry=(item,itemType,coords,options)
        if i<len(self.items):self.items[i]=entry
        else:self.items.append(entry)
        return item


class FigurePanel(ttk.Frame):
    def __init__(self,parent: Any,app: App,kind: str,expanded: bool=False) -> None:
        super().__init__(parent);self.app=app;self.kind=kind;self.expanded=expanded
        self.plot: plotting.Plot | None=None;self.pan_origin: tuple[float,float] | None=None
        self.scale_text=tk.StringVar(value='100%');self.detail=tk.StringVar(value='尚未计算；几何数据未验证。')
        self.hover=tk.StringVar(value='滚轮以光标缩放 · 中键平移')
        toolbar=ttk.Frame(self,padding=2);toolbar.pack(fill='x')
        for title,command in [('放大',lambda:self.zoom(1.25)),('缩小',lambda:self.zoom(.8)),
                              ('适应全部',self.fit),('恢复视图',self.restore)]:
            ttk.Button(toolbar,text=title,command=command,width=7,style='Compact.TButton').pack(side='left',padx=1)
        ttk.Label(toolbar,textvariable=self.scale_text,width=8).pack(side='left',padx=3)
        if not expanded:ttk.Button(toolbar,text='放大查看',command=lambda:app.open_figure(kind),width=9,style='Compact.TButton').pack(side='left',padx=2)
        if kind=='scene':
            mode=ttk.Frame(self,padding=2);mode.pack(fill='x')
            selector=ttk.Combobox(mode,textvariable=app.view_mode,values=('浏览模式','编辑点位模式'),width=12,state='readonly')
            selector.pack(side='left');selector.bind('<<ComboboxSelected>>',lambda e:app.map_edit.set(app.view_mode.get()=='编辑点位模式'))
            if expanded:
                point_type=ttk.Combobox(mode,textvariable=app.map_mode,values=('打卡点','辅助点'),width=7,state='readonly')
                point_type.pack(side='left',padx=3);point_type.bind('<<ComboboxSelected>>',app.choose_map_mode)
            ttk.Label(mode,text='浏览左拖/中拖平移；编辑按当前点类型拖点',font=('Microsoft YaHei UI',8)).pack(side='left',padx=3)
        detail_label=tk.Label(self,textvariable=self.detail,wraplength=800,height=2,anchor='w',justify='left',bg='#edf2f8',fg='#25334c',font=('Microsoft YaHei UI',8))
        detail_label.pack(side='bottom',fill='x')
        ttk.Label(self,textvariable=self.hover,font=('Microsoft YaHei UI',8),padding=2).pack(side='bottom',fill='x')
        self.bind('<Configure>',lambda e:detail_label.configure(wraplength=max(160,e.width-8)))
        self.canvas=RetainedCanvas(self,bg='white',highlightthickness=0)
        self.canvas.view_footer=36 if kind=='scene' else 64
        self.canvas.pack(fill='both',expand=True)
        self.canvas.bind('<Configure>',lambda e:app.schedule_render())
        self.canvas.bind('<MouseWheel>',self.wheel)
        self.canvas.bind('<Button-4>',lambda e:self.wheel(e,1.2))
        self.canvas.bind('<Button-5>',lambda e:self.wheel(e,1/1.2))
        self.canvas.bind('<ButtonPress-2>',self.pan_press);self.canvas.bind('<B2-Motion>',self.pan_move)
        self.canvas.bind('<ButtonRelease-2>',lambda e:setattr(self,'pan_origin',None))
        self.canvas.bind('<Motion>',self.motion)
        self.canvas.bind('<ButtonPress-1>',self.left_press);self.canvas.bind('<B1-Motion>',self.left_move)
        self.canvas.bind('<ButtonRelease-1>',self.left_release)

    @property
    def camera(self) -> Viewport:return self.canvas.view_state

    def zoom(self,factor: float,x: float | None=None,y: float | None=None) -> None:
        if self.plot is None or self.app.drag_plot is not None:return
        self.camera.zoom_at(factor,self.canvas.winfo_width()/2 if x is None else x,self.canvas.winfo_height()/2 if y is None else y)
        self.draw()

    def wheel(self,event: Any,factor: float | None=None) -> str:
        if factor is None:factor=1.2**max(-5.,min(5.,event.delta/120))
        self.zoom(factor,event.x,event.y);return 'break'

    def pan_press(self,event: Any) -> str:
        if self.app.drag_plot is None:self.pan_origin=(event.x,event.y)
        return 'break'

    def pan_move(self,event: Any) -> str:
        if self.pan_origin is not None and self.plot is not None:
            x,y=self.pan_origin;self.camera.pan(event.x-x,event.y-y);self.pan_origin=(event.x,event.y);self.draw()
        return 'break'

    def left_press(self,event: Any) -> None:
        if self.kind=='scene' and self.app.map_edit.get():self.app.map_press(event)
        else:self.pan_press(event)

    def left_move(self,event: Any) -> None:
        if self.kind=='scene' and self.app.map_edit.get():self.app.map_drag(event)
        else:self.pan_move(event)

    def left_release(self,event: Any) -> None:
        if self.kind=='scene' and self.app.map_edit.get():self.app.map_release(event)
        self.pan_origin=None

    def fit(self) -> None:
        self.camera.home=None;self.draw()

    def restore(self) -> None:
        self.camera.restore();self.draw()

    def motion(self,event: Any) -> None:
        if self.plot is not None:
            x,y=self.plot.unxy(event.x,event.y);self.hover.set(f'光标 X={x:.3f} mm，Y={y:.3f} mm · 等比例 · 滚轮缩放/中键平移')

    def draw(self) -> None:
        c=self.canvas
        if not c.winfo_exists() or not c.winfo_ismapped():return
        c.begin()
        try:
            result=self.app.result
            if self.kind=='scene':
                if result is None:
                    project=self.app.collect();project.check_structure()
                    a,b,d,e=project.route.field
                    if a>=b or d>=e:raise ProjectError('场地边界须满足最小值小于最大值。')
                    self.plot=plotting.route_edit(c,project,self.app.waypoints.selected(),
                        auxiliary_selected=self.app.auxiliary.editor.selected(),mode=self.app.map_mode.get())
                else:
                    self.plot=plotting.scene(c,result,min(int(self.app.frame.get()),len(result.path)-1),
                        selected=self.app.waypoints.selected(),auxiliary_selected=self.app.auxiliary.editor.selected())
                self.scene_detail()
            elif result is not None and len(result.profile):
                self.plot=plotting.cam(c,result);self.cam_detail()
            else:
                self.plot=None;c.create_text(20,30,anchor='nw',text='尚无当前有效凸轮，请重新计算。')
                self.detail.set('输入已更改或尚未计算：压力角、接触、间隙未验证。')
            self.scale_text.set(f'{self.camera.zoom*100:.0f}%')
        except (ProjectError,ValueError) as exc:
            self.plot=None;c.create_text(20,50,anchor='nw',text=f'请检查输入：{exc}')
            self.detail.set('输入无效；当前结果未验证。')
        finally:c.end()

    def scene_detail(self) -> None:
        editor=self.app.map_editor();selected=editor.selected()
        if not selected:self.detail.set('蓝圆=比赛必过点；紫菱=辅助点。辅助不计打卡；图形缩放不改变坐标。');return
        i=selected[0];point=editor.document.points[i]
        if editor is self.app.auxiliary.editor:extra=f'所属区间 {point[2]:g}，不计入打卡'
        else:
            r=self.app.result
            row=None if r is None or i>=len(r.checkpoint_rows) or not len(r.profile) else r.checkpoint_rows[i]
            if row is None:extra='打卡偏差未验证（需要当前有效计算）'
            else:
                assert r is not None
                extra=f"名义偏差 {row['error_mm']:.3f} mm / 容差 {r.project.route.tolerance:g} mm"
        self.detail.set(f'{self.app.map_mode.get()} {i+1}：X={point[0]:.3f}，Y={point[1]:.3f} mm；{extra}')

    def cam_detail(self) -> None:
        r=self.app.result;plot=self.plot
        if r is None or plot is None:return
        requested=self.app.detail_angle
        if requested is None:
            i=min(int(self.app.frame.get()),len(r.drive_s)-1)
            requested=float(r.drive_s[i]/(math.pi*r.project.vehicle.drive_diameter*r.project.cam.ratio)*360)
        k=int(np.argmin(abs(np.degrees(r.cam_angle)-requested)))
        center=r.pitch[k];plot.circle(float(center[0]),float(center[1]),r.project.mechanism.roller,'#159c8c')
        plot.canvas.create_oval(*[v for pt in (plot.xy(-2/plot.sx,2/plot.sy),plot.xy(2/plot.sx,-2/plot.sy)) for v in pt],fill='#25334c',tags='rotation_center')
        plot.label(0,0,'旋转中心')
        pressure='未验证' if k>=len(r.pressure) or not math.isfinite(float(r.pressure[k])) else f'{r.pressure[k]:.3f}°'
        gap='未验证'
        if r.geometry and k<len(r.geometry['gaps']):
            nearest=r.geometry['nearest'][k];seg=int(r.geometry['segments'][k]);value=float(r.geometry['gaps'][k])
            gap=f'{value:.5f} mm（主端采样）'
            plot.line(np.array([center,nearest]),'#df9131',2)
            x,y=plot.xy(float(nearest[0]),float(nearest[1]))
            plot.canvas.create_oval(x-4,y-4,x+4,y+4,fill='#c42b41',outline='white',tags='cam_contact')
            if value<0:
                pts=r.profile[[seg%len(r.profile),(seg+1)%len(r.profile)]]
                coords=[v for x,y in pts for v in plot.xy(float(x),float(y))]
                plot.canvas.create_line(*coords,fill='#c42b41',width=4,tags='interference_segment')
        self.detail.set(f'驱动角 {math.degrees(r.cam_angle[k]):.3f}°（最近样本） · 压力角 {pressure} · 间隙 {gap} · 动力学/实车未验证')
