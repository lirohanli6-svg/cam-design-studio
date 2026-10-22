from __future__ import annotations

import copy
import math
from dataclasses import dataclass, field
from typing import Any, Callable

import numpy as np
from numpy.typing import NDArray

from .project import Project, ProjectError
from .route_shape import required_nodes, sample_route
from .cam_geometry import contact_evidence
from .route_metrics import ordered_deviations

Array = NDArray[np.float64]


@dataclass
class Check:
    name: str
    status: str
    detail: str


@dataclass
class Result:
    project: Project
    path: Array
    heading: Array
    rear: Array
    rear_s: Array
    drive_s: Array
    delta: Array
    stats: dict[str, float]
    checks: list[Check]
    stroke: Array = field(default_factory=lambda: np.empty(0))
    cam_angle: Array = field(default_factory=lambda: np.empty(0))
    cam_delta: Array = field(default_factory=lambda: np.empty(0))
    pitch: Array = field(default_factory=lambda: np.empty((0, 2)))
    profile: Array = field(default_factory=lambda: np.empty((0, 2)))
    pressure: Array = field(default_factory=lambda: np.empty(0))
    pivot: Array = field(default_factory=lambda: np.zeros(2))
    contact_centers: Array = field(default_factory=lambda: np.empty((0, 2)))
    target_path: Array = field(default_factory=lambda: np.empty((0, 2)))

    geometry: dict = field(default_factory=dict)
    precision: dict = field(default_factory=dict)
    candidate_audit: dict = field(default_factory=dict)
    sampling: dict[str, Any] = field(default_factory=dict)
    checkpoint_rows: list[dict[str, Any]] = field(default_factory=list)

    @property
    def valid(self) -> bool:
        return not any(c.status == "fail" for c in self.checks)


