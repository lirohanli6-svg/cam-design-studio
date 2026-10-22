from __future__ import annotations

import csv
import math
import os
import shutil
import tempfile
from pathlib import Path
from typing import Callable

import ezdxf
import numpy as np

from .engine import Result
from .markings import cam_markings
from .project import ProjectError
from .version import VERSION


def report_text(result: Result) -> str:
    labels = {"pass": "通过", "warn": "注意", "fail": "不满足"}
    p = result.project
    model = "平推滑块—连杆机构" if p.mechanism.kind == "flat" else "转轴摆臂直接顶球机构"
    rows = [f"# {p.name} · 计算检查报告", "", f"软件版本：{VERSION}；本报告是设计及名义回算，最终轮廓请另做独立二次检验。", "", f"机构模型：{model}。", "",
            "演示参数，必须用实车尺寸替换。" if p.demo_mode else "项目已标为实车参数，仍须核对和试跑。",
            "", "长度 mm；角度 °；速度为后轴中点恒定地速 mm/s。",
            "纯滚动、理想平面机构；未计算弹簧力、惯性、间隙、轮胎打滑和车壳包络。",
            "轮→凸轮传动比 = 驱动轮转数 / 凸轮转数；前轮左转为正；凸轮 +1 为逆时针。",
            "", "## 结果", ""]
    names = {"drive_distance": "驱动轮里程 mm", "rear_distance": "后轴中点里程 mm",
             "sensor_distance": "目标传感器路线长 mm", "wheel_turns": "驱动轮圈数",
             "time": "运行时间 s", "max_steer": "最大前轮转角 °", "cam_usage": "凸轮使用率（0～1）",
             "cam_rpm_mean": "凸轮平均转速 rpm", "max_pressure": "整圈最大压力角 °",
             "min_curvature_radius": "最小凸曲率半径 mm", "min_profile_radius": "轮廓最小半径 mm",
             "max_profile_radius": "轮廓最大半径 mm", "stroke_min": "整圈最小位移 mm",
             "stroke_max": "整圈最大位移 mm", "checkpoint_error": "回算顺序打卡最大偏差 mm",
             "replay_error": "名义转向回算最大差 mm", "max_steer_rate": "比赛段最大转向变化速度 °/s",
             "min_contact_gap": "整圈最小接触间隙 mm", "undercut_margin": "整圈局部过切余量 mm",
             "cam_points_requested": "请求轮廓起始点数", "cam_points_used": "实际轮廓点数"}
    rows.extend(f"- {names.get(key, key)}：{value:.5f}" for key, value in result.stats.items())
    rows.extend(["", "## 检查", ""])
    rows.extend(f"- [{labels[c.status]}] {c.name}：{c.detail}" for c in result.checks)
    if result.sampling:
        rows.extend(["", "## 采样加密与逐点打卡", "", "设计曲线经过必过点，不代表凸轮回算必然满足；名义回算通过也不能替代最终DXF接触检验。",
                     "", "|轮廓点数|名义回算最大差 mm|必过点最大偏差 mm|漏点编号|", "|---|---|---|---|"])
        rows.extend(f"|{r['points']}|{r['replay_error_mm']:.4f}|{r['checkpoint_error_mm']:.4f}|{r['missed'] or '无'}|" for r in result.sampling["attempts"])
        rows.extend(["", "|点号|目标 X,Y mm|回算偏差 mm|容差 mm|状态|", "|---|---|---|---|---|"])
        rows.extend(f"|{r['number']}|{r['target']}|{r['error_mm']:.4f}|{p.route.tolerance:g}|{'通过' if r['passed'] else '不通过'}|" for r in result.checkpoint_rows)
    rows.extend(["", "## 比赛必过点与辅助控制", "",
                 f"比赛必过点 {len(p.route.waypoints)} 个，按原顺序打卡；辅助控制点 {len(p.route.auxiliary_points)} 个，不要求曲线穿过，不计入比赛数量。",
                 "辅助点段号按起点与必过点连接顺序计数；起点与首点同坐标不重复计段。"])
    rows.extend(f"- 辅助 {i}：比赛段 {int(segment)+1}，引导坐标 ({x:g}, {y:g}) mm" for i,(segment,x,y) in enumerate(p.route.auxiliary_points,1))
    rows.extend(["", "## 凸轮出发点与旋转方向", ""])
    if len(result.profile):
        markings = cam_markings(result)
        x, y = map(float, markings.start)
        rows.extend([
            f"- 出发点 S0：驱动轮里程为 0 时的实际轮廓接触点；DXF 坐标 ({x:.3f}, {y:.3f}) mm，已包含起始相位 {p.cam.phase:g}°。",
            f"- 凸轮实体旋向：{markings.direction}；观察方向为正对软件凸轮图 / DXF 所示这一面。从另一面观察，视觉上的顺逆时针相反。",
            f"- 起跑前轮角：{math.degrees(result.delta[0]):.3f}°（左转为正、右转为负）；起跑从动件位移：{result.stroke[0]:.3f} mm。",
            "- 装车时打开「机构运动」，将进度条拖到最左，按图中起跑姿态，让标记 S0 对准滚子/球与凸轮的接触位置，再固定凸轮与传动轴。不要一律把前轮摆正。",
            "- 导出轮廓的显示相位已计入装配姿态；不要重复叠加相位。橙色箭头表示凸轮实体的转动方向，并非 CSV 轮廓采样点的排列方向。",
            "- DXF 的 START_REFERENCE 与 ROTATION_REFERENCE 是装配参考标记图层；加工外形使用 CAM_OUTLINE，参考标记不作为切割轮廓。需要刻印时单独安排标记工序。",
            "- 选择「仅轮廓与孔」时不导出参考标记，软件预览仍显示出发点和旋向。",
        ])
    else:
        rows.append("尚未生成凸轮轮廓，暂无出发点标记。")
    rows.extend(["", "## 机构坐标说明", "",
                 "平推：滑块沿凸轮坐标 Y 方向移动，横坐标为导向偏置；连杆连接滑块与转向臂销。",
                 "顶球：球心固定在转向摆臂上，随转向轴转动，凸轮直接接触球面。",
                 "基圆输入为设计保底半径；中位抬升使实际最小半径可能更大。中位安装高度由这两项计算。",
                 f"当前机构转轴在凸轮平面内约为 ({result.pivot[0]:.3f}, {result.pivot[1]:.3f}) mm。",
                 "这些是理想机构模型，并未自动识别 YDI STEP；需根据实际装配核对销、槽、连杆与球的运动。",
                 "导出采用离散多段线；加工前应核对采样精度、闭合、孔径与干涉。"])
    return "\n".join(rows)+"\n"


