"""Independent polygon/contact/kinematic verification. Never imports engine.

Motion is recovered from the Minkowski boundary of the DXF polygon and a disk,
then intersected with the measured follower locus. Nominal data is comparison
only. No generated pitch curve, stroke or steering is used to drive motion.
"""
from __future__ import annotations

import copy
import csv
import hashlib
import json
import math
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import ezdxf
from ezdxf.entities import LWPolyline
import numpy as np
from numpy.typing import NDArray

from .project import Project, ProjectError
from .version import VERSION

Array = NDArray[np.float64]
PASS, FAIL, UNKNOWN = "通过", "不通过", "未验证"


@dataclass
class VerificationInput:
    project: Project
    pivot_x: float
    pivot_y: float
    guide_x: float
    install_angle: float
    direction: int
    start_heading: float
    run_start: float = 0.0
    run_end: float = 240.0
    angle_step: float = 1.0
    contact_tolerance: float = 0.01
    solve_min: float = -80.0
    solve_max: float = 80.0
    contact_jump: float = 10.0
    stability_mm: float = 1.0
    stability_deg: float = 0.1
    nominal_tolerance_deg: float = 0.5
    sources: dict[str, str] = field(default_factory=lambda: {
        "车辆": "设计项目继承，未独立实测", "机构": "设计项目继承，未独立实测",
        "传动": "设计项目继承，未独立实测", "装配": "按设计尺寸推算，未独立实测",
        "起跑姿态": "设计项目继承，须现场核定", "运行区间": "用户设定，须核定", "路线": "项目中的比赛要求"})

    @classmethod
    def from_project(cls, project: Project, run_end: float = 240.0) -> VerificationInput:
        p = copy.deepcopy(project)
        m, c = p.mechanism, p.cam
        beta = math.radians(m.arm_angle)
        neutral_x = m.offset if m.kind == "flat" else m.pivot_x+m.arm*math.cos(beta)
        r = c.base_radius+c.neutral_lift+m.roller
        if r <= abs(neutral_x):
            raise ProjectError("无法按设计尺寸推算安装高度，请先修正设计，再在二次检验中填写实测装配值。")
        neutral_y = math.sqrt(r*r-neutral_x*neutral_x)
        if m.kind == "flat":
            radicand = m.link*m.link-(m.pivot_x+m.arm*math.cos(beta))**2
            if radicand <= 0:
                raise ProjectError("设计连杆不能闭合，无法推算装配初值。")
            pivot_x = m.offset+m.pivot_x
            pivot_y = neutral_y-m.arm*math.sin(beta)-m.branch*math.sqrt(radicand)
        else:
            pivot_x, pivot_y = m.pivot_x, neutral_y-m.arm*math.sin(beta)
        heading = p.route.heading
        if heading is None:
            dx = p.route.waypoints[1][0]-p.route.start_x
            dy = p.route.waypoints[1][1]-p.route.start_y
            heading = math.degrees(math.atan2(dy, dx))
        return cls(p, pivot_x, pivot_y, m.offset, -c.phase, c.direction, heading, run_end=run_end)

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["project"] = self.project.to_dict()
        return {"format": "solar-cam-verification", "schema_version": 1, **value}

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> VerificationInput:
        data = dict(value)
        if data.pop("format", None) != "solar-cam-verification" or data.pop("schema_version", None) != 1:
            raise ProjectError("二次检验配置格式或版本不支持。")
        data["project"] = Project.from_dict(data["project"])
        return cls(**data)

    def validate(self) -> None:
        self.project.check_structure()
        for key, value in vars(self).items():
            if key not in ("project", "sources") and (isinstance(value, bool) or not math.isfinite(float(value))):
                raise ProjectError(f"检验参数 {key} 必须是有限数值。")
        v, m, r = self.project.vehicle, self.project.mechanism, self.project.route
        if m.kind not in ("flat", "ball"):
            raise ProjectError(f"不支持的机构 {m.kind}；仅支持平推与直接顶球。")
        if min(v.wheelbase, v.track, v.drive_diameter, m.arm, m.link, m.roller, self.project.cam.ratio) <= 0:
            raise ProjectError("轴距、轮距、驱动轮直径、臂长、连杆、接触半径及减速比必须大于零。")
        if not 0 < v.max_steer < 85 or m.min_stroke >= m.max_stroke:
            raise ProjectError("转角限位或行程上下限无效。")
        if self.direction not in (-1, 1) or m.branch not in (-1, 1):
            raise ProjectError("旋向与闭合分支只能为 +1 / -1。")
        if not 0 <= self.run_start < self.run_end <= 360:
            raise ProjectError("计划运行区间须满足 0 ≤ 起始角 < 结束角 ≤ 360°；只回算该区间的比赛路线。")
        if not 0.1 <= self.angle_step <= 10 or not 0.00001 <= self.contact_tolerance <= 0.1:
            raise ProjectError("角步长应为 0.1～10°；接触求解容差应为 0.00001～0.1 mm。")
        if not -85 < self.solve_min < self.solve_max < 85:
            raise ProjectError("搜索转角范围应位于 (-85°,85°) 内。")
        if self.solve_min > -v.max_steer or self.solve_max < v.max_steer:
            raise ProjectError("搜索转角范围必须覆盖实车正负转角限位，否则无法检验超限。")
        if min(self.contact_jump, self.stability_mm, self.stability_deg, self.nominal_tolerance_deg) <= 0:
            raise ProjectError("突跳、收敛及名义角度比较阈值须大于零。")
        if len(r.field) != 4 or r.field[0] >= r.field[1] or r.field[2] >= r.field[3] or r.tolerance < 0:
            raise ProjectError("场地边界或打卡容差无效。")
        if not 2 <= len(r.waypoints) <= 200 or any(len(o) != 3 or o[2] <= 0 for o in r.obstacles):
            raise ProjectError("打卡点数须为 2～200；障碍物必须为 x,y,正半径。")