def validate(p: Project) -> None:
    p.check_structure()
    v, r, m, c, o = p.vehicle, p.route, p.mechanism, p.cam, p.optimization
    try:
        p.to_dict()  # JSON is separately checked by project persistence.
        groups = (vars(v), vars(m), vars(c), vars(o))
        for group in groups:
            for value in group.values():
                if not isinstance(value, (str, bool)) and not math.isfinite(float(value)):
                    raise ProjectError("尺寸和设置必须为有限数值。")
        if any(getattr(v, k) <= 0 for k in ("wheelbase", "track", "front_diameter",
                                           "drive_diameter", "passive_diameter", "speed")):
            raise ProjectError("轴距、轮距、轮径和速度必须大于零。",fields=tuple('vehicle.'+k for k in ('wheelbase','track','front_diameter','drive_diameter','passive_diameter','speed') if getattr(v,k)<=0))
        if v.sensor_x <= 0:
            raise ProjectError("本版传感器必须在后轴前方（传感器 X > 0）。",fields=('vehicle.sensor_x',))
        if v.drive_side not in ("left", "right") or m.kind not in ("flat", "ball") or m.side not in ("left", "right"):
            raise ProjectError("驱动侧或机构选项不支持。")
        if not 0 < v.max_steer < 85 or not 0 < c.pressure_limit < 85:
            raise ProjectError("转向角/压力角上限应在 0° 到 85° 之间。",fields=tuple(k for k,x in [('vehicle.max_steer',v.max_steer),('cam.pressure_limit',c.pressure_limit)] if not 0<x<85))
        if min(m.arm, m.link, m.roller, c.base_radius, c.thickness, c.ratio) <= 0:
            raise ProjectError("臂长、连杆、接触半径、基圆、厚度和传动比必须大于零。",fields=tuple(k for k,x in [('mechanism.arm',m.arm),('mechanism.link',m.link),('mechanism.roller',m.roller),('cam.base_radius',c.base_radius),('cam.thickness',c.thickness),('cam.ratio',c.ratio)] if x<=0))
        if c.neutral_lift < 0 or not 0 <= c.hole_diameter < 2*c.base_radius:
            raise ProjectError("中位抬升量不能为负，孔径应小于基圆直径。",fields=('cam.neutral_lift','cam.hole_diameter','cam.base_radius'))
        if m.min_stroke >= m.max_stroke or m.branch not in (-1, 1) or c.direction not in (-1, 1):
            raise ProjectError("行程上下限、连杆分支或凸轮旋向设置不正确。",fields=('mechanism.min_stroke','mechanism.max_stroke','mechanism.branch','cam.direction'))
        if int(c.points) != c.points or not 360 <= c.points <= 4000:
            raise ProjectError("廓线点数应为 360～4000 的整数。",fields=('cam.points',))
        if int(o.iterations) != o.iterations or not 1 <= o.iterations <= 6:
            raise ProjectError("优化轮数应为 1～6 的整数。",fields=('optimization.iterations',))
        if min(o.smooth_weight, o.length_weight, o.pressure_weight) < 0:
            raise ProjectError("优化权重不能为负。",fields=tuple('optimization.'+k for k in ('smooth_weight','length_weight','pressure_weight') if getattr(o,k)<0))
        if not 1 <= r.step <= 20 or not 0.2 <= r.tension <= 1.5 or r.tolerance < 0:
            raise ProjectError("采样步长应为 1～20 mm，路线张力 0.2～1.5，容差不能为负。",fields=tuple(k for k,ok in [('route.step',1<=r.step<=20),('route.tension',.2<=r.tension<=1.5),('route.tolerance',r.tolerance>=0)] if not ok))
        for value in (r.start_x, r.start_y, r.tolerance, r.tension, r.step):
            if not math.isfinite(value):
                raise ProjectError("起点及路线设置必须是有限数值。")
        if r.heading is not None and not math.isfinite(r.heading):
            raise ProjectError("方向角必须为有限数值或留空。",fields=('route.heading',))
        bounds = np.asarray(r.field, dtype=float)
        points = np.asarray(r.waypoints, dtype=float)
        if bounds.shape != (4,) or not np.isfinite(bounds).all() or bounds[0] >= bounds[1] or bounds[2] >= bounds[3]:
            raise ProjectError("场地输入顺序为 xmin,xmax,ymin,ymax，最大值必须大于最小值。",fields=('bounds.0','bounds.1','bounds.2','bounds.3'))
        if points.ndim != 2 or points.shape[1] != 2 or not 2 <= len(points) <= 200 or not np.isfinite(points).all():
            raise ProjectError("打卡点应为 2～200 行，每行两列有限坐标。",fields=('route.waypoints',))
        if np.any(np.linalg.norm(np.diff(points, axis=0), axis=1) < 0.01):
            raise ProjectError("相邻打卡点不能重合；若起点即第一点，只填一次即可。",fields=('route.waypoints',))
        start = np.array([r.start_x, r.start_y])
        effective = np.vstack([start, points]) if np.linalg.norm(points[0]-start) > 0.01 else points
        length = float(np.sum(np.linalg.norm(np.diff(effective, axis=0), axis=1)))
        if length > 200000:
            raise ProjectError("路线过长，请检查毫米单位。",fields=('route.waypoints','route.start_x','route.start_y'))
        if length/r.step > 50000:
            raise ProjectError("采样点过多；请增大采样步长或缩短路线。",fields=('route.step','route.waypoints'))
        if any(not math.isfinite(x) or abs(x) > 60 for x in r.tangent_offsets):
            raise ProjectError("路线局部切线调整必须为 ±60° 内的有限数值。",fields=('route.tangent_offsets',))
        for obstacle in r.obstacles:
            if len(obstacle) != 3 or not all(math.isfinite(x) for x in obstacle) or obstacle[2] <= 0:
                raise ProjectError("每个障碍物输入 x,y,半径，半径应大于零。",fields=('route.obstacles',))
        if not 0 < r.steer_rate_limit <= 10000:
            raise ProjectError("转向变化速度上限须为 0～10000°/s 内的正数。",fields=('route.steer_rate_limit',))
        if abs(r.start_steer)>v.max_steer:
            raise ProjectError("起跑前轮角超出车辆限位。",fields=('route.start_steer','vehicle.max_steer'))
        if len(r.auxiliary_points) > 40:
            raise ProjectError("辅助点最多 40 个。",fields=('route.auxiliary_points',))
        segments = len(effective)-1
        for segment,x,y in r.auxiliary_points:
            if int(segment)!=segment or not 0 <= segment < segments:
                raise ProjectError("辅助点的比赛段编号无效；起点不同于首个必过点时包含起点连接段。",fields=('route.auxiliary_points',))
            if not bounds[0] <= x <= bounds[1] or not bounds[2] <= y <= bounds[3]:
                raise ProjectError("辅助点坐标必须在当前场地内。",fields=('route.auxiliary_points',))
    except (TypeError, ValueError, OverflowError) as exc:
        if isinstance(exc, ProjectError):
            raise
        raise ProjectError("参数格式错误，请检查数字、单位及项目文件。") from exc


def _initial_heading(p: Project) -> float:
    if p.route.heading is not None:
        return math.radians(p.route.heading)
    target = np.asarray(p.route.waypoints[1]) - [p.route.start_x, p.route.start_y]
    if np.linalg.norm(target) < 1e-9:
        raise ProjectError("默认方向无法确定，请填写起跑方向角。",fields=('route.heading','route.waypoints'))
    return float(np.arctan2(target[1], target[0]))


def _abort(cancelled: Callable[[], bool] | None) -> None:
    if cancelled and cancelled():
        raise ProjectError("计算已停止；未替换原方案。")


def _route(p: Project, cancelled: Callable[[], bool] | None = None) -> Array:
    k=math.tan(math.radians(p.route.start_steer))/p.vehicle.wheelbase
    sensor_tangent=_initial_heading(p)+math.atan2(p.vehicle.sensor_x*k,1-p.vehicle.sensor_y*k)
    return sample_route(p, sensor_tangent, cancelled)