def export_design(result: Result, folder: Path, *, overwrite: bool = False,
                  cancelled: Callable[[], bool] | None = None) -> dict[str, Path]:
    """Stage the whole set and restore originals on ordinary IO/cancel errors.

    Five file replacements are not a power-loss transaction. Retain backups
    for manual recovery if rollback itself encounters an IO failure.
    """
    if not len(result.profile) or not np.isfinite(result.profile).all():
        raise ProjectError("当前结果没有有效轮廓，不能导出；请先调整参数重新计算。")
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    names = ("cam.dxf", "motion.csv", "cam.csv", "检查报告.md", "project.cam.json")
    def stop() -> None:
        if cancelled and cancelled():
            raise ProjectError("导出已停止；已有结果未替换或已恢复。")
    stop()
    if not overwrite and any((folder/name).exists() for name in names):
        raise ProjectError("导出目录已有同名结果，请选择新目录或明确允许覆盖。")
    stage = Path(tempfile.mkdtemp(prefix=".solarcam-export-", dir=folder))
    keep_backup = False
    replaced: list[Path] = []
    try:
        staged = _write_design(result, stage)
        stop()
        backup = stage/"backup"
        backup.mkdir()
        for name in names:
            target = folder/name
            if target.exists():
                if not overwrite:
                    raise ProjectError("导出期间出现同名文件，请重新选择目录。")
                if not target.is_file():
                    raise ProjectError(f"目标 {name} 不是文件，请选择其他目录。")
                shutil.copy2(target, backup/name)
        try:
            for name in names:
                stop()
                target = folder/name
                os.replace(stage/name, target)
                replaced.append(target)
        except (OSError, ProjectError):
            for target in reversed(replaced):
                try:
                    saved = backup/target.name
                    if saved.exists():
                        os.replace(saved, target)
                    else:
                        target.unlink()
                except OSError:
                    keep_backup = True
            if keep_backup:
                raise ProjectError(f"导出及回滚失败；请停止使用该目录，从 {backup} 恢复原文件后重试。")
            raise
        return {key: folder/path.name for key, path in staged.items()}
    finally:
        if not keep_backup:
            shutil.rmtree(stage, ignore_errors=True)


