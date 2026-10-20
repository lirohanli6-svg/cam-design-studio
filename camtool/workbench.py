"""Workbench data: timeline, diagnostics, comparison, imported data and sensitivity."""
from __future__ import annotations

import copy
import csv
import hashlib
import io
import json
import math
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

import numpy as np

from .engine import Array, Result
from .project import Mechanism, Project, ProjectError, Vehicle
from .version import VERSION


def demo_project(problem: bool = False) -> Project:
    p = Project.demo()
    p.name = "问题演示（理论案例）" if problem else "正常演示（理论案例）"
    if problem:
        p.vehicle.max_steer = 5
        p.cam.pressure_limit = 15
        p.route.obstacles = [[2100., 850., 60.]]
    return p


def time_axis(result: Result) -> Array:
    return result.rear_s/result.project.vehicle.speed


def index_at_time(result: Result, seconds: float) -> int:
    times = time_axis(result)
    i = min(len(times)-1, int(np.searchsorted(times, max(0, seconds))))
    return i-1 if i and abs(times[i-1]-seconds) < abs(times[i]-seconds) else i


def cam_degrees(result: Result) -> Array:
    return result.drive_s/(math.pi*result.project.vehicle.drive_diameter*result.project.cam.ratio)*360


def ordered_deviations(path: Array, targets: list[list[float]], tolerance: float) -> list[dict[str, Any]]:
    if len(path) < 2:
        raise ProjectError("轨迹至少需要两个采样点。")
    edges = np.diff(path, axis=0)
    lengths2 = np.sum(edges*edges, axis=1)
    cursor = 0.
    rows = []
    for number, point in enumerate(targets, 1):
        t = np.clip(np.sum((np.asarray(point)-path[:-1])*edges, axis=1)/np.maximum(lengths2,1e-20), 0, 1)
        segment = min(int(cursor), len(t)-1)
        t[segment] = max(t[segment], cursor-segment)
        positions = np.arange(len(t))+t
        distances = np.linalg.norm(path[:-1]+t[:,None]*edges-point, axis=1)
        distances[positions < cursor-1e-9] = np.inf
        index = int(np.argmin(distances))
        error = float(distances[index])
        if math.isfinite(error):
            cursor = float(positions[index])
        rows.append(dict(number=number, target=point, error_mm=error if math.isfinite(error) else None,
                         position=cursor, point=(path[index]+t[index]*edges[index]).tolist(),
                         passed=error <= tolerance))
    return rows


def checkpoint_progress(result: Result, index: int) -> tuple[int, int, float]:
    rows = ordered_deviations(result.path, result.project.route.waypoints, result.project.route.tolerance)
    passed = 0
    for row in rows:
        if not row["passed"] or row["position"] > index+1e-9:
            break
        passed += 1
    target = result.project.route.waypoints[min(passed, len(rows)-1)]
    distance = float(np.linalg.norm(result.path[index]-target))
    return passed, len(rows), distance


@dataclass
class Diagnostic:
    name: str
    status: str
    actual: str
    limit: str
    basis: str
    advice: str
    route_index: int | None = None
    angle_deg: float | None = None
    curve_kind: str = "steer"
    point: list[float] | None = None
    scope: str = "未提供可定位采样值"