def _motion(path: Array, p: Project, cancelled: Callable[[], bool] | None = None) -> tuple[Array, Array, Array, Array, Array]:
    v = p.vehicle
    heading = np.zeros(len(path))
    heading[0] = _initial_heading(p)
    rear_s = np.zeros(len(path))
    drive_s = np.zeros(len(path))
    delta = np.zeros(len(path))
    drive_y = v.track/2 * (1 if v.drive_side == "left" else -1)
    for i, D in enumerate(np.diff(path, axis=0)):
        if i % 128 == 0:
            _abort(cancelled)
        theta = heading[i]
        def f(angle: float) -> float:
            return float((-math.sin(angle)*D[0]+math.cos(angle)*D[1])/v.sensor_x)
        a = f(theta)
        b = f(theta+a/2)
        c = f(theta+b/2)
        d = f(theta+c)
        change = (a+2*b+2*c+d)/6
        mid = theta+change/2
        factor = change/(2*math.sin(change/2)) if abs(change) > 1e-8 else 1.0
        rear_step = float(D @ [math.cos(mid), math.sin(mid)])*factor+v.sensor_y*change
        drive_step = rear_step-drive_y*change
        if rear_step <= 1e-7 or drive_step <= 1e-7:
            raise ProjectError("路线需要倒行或驱动轮反转，本版只支持前进；请增加转弯空间并检查起跑朝向。",fields=('route.heading','route.waypoints'))
        heading[i+1] = theta+change
        rear_s[i+1] = rear_s[i]+rear_step
        drive_s[i+1] = drive_s[i]+drive_step
        delta[i] = math.atan(v.wheelbase*change/rear_step)
    # The integrated turning angle belongs to the interval midpoint, not its
    # left endpoint. Align it to node odometry before cam resampling; otherwise
    # the initial steering and periodic pressure peak drift with route step.
    midpoint_s = (rear_s[:-1]+rear_s[1:])/2
    interval_delta = delta[:-1].copy()
    delta = np.interp(rear_s, midpoint_s, interval_delta)
    if len(interval_delta)>1:
        delta[0] = interval_delta[0]+(rear_s[0]-midpoint_s[0])*(interval_delta[1]-interval_delta[0])/(midpoint_s[1]-midpoint_s[0])
        delta[-1] = interval_delta[-1]+(rear_s[-1]-midpoint_s[-1])*(interval_delta[-1]-interval_delta[-2])/(midpoint_s[-1]-midpoint_s[-2])
    rear = path-np.column_stack([v.sensor_x*np.cos(heading)-v.sensor_y*np.sin(heading),
                                v.sensor_x*np.sin(heading)+v.sensor_y*np.cos(heading)])
    return heading, rear, rear_s, drive_s, delta


def _scene_checks(result: Result) -> list[Check]:
    p = result.project
    r, v = p.route, p.vehicle
    xmin, xmax, ymin, ymax = r.field
    tangent = np.column_stack([np.cos(result.heading), np.sin(result.heading)])
    normal = np.column_stack([-np.sin(result.heading), np.cos(result.heading)])
    trajectories = [result.path, result.rear+v.wheelbase*tangent,
                    result.rear+v.track/2*normal, result.rear-v.track/2*normal]
    centers = np.vstack(trajectories)
    outside = bool(np.any((centers[:, 0] < xmin) | (centers[:, 0] > xmax)
                          | (centers[:, 1] < ymin) | (centers[:, 1] > ymax)))
    checks = [Check("场地边界", "fail" if outside else "pass",
                    "传感器或轮心越界；扩大转弯空间。" if outside else "传感器与三个轮心均在场地内（未含车壳包络）。")]
    hit = False
    for x, y, radius in r.obstacles:
        for trajectory in trajectories:
            start, segment = trajectory[:-1], np.diff(trajectory, axis=0)
            projection = np.clip(np.sum((np.array([x, y])-start)*segment, axis=1)
                                 /np.maximum(np.sum(segment**2, axis=1), 1e-16), 0, 1)
            distance = np.linalg.norm(np.array([x, y])-start-projection[:, None]*segment, axis=1)
            if np.min(distance) <= radius:
                hit = True
                break
    checks.append(Check("圆形障碍物", "fail" if hit else "pass",
                        "传感器/轮心轨迹与障碍物相交。" if hit else "传感器/轮心轨迹未进入障碍物（需另核对车壳和轮宽）。"))
    result.checkpoint_rows = ordered_deviations(result.path,r.waypoints,r.tolerance)
    deviations = [row["error_mm"] if row["error_mm"] is not None else math.inf for row in result.checkpoint_rows]
    result.stats["checkpoint_error"] = max(deviations)
    missed = [str(row["number"]) for row in result.checkpoint_rows if not row["passed"]]
    checks.append(Check("顺序打卡", "fail" if missed else "pass",
                        f"凸轮名义回算：{len(deviations)-len(missed)}/{len(deviations)} 个必过点在容差内；最大偏差 {max(deviations):.3f} mm；统一容差 {r.tolerance:g} mm。"+
                        ("漏点编号："+"、".join(missed)+"。" if missed else "不要求回算曲线恰好穿过圆心。")))
    if not len(result.profile):
        checks[-1] = Check("顺序打卡","warn","未生成有效凸轮轮廓，逐点名义回算未验证；设计曲线经过点位不能作为凸轮打卡通过依据。")
    steering = float(np.degrees(np.abs(result.delta)).max())
    result.stats["max_steer"] = steering
    checks.append(Check("前轮转角", "pass" if steering <= v.max_steer else "fail",
                        f"最大 {steering:.2f}° / 限值 {v.max_steer:g}°；超限需增大转弯空间。"))
    return checks


