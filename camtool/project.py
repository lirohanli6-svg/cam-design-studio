from __future__ import annotations

import csv
import io
import json
import math
import os
import tempfile
from dataclasses import asdict, dataclass, field as data_field
from pathlib import Path
from typing import Any


class ProjectError(ValueError):
    """Invalid input; optional field keys let the UI locate it without report parsing."""
    def __init__(self,message: str,*,fields: tuple[str,...] = ()) -> None:
        super().__init__(message);self.fields=fields


@dataclass
class Vehicle:
    wheelbase: float = 120.0
    track: float = 160.0
    front_diameter: float = 30.0
    drive_diameter: float = 130.0
    passive_diameter: float = 130.0
    sensor_x: float = 60.0
    sensor_y: float = 0.0
    drive_side: str = "right"
    speed: float = 80.0
    max_steer: float = 40.0


@dataclass
class Mechanism:
    kind: str = "flat"
    side: str = "left"
    arm: float = 25.0
    arm_angle: float = 0.0
    pivot_x: float = -25.0
    link: float = 80.0
    branch: int = 1
    offset: float = 0.0
    roller: float = 2.0
    min_stroke: float = -15.0
    max_stroke: float = 15.0
    steer_zero: float = 0.0


@dataclass
class Cam:
    base_radius: float = 40.0
    neutral_lift: float = 15.0
    hole_diameter: float = 6.0
    thickness: float = 4.0
    ratio: float = 16.0
    direction: int = 1
    phase: float = 0.0
    points: int = 1200
    pressure_limit: float = 45.0
    reference_layers: bool = True


@dataclass
class Route:
    field: list[float] = data_field(default_factory=lambda: [0.0, 5000.0, 0.0, 2000.0])
    waypoints: list[list[float]] = data_field(default_factory=lambda: [
        [300.0, 400.0], [1100.0, 400.0], [2100.0, 850.0],
        [3300.0, 600.0], [4400.0, 700.0]])
    obstacles: list[list[float]] = data_field(default_factory=list)
    start_x: float = 300.0
    start_y: float = 400.0
    heading: float | None = 0.0
    tolerance: float = 20.0
    tension: float = 0.75
    step: float = 5.0
    tangent_offsets: list[float] = data_field(default_factory=list)
    # [effective segment index (zero based), X, Y]; soft shape guides, never visits.
    auxiliary_points: list[list[float]] = data_field(default_factory=list)
    steer_rate_limit: float = 120.0
    start_steer: float = 0.0


@dataclass
class Optimization:
    iterations: int = 2
    smooth_weight: float = 1.0
    length_weight: float = 1.0
    pressure_weight: float = 1.0