def diagnostics(result: Result) -> list[Diagnostic]:
    p, v, m, c = result.project, result.project.vehicle, result.project.mechanism, result.project.cam
    phi = cam_degrees(result)
    rows = []
    for check in result.checks:
        d = Diagnostic(check.name, check.status, check.detail, "见判定依据", check.detail,
                       "核对本项参数与模型适用范围后重新计算；不能保证调整后通过。")
        route_index: int | None = None
        cam_index: int | None = None
        if check.name == "前轮转角":
            route_index = int(np.argmax(np.abs(result.delta)))
            d.actual, d.limit = f"{np.degrees(abs(result.delta[route_index])):.4f}°", f"≤ {v.max_steer:g}°"
            d.advice = "该处路线所需转角达到最大；增加转弯过渡空间或核对轴距/传感器安装位置。不可只调大机械限位来掩盖实车能力。"
        elif check.name == "完整周期转向" and len(result.cam_delta):
            cam_index = int(np.argmax(np.abs(result.cam_delta)))
            d.actual, d.limit = f"{np.degrees(abs(result.cam_delta[cam_index])):.4f}°", f"≤ {v.max_steer:g}°"
            d.advice = "最大值取自整圈转角；若在回程段，应调整回程/路线、机构或传动参数后复核，不能用比赛段正常推断整圈正常。"
        elif check.name == "压力角" and len(result.pressure):
            cam_index = int(np.argmax(result.pressure))
            d.curve_kind = "pressure"
            d.actual, d.limit = f"{result.pressure[cam_index]:.4f}°", f"≤ {c.pressure_limit:g}°"
            d.advice = "此处接触法线与从动件运动方向夹角最大。可尝试增大凸轮尺度、核对机构偏置或降低转向变化速度；每项调整后须重算并做独立二次检验。"
        elif check.name == "完整周期行程" and len(result.contact_centers):
            neutral = result.contact_centers[0,1]-result.stroke[0]
            stroke = result.contact_centers[:,1]-neutral
            violation = np.maximum(m.min_stroke-stroke, stroke-m.max_stroke)
            cam_index = int(np.argmax(violation if np.max(violation)>0 else np.abs(stroke)))
            d.curve_kind = "stroke"
            d.actual, d.limit = f"{stroke[cam_index]:.4f} mm", f"{m.min_stroke:g}～{m.max_stroke:g} mm"
            d.advice = "根据整圈位移与实测滑块/摆臂可用行程的差值，核对中位、臂长和限位；不得把尚未实测的行程扩大后当作通过。"
        elif check.name == "顺序打卡":
            cursor = 0
            errors = []
            for point in p.route.waypoints:
                distance = np.linalg.norm(result.path[cursor:]-point, axis=1)
                i = cursor+int(np.argmin(distance))
                errors.append((float(np.min(distance)),i))
                cursor = i
            error, route_index = max(errors)
            d.actual, d.limit = f"{error:.4f} mm（最近采样点）", f"≤ {p.route.tolerance:g} mm"
            d.advice = "定位到顺序约束下偏差最大的打卡点附近。核对打卡顺序、坐标原点和传感器安装值，必要时增加过渡点并提高采样精度。"
        elif check.name == "名义转向正向回算" and len(result.target_path)==len(result.path):
            replay_errors = np.linalg.norm(result.path-result.target_path,axis=1)
            route_index = int(np.argmax(replay_errors))
            d.actual,d.limit = f"{replay_errors[route_index]:.4f} mm",f"≤ {max(p.route.tolerance,.1):g} mm"
            d.advice = "此位置的凸轮角重采样回算差最大。可增加轮廓采样密度、减缓急变转向并独立检验最终 DXF；动画不能代替实车试验。"
        elif check.name == "场地边界":
            tracks = _wheel_tracks(result)
            xmin,xmax,ymin,ymax = p.route.field
            worst = -math.inf
            for path in tracks:
                extent = np.maximum.reduce([xmin-path[:,0],path[:,0]-xmax,ymin-path[:,1],path[:,1]-ymax])
                i = int(np.argmax(extent))
                if extent[i]>worst:
                    worst,route_index,d.point = float(extent[i]),i,path[i].tolist()
            d.actual,d.limit = f"最大越界量 {max(0,worst):.3f} mm", f"X[{xmin:g},{xmax:g}] Y[{ymin:g},{ymax:g}] mm"
            d.advice = "按定位的轮心/传感器坐标核对场地原点与实际边界，再调整路线；实际场地不能为让检查通过而随意放大。车壳和轮宽另需复核。"
        elif check.name == "圆形障碍物":
            clearance = math.inf
            radius_at = 0.
            for path in _wheel_tracks(result):
                edges = np.diff(path,axis=0)
                for x,y,radius in p.route.obstacles:
                    circle_center = np.array([x,y])
                    t = np.clip(np.sum((circle_center-path[:-1])*edges,axis=1)/np.maximum(np.sum(edges*edges,axis=1),1e-20),0,1)
                    distance = np.linalg.norm(path[:-1]+t[:,None]*edges-circle_center,axis=1)
                    i = int(np.argmin(distance))
                    if distance[i]-radius < clearance:
                        clearance,radius_at,route_index = float(distance[i]-radius),radius,i
                        d.point = (path[i]+t[i]*edges[i]).tolist()
            if math.isfinite(clearance):
                d.actual,d.limit = f"圆心最短距离 {clearance+radius_at:.3f} mm",f"> 障碍半径 {radius_at:g} mm"
                d.advice = "根据轨迹线段到障碍圆心的最短距离定位。核对障碍位置与半径，改变过渡路线后复算；轮宽和车壳仍未纳入。"
            else:
                d.actual,d.limit = "未输入圆障碍物","只核查已输入的障碍物"
        elif check.name == "单圈容量":
            route_index = len(result.path)-1
            d.actual,d.limit = f"{result.stats['cam_usage']*100:.3f}%", "< 98%"
            d.advice = "按驱动轮所需圈数和凸轮减速比确定占用角；核对实车传动，可尝试增大轮→凸轮减速比并重新核对轮径、路线和回程。"
        elif check.name == "基圆与中心孔" and len(result.profile):
            cam_index = int(np.argmin(np.linalg.norm(result.profile,axis=1)))
            d.actual,d.limit = f"轮廓半径 {np.linalg.norm(result.profile[cam_index]):.3f} mm",f"≥基圆 {c.base_radius:g}；>孔半径 {c.hole_diameter/2:g} mm"
            d.advice = "按最小轮廓半径核对基圆、中位抬升与孔尺寸。减小孔或改尺度须同时满足实际轴、轮毂和接触安装要求。"
        elif check.name == "局部过切" and len(result.pitch)>3:
            pitch=result.pitch[:-1]
            tangent=(np.roll(pitch,-1,axis=0)-np.roll(pitch,1,axis=0))/2
            second=np.roll(pitch,-1,axis=0)-2*pitch+np.roll(pitch,1,axis=0)
            orientation=1 if np.sum(pitch[:,0]*np.roll(pitch[:,1],-1)-pitch[:,1]*np.roll(pitch[:,0],-1))>0 else -1
            curvature=orientation*(tangent[:,0]*second[:,1]-tangent[:,1]*second[:,0])/np.maximum(np.linalg.norm(tangent,axis=1)**3,1e-20)
            cam_index=int(np.argmax(curvature))
            rho=1/float(curvature[cam_index]) if curvature[cam_index]>0 else math.inf
            d.actual,d.limit=f"最小凸曲率半径 {rho:.4f} mm",f"> {m.roller*1.01:g} mm"
            d.advice="此处离散理论轮廓曲率最紧。可尝试增加凸轮尺度、减缓轮廓变化或核对滚子半径；调整后应提高采样密度并重新检验最终轮廓。"
        elif check.name == "全局接触干涉" and len(result.pitch)>1:
            outline=result.profile[:-1]
            edges=np.roll(outline,-1,axis=0)-outline
            length2=np.sum(edges*edges,axis=1)
            minimum=math.inf
            for i,center in enumerate(result.pitch[:-1]):
                t=np.clip(np.sum((center-outline)*edges,axis=1)/np.maximum(length2,1e-20),0,1)
                closest_distance=float(np.linalg.norm(center-outline-t[:,None]*edges,axis=1).min())
                if closest_distance<minimum: minimum,cam_index=closest_distance,i
            threshold=m.roller-max(.02,m.roller*.01)
            d.actual,d.limit=f"中心到全轮廓最短距离 {minimum:.4f} mm",f"≥ {threshold:g} mm（离散容差）"
            d.advice="定位到滚子/球中心与整条轮廓最小间距的采样角。核对轮廓凹陷、接触半径及机构偏置，可改尺度或转向过渡后重算；不是连续干涉的严格证明。"
        elif check.name == "机构可逆传动" and len(result.cam_delta):
            grid=np.linspace(min(float(result.cam_delta.min()),math.radians(m.steer_zero))-1e-5,
                             max(float(result.cam_delta.max()),math.radians(m.steer_zero))+1e-5,401)
            sign=-1 if m.side=="right" else 1
            beta=math.radians(m.arm_angle)+sign*(grid-math.radians(m.steer_zero))
            px=m.pivot_x+m.arm*np.cos(beta)
            root=m.link*m.link-px*px
            if np.all(root>0):
                derivative=sign*m.arm*(np.cos(beta)+m.branch*px*np.sin(beta)/np.sqrt(root))
                i=int(np.argmin(abs(derivative)))
                cam_index=int(np.argmin(abs(result.cam_delta-grid[i])))
                d.actual,d.limit=f"最小 |dY/dδ| {abs(derivative[i]):.5f} mm/rad",f"导数同号且 |dY/dδ| > {m.arm*.001:g}"
                d.basis+=" 标记为区间扫描转角最近的凸轮采样位置，并非解析死点的精确角。"
            d.advice="检查平推连杆中位、分支和转轴位置是否跨越死点。改动机构后必须按实测装配重新核查闭合、多解和整圈行程。"
        if cam_index is not None:
            d.angle_deg = float(np.degrees(result.cam_angle[cam_index]))
            route_index = int(np.argmin(abs(phi-d.angle_deg))) if d.angle_deg<=phi[-1]+1e-9 else None
            d.scope = "比赛运行段" if route_index is not None else "整圈回程段；无比赛路线对应位置"
        elif route_index is not None:
            d.angle_deg = float(phi[route_index])
            d.scope = "比赛运行段"
        if route_index is not None:
            d.route_index = route_index
            if d.point is None:
                d.point = result.path[route_index].tolist()
        rows.append(d)
    return rows


