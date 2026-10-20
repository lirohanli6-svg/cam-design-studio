"""Non-scale parameter sketches. Focus highlights the measurement position."""
import tkinter as tk

from .project import Project


def draw_dimensions(canvas: tk.Canvas,project: Project,focus: str,kind: str) -> None:
    canvas.delete("all")
    red,ink,muted="#cc354a","#25334c","#8a9bae"
    def color(key: str) -> str:
        return red if focus==key else ink
    def line(points: tuple[float,...],key: str,text: str,x: float,y: float) -> None:
        canvas.create_line(*points,fill=color(key),width=2 if focus==key else 1,arrow="both")
        canvas.create_text(x,y,text=text,fill=color(key),font=("Microsoft YaHei UI",8))
    canvas.create_text(8,8,text="尺寸示意（非比例）· 点击输入框高亮测量位置",anchor="nw",fill=muted,font=("Microsoft YaHei UI",8))
    if kind=="vehicle":
        v=project.vehicle
        for y,side in [(58,"left"),(118,"right")]:
            key="vehicle.drive_diameter" if side==v.drive_side else "vehicle.passive_diameter"
            canvas.create_rectangle(68,y-12,83,y+12,outline=color(key),width=3 if focus==key else 1)
        canvas.create_line(76,58,76,118,fill=ink,width=2)
        canvas.create_line(76,88,226,88,fill=muted,width=2)
        canvas.create_line(226,77,226,99,fill=color("vehicle.front_diameter"),width=5)
        canvas.create_text(230,59,text="车头 →",fill=ink,font=("Microsoft YaHei UI",8))
        sensor_y=62 if v.sensor_y>=0 else 115
        canvas.create_line(76,88,298,88,fill=muted,dash=(3,3))
        canvas.create_oval(293,sensor_y-5,303,sensor_y+5,fill="#159c8c",outline="#159c8c")
        canvas.create_text(292,sensor_y-16,text="传感器",fill="#159c8c",font=("Microsoft YaHei UI",8))
        line((76,148,226,148),"vehicle.wheelbase",f"轴距 {v.wheelbase:g}",150,163)
        line((40,58,40,118),"vehicle.track",f"轮距 {v.track:g}",50,37)
        line((76,130,298,130),"vehicle.sensor_x",f"X={v.sensor_x:g}",270,146)
        line((320,88,320,sensor_y),"vehicle.sensor_y",f"Y={v.sensor_y:g}",292,100)
        if focus in ("vehicle.max_steer","vehicle.speed","vehicle.drive_side"):
            canvas.create_line(226,88,247,71,fill=red,width=3,arrow="last")
            canvas.create_text(170,178,text=f"转角限位 ±{v.max_steer:g}°；速度 {v.speed:g} mm/s",fill=red,font=("Microsoft YaHei UI",8))
        else:
            canvas.create_text(80,178,text="后轴中点为车体原点；图中向右 +X、向上 +Y",anchor="w",fill=muted,font=("Microsoft YaHei UI",8))
    else:
        m=project.mechanism
        canvas.create_oval(58,52,138,132,outline=muted)
        canvas.create_oval(95,89,101,95,outline=ink)
        canvas.create_text(88,143,text="凸轮轴",fill=ink,font=("Microsoft YaHei UI",8))
        cx,cy=(99,43) if m.kind=="flat" else (151,68)
        arm_key="mechanism.arm"
        canvas.create_oval(cx-6,cy-6,cx+6,cy+6,outline=color("mechanism.roller"),width=3 if focus=="mechanism.roller" else 1)
        canvas.create_text(cx,cy-15,text=f"r={m.roller:g}",fill=color("mechanism.roller"),font=("Microsoft YaHei UI",8))
        px,py=250,111
        pin=(245,46) if m.kind=="flat" else (cx,cy)
        canvas.create_line(px,py,*pin,fill=color(arm_key),width=4)
        canvas.create_text(277,76,text=f"臂 {m.arm:g}",fill=color(arm_key),font=("Microsoft YaHei UI",8))
        if m.kind=="flat":
            canvas.create_line(99,28,99,150,fill=color("mechanism.offset"),dash=(3,3))
            canvas.create_line(cx,cy,*pin,fill=color("mechanism.link"),width=2)
            canvas.create_text(177,37,text=f"连杆 {m.link:g}",fill=color("mechanism.link"),font=("Microsoft YaHei UI",8))
        canvas.create_oval(px-4,py-4,px+4,py+4,fill=color("mechanism.pivot_x"))
        canvas.create_text(px,134,text="转向轴",fill=color("mechanism.pivot_x"),font=("Microsoft YaHei UI",8))
        line((99,157,250,157),"mechanism.pivot_x",f"横向位置 {m.pivot_x:g}",180,173)
        if focus in ("mechanism.arm_angle","mechanism.steer_zero","mechanism.min_stroke","mechanism.max_stroke","mechanism.branch","mechanism.side","mechanism.offset"):
            canvas.create_arc(225,86,275,136,start=70,extent=65,style="arc",outline=red,width=2)
        canvas.create_text(8,194,text=f"{'平推连杆' if m.kind=='flat' else '直接顶球'} · 中位臂角 {m.arm_angle:g}° · 前轮零位 {m.steer_zero:g}°",anchor="w",fill=ink,font=("Microsoft YaHei UI",8))