@dataclass
class VerificationCheck:
    name: str
    status: str
    detail: str


@dataclass
class VerificationResult:
    inputs: VerificationInput
    filename: str
    sha256: str
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    checks: list[VerificationCheck] = field(default_factory=list)
    issues: list[dict[str, Any]] = field(default_factory=list)
    checkpoints: list[dict[str, Any]] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)
    angles: Array = field(default_factory=lambda: np.empty(0))
    steering: Array = field(default_factory=lambda: np.empty(0))
    contacts: Array = field(default_factory=lambda: np.empty((0, 2)))
    centers: Array = field(default_factory=lambda: np.empty((0, 2)))
    path: Array = field(default_factory=lambda: np.empty((0, 2)))
    path_angles: Array = field(default_factory=lambda: np.empty(0))
    rear: Array = field(default_factory=lambda: np.empty((0, 2)))
    heading: Array = field(default_factory=lambda: np.empty(0))
    reference: Array = field(default_factory=lambda: np.empty((0, 2)))
    nominal: Array = field(default_factory=lambda: np.empty((0, 2)))

    @property
    def kinematic_pass(self) -> bool:
        required = {"DXF 闭合、非法几何及自交", "整圈几何接触", "整圈多解", "整圈接触干涉",
                    "接触位置连续性", "整圈行程", "整圈转角限位", "整圈死点", "计划段轨迹",
                    "顺序打卡及偏差", "场地边界", "圆形障碍物", "精度收敛"}
        statuses = {c.name: c.status for c in self.checks}
        return bool(len(self.path)) and all(statuses.get(name) == PASS for name in required) and not any(c.status == FAIL for c in self.checks)

    def add(self, name: str, status: str, detail: str) -> None:
        self.checks.append(VerificationCheck(name, status, detail))

    def issue(self, angle: float | None, point: Any, reason: str) -> None:
        if len(self.issues) < 500:
            self.issues.append({"angle_deg": angle, "position_mm": None if point is None else list(map(float, point)), "reason": reason})


class UnsupportedGeometry(ProjectError):
    pass


def _cross(a: Array, b: Array) -> Array:
    return a[..., 0]*b[..., 1]-a[..., 1]*b[..., 0]


def _read_outline(path: Path) -> tuple[Array, list[str]]:
    try:
        doc = ezdxf.readfile(path)
    except Exception as exc:
        raise ProjectError(f"DXF 无法读取：{exc}") from exc
    audit = doc.audit()
    if audit.errors or audit.fixes:
        raise ProjectError("DXF 结构审计发现错误或自动修复；请另存并确认最终文件，检验器不会静默采用修复后的轮廓。")
    if doc.units != 4:
        raise UnsupportedGeometry("仅接受明确标为毫米 ($INSUNITS=4) 的 DXF；不猜测未声明的单位。")
    entities = list(doc.modelspace())
    contours = [e for e in entities if e.dxf.layer == "CAM_OUTLINE"]
    if len(contours) != 1:
        raise ProjectError(f"CAM_OUTLINE 必须恰有一条封闭轮廓，实际为 {len(contours)} 个图元。")
    e = contours[0]
    if not isinstance(e, LWPolyline):
        raise UnsupportedGeometry(f"CAM_OUTLINE 的 {e.dxftype()} 尚不支持；只支持本软件导出的直线段 LWPOLYLINE。")
    if not e.closed:
        raise ProjectError("CAM_OUTLINE 未设置闭合标志，不能把开放折线当闭合轮廓。")
    raw: Array = np.asarray(list(e.get_points("xyseb")), dtype=np.float64)
    if not np.isfinite(raw).all() or not 3 <= len(raw) <= 5000:
        raise ProjectError("轮廓应含 3～5000 个有限顶点。")
    if np.any(raw[:, 2:] != 0) or float(e.dxf.const_width) != 0:
        raise UnsupportedGeometry("轮廓含 bulge 圆弧或非零线宽，第一版不支持；请导出直线段多段线。")
    if abs(float(e.dxf.elevation)) > 1e-9 or tuple(e.dxf.extrusion) != (0.0, 0.0, 1.0):
        raise UnsupportedGeometry("仅支持 Z=0、法向 +Z 的二维轮廓，不支持倾斜或反向 OCS。")
    points = raw[:, :2]
    if np.linalg.norm(points[-1]-points[0]) < 1e-9:
        points = points[:-1]
    edges = np.roll(points, -1, axis=0)-points
    lengths = np.linalg.norm(edges, axis=1)
    if len(points) < 3 or np.any(lengths < 1e-7) or np.max(np.abs(points)) > 100000:
        raise ProjectError("轮廓存在零长度边、重复顶点或不合理坐标。")
    if abs(float(np.sum(_cross(points, np.roll(points, -1, axis=0))))) < 1e-7:
        raise ProjectError("轮廓面积为零或退化。")
    # Non-adjacent segment intersections, including touches/collinear overlap.
    for i in range(len(points)-2):
        js = np.arange(i+2, len(points))
        if i == 0:
            js = js[js != len(points)-1]
        a, b, c, d = points[i], points[i+1], points[js], points[(js+1) % len(points)]
        overlap = (np.maximum(np.minimum(a, b), np.minimum(c, d)) <= np.minimum(np.maximum(a, b), np.maximum(c, d))+1e-9).all(axis=1)
        ab, cd = b-a, d-c
        hit = overlap & (_cross(ab, c-a)*_cross(ab, d-a) <= 1e-12) & (_cross(cd, a-c)*_cross(cd, b-c) <= 1e-12)
        if np.any(hit):
            raise ProjectError(f"轮廓自交或非相邻边接触：边 {i+1} 与边 {int(js[np.flatnonzero(hit)[0]])+1}。")
    ignored = sorted({e.dxf.layer+":"+e.dxftype() for e in entities if e.dxf.layer != "CAM_OUTLINE"})
    return points, ignored