def _wheel_tracks(result: Result) -> list[Array]:
    v = result.project.vehicle
    tangent = np.column_stack([np.cos(result.heading),np.sin(result.heading)])
    normal = np.column_stack([-np.sin(result.heading),np.cos(result.heading)])
    return [result.path,result.rear+v.wheelbase*tangent,result.rear+v.track/2*normal,result.rear-v.track/2*normal]


def comparison_metrics(result: Result | None) -> dict[str, float | None]:
    if result is None:
        return dict.fromkeys(("路线长度 mm","最大前轮角 °","最大压力角 °","最大打卡偏差 mm","不满足项数量"))
    return {"路线长度 mm":result.stats["sensor_distance"], "最大前轮角 °":result.stats["max_steer"],
            "最大压力角 °":result.stats.get("max_pressure"), "最大打卡偏差 mm":result.stats["checkpoint_error"],
            "不满足项数量":float(sum(c.status=="fail" for c in result.checks))}


@dataclass
class PlanComparison:
    original: Result | None
    optimized: Result
    original_project: Project
    original_error: str = ""

    @classmethod
    def capture(cls, original: Result | None, optimized: Result, original_project: Project | None=None, original_error: str="") -> PlanComparison:
        before=original.project if original is not None else original_project
        if before is None: raise ProjectError("原方案未计算时必须保留原项目快照。")
        return cls(copy.deepcopy(original),copy.deepcopy(optimized),copy.deepcopy(before),original_error)

    def verdict(self) -> str:
        a,b = comparison_metrics(self.original),comparison_metrics(self.optimized)
        if self.original is None:
            return f"原方案无法计算：{self.original_error}；原输入已保留，五项原指标未验证，无法判断改善。"
        changes=[]
        for key,before in a.items():
            after=b[key]
            if before is not None and after is not None: changes.append(after-before)
        better,worse = any(x < -1e-7 for x in changes),any(x > 1e-7 for x in changes)
        if better and not worse:
            return "表中指标有改善且无增大项；仍须逐项检查与实车核对。"
        if better and worse:
            return "指标存在取舍，未形成整体改善；请比较每项实际数值后选择。"
        return "表中五项指标未改善（无变化或变差）；未自动替换原方案。"

    def export(self, folder: Path) -> None:
        folder=Path(folder)
        files=[folder/"原方案.cam.json",folder/"优化方案.cam.json",folder/"优化对比.json"]
        if any(p.exists() for p in files):
            raise ProjectError("对比目录已有同名方案，请选择新的目录。")
        folder.mkdir(parents=True,exist_ok=True)
        self.original_project.save(files[0]); self.optimized.project.save(files[1])
        files[2].write_text(json.dumps({"software_version":VERSION,"verdict":self.verdict(),
            "original":comparison_metrics(self.original),"optimized":comparison_metrics(self.optimized),
            "original_error":self.original_error,"original_path":[] if self.original is None else self.original.path.tolist(),
            "optimized_path":self.optimized.path.tolist()},ensure_ascii=False,indent=2,allow_nan=False),encoding="utf-8")