def calculate(project: Project, cancelled: Callable[[], bool] | None = None, *, precision: bool = True) -> Result:
    _abort(cancelled)
    p = copy.deepcopy(project)
    validate(p)
    path = _route(p, cancelled)
    heading, rear, rear_s, drive_s, delta = _motion(path, p, cancelled)
    stats = {"drive_distance": float(drive_s[-1]), "rear_distance": float(rear_s[-1]),
             "sensor_distance": float(np.sum(np.linalg.norm(np.diff(path, axis=0), axis=1))),
             "wheel_turns": float(drive_s[-1]/(math.pi*p.vehicle.drive_diameter)),
             "time": float(rear_s[-1]/p.vehicle.speed)}
    result = Result(p, path, heading, rear, rear_s, drive_s, delta, stats, [])
    _cam(result, cancelled)
    _replay(result, cancelled)
    _abort(cancelled)
    result.checks.extend(_scene_checks(result))
    rate = float(np.max(np.abs(np.gradient(np.degrees(delta), rear_s/p.vehicle.speed))))
    result.stats["max_steer_rate"] = rate
    result.checks.append(Check("转向变化速度", "pass" if rate <= p.route.steer_rate_limit else "fail",
                              f"比赛段最大 {rate:.3f}°/s；规划限值 {p.route.steer_rate_limit:g}°/s（理论恒速）。"))
    if precision and len(result.profile):
        # Re-run the entire design at half route step and twice angular density.
        # Internal refinement is separate from user input range validation.
        fine_p = copy.deepcopy(p)
        fine_p.route.step = max(1., p.route.step/2)
        fine_p.cam.points = min(4000, int(result.stats["cam_points_used"])*2)
        try:
            fine = calculate(fine_p, cancelled, precision=False)
        except ProjectError as exc:
            _abort(cancelled)
            result.precision=dict(stable=False, refined=True, error=str(exc),
                                  route_step=[p.route.step,fine_p.route.step],cam_points=[p.cam.points,fine_p.cam.points])
            result.checks.append(Check("精度复算约束", "fail", f"细采样无法完成：{exc}；粗采样不能作为通过依据。"))
            return result
        if not len(fine.profile):
            result.precision=dict(stable=False,refined=True,error="细采样未生成有效轮廓")
            result.checks.append(Check("精度复算约束", "fail", "细采样未生成有效轮廓，不能比较压力/曲率/间隙，禁止按粗采样判断通过。"))
            return result
        keys = ("max_pressure", "min_curvature_radius", "min_contact_gap")
        limits = (0.5, max(.1, p.mechanism.roller*.05), .02)
        metrics = {}
        stable = True
        for key,limit in zip(keys,limits):
            coarse, refined = result.stats.get(key), fine.stats.get(key)
            difference = abs(refined-coarse) if coarse is not None and refined is not None else float("inf")
            if coarse == refined: difference = 0.
            metrics[key] = dict(coarse=coarse, fine=refined, difference=difference, tolerance=limit)
            stable = stable and difference <= limit
        names = {"压力角", "局部过切", "全局接触干涉"}
        statuses = {c.name:c.status for c in result.checks if c.name in names}
        refined_statuses = {c.name:c.status for c in fine.checks if c.name in names}
        stable = stable and statuses==refined_statuses
        coarse_points = int(result.stats["cam_points_used"])
        fine_points = int(fine.stats["cam_points_used"])
        refined = fine_p.route.step < p.route.step and fine_points > coarse_points
        result.precision = dict(stable=bool(stable and refined), refined=refined, metrics=metrics,
                                route_step=[p.route.step,fine_p.route.step], cam_points=[coarse_points,fine_points],
                                requested_cam_points=[p.cam.points,fine_p.cam.points],
                                coarse_status=statuses, fine_status=refined_statuses)
        text = "; ".join(f"{key}: {row['coarse']:.5g} → {row['fine']:.5g}，差 {row['difference']:.4g} / 稳定阈值 {row['tolerance']:.4g}" for key,row in metrics.items())
        result.checks.append(Check("主设计采样复算", "pass" if stable and refined else "warn",
                                  ("采样结果稳定；" if stable and refined else "未确认稳定，请提高精度/核对问题区间；")+text+
                                  f"。路线步长 {p.route.step:g}→{fine_p.route.step:g} mm，实际轮廓点数 {coarse_points}→{fine_points}（触顶未加密时不判稳定）；有限采样不是连续几何证明。"))
        if any(value=="fail" and statuses.get(name)!="fail" for name,value in refined_statuses.items()):
            result.checks.append(Check("精度复算约束", "fail", "细采样出现新增不通过项，禁止按粗采样通过判断；详情见主设计采样复算。"))
    return result