def _distance(point: Array, polygon: Array) -> tuple[float, Array, int, float]:
    edges = np.roll(polygon, -1, axis=0)-polygon
    t = np.clip(np.sum((point-polygon)*edges, axis=1)/np.sum(edges*edges, axis=1), 0, 1)
    nearest = polygon+t[:, None]*edges
    distances = np.linalg.norm(nearest-point, axis=1)
    index = int(np.argmin(distances))
    # Winding parity makes points in the cam material ineligible as disk centers.
    a, b = polygon, np.roll(polygon, -1, axis=0)
    crossing = (a[:, 1] > point[1]) != (b[:, 1] > point[1])
    valid = np.flatnonzero(crossing)
    intersections = a[valid, 0]+(point[1]-a[valid, 1])*(b[valid, 0]-a[valid, 0])/(b[valid, 1]-a[valid, 1])
    inside = int(np.count_nonzero(intersections > point[0])) % 2 == 1
    return (-1 if inside else 1)*float(distances[index]), nearest[index], index, float(t[index])


def _circles(a: Array, ra: float, b: Array, rb: float, tol: float) -> list[Array]:
    vector = b-a
    d = float(np.linalg.norm(vector))
    if d < 1e-10 or d > ra+rb+tol or d < abs(ra-rb)-tol:
        return []
    x = (ra*ra-rb*rb+d*d)/(2*d)
    h2 = ra*ra-x*x
    if h2 < -tol*max(ra, rb):
        return []
    axis = vector/d
    middle = a+x*axis
    side = math.sqrt(max(0, h2))*np.array([-axis[1], axis[0]])
    return [middle+side, middle-side]


def _locus_centers(polygon: Array, q: VerificationInput, tol: float) -> list[Array]:
    """Intersect offset edge segments / vertex circles with the follower locus.

    Global distance filtering later removes concave/occluded offset pieces.
    This includes every exterior disk contact candidate on the input polygon.
    """
    m = q.project.mechanism
    edges = np.roll(polygon, -1, axis=0)-polygon
    lengths = np.linalg.norm(edges, axis=1)
    orientation = 1 if np.sum(_cross(polygon, np.roll(polygon, -1, axis=0))) > 0 else -1
    normals = orientation*np.column_stack([edges[:, 1], -edges[:, 0]])/lengths[:, None]
    offset = polygon+m.roller*normals
    candidates: list[Array] = []
    if m.kind == "flat":
        mask = np.abs(edges[:, 0]) > 1e-10
        if np.any((~mask) & (np.abs(offset[:, 0]-q.guide_x) < 1e-9)):
            raise UnsupportedGeometry("平推导向线与轮廓的偏置直边重合，存在退化的连续接触候选；当前解析求解器未验证此配置。")
        t = np.zeros(len(edges))
        t[mask] = (q.guide_x-offset[mask, 0])/edges[mask, 0]
        mask &= (t >= -1e-10) & (t <= 1+1e-10)
        candidates.extend(offset[mask]+t[mask, None]*edges[mask])
        dx = q.guide_x-polygon[:, 0]
        near = np.abs(dx) <= m.roller+1e-10
        heights = np.sqrt(np.maximum(0, m.roller*m.roller-dx[near]**2))
        for sign in (-1, 1):
            candidates.extend(np.column_stack([np.full(len(heights), q.guide_x), polygon[near, 1]+sign*heights]))
    else:
        pivot = np.array([q.pivot_x, q.pivot_y])
        w = offset-pivot
        aa = lengths*lengths
        bb = 2*np.sum(w*edges, axis=1)
        cc = np.sum(w*w, axis=1)-m.arm*m.arm
        discriminant = bb*bb-4*aa*cc
        for sign in (-1, 1):
            t = (-bb+sign*np.sqrt(np.maximum(0, discriminant)))/(2*aa)
            mask = (discriminant >= -1e-8) & (t >= -1e-10) & (t <= 1+1e-10)
            candidates.extend(offset[mask]+t[mask, None]*edges[mask])
        distance = np.linalg.norm(polygon-pivot, axis=1)
        near = (distance <= m.arm+m.roller+tol) & (distance >= abs(m.arm-m.roller)-tol)
        if np.any(distance < 1e-9) and abs(m.arm-m.roller) < 1e-9:
            raise UnsupportedGeometry("球臂轨迹圆与轮廓顶点的接触圆重合，退化连续接触候选未验证。")
        for vertex in polygon[near]:
            candidates.extend(_circles(pivot, m.arm, vertex, m.roller, tol))
    return candidates