@dataclass
class RouteHistory:
    undo_stack: list[dict[str, Any]] = field(default_factory=list)
    redo_stack: list[dict[str, Any]] = field(default_factory=list)

    def commit(self, previous: dict[str, Any], current: dict[str, Any]) -> None:
        if previous != current:
            self.undo_stack.append(copy.deepcopy(previous))
            self.undo_stack=self.undo_stack[-100:]
            self.redo_stack.clear()

    def undo(self, current: dict[str, Any]) -> dict[str, Any] | None:
        if not self.undo_stack: return None
        self.redo_stack.append(copy.deepcopy(current))
        return self.undo_stack.pop()

    def redo(self, current: dict[str, Any]) -> dict[str, Any] | None:
        if not self.redo_stack: return None
        self.undo_stack.append(copy.deepcopy(current))
        return self.redo_stack.pop()


@dataclass
class MeasuredTrack:
    path: Array
    time_s: Array
    metadata: dict[str,str]
    filename: str
    sha256: str


TRACK_TEMPLATE = "# unit=mm\n# frame=field_xy\n# axes=x_right_y_up\n# point=sensor\n# kind=measured\n# source=请填写真实采集设备、日期和记录编号\nt_s,x,y\n"


def read_measured_track(filename: Path) -> MeasuredTrack:
    path=Path(filename)
    if path.stat().st_size>12_000_000:
        raise ProjectError("实测轨迹 CSV 超过 12 MB；请先按实际采样要求分段。")
    raw=path.read_bytes()
    text=raw.decode("utf-8-sig")
    metadata: dict[str,str]={}
    data=[]
    for line in text.splitlines():
        if line.lstrip().startswith("#"):
            item=line.lstrip()[1:].strip()
            if "=" in item:
                key,value=item.split("=",1); metadata[key.strip()]=value.strip()
        elif line.strip(): data.append(line)
    if metadata.get("unit") not in ("mm","cm","m") or metadata.get("frame")!="field_xy" or metadata.get("axes")!="x_right_y_up" or metadata.get("point")!="sensor":
        raise ProjectError("CSV 必须声明 # unit=mm/cm/m、# frame=field_xy、# axes=x_right_y_up、# point=sensor。不会猜测单位、坐标系或测量点。")
    if not metadata.get("source") or metadata["source"].startswith("请填写"):
        raise ProjectError("实测 CSV 须以 # source=... 记录采集设备/日期/记录编号；软件不会替你生成实测数据。")
    metadata.setdefault("kind","measured")
    if metadata["kind"] not in ("measured","synthetic"):
        raise ProjectError("CSV kind 仅支持 measured（声明实测）或 synthetic（测试/理论），不会混淆两类来源。")
    rows=list(csv.DictReader(io.StringIO("\n".join(data))))
    if not 2<=len(rows)<=50000:
        raise ProjectError("实测轨迹须包含 2～50000 行。")
    try:
        scale={"mm":1.,"cm":10.,"m":1000.}[metadata["unit"]]
        points=np.asarray([[float(row["x"])*scale,float(row["y"])*scale] for row in rows],dtype=float)
        has_time="t_s" in rows[0]
        times=np.asarray([float(row["t_s"]) for row in rows],dtype=float) if has_time else np.empty(0)
    except (ValueError,KeyError,TypeError) as exc:
        raise ProjectError("CSV 数据列应为 x,y，可另带严格递增的 t_s（秒）。") from exc
    if not np.isfinite(points).all() or np.max(abs(points))>1e7 or not np.isfinite(times).all() or (len(times) and np.any(np.diff(times)<=0)):
        raise ProjectError("CSV 含非法坐标或时间；时间须严格递增。")
    return MeasuredTrack(points,times,metadata,str(path.resolve()),hashlib.sha256(raw).hexdigest())