def follower_positions(delta: Array, p: Project) -> tuple[Array, Array, Array]:
    """Exact planar slider/link or pivoting ball geometry, in the cam plane.

    Flat: slider x=offset, steering pivot offset horizontally by pivot_x;
    the rod joins the slider center and the rotating arm pin.
    Ball: center is rigidly on the steering arm, with pivot_x measured from cam.
    Neutral mounting height is set by base + neutral lift + contact radius.
    """
    m, c = p.mechanism, p.cam
    angle0 = math.radians(m.arm_angle)
    sign = -1 if m.kind == "flat" and m.side == "right" else 1
    angle = angle0+sign*(delta-math.radians(m.steer_zero))
    radius0 = c.base_radius+c.neutral_lift+m.roller
    neutral_x = m.offset if m.kind == "flat" else m.pivot_x+m.arm*math.cos(angle0)
    if abs(neutral_x) >= radius0:
        raise ProjectError("接触中位横坐标超出中位半径；增大基圆或调整偏置/转轴位置。",fields=('cam.base_radius','cam.neutral_lift','mechanism.pivot_x')+(('mechanism.offset',) if m.kind=='flat' else ()))
    neutral_y = math.sqrt(radius0**2-neutral_x**2)
    if m.kind == "flat":
        pin_x = m.pivot_x+m.arm*np.cos(angle)
        pin_y = m.arm*np.sin(angle)
        root = m.link**2-pin_x**2
        root0 = m.link**2-(m.pivot_x+m.arm*math.cos(angle0))**2
        if root0 <= 0 or np.any(root <= 0.01):
            raise ProjectError("平推连杆无法闭合或接近死点；检查连杆长、转轴横距和转向臂尺寸。",fields=('mechanism.link','mechanism.pivot_x','mechanism.arm','mechanism.arm_angle'))
        y0 = m.arm*math.sin(angle0)+m.branch*math.sqrt(root0)
        stroke = pin_y+m.branch*np.sqrt(root)-y0
        centers = np.column_stack([np.full(len(delta), m.offset), neutral_y+stroke])
        velocity = np.column_stack([np.zeros(len(delta)), np.ones(len(delta))])
        pivot = np.array([m.offset+m.pivot_x, neutral_y-y0])
    else:
        pivot = np.array([m.pivot_x, neutral_y-m.arm*math.sin(angle0)])
        centers = pivot+m.arm*np.column_stack([np.cos(angle), np.sin(angle)])
        stroke = centers[:, 1]-neutral_y
        velocity = np.column_stack([-np.sin(angle), np.cos(angle)])
    return centers, velocity, pivot


def _interpolate_cubic(x: Array, y: Array, target: Array) -> Array:
    slopes = np.gradient(y, x)
    index = np.clip(np.searchsorted(x, target)-1, 0, len(x)-2)
    h = x[index+1]-x[index]
    t = (target-x[index])/h
    return ((2*t**3-3*t**2+1)*y[index]+(t**3-2*t**2+t)*h*slopes[index]
            +(-2*t**3+3*t**2)*y[index+1]+(t**3-t**2)*h*slopes[index+1])


def _sample_cam_steering(result: Result, points: int) -> tuple[Array, Array]:
    p = result.project
    phi_run = result.drive_s/(math.pi*p.vehicle.drive_diameter*p.cam.ratio)*2*math.pi
    phi = np.linspace(0, 2*math.pi, int(points), endpoint=False)
    run = phi <= phi_run[-1]
    steering = np.empty(len(phi))
    steering[run] = _interpolate_cubic(phi_run, result.delta, phi[run])
    # Close unused sector with matching value/slope. Use zero return endpoint
    # acceleration rather than amplifying noisy one-sided second differences.
    span = 2*math.pi-phi_run[-1]
    d1 = np.gradient(result.delta, phi_run)
    # Keep endpoint derivative matching local: extending it over the whole
    # spare sector can create a huge non-race overshoot. Settle at both ends,
    # then use a C2 rest-to-rest return between those settling intervals.
    settle = min(.15, span/4)
    end_slope, start_slope = d1[-1], d1[0]
    departure = result.delta[-1]+end_slope*settle*.2
    arrival = result.delta[0]-start_slope*settle*.2
    def hermite(u: Array, y0: float, y1: float, s0: float, s1: float, width: float) -> Array:
        m0,m1=s0*width,s1*width
        change=y1-y0
        return y0+m0*u+(10*change-6*m0-4*m1)*u**3+(-15*change+8*m0+7*m1)*u**4+(6*change-3*m0-3*m1)*u**5
    elapsed=phi[~run]-phi_run[-1]
    values=np.empty(len(elapsed))
    head=elapsed<settle; tail=elapsed>span-settle; middle=~(head|tail)
    values[head]=hermite(elapsed[head]/settle,float(result.delta[-1]),float(departure),float(end_slope),0.,settle)
    values[middle]=hermite((elapsed[middle]-settle)/(span-2*settle),float(departure),float(arrival),0.,0.,span-2*settle)
    values[tail]=hermite((elapsed[tail]-(span-settle))/settle,float(arrival),float(result.delta[0]),0.,float(start_slope),settle)
    steering[~run]=values
    return phi, steering