def _write_design(result: Result, folder: Path) -> dict[str, Path]:
    """Write only to a private staging directory, without touching originals."""
    files = {"dxf": folder/"cam.dxf", "motion_csv": folder/"motion.csv",
             "cam_csv": folder/"cam.csv", "report": folder/"检查报告.md",
             "project": folder/"project.cam.json"}
    p = result.project
    with files["motion_csv"].open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["drive_s_mm", "rear_s_mm", "time_s", "steer_deg", "follower_mm",
                         "cam_angle_deg", "sensor_x_mm", "sensor_y_mm"])
        for i, distance in enumerate(result.drive_s):
            writer.writerow([float(distance), float(result.rear_s[i]), float(result.rear_s[i]/p.vehicle.speed),
                             float(np.degrees(result.delta[i])), float(result.stroke[i]),
                             float(distance/(math.pi*p.vehicle.drive_diameter*p.cam.ratio)*360),
                             float(result.path[i, 0]), float(result.path[i, 1])])
    with files["cam_csv"].open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["cam_angle_deg", "pitch_x_mm", "pitch_y_mm", "profile_x_mm", "profile_y_mm",
                         "steer_deg", "pressure_deg"])
        for i, angle in enumerate(result.cam_angle):
            writer.writerow([float(np.degrees(angle)), *map(float, result.pitch[i]),
                             *map(float, result.profile[i]), float(np.degrees(result.cam_delta[i])),
                             float(result.pressure[i])])
    doc = ezdxf.new("R2010")
    doc.units = 4  # INSUNITS millimeters. Geometry is ModelSpace 1:1.
    doc.header["$MEASUREMENT"] = 1
    for name, color in [("CAM_OUTLINE", 7), ("BASE_CIRCLE", 8), ("CENTER_HOLE", 1),
                        ("ROLLER_PATH", 4), ("STEERING_AXIS", 3),
                        ("START_REFERENCE", 1), ("ROTATION_REFERENCE", 30)]:
        doc.layers.new(name=name, dxfattribs={"color": color})
    space = doc.modelspace()
    space.add_lwpolyline(result.profile[:-1].tolist(), close=True, dxfattribs={"layer": "CAM_OUTLINE"})
    if p.cam.hole_diameter > 0:
        space.add_circle((0, 0), p.cam.hole_diameter/2, dxfattribs={"layer": "CENTER_HOLE"})
    if p.cam.reference_layers:
        space.add_circle((0, 0), p.cam.base_radius, dxfattribs={"layer": "BASE_CIRCLE"})
        space.add_lwpolyline(result.pitch[:-1].tolist(), close=True, dxfattribs={"layer": "ROLLER_PATH"})
        x, y = map(float, result.pivot)
        space.add_line((x-3, y), (x+3, y), dxfattribs={"layer": "STEERING_AXIS"})
        space.add_line((x, y-3), (x, y+3), dxfattribs={"layer": "STEERING_AXIS"})
        markings = cam_markings(result)
        start = tuple(map(float, markings.start))
        radius = float(np.linalg.norm(result.profile, axis=1).max())
        text_height = max(1.5, radius*0.045)
        start_layer = {"layer": "START_REFERENCE"}
        rotation_layer = {"layer": "ROTATION_REFERENCE"}
        space.add_line((0, 0), start, dxfattribs=start_layer)
        space.add_circle(start, text_height*0.35, dxfattribs=start_layer)
        space.add_text("S0 START (s=0)", dxfattribs={**start_layer, "height": text_height,
                        "insert": (start[0]+text_height, start[1]+text_height)})
        space.add_lwpolyline(markings.arrow.tolist(), dxfattribs=rotation_layer)
        space.add_lwpolyline(markings.arrowhead.tolist(), dxfattribs=rotation_layer)
        rotation = "CCW" if p.cam.direction == 1 else "CW"
        space.add_text(f"ROTATION: {rotation} / VIEW THIS FACE", dxfattribs={**rotation_layer,
                        "height": text_height, "insert": (-radius, -radius*1.32)})
    doc.saveas(files["dxf"])
    files["report"].write_text(report_text(result), encoding="utf-8")
    p.save(files["project"])
    return files