def parameter_profile(project: Project, source: str) -> dict[str, Any]:
    project.check_structure()
    v,m=project.vehicle,project.mechanism
    if min(v.wheelbase,v.track,v.drive_diameter,v.front_diameter,v.passive_diameter,m.arm,m.link,m.roller,project.cam.ratio)<=0:
        raise ProjectError("尺寸与减速比须大于零；不能保存非法参数配置。")
    if not source.strip(): raise ProjectError("请记录参数来源；未实测时请明确写未实测。")
    return {"format":"solarcam-parameter-profile","schema_version":1,"software_version":VERSION,
            "source":source.strip(),"vehicle":asdict(project.vehicle),"mechanism":asdict(project.mechanism),
            "transmission":{k:getattr(project.cam,k) for k in ("ratio","phase","direction")}}


def apply_parameter_profile(project: Project, data: dict[str, Any]) -> Project:
    if not isinstance(data,dict) or data.get("format")!="solarcam-parameter-profile" or data.get("schema_version")!=1 or not isinstance(data.get("source"),str):
        raise ProjectError("参数配置格式/版本/来源无效。")
    try:
        p=copy.deepcopy(project)
        p.vehicle=Vehicle(**data["vehicle"]); p.mechanism=Mechanism(**data["mechanism"])
        if set(data["transmission"])!={"ratio","phase","direction"}: raise ProjectError("传动配置字段不完整。")
        for key,value in data["transmission"].items(): setattr(p.cam,key,value)
        p.check_structure()
        parameter_profile(p,data["source"])
        return p
    except (TypeError,KeyError,ValueError) as exc:
        raise ProjectError(f"参数配置错误：{exc}") from exc