def _inverse(center: Array, q: VerificationInput, tol: float) -> list[tuple[float, Array]]:
    m = q.project.mechanism
    pivot = np.array([q.pivot_x, q.pivot_y])
    sign = -1 if m.kind == "flat" and m.side == "right" else 1
    pins = _circles(pivot, m.arm, center, m.link, tol) if m.kind == "flat" else [center]
    found = []
    for pin in pins:
        if m.kind == "flat" and (center[1]-pin[1])*m.branch < -tol:
            continue
        beta = math.atan2(pin[1]-pivot[1], pin[0]-pivot[0])
        difference = (math.degrees(beta)-m.arm_angle+180) % 360-180
        delta = m.steer_zero+sign*difference
        if q.solve_min-1e-7 <= delta <= q.solve_max+1e-7:
            found.append((delta, pin))
    return found


def _neutral_y(q: VerificationInput) -> float:
    m = q.project.mechanism
    beta = math.radians(m.arm_angle)
    if m.kind == "ball":
        return q.pivot_y+m.arm*math.sin(beta)
    x = q.pivot_x+m.arm*math.cos(beta)-q.guide_x
    radicand = m.link*m.link-x*x
    if radicand <= 0:
        raise ProjectError("实测安装值在机构中位不能闭合。")
    return q.pivot_y+m.arm*math.sin(beta)+m.branch*math.sqrt(radicand)


def _contact(polygon: Array, q: VerificationInput, tol: float) -> list[tuple[float, Array, Array, bool, float]]:
    roots: list[tuple[float, Array, Array, bool, float]] = []
    m = q.project.mechanism
    for center in _locus_centers(polygon, q, tol):
        inverses = _inverse(center, q, tol)
        if not inverses:
            continue
        distance, contact, index, t = _distance(center, polygon)
        # Offset primitives behind the true global envelope are not contacts.
        # The user residual tolerance must not admit tiny *penetrations* and
        # thereby turn adjacent offset-arc candidates into false extra roots.
        if abs(distance-m.roller) > tol or distance < m.roller-min(1e-9, tol*1e-4):
            continue
        for delta, pin in inverses:
            if any(abs(delta-root[0]) < 1e-6 and np.linalg.norm(center-root[1]) < 1e-5 for root in roots):
                continue
            beta = math.atan2(pin[1]-q.pivot_y, pin[0]-q.pivot_x)
            if m.kind == "ball":
                velocity = m.arm*np.array([-math.sin(beta), math.cos(beta)])
            else:
                dy = center[1]-pin[1]
                dx = pin[0]-center[0]
                derivative = math.inf if abs(dy) < 1e-8 else m.arm*(math.cos(beta)+dx*math.sin(beta)/dy)
                velocity = np.array([0., derivative])
            normal = (center-contact)/m.roller
            dead = not np.isfinite(velocity).all() or abs(float(velocity @ normal)) < m.arm*0.005
            roots.append((delta, center, contact, dead, index+t))
    return roots


def _sample(polygon: Array, q: VerificationInput, step: float, tolerance: float,
            cancelled: Callable[[], bool], progress: Callable[[str], None]) -> dict[str, Any]:
    angles = np.unique(np.r_[np.arange(0, 360, step), 360., q.run_start, q.run_end])
    steering = np.full(len(angles), np.nan)
    centers = np.full((len(angles), 2), np.nan)
    contacts = np.full((len(angles), 2), np.nan)
    dead = np.zeros(len(angles), dtype=bool)
    counts = np.zeros(len(angles), dtype=int)
    alternatives: dict[int, list[dict[str, Any]]] = {}
    for i, angle in enumerate(angles):
        if cancelled():
            raise ProjectError("二次检验已停止。")
        a = math.radians(q.install_angle+q.direction*angle)
        matrix = np.array([[math.cos(a), -math.sin(a)], [math.sin(a), math.cos(a)]])
        rotated = polygon @ matrix.T
        try:
            roots = _contact(rotated, q, tolerance)
        except UnsupportedGeometry as exc:
            raise UnsupportedGeometry(f"凸轮角 {angle:.6f}°：{exc}") from exc
        counts[i] = len(roots)
        if len(roots) > 1:
            alternatives[i] = [dict(steer_deg=root[0], center_world_mm=root[1].tolist(), contact_world_mm=root[2].tolist()) for root in roots[:8]]
        if len(roots) == 1:
            delta, center, contact, singular, _ = roots[0]
            steering[i], centers[i], contacts[i], dead[i] = delta, center, contact @ matrix, singular
        if i % 30 == 0:
            progress(f"接触反算：步长 {step:g}° · {i+1}/{len(angles)}，当前凸轮角 {angle:.2f}°")
    return dict(angles=angles, steering=steering, centers=centers, contacts=contacts, dead=dead, counts=counts, alternatives=alternatives)