def _cam(result: Result, cancelled: Callable[[], bool] | None = None) -> None:
    p = result.project
    m, c, v = p.mechanism, p.cam, p.vehicle
    wheel_circumference = math.pi*v.drive_diameter
    phi_run = result.drive_s/(wheel_circumference*c.ratio)*2*math.pi
    usage = float(phi_run[-1]/(2*math.pi))
    result.stats["cam_usage"] = usage
    result.stats["cam_rpm_mean"] = usage/result.stats["time"]*60
    result.checks.append(Check("单圈容量", "pass" if usage < 0.98 else "fail",
                              f"使用 {usage*100:.1f}%；至少预留 2% 回程。传动比定义为驱动轮圈数/凸轮圈数。"))
    if usage >= 0.98:
        result.checks.append(Check("凸轮未生成", "fail", "一圈容量不足；请增大轮→凸轮减速比，禁止截取轮廓。"))
        return
    requested = int(c.points)
    count = requested
    attempts = []
    while True:
        _abort(cancelled)
        phi, steering = _sample_cam_steering(result,count)
        sensor, _, _ = _simulate_steering(result,np.r_[phi,2*math.pi],np.r_[steering,steering[0]],cancelled)
        replay_error = float(np.linalg.norm(sensor-result.path,axis=1).max())
        visits = ordered_deviations(sensor,p.route.waypoints,p.route.tolerance)
        checkpoint_error = max(row["error_mm"] if row["error_mm"] is not None else math.inf for row in visits)
        passed = checkpoint_error <= p.route.tolerance+1e-6 and replay_error <= max(p.route.tolerance,.1)
        attempts.append(dict(points=count,replay_error_mm=replay_error,checkpoint_error_mm=checkpoint_error,
                             missed=[row["number"] for row in visits if not row["passed"]],passed=passed))
        if passed or count>=4000: break
        count = min(4000,count*2)
    result.sampling = dict(requested=requested,used=count,attempts=attempts,passed=passed,
                           limit=4000,automatic=count!=requested)
    result.stats.update(cam_points_requested=float(requested),cam_points_used=float(count))
    result.checks.append(Check("凸轮采样检查","pass" if passed else "fail",
        f"请求 {requested} 点，实际 {count} 点；名义回算最大偏差 {replay_error:.3f} mm，必过点最大偏差 {checkpoint_error:.3f} mm / {p.route.tolerance:g} mm。"+
        ("已按实际回算误差自动加密，未改硬件、必过点或容差。" if count!=requested else "")+
        ("有限采样，最终DXF仍须独立检验。" if passed else "已达4000点上限仍未满足，不能按设计曲线经过点位就认定打卡通过。")))
    centers, velocities, pivot = follower_positions(steering, p)
    if m.kind == "flat":
        low, high = min(float(steering.min()), math.radians(m.steer_zero)), max(float(steering.max()), math.radians(m.steer_zero))
        grid = np.linspace(low-1e-5, high+1e-5, 401)
        sign = -1 if m.side == "right" else 1
        beta = math.radians(m.arm_angle)+sign*(grid-math.radians(m.steer_zero))
        pin_x = m.pivot_x+m.arm*np.cos(beta)
        root = m.link**2-pin_x**2
        if np.min(root) <= 0:
            reversible = False
        else:
            derivative = sign*m.arm*(np.cos(beta)+m.branch*pin_x*np.sin(beta)/np.sqrt(root))
            reversible = bool(np.all(derivative > m.arm*1e-3) or np.all(derivative < -m.arm*1e-3))
        result.checks.append(Check("机构可逆传动", "pass" if reversible else "fail",
                                  "本周期平推位移到转角单调可逆。" if reversible else
                                  "平推行程内存在死点或同位移多解，不能可靠决定转向；调整中位角、转轴或连杆。"))
    run_centers, _, _ = follower_positions(result.delta, p)
    # Stroke is follower Y displacement from the physical neutral position.
    neutral_center = follower_positions(np.array([math.radians(m.steer_zero)]), p)[0][0]
    result.stroke = run_centers[:, 1]-neutral_center[1]
    full_stroke = centers[:, 1]-neutral_center[1]
    rotation = math.radians(c.phase)-c.direction*phi
    cos, sin = np.cos(rotation), np.sin(rotation)
    pitch = np.column_stack([cos*centers[:, 0]-sin*centers[:, 1],
                             sin*centers[:, 0]+cos*centers[:, 1]])
    tangent = (np.roll(pitch, -1, axis=0)-np.roll(pitch, 1, axis=0))/2
    speed = np.linalg.norm(tangent, axis=1)
    if np.min(speed) < 1e-8:
        raise ProjectError("凸轮理论轮廓出现尖点；调整基圆、机构或路线。",fields=('cam.base_radius','mechanism.arm','mechanism.arm_angle','route.waypoints'))
    orientation = 1 if np.sum(pitch[:, 0]*np.roll(pitch[:, 1], -1)-pitch[:, 1]*np.roll(pitch[:, 0], -1)) > 0 else -1
    outward = orientation*np.column_stack([tangent[:, 1], -tangent[:, 0]])/speed[:, None]
    profile = pitch-m.roller*outward
    rotated_velocity = np.column_stack([cos*velocities[:, 0]-sin*velocities[:, 1],
                                        sin*velocities[:, 0]+cos*velocities[:, 1]])
    dot = np.abs(np.sum(outward*rotated_velocity, axis=1))/np.linalg.norm(rotated_velocity, axis=1)
    pressure = np.degrees(np.arccos(np.clip(dot, 0, 1)))
    second = np.roll(pitch, -1, axis=0)-2*pitch+np.roll(pitch, 1, axis=0)
    curvature = orientation*(tangent[:, 0]*second[:, 1]-tangent[:, 1]*second[:, 0])/speed**3
    positive = curvature[curvature > 1e-10]
    rho = float(1/np.max(positive)) if len(positive) else math.inf
    min_radius = float(np.linalg.norm(profile, axis=1).min())
    result.stats.update(max_pressure=float(pressure.max()), min_curvature_radius=rho,
                        min_profile_radius=min_radius, max_profile_radius=float(np.linalg.norm(profile, axis=1).max()),
                        stroke_min=float(full_stroke.min()), stroke_max=float(full_stroke.max()))
    result.checks.extend([
        Check("完整周期转向", "pass" if np.max(np.abs(np.degrees(steering))) <= v.max_steer else "fail",
              f"运行与回程最大转角 {np.max(np.abs(np.degrees(steering))):.2f}°；机械限位 {v.max_steer:g}°。"),
        Check("完整周期行程", "pass" if full_stroke.min() >= m.min_stroke and full_stroke.max() <= m.max_stroke else "fail",
              f"{full_stroke.min():.2f}～{full_stroke.max():.2f} mm（含余段回程）；限值 {m.min_stroke:g}～{m.max_stroke:g}。"),
        Check("压力角", "pass" if pressure.max() <= c.pressure_limit else "fail",
              f"最大 {pressure.max():.2f}° / 限值 {c.pressure_limit:g}°；增大凸轮、调整机构或放缓转向。"),
        Check("局部过切", "pass" if rho > m.roller*1.01 else "fail",
              f"理论轮廓最小凸曲率半径 {rho:.3f} mm；滚子/球半径 {m.roller:g} mm。"),
        Check("基圆与中心孔", "pass" if min_radius >= c.base_radius-0.01 and min_radius > c.hole_diameter/2 else "fail",
              f"实际最小半径 {min_radius:.2f} mm；设计保底基圆 {c.base_radius:g} mm；孔径 {c.hole_diameter:g} mm。"),
        Check("实车核对", "warn", "理想平面、纯滚动模型；弹簧、间隙、车壳包络和实车打滑须另行验证。")])
    result.cam_angle = np.r_[phi, 2*math.pi]
    result.cam_delta = np.r_[steering, steering[0]]
    result.pitch = np.vstack([pitch, pitch[0]])
    result.profile = np.vstack([profile, profile[0]])
    result.pressure = np.r_[pressure, pressure[0]]
    result.pivot = pivot
    result.contact_centers = np.vstack([centers, centers[0]])
    # Global roller-envelope interference: the local normal offset alone is
    # insufficient when a distant lobe intrudes into the roller circle.
    evidence = contact_evidence(pitch, profile, m.roller, cancelled)
    result.geometry = evidence
    gap = evidence["minimum"]
    result.stats["min_contact_gap"] = gap
    result.stats["undercut_margin"] = rho-m.roller*1.01
    interference = gap < -max(.02,m.roller*.01)
    result.checks.append(Check("全局接触干涉", "fail" if interference else "pass",
                              f"整圈最小间隙 {gap:.5f} mm，允许离散误差 {-max(.02,m.roller*.01):.4f} mm；负值为轮廓侵入滚子/球。有限采样须另做加工/装配复核。"))