def perturbation_analysis(result: Result, dxf: Path, wheel_min: float, wheel_max: float,
                          phase_min: float, phase_max: float, count: int = 3, angle_step: float = 2.,
                          cancelled: Callable[[],bool] | None = None,
                          progress: Callable[[str],None] | None = None) -> list[dict[str, Any]]:
    from .verification import VerificationInput, verify_dxf
    values=[wheel_min,wheel_max,phase_min,phase_max,angle_step]
    if not all(math.isfinite(x) for x in values) or not -30<=wheel_min<=wheel_max<=30 or not -90<=phase_min<=phase_max<=90 or not 2<=count<=7 or int(count)!=count:
        raise ProjectError("单因素扰动：轮径范围须在 ±30% 内，相位在 ±90° 内，每组 2～7 个采样值。")
    cancelled=cancelled or (lambda:False); progress=progress or (lambda text:None)
    settings=VerificationInput.from_project(result.project,float(cam_degrees(result)[-1]))
    settings.angle_step=angle_step
    jobs=[("基线",0.)]+[("轮径 %",float(v)) for v in np.unique(np.linspace(wheel_min,wheel_max,count)) if v!=0]+[("安装相位 °",float(v)) for v in np.unique(np.linspace(phase_min,phase_max,count)) if v!=0]
    outputs=[]
    for index,(kind,value) in enumerate(jobs):
        if cancelled(): raise ProjectError("扰动分析已停止。")
        q=copy.deepcopy(settings)
        if kind=="轮径 %": q.project.vehicle.drive_diameter*=1+value/100
        if kind=="安装相位 °": q.install_angle+=value
        progress(f"理论单因素扰动 {index+1}/{len(jobs)}：{kind} {value:g}；最终凸轮轮廓保持不变")
        r=verify_dxf(dxf,q,cancelled=cancelled,reference=result.target_path)
        errors=[p["deviation_mm"] for p in r.checkpoints if p["deviation_mm"] is not None]
        outputs.append({"kind":kind,"value":value,"prediction":True,"path":r.path.tolist(),
                        "max_checkpoint_error_mm":max(errors) if errors else None,
                        "failures":sum(c.status=="不通过" for c in r.checks),
                        "unknown":sum(c.status=="未验证" for c in r.checks),
                        "checks":[asdict(c) for c in r.checks],"dxf_sha256":r.sha256,
                        "step_deg":angle_step,"fine_step_deg":angle_step/2})
    return outputs