def _trajectory(q: VerificationInput, samples: dict[str, Any]) -> tuple[Array, Array, Array, Array]:
    mask = (samples["angles"] >= q.run_start-1e-9) & (samples["angles"] <= q.run_end+1e-9)
    angles = samples["angles"][mask]
    deltas = samples["steering"][mask]
    if not np.isfinite(deltas).all():
        raise ProjectError("计划运行段存在无法接触或多解，不能以猜测分支回算轨迹。")
    v = q.project.vehicle
    yaw = np.empty(len(angles))
    yaw[0] = math.radians(q.start_heading)
    rear = np.empty((len(angles), 2))
    c, s = math.cos(yaw[0]), math.sin(yaw[0])
    rear[0] = [q.project.route.start_x-c*v.sensor_x+s*v.sensor_y,
               q.project.route.start_y-s*v.sensor_x-c*v.sensor_y]
    drive_y = v.track/2 if v.drive_side == "left" else -v.track/2
    for i in range(1, len(angles)):
        curvature = math.tan(math.radians((deltas[i]+deltas[i-1])/2))/v.wheelbase
        drive_ds = (angles[i]-angles[i-1])/360*math.pi*v.drive_diameter*q.project.cam.ratio
        factor = 1-drive_y*curvature
        if factor <= 1e-8:
            raise ProjectError(f"凸轮角 {angles[i]:.3f}° 要求驱动轮反转/运动奇异。")
        ds = drive_ds/factor
        turn = ds*curvature
        chord = ds if abs(turn) < 1e-12 else 2*math.sin(turn/2)/curvature
        rear[i] = rear[i-1]+chord*np.array([math.cos(yaw[i-1]+turn/2), math.sin(yaw[i-1]+turn/2)])
        yaw[i] = yaw[i-1]+turn
    path = rear+np.column_stack([v.sensor_x*np.cos(yaw)-v.sensor_y*np.sin(yaw),
                                 v.sensor_x*np.sin(yaw)+v.sensor_y*np.cos(yaw)])
    return angles, path, rear, yaw


def _point_to_segments(point: Array, line: Array) -> tuple[Array, Array]:
    edges = np.diff(line, axis=0)
    t = np.clip(np.sum((point-line[:-1])*edges, axis=1)/np.maximum(np.sum(edges*edges, axis=1), 1e-20), 0, 1)
    return np.linalg.norm(line[:-1]+t[:, None]*edges-point, axis=1), t


def _path_checks(result: VerificationResult) -> None:
    q, v, r = result.inputs, result.inputs.project.vehicle, result.inputs.project.route
    cursor = 0.0
    for number, point in enumerate(r.waypoints, 1):
        distance, t = _point_to_segments(np.array(point), result.path)
        segment = min(int(cursor), len(t)-1)
        if cursor > segment and segment < len(t):
            t[segment] = max(t[segment], cursor-segment)
            nearest = result.path[segment]+t[segment]*(result.path[segment+1]-result.path[segment])
            distance[segment] = np.linalg.norm(nearest-np.array(point))
        positions = np.arange(len(t))+t
        distance[positions < cursor-1e-9] = np.inf
        index = int(np.argmin(distance))
        deviation = float(distance[index])
        if math.isfinite(deviation):
            cursor = float(positions[index])
            angle = float(np.interp(cursor, np.arange(len(result.path_angles)), result.path_angles))
        else:
            angle = None
        status = PASS if deviation <= r.tolerance else FAIL
        result.checkpoints.append(dict(number=number, target=point, deviation_mm=deviation if math.isfinite(deviation) else None,
                                       angle_deg=angle, status=status))
        if status == FAIL:
            result.issue(angle, point, f"第 {number} 点顺序打卡偏差超过 {r.tolerance:g} mm")
    valid = all(p["status"] == PASS for p in result.checkpoints)
    result.add("顺序打卡及偏差", PASS if valid else FAIL, f"按计划段的访问顺序逐点比较；统一容差 {r.tolerance:g} mm，详见逐点表。")
    tangent = np.column_stack([np.cos(result.heading), np.sin(result.heading)])
    normal = np.column_stack([-np.sin(result.heading), np.cos(result.heading)])
    paths = [result.path, result.rear+v.wheelbase*tangent,
             result.rear+v.track/2*normal, result.rear-v.track/2*normal]
    xmin, xmax, ymin, ymax = r.field
    outside = False
    obstacle_hit = False
    for track in paths:
        bad = (track[:, 0] < xmin) | (track[:, 0] > xmax) | (track[:, 1] < ymin) | (track[:, 1] > ymax)
        if np.any(bad):
            index = int(np.flatnonzero(bad)[0])
            result.issue(float(result.path_angles[index]), track[index], "传感器或轮心越出场地边界")
            outside = True
        for x, y, radius in r.obstacles:
            distances, t = _point_to_segments(np.array([x, y]), track)
            if distances.min() <= radius:
                index = int(np.argmin(distances))
                point = track[index]+t[index]*(track[index+1]-track[index])
                angle = float(result.path_angles[index]+t[index]*(result.path_angles[index+1]-result.path_angles[index]))
                result.issue(angle, point, "传感器/轮心轨迹与圆形障碍物相交")
                obstacle_hit = True
    result.add("场地边界", FAIL if outside else PASS, "检查计划运行段的传感器与三个轮心；未含轮宽和车壳。")
    result.add("圆形障碍物", FAIL if obstacle_hit else PASS, "检查轨迹线段到圆障碍物的距离；仅覆盖传感器与轮心。")
    if len(result.reference) >= 2:
        deviations = [float(_point_to_segments(point, result.reference)[0].min()) for point in result.path]
        maximum = max(deviations)
        result.metrics["reference_path_max_error_mm"] = maximum
        result.add("原路线偏差", PASS if maximum <= r.tolerance else FAIL,
                   f"独立轨迹各采样点到输入设计路线折线的最大距离 {maximum:.4f} mm；门槛 {r.tolerance:g} mm。")
    else:
        result.add("原路线偏差", UNKNOWN, "未输入设计曲线；已比较顺序打卡要求，叠加图的打卡点连线不是设计曲线。")