def _simulate_steering(result: Result, angles: Array, steering: Array,
                       cancelled: Callable[[], bool] | None = None) -> tuple[Array, Array, Array]:
    p, v = result.project, result.project.vehicle
    drive_y = v.track/2 * (1 if v.drive_side == "left" else -1)
    phi = result.drive_s/(math.pi*v.drive_diameter*p.cam.ratio)*2*math.pi
    mid_phi = (phi[1:]+phi[:-1])/2
    delta = np.interp(mid_phi, angles, steering)
    rear = np.empty_like(result.rear)
    heading = np.empty_like(result.heading)
    rear[0], heading[0] = result.rear[0], result.heading[0]
    for i, ds in enumerate(np.diff(result.drive_s)):
        if i % 128 == 0:
            _abort(cancelled)
        k = math.tan(delta[i])/v.wheelbase
        denominator = 1-drive_y*k
        if denominator <= 0:
            raise ProjectError("凸轮回算需要驱动轮倒转，请调整路线。")
        distance = ds/denominator
        angle = distance*k
        midpoint = heading[i]+angle/2
        chord = distance*float(np.sinc(angle/(2*math.pi)))
        rear[i+1] = rear[i]+chord*np.array([math.cos(midpoint), math.sin(midpoint)])
        heading[i+1] = heading[i]+angle
    sensor = rear+np.column_stack([v.sensor_x*np.cos(heading)-v.sensor_y*np.sin(heading),
                                   v.sensor_x*np.sin(heading)+v.sensor_y*np.cos(heading)])
    return sensor, rear, heading