@dataclass
class Project:
    name: str = "演示项目 · 尺寸须核对"
    demo_mode: bool = True
    vehicle: Vehicle = data_field(default_factory=Vehicle)
    mechanism: Mechanism = data_field(default_factory=Mechanism)
    cam: Cam = data_field(default_factory=Cam)
    route: Route = data_field(default_factory=Route)
    optimization: Optimization = data_field(default_factory=Optimization)

    @classmethod
    def demo(cls) -> Project:
        return cls()

    def to_dict(self) -> dict[str, Any]:
        return {"format": "solar-cam-project", "version": 1, **asdict(self)}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Project:
        if data.get("format") != "solar-cam-project" or data.get("version") != 1:
            raise ProjectError("项目文件格式或版本不支持。")
        try:
            project = cls(name=data["name"], demo_mode=data["demo_mode"],
                       vehicle=Vehicle(**data["vehicle"]), mechanism=Mechanism(**data["mechanism"]),
                       cam=Cam(**data["cam"]), route=Route(**data["route"]),
                       optimization=Optimization(**data["optimization"]))
            project.check_structure()
            return project
        except (KeyError, TypeError) as exc:
            raise ProjectError(f"项目文件缺少参数或参数名称有误：{exc}") from exc

    def check_structure(self) -> None:
        def number(value: Any) -> bool:
            return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
        if not isinstance(self.name, str) or not isinstance(self.demo_mode, bool):
            raise ProjectError("项目名称或演示标记格式错误。")
        for group_name, group, default in [("vehicle",self.vehicle, Vehicle()), ("mechanism",self.mechanism, Mechanism()),
                               ("cam",self.cam, Cam()), ("optimization",self.optimization, Optimization())]:
            for key, expected in vars(default).items():
                value = getattr(group, key)
                valid = isinstance(value, str) if isinstance(expected, str) else (
                    isinstance(value, bool) if isinstance(expected, bool) else number(value))
                if not valid:
                    raise ProjectError(f"项目字段 {group_name}.{key} 格式错误，须为有限数值或有效选项。",fields=(f"{group_name}.{key}",))
        if self.vehicle.drive_side not in ("left", "right") or self.mechanism.kind not in ("flat", "ball") or self.mechanism.side not in ("left", "right"):
            raise ProjectError("项目中的机构或驱动侧不支持。")
        if self.cam.direction not in (-1, 1) or self.mechanism.branch not in (-1, 1):
            raise ProjectError("项目中的旋向或连杆分支不支持。")
        r = self.route
        for key in ("start_x", "start_y", "tolerance", "tension", "step", "steer_rate_limit", "start_steer"):
            if not number(getattr(r, key)):
                raise ProjectError(f"路线字段 {key} 格式错误。",fields=(f"route.{key}",))
        if r.heading is not None and not number(r.heading):
            raise ProjectError("项目中的起跑方向格式错误。",fields=('route.heading',))
        for field_key, values, width in [("route.waypoints",r.waypoints, 2), ("route.obstacles",r.obstacles, 3), ("route.auxiliary_points",r.auxiliary_points, 3)]:
            if not isinstance(values, list) or any(not isinstance(row, list) or len(row) != width
                 or not all(number(x) for x in row) for row in values):
                raise ProjectError("项目中的打卡点或障碍物行格式错误。",fields=(field_key,))
        if not isinstance(r.field, list) or len(r.field) != 4 or not all(number(x) for x in r.field):
            raise ProjectError("项目场地边界格式错误。",fields=('bounds.0','bounds.1','bounds.2','bounds.3'))
        if not isinstance(r.tangent_offsets, list) or not all(number(x) for x in r.tangent_offsets):
            raise ProjectError("项目路线调整格式错误。",fields=('route.tangent_offsets',))

    def save(self, path: str | Path) -> None:
        target = Path(path)
        # Replace atomically so an interrupted save preserves the preceding project.
        text = json.dumps(self.to_dict(), ensure_ascii=False, indent=2, allow_nan=False)
        temp: str | None = None
        try:
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=target.parent,
                                             suffix=".tmp", delete=False) as stream:
                temp = stream.name
                stream.write(text)
            os.replace(temp, target)
        finally:
            if temp is not None and os.path.exists(temp):
                os.unlink(temp)

    @classmethod
    def load(cls, path: str | Path) -> Project:
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
            if not isinstance(data, dict):
                raise ProjectError("项目文件内容必须是一个项目对象。")
            return cls.from_dict(data)
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise ProjectError("项目文件不是有效的 UTF-8 JSON。") from exc


def read_waypoints(path: str | Path) -> list[list[float]]:
    try:
        text = Path(path).read_text(encoding="utf-8-sig")
    except UnicodeDecodeError:
        text = Path(path).read_text(encoding="gb18030")
    rows = list(csv.reader(io.StringIO(text)))
    result: list[list[float]] = []
    for index, row in enumerate(rows):
        if not row or all(not value.strip() for value in row):
            continue
        if index == 0 and [v.strip().lower() for v in row[:2]] == ["x", "y"]:
            continue
        try:
            if len(row) != 2:
                raise ValueError
            point = [float(row[0]), float(row[1])]
            if not all(math.isfinite(v) for v in point):
                raise ValueError
            result.append(point)
        except ValueError as exc:
            raise ProjectError(f"CSV 第 {index + 1} 行应为两列有限数值 x,y（毫米）。") from exc
    if len(result) < 2:
        raise ProjectError("至少需要两个打卡点。")
    return result