def verify_dxf(filename: Path, inputs: VerificationInput, *, reference: Any = None, nominal: Any = None,
               cancelled: Callable[[], bool] | None = None,
               progress: Callable[[str], None] | None = None) -> VerificationResult:
    q = copy.deepcopy(inputs)
    q.validate()
    path = Path(filename)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    result = VerificationResult(q, str(path.resolve()), digest)
    progress = progress or (lambda text: None)
    cancelled = cancelled or (lambda: False)
    for value, attr in ((reference, "reference"), (nominal, "nominal")):
        if value is not None:
            array = np.asarray(value, dtype=float)
            if array.ndim != 2 or array.shape[1] != 2 or not np.isfinite(array).all() or len(array) > 50000:
                raise ProjectError(f"比较数据 {attr} 应为最多 50000 行的两列有限数字。")
            if attr == "nominal" and (len(array) < 2 or np.any(np.diff(array[:, 0]) <= 0)):
                raise ProjectError("名义转角比较数据的角度必须严格递增。")
            setattr(result, attr, array)
    try:
        polygon, ignored = _read_outline(path)
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ProjectError("读取期间被检文件发生变化，请重新检验。")
        result.add("DXF 闭合、非法几何及自交", PASS, f"毫米二维闭合简单多边形，{len(polygon)} 条直线段；非相邻线段交叉/相触检查通过。")
        result.metrics["ignored_non_outline_entities"] = ignored
        result.metrics["vertex_count"] = len(polygon)
        neutral = _neutral_y(q)
    except (ProjectError, ValueError, TypeError) as exc:
        result.add("DXF / 机构输入", UNKNOWN if isinstance(exc, UnsupportedGeometry) else FAIL, str(exc))
        result.issue(None, None, str(exc))
        for name in ("整圈几何接触", "整圈多解", "整圈接触干涉", "接触位置连续性", "整圈行程", "整圈转角限位", "整圈死点", "计划段轨迹", "顺序打卡及偏差", "场地边界", "圆形障碍物", "原路线偏差", "精度收敛"):
            result.add(name, UNKNOWN, "前置轮廓或机构输入无效，未执行。")
        _limitations(result)
        return result
    try:
        coarse = _sample(polygon, q, q.angle_step, q.contact_tolerance, cancelled, progress)
        fine = _sample(polygon, q, q.angle_step/2, q.contact_tolerance/5, cancelled, progress)
    except UnsupportedGeometry as exc:
        result.add("退化接触配置", UNKNOWN, str(exc))
        result.issue(None, None, str(exc))
        for name in ("整圈几何接触", "整圈多解", "整圈接触干涉", "接触位置连续性", "整圈行程", "整圈转角限位", "整圈死点", "计划段轨迹", "顺序打卡及偏差", "场地边界", "圆形障碍物", "原路线偏差", "精度收敛", "名义转角比较"):
            result.add(name, UNKNOWN, "遇到不支持的退化接触配置，未推断运动。")
        _limitations(result)
        return result
    result.angles, result.steering = fine["angles"], fine["steering"]
    result.contacts, result.centers = fine["contacts"], fine["centers"]
    result.metrics.update(full_circle_deg=[0, 360], run_interval_deg=[q.run_start, q.run_end],
                          coarse_step_deg=q.angle_step, fine_step_deg=q.angle_step/2,
                          coarse_tolerance_mm=q.contact_tolerance, fine_tolerance_mm=q.contact_tolerance/5,
                          solve_angle_interval_deg=[q.solve_min, q.solve_max], issues_cap=500)
    missing = fine["counts"] == 0
    multiple = fine["counts"] > 1
    for sample_index in np.flatnonzero(missing | multiple):
        i = int(sample_index)
        reason = ("搜索角度范围内无法找到无穿透接触；无有效接触位置，须核对安装高度/偏置/角度范围" if missing[i]
                  else f"存在 {fine['counts'][i]} 个机构接触解；未猜测分支。候选固定机构坐标：{fine['alternatives'].get(int(i), [])}")
        result.issue(float(result.angles[i]), None, reason)
    result.add("整圈几何接触", FAIL if missing.any() else PASS, f"细网格 {len(result.angles)} 个位置，无法接触 {int(missing.sum())} 处；搜索范围之外不作接触保证。")
    result.add("整圈多解", FAIL if multiple.any() else PASS, f"在指定机构转角搜索区间内查找全部候选，多解 {int(multiple.sum())} 处。")
    available = np.isfinite(result.steering)
    complete = bool(available.all())
    result.add("整圈接触干涉", PASS if complete else UNKNOWN,
               "所有保留解均复核球/滚子中心到全部轮廓线段的距离及外部位置。" if complete else "存在无法接触或多解的位置，无法宣称整圈无干涉；候选解均已作全轮廓穿透过滤。")
    discontinuities = []
    for i in range(1, len(result.angles)):
        if available[i-1] and available[i]:
            jump = float(np.linalg.norm(result.contacts[i]-result.contacts[i-1]))
            threshold = max(q.contact_jump, 4*np.linalg.norm(polygon, axis=1).max()*math.radians(result.angles[i]-result.angles[i-1]))
            if jump > threshold:
                discontinuities.append(i)
                result.issue(float(result.angles[i]), result.contacts[i], f"接触点突跳 {jump:.3f} mm，阈值 {threshold:.3f} mm（DXF 局部坐标）")
    result.add("接触位置连续性", FAIL if discontinuities else PASS if complete else UNKNOWN,
               f"相邻角采样的局部接触点突跳 {len(discontinuities)} 处；阈值至少 {q.contact_jump:g} mm，有限采样不证明连续性。")
    if available.any():
        stroke = result.centers[available, 1]-neutral
        result.metrics.update(stroke_min_mm=float(stroke.min()), stroke_max_mm=float(stroke.max()),
                              max_steer_deg=float(np.max(np.abs(result.steering[available]))))
        for name, bad, detail in [
            ("整圈行程", (stroke.min() < q.project.mechanism.min_stroke-q.contact_tolerance or stroke.max() > q.project.mechanism.max_stroke+q.contact_tolerance),
             f"已求解位置行程 {stroke.min():.4f}～{stroke.max():.4f} mm；限值 {q.project.mechanism.min_stroke:g}～{q.project.mechanism.max_stroke:g} mm。"),
            ("整圈转角限位", bool(np.max(np.abs(result.steering[available])) > q.project.vehicle.max_steer),
             f"已求解位置最大转角 {np.max(np.abs(result.steering[available])):.4f}°；限值 ±{q.project.vehicle.max_steer:g}°。"),
            ("整圈死点", bool(fine["dead"].any()), "检查接触法线方向的机构运动雅可比；阈值为臂长的 0.5%，接近零时不通过。")]:
            result.add(name, FAIL if bad else PASS if complete else UNKNOWN, detail)
        all_strokes = result.centers[:, 1]-neutral
        for sample_index in np.flatnonzero(available & ((all_strokes < q.project.mechanism.min_stroke-q.contact_tolerance) |
                                                       (all_strokes > q.project.mechanism.max_stroke+q.contact_tolerance))):
            i = int(sample_index)
            result.issue(float(result.angles[i]), result.contacts[i], f"行程 {all_strokes[i]:.4f} mm 超限；位置为 DXF 局部接触坐标")
        for sample_index in np.flatnonzero(available & (np.abs(result.steering) > q.project.vehicle.max_steer)):
            i = int(sample_index)
            result.issue(float(result.angles[i]), result.contacts[i], f"前轮角 {result.steering[i]:.4f}° 超限；位置为 DXF 局部接触坐标")
        for sample_index in np.flatnonzero(fine["dead"]):
            i = int(sample_index)
            result.issue(float(result.angles[i]), result.contacts[i], "接触或连杆传动接近死点")
    else:
        for name in ("整圈行程", "整圈转角限位", "整圈死点"):
            result.add(name, UNKNOWN, "没有唯一接触解。")
    try:
        result.path_angles, result.path, result.rear, result.heading = _trajectory(q, fine)
        result.add("计划段轨迹", PASS, f"仅从 {q.run_start:g}° 积分至 {q.run_end:g}°；驱动里程由独立填写的轮径和减速比确定，余段回程未加入比赛路线。")
        _path_checks(result)
    except ProjectError as exc:
        result.add("计划段轨迹", FAIL, str(exc))
        result.issue(None, None, str(exc))
        for name in ("顺序打卡及偏差", "场地边界", "圆形障碍物", "原路线偏差"):
            result.add(name, UNKNOWN, "计划运行段无有效的唯一运动解。")
    try:
        ca, cp, _, _ = _trajectory(q, coarse)
        if not len(result.path):
            raise ProjectError("细网格轨迹不可用。")
        comparable = np.column_stack([np.interp(ca, result.path_angles, result.path[:, j]) for j in range(2)])
        position_error = float(np.max(np.linalg.norm(cp-comparable, axis=1)))
        common_delta = np.interp(coarse["angles"], result.angles, result.steering)
        if not np.isfinite(common_delta).all() or not np.isfinite(coarse["steering"]).all():
            raise ProjectError("整圈存在未解/多解，不能确认整圈收敛。")
        delta_error = float(np.max(np.abs(common_delta-coarse["steering"])))
        new_failures = bool(np.any(fine["counts"] != 1) or fine["dead"].any())
        stable = position_error <= q.stability_mm and delta_error <= q.stability_deg and not new_failures
        result.metrics.update(stability_position_mm=position_error, stability_steer_deg=delta_error)
        result.add("精度收敛", PASS if stable else FAIL,
                   f"角步长减半、接触容差收紧至 1/5：轨迹差 {position_error:.5f}/{q.stability_mm:g} mm，公共角转角差 {delta_error:.6f}/{q.stability_deg:g}°；仍为有限采样数值稳定性检验。")
    except ProjectError as exc:
        result.add("精度收敛", UNKNOWN, str(exc)+" 已执行粗细两次求解，未以缺失值冒充稳定。")
    if len(result.nominal) >= 2:
        run_mask = (result.angles >= q.run_start) & (result.angles <= q.run_end)
        mask = run_mask & available
        if result.nominal[0, 0] > q.run_start or result.nominal[-1, 0] < q.run_end or not available[run_mask].all():
            result.add("名义转角比较", UNKNOWN, "名义比较数据未覆盖整个计划运行段，或反算解缺失。")
        else:
            difference = float(np.max(np.abs(result.steering[mask]-np.interp(result.angles[mask], result.nominal[:, 0], result.nominal[:, 1]))))
            result.metrics["nominal_max_difference_deg"] = difference
            result.add("名义转角比较", PASS if difference <= q.nominal_tolerance_deg else FAIL,
                       f"计划段最大差 {difference:.5f}°，门槛 {q.nominal_tolerance_deg:g}°；名义数据仅用于此比较，未驱动接触求解或轨迹。")
            if difference > q.nominal_tolerance_deg:
                local = np.abs(result.steering[mask]-np.interp(result.angles[mask], result.nominal[:, 0], result.nominal[:, 1]))
                index = int(np.flatnonzero(mask)[int(np.argmax(local))])
                result.issue(float(result.angles[index]), result.contacts[index], f"反算与名义转角最大差 {difference:.5f}°；位置为 DXF 局部接触坐标")
    else:
        result.add("名义转角比较", UNKNOWN, "未提供名义转角比较数据；独立几何接触和轨迹仍可完成。")
    _limitations(result)
    if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
        result.add("被检文件一致性", FAIL, "检验过程中原 DXF 文件发生改变；本报告对应先前读取的轮廓和哈希，须重新检验当前文件。")
    return result