def _replay(result: Result, cancelled: Callable[[], bool] | None = None) -> None:
    """Forward integrate steering resampled from the generated cam schedule.

    This independently checks loss due to cam angular sampling; it is a
    nominal kinematic check, not a dynamics/contact-force simulation.
    """
    result.target_path = result.path.copy()
    if not len(result.profile):
        return
    p = result.project
    sensor, rear, heading = _simulate_steering(result,result.cam_angle,result.cam_delta,cancelled)
    error = float(np.linalg.norm(sensor-result.target_path, axis=1).max())
    result.stats["replay_error"] = error
    result.path, result.rear, result.heading = sensor, rear, heading
    result.checks.append(Check("名义转向正向回算", "pass" if error <= max(p.route.tolerance, 0.1) else "fail",
                              f"凸轮角采样转向回算与目标路线最大差 {error:.3f} mm；不含接触变形或打滑。"))


def optimize(project: Project, progress: Callable[[str], None] | None = None,
             cancelled: Callable[[], bool] | None = None) -> Result:
    """Bounded deterministic coordinate search over path tension/tangents.

    Feasibility ranks before weighted objectives; this is local search and
    does not claim a globally optimal or always feasible route.
    """
    validate(project)
    try:
        best = calculate(project, cancelled, precision=False)
    except ProjectError as initial_error:
        recovered: Result | None = None
        for offset in (0.15, -0.15, 0.3, -0.3, 0.45, -0.45, 0.6):
            if cancelled and cancelled():
                raise ProjectError("优化已停止；原项目和结果仍可使用。")
            candidate = copy.deepcopy(project)
            candidate.route.tension = min(1.5, max(0.2, project.route.tension+offset))
            if progress:
                progress(f"寻找可计算初始路线 · 张力 {candidate.route.tension:.2f}")
            try:
                recovered = calculate(candidate, cancelled, precision=False)
                break
            except ProjectError:
                continue
        if recovered is None:
            raise ProjectError(f"局部搜索未找到可计算路线，请核对几何或改动节点。初始问题：{initial_error}") from initial_error
        best = recovered
    baseline = best.stats["sensor_distance"]
    def rank(result: Result) -> tuple[int, float, float]:
        s, p = result.stats, result.project
        fails = sum(c.status == "fail" for c in result.checks)
        violation = (max(0.0, s.get("max_pressure", 180)/p.cam.pressure_limit-1)
                     +max(0., -s.get("undercut_margin", -1e6))/p.mechanism.roller
                     +max(0., -s.get("min_contact_gap", -1e6)-max(.02,p.mechanism.roller*.01))/p.mechanism.roller
                     +max(0., s["max_steer_rate"]/p.route.steer_rate_limit-1)
                     +max(0.0, s["max_steer"]/p.vehicle.max_steer-1)
                     +max(0.0, s.get("checkpoint_error", 1e6)/max(p.route.tolerance, 0.1)-1)
                     +max(0.0, s.get("stroke_max", 0)-p.mechanism.max_stroke)/p.mechanism.arm
                     +max(0.0, p.mechanism.min_stroke-s.get("stroke_min", 0))/p.mechanism.arm)
        smooth = float(np.mean(np.diff(result.delta)**2))*1e5
        objective = (p.optimization.length_weight*s["sensor_distance"]/baseline
                     +p.optimization.smooth_weight*smooth
                     +p.optimization.pressure_weight*s.get("max_pressure", 180)/90)
        return fails, violation, objective
    attempts = 0
    for cycle in range(int(project.optimization.iterations)):
        seed = best.project
        count = max(0, len(required_nodes(seed))-2)
        knots = list(range(count))
        for knob in [-1, *knots]:
            for direction in [-1, 1]:
                if cancelled and cancelled():
                    raise ProjectError("优化已停止；原项目和结果仍可使用。")
                candidate = copy.deepcopy(best.project)
                if knob == -1:
                    candidate.route.tension = min(1.5, max(0.2, candidate.route.tension+direction*0.15/(cycle+1)))
                else:
                    while len(candidate.route.tangent_offsets) < count:
                        candidate.route.tangent_offsets.append(0.0)
                    candidate.route.tangent_offsets[knob] = max(-50.0, min(50.0,
                        candidate.route.tangent_offsets[knob]+direction*8/(cycle+1)))
                attempts += 1
                if progress:
                    progress(f"优化第 {cycle+1}/{project.optimization.iterations} 轮 · 候选 {attempts}")
                try:
                    result = calculate(candidate, cancelled, precision=False)
                except ProjectError:
                    continue
                if rank(result) < rank(best):
                    best = result
    _abort(cancelled)
    best = calculate(best.project, cancelled)
    if len(best.profile):
        from .candidate_audit import audit_candidate
        audit, _ = audit_candidate(best, cancelled)
        best.candidate_audit = dict(sha256=audit.sha256, passed=audit.kinematic_pass,
                                   checks=[vars(c) for c in audit.checks])
        best.checks.append(Check("候选DXF独立检验", "pass" if audit.kinematic_pass else "fail",
                                  f"最终导出轮廓 SHA256 {audit.sha256}；从轮廓重新求接触、转角和计划段轨迹。"+
                                  "；".join(c.name+"："+c.detail for c in audit.checks if c.status=="不通过")))
    best.checks.append(Check("本地优化", "warn", f"已比较 {attempts} 个候选；采用局部张力/切线搜索，不保证全局最优。"))
    return best