def _limitations(result: VerificationResult) -> None:
    result.add("独立实测参数", UNKNOWN, "数据来源由用户声明、软件未核实；继承设计参数不能排除共同参数错误。当前来源："+json.dumps(result.inputs.sources, ensure_ascii=False))
    for name, detail in [
        ("弹簧压紧力及脱离接触", "需弹簧预紧力、刚度、安装方向、从动件质量、真实转速和接触力模型。"),
        ("摩擦与驱动力矩", "需摩擦系数、轴承/导向阻力、电机力矩转速曲线、传动效率和负载。"),
        ("轮胎打滑及实车性能", "需胎面/地面摩擦、载荷分配、质心、实车速度和轨迹试验；本轮未实施实车试验。"),
        ("车壳轮宽及空间干涉", "需车壳包络、轮宽、装配三维模型和独立空间干涉检查。"),
        ("连续几何严格证明", "仅对输入折线、求解范围和离散凸轮角作检查；缩小步长稳定不等于连续运动的严格证明。")]:
        result.add(name, UNKNOWN, detail)


def load_nominal_csv(path: Path) -> Array:
    with Path(path).open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    try:
        points = np.array([[float(r["cam_angle_deg"]), float(r["steer_deg"])] for r in rows])
    except (KeyError, ValueError) as exc:
        raise ProjectError("比较 CSV 必须含 cam_angle_deg 和 steer_deg 列。") from exc
    return points


def verification_report(result: VerificationResult) -> str:
    rows = [f"# 独立二次检验报告 · 软件 {VERSION}", "", f"- 生成时间（UTC）：{result.timestamp}",
            f"- 被检文件：{result.filename}", f"- SHA-256：{result.sha256}",
            "- 输入链：最终 DXF 折线 → 全局无穿透滚子/球接触 → 独立机构反解 → 独立车辆积分。",
            "- 名义转角、motion.csv、cam.csv 未作为运动输入；若提供，仅用于比较。",
            "- 问题位置：接触/行程/转角采用 DXF 局部接触坐标；多解候选明确列出固定机构坐标；打卡/边界/障碍物采用场地坐标。无法接触时没有有效接触位置。",
            "- 结论按项列出；几何运动学通过不代表实车性能保证。", "", "## 检查及判定依据", ""]
    rows += [f"- **{c.status}** · {c.name}：{c.detail}" for c in result.checks]
    rows += ["", "## 打卡偏差", "", "| 点 | 目标坐标 mm | 偏差 mm | 凸轮角 ° | 状态 |", "|---|---|---|---|---|"]
    for p in result.checkpoints:
        rows.append(f"| {p['number']} | {p['target']} | {p['deviation_mm']} | {p['angle_deg']} | {p['status']} |")
    rows += ["", "## 问题位置（最多 500 条）", ""]
    rows += [f"- 凸轮角 {i['angle_deg']}°；位置 {i['position_mm']} mm：{i['reason']}" for i in result.issues]
    rows += ["", "## 精度、范围与指标", "", "```json", json.dumps(result.metrics, ensure_ascii=False, indent=2), "```",
             "", "## 本次参数和来源快照", "", "```json", json.dumps(result.inputs.to_dict(), ensure_ascii=False, indent=2), "```"]
    return "\n".join(rows)+"\n"


def export_verification(result: VerificationResult, folder: Path, *, overwrite: bool = False) -> dict[str, Path]:
    folder = Path(folder)
    files = {"report": folder/"二次检验报告.md", "result": folder/"二次检验结果.json", "parameters": folder/"二次检验参数.json",
             "motion": folder/"独立回算轨迹.csv", "contact": folder/"独立接触转角.csv"}
    if not overwrite and any(p.exists() for p in files.values()):
        raise ProjectError("目录已有二次检验结果；请选择新目录或明确允许覆盖。")
    folder.mkdir(parents=True, exist_ok=True)
    files["report"].write_text(verification_report(result), encoding="utf-8")
    snapshot = {"software_version": VERSION, "timestamp": result.timestamp, "filename": result.filename, "sha256": result.sha256,
                "checks": [asdict(c) for c in result.checks], "issues": result.issues, "checkpoints": result.checkpoints,
                "metrics": result.metrics, "parameters": result.inputs.to_dict(),
                "reference_comparison_only": result.reference.tolist(), "nominal_comparison_only": result.nominal.tolist()}
    files["result"].write_text(json.dumps(snapshot, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    files["parameters"].write_text(json.dumps(result.inputs.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    with files["motion"].open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["cam_angle_deg", "sensor_x_mm", "sensor_y_mm", "rear_x_mm", "rear_y_mm", "heading_deg"])
        for a, point, rear, h in zip(result.path_angles, result.path, result.rear, result.heading):
            writer.writerow([a, *point, *rear, math.degrees(h)])
    with files["contact"].open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["cam_angle_deg", "steer_deg", "contact_local_x_mm", "contact_local_y_mm", "center_world_x_mm", "center_world_y_mm"])
        for a, d, contact, center in zip(result.angles, result.steering, result.contacts, result.centers):
            writer.writerow([a, d if np.isfinite(d) else "", *[v if np.isfinite(v) else "" for v in contact], *[v if np.isfinite(v) else "" for v in center]])
    return files
