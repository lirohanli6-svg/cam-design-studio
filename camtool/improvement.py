"""Permission-bounded design trials using the existing engine and DXF audit.

No thresholds are design variables. Coarse screening is never a final verdict.
"""
from __future__ import annotations

import copy
import hashlib
import itertools
import json
import math
import os
import tempfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

import numpy as np

from .auxiliary_search import rank as engine_rank
from .candidate_audit import audit_candidate
from .engine import Result, calculate, follower_positions, validate
from .export import export_design
from .project import Project, ProjectError
from .verification import VerificationResult, export_verification
from .version import VERSION
from .workbench import diagnostics, Diagnostic


@dataclass(frozen=True)
class Parameter:
    key: str
    name: str
    unit: str
    purpose: str
    low: float
    high: float
    kinds: tuple[str, ...] = ("flat", "ball")

    @property
    def hardware(self) -> bool:
        return not self.key.startswith("route.")


PARAMETERS = (
    Parameter("route.tension", "路线张力", "无量纲", "调整既有路线样条形状；必过点、顺序和容差保持", .2, 1.5),
    Parameter("cam.base_radius", "凸轮基圆半径", "mm", "改变轮廓尺度，也联动中位接触高度和转轴安装位置", .1, 500),
    Parameter("mechanism.arm", "转向臂长度", "mm", "改变从动件位移和传力几何；不降低原路线所需前轮角", .1, 500),
    Parameter("mechanism.arm_angle", "转向臂中位角", "°", "改变整圈机构几何和隐含安装位置，不是仅修改数值零点", -180, 180),
    Parameter("mechanism.pivot_x", "转轴横向位置", "mm", "平推为相对导向线横距；顶球为相对凸轮轴横距", -500, 500),
    Parameter("mechanism.offset", "导向偏置", "mm", "平推导向线相对凸轮轴的位置；顶球机构无此调整", -500, 500, ("flat",)),
)
BY_KEY = {p.key: p for p in PARAMETERS}
UNSUPPORTED = "第一版暂不调整：连杆长度、传动比、轮径、滚子/球半径、中位抬升、转角零点、局部辅助点和切线偏置。它们保持原值。"


def value(project: Project, key: str) -> float:
    group, name = key.split(".")
    return float(getattr(getattr(project, group), name))


def set_value(project: Project, key: str, number: float) -> None:
    group, name = key.split(".")
    setattr(getattr(project, group), name, number)


@dataclass(frozen=True)
class Adjustment:
    key: str
    lower: float
    upper: float


@dataclass
class TrialSettings:
    adjustments: list[Adjustment] = field(default_factory=list)
    mode: str = "route"
    evaluations: int = 12
    seconds: float = 60
    audit_top: int = 2
    range_sources: dict[str,str] = field(default_factory=dict)
    allow_installation_change: bool = False

    def validate(self, project: Project) -> None:
        validate(project)
        if self.mode not in ("route", "structure"):
            raise ProjectError("请选择仅调整路线或允许结构改型。")
        if not self.adjustments:
            raise ProjectError("所有参数当前锁定；请主动勾选至少一个允许调整的参数。")
        if any(isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x)
               for x in (self.evaluations, self.seconds, self.audit_top)):
            raise ProjectError("预算必须为有限数值。")
        if int(self.evaluations) != self.evaluations or not 1 <= self.evaluations <= 80:
            raise ProjectError("实际候选预算须为1～80的整数。")
        if not 1 <= self.seconds <= 300 or int(self.audit_top) != self.audit_top or not 1 <= self.audit_top <= 3:
            raise ProjectError("整体时间预算1～300秒，复算/审计候选数1～3。")
        seen = set()
        for a in self.adjustments:
            spec = BY_KEY.get(a.key)
            if spec is None or a.key in seen:
                raise ProjectError("存在未支持或重复的调整参数。")
            seen.add(a.key)
            if project.mechanism.kind not in spec.kinds or (self.mode == "route" and spec.hardware):
                raise ProjectError(f"当前模式/机构不允许调整{spec.name}。")
            if any(isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x) for x in (a.lower, a.upper)):
                raise ProjectError(f"{spec.name}的上下限必须为有限数值。")
            if not spec.low <= a.lower <= a.upper <= spec.high:
                raise ProjectError(f"{spec.name}试算边界为{spec.low:g}～{spec.high:g} {spec.unit}。该边界不代表实物可行。")
            if not a.lower <= value(project, a.key) <= a.upper:
                raise ProjectError(f"{spec.name}范围需包含当前值，才能做单参数对照；请扩大范围。")
        if not isinstance(self.allow_installation_change,bool):raise ProjectError("安装联动授权必须明确为是或否。")
        if any(BY_KEY[a.key].hardware for a in self.adjustments) and not self.allow_installation_change:
            raise ProjectError("结构试算会联动中位高度/安装位置，请明确允许安装联动；不等于只替换凸轮。")
        if not any(a.upper > a.lower for a in self.adjustments):
            raise ProjectError("所有范围均固定为当前值，没有可试算的变化。")


def assert_permissions(original: Project, candidate: Project, settings: TrialSettings) -> None:
    """Only explicit selected scalars can differ; includes sampling and limits."""
    if any(BY_KEY[d["parameter"]].hardware for d in modifications(original,candidate)) and not settings.allow_installation_change:
        raise ProjectError("未授权安装位置联动，不能使用结构候选。")
    before, after = original.to_dict(), candidate.to_dict()
    for a in settings.adjustments:
        if not a.lower <= value(candidate, a.key) <= a.upper:
            raise ProjectError(f"候选{BY_KEY[a.key].name}越过授权范围。")
        group, name = a.key.split(".")
        before[group].pop(name)
        after[group].pop(name)
    if before != after:
        raise ProjectError("候选改变了未授权参数、比赛要求、采样或验收门槛，已拒绝。")


def modifications(original: Project, candidate: Project) -> list[dict[str, Any]]:
    return [dict(parameter=p.key, part="路线" if not p.hardware else ("凸轮/安装" if p.key.startswith("cam.") else "转向机构"),
                 name=p.name, unit=p.unit, before=value(original,p.key), after=value(candidate,p.key),
                 change=value(candidate,p.key)-value(original,p.key), purpose=p.purpose)
            for p in PARAMETERS if value(original,p.key) != value(candidate,p.key)]


def software_pass(result: Result | None) -> bool:
    return result is not None and bool(len(result.profile)) and result.valid and bool(result.precision.get("stable"))


def failures(result: Result | None) -> set[str]:
    return set() if result is None else {c.name for c in result.checks if c.status == "fail"}


def physical_cost(result: Result | None) -> dict[str, Any]:
    if result is None:
        return {"state": "未计算，不能推断安装位置"}
    p = result.project
    try:
        center, _, pivot = follower_positions(np.array([math.radians(p.mechanism.steer_zero)]),p)
    except ProjectError as exc:
        return {"state":"安装几何不可计算："+str(exc)}
    profile = result.profile
    lo, hi = result.stats.get("stroke_min"), result.stats.get("stroke_max")
    span = None if lo is None or hi is None else hi-lo
    available = p.mechanism.max_stroke-p.mechanism.min_stroke
    interpretation="无整圈行程结果"
    if lo is not None and hi is not None:
        interpretation=("总跨度大于填写的可用行程；需实际改型" if hi-lo>available else (
            "跨度可容纳但位置超限；须核对真实安装中位，不能仅改零点" if lo<p.mechanism.min_stroke or hi>p.mechanism.max_stroke else "整圈行程在填写范围内"))
    return dict(pivot_x_mm=float(pivot[0]),pivot_y_mm=float(pivot[1]),
                neutral_contact_x_mm=float(center[0,0]),neutral_contact_y_mm=float(center[0,1]),
                profile_width_mm=None if not len(profile) else float(np.ptp(profile[:,0])),
                profile_height_mm=None if not len(profile) else float(np.ptp(profile[:,1])),
                profile_enclosing_diameter_mm=None if not len(profile) else float(2*np.linalg.norm(profile,axis=1).max()),
                stroke_min_mm=lo,stroke_max_mm=hi,stroke_span_mm=span,available_stroke_mm=available,
                stroke_interpretation=interpretation)


def compare(original: Result | None, candidate: Result) -> dict[str, Any]:
    def facts(result: Result) -> dict[str,dict[str,Any]]:
        rows={}
        for d in diagnostics(result):
            row=asdict(d);row['pose']=None
            if d.angle_deg is not None and len(result.cam_angle) and len(result.cam_delta):
                i=int(np.argmin(abs(np.degrees(result.cam_angle)-d.angle_deg)))
                row['pose']=dict(note="最坏角度最近的机构采样姿态",cam_sample_deg=float(np.degrees(result.cam_angle[i])),
                    steer_deg=float(np.degrees(result.cam_delta[i])),pivot_mm=result.pivot.tolist(),
                    follower_center_mm=None if i>=len(result.contact_centers) else result.contact_centers[i].tolist())
            rows[d.name]=row
        return rows
    before = {} if original is None else facts(original)
    after = facts(candidate)
    rows = [dict(name=name,before=before.get(name),after=after.get(name)) for name in dict.fromkeys([*before,*after])]
    resolved = failures(original)-failures(candidate) if original is not None else set()
    new = failures(candidate)-failures(original) if original is not None else set()
    improved, worsened = [], []
    directions = {"max_steer":-1,"max_pressure":-1,"checkpoint_error":-1,"replay_error":-1,
                  "max_steer_rate":-1,"undercut_margin":1,"min_contact_gap":1}
    for key, direction in directions.items():
        if original is not None and key in original.stats and key in candidate.stats:
            delta = candidate.stats[key]-original.stats[key]
            row = dict(metric=key,before=original.stats[key],after=candidate.stats[key],change=delta)
            if direction*delta>1e-7: improved.append(row)
            elif direction*delta < -1e-7: worsened.append(row)
    return dict(checks=rows,resolved=sorted(resolved),new_failures=sorted(new),remaining=sorted(failures(candidate)),
                improved_metrics=improved,worsened_metrics=worsened,
                baseline_cost=physical_cost(original),candidate_cost=physical_cost(candidate),
                baseline_sampling=None if original is None else dict(requested=original.project.cam.points,route_step=original.project.route.step,
                                                                    actual=original.sampling,precision=original.precision),
                candidate_sampling=dict(requested=candidate.project.cam.points,route_step=candidate.project.route.step,
                                        actual=candidate.sampling,precision=candidate.precision))


COMPONENTS = (
    ("路线",("route.tension",),"允许调整路线"),
    ("凸轮尺寸",("cam.base_radius",),"尚未制造，允许改尺寸"),
    ("转向臂尺寸",("mechanism.arm",),"尚未制造，允许改尺寸"),
    ("机构安装",("mechanism.arm_angle","mechanism.pivot_x","mechanism.offset"),"允许调整安装"),
)


def exploration_range(project: Project,key: str) -> dict[str,Any]:
    """Exploration only; never label these bounds as mechanical safety limits."""
    p=BY_KEY[key];x=value(project,key)
    widths={'route.tension':.2,'mechanism.arm_angle':10.,'mechanism.pivot_x':5.,'mechanism.offset':5.}
    width=widths.get(key,max(3.,abs(x)*.1))
    lo,hi=x-width,x+width
    if key=='cam.base_radius':lo,hi=x-max(2.,abs(x)*.1),x+max(15.,abs(x)*.5)
    return dict(key=key,name=p.name,unit=p.unit,current=x,lower=max(p.low,lo),upper=min(p.high,hi),
                source='探索暂定范围（无实测机械依据；不是安全范围）')


def diagnose(result: Result, checks: list[Diagnostic] | None = None) -> dict[str,Any]:
    """Computed facts and model-dependency hypotheses, never inferred root causes."""
    p=result.project;rows=diagnostics(result) if checks is None else checks;problems=[];recommended: dict[str,list[str]]={}
    geometry=['cam.base_radius','mechanism.arm','mechanism.arm_angle','mechanism.pivot_x']
    if p.mechanism.kind=='flat':geometry.append('mechanism.offset')
    for d in rows:
        if d.status!='fail':continue
        keys: list[str]=[];facts=[f'{d.actual}；限制：{d.limit}',d.impact];hypotheses=[]
        if d.name in ('前轮转角','完整周期转向'):
            keys=['route.tension']
            hypotheses=['运行段优先核对局部曲率、起始姿态、轴距与传感器几何（车辆尺寸仍锁定）。' if d.route_index is not None else
                        '峰值位于回程；可能与末端转向到起始转向的衔接及运行/回程角度分配有关，不能套用比赛路线位置。',
                        '改变转向臂长度不能直接减少固定路线所需前轮转角；不建议放宽机械限位。']
        elif d.name=='完整周期行程':
            keys=['mechanism.arm_angle','mechanism.pivot_x']
            if p.mechanism.kind=='flat':keys.append('mechanism.offset')
            keys+=['mechanism.arm','route.tension']
            m=d.measurements
            if m:
                facts.append(f"整圈 {m['min_mm']:.4f}～{m['max_mm']:.4f} mm；总跨度 {m['span_mm']:.4f} mm / 可用跨度 {m['available_span_mm']:.4f} mm；负向越界 {m['negative_excess_mm']:.4f} mm，正向越界 {m['positive_excess_mm']:.4f} mm。")
                hypotheses.append('总跨度超过允许跨度，单纯重分配中位不能容纳。需比较运动规律/臂长/安装几何的真实效果。' if m['classification']=='总跨度不足' else
                                  '总跨度可容纳时优先核对安装中位和正负行程分配；尚不能确认实物中位可调整。')
            hypotheses.append('改变中位必须重算整圈几何、接触与转向；不能仅修改报告零点或扩大行程门槛。')
        elif d.name in ('压力角','局部过切','全局接触干涉'):
            keys=['route.tension',*geometry]
            hypotheses=['可能与运动变化、基圆、臂长、中位角、转轴位置'+('及导向偏置' if p.mechanism.kind=='flat' else '及摆臂球心圆弧几何')+'耦合，需逐项对比。',
                        '增大基圆不保证改善；基圆与中心孔通过也不能证明压力角或接触合理。']
            if d.name=='局部过切':facts.append('这是局部凸曲率相对滚子/球半径不足的判据，与远处轮廓侵入是不同检查。')
            if d.name=='全局接触干涉':facts.append('这是非邻近轮廓侵入滚子/球的全轮廓接触检查，局部曲率通过不能排除它。')
            if d.name!='压力角':hypotheses.append('接触尺寸本版保持锁定；不能靠减小接触半径、放宽间隙或仅增采样点宣布解决。')
            hypotheses.append('基圆/中位/安装几何联动；改变尺度可能需要同步改变实际安装位置，必须核对装配代价。')
        elif d.name in ('单圈容量','凸轮未生成'):
            keys=['route.tension']
            hypotheses=['驱动轮直径与驱动轮到凸轮传动比共同影响运行/回程角度分配，本版锁定，需实物核对，不能断言轮径过小。',
                        '容量通过不等于分配最优；增大驱动轮也不保证压力角改善。']
        elif d.name in ('顺序打卡','名义转向正向回算','场地边界','圆形障碍物','转向变化速度'):
            keys=['route.tension'];hypotheses=['优先比较必过点之间的路线形状与起始姿态；保持必过点、顺序、场地及容差不变。']
        elif d.name in ('基圆与中心孔','机构可逆传动'):
            keys=geometry;hypotheses=['需核对当前机构闭合、安装位置与运动分支；这些仅为可对比因素，尚未确认根因。']
        else:hypotheses=['本项没有自动调整规则；先查看原判定依据并核对对应输入/模型，不能猜测尺寸。']
        for key in keys:recommended.setdefault(key,[]).append(d.name)
        problems.append(dict(name=d.name,diagnostic=asdict(d),confirmed=facts,hypotheses=hypotheses,parameters=keys,
                             next_action='先核对定位与实物限制，再确认允许改动的部件；未授权也可阅读全部诊断。'))
    under=next((d for d in rows if d.name=='局部过切' and d.status=='fail'),None)
    interference=next((d for d in rows if d.name=='全局接触干涉' and d.status=='fail'),None)
    correlation=None
    if under and interference and under.angle_deg is not None and interference.angle_deg is not None:
        gap=abs((under.angle_deg-interference.angle_deg+180)%360-180)
        correlation=dict(separation_deg=gap,note=f'过切与干涉的离散最坏点相差{gap:.4f}°；相近不证明同源，最坏点不同也不能排除其他位置重叠。需比较运动规律和机构几何。')
    return dict(project=p.to_dict(),problems=problems,all_checks=[asdict(d) for d in rows],recommended=recommended,
                precision=result.precision,correlation=correlation,
                recommendations=[dict(**exploration_range(p,k),related_checks=names) for k,names in recommended.items()],
                transmission=dict(drive_diameter_mm=p.vehicle.drive_diameter,passive_diameter_mm=p.vehicle.passive_diameter,
                                  ratio=p.cam.ratio,usage=result.stats.get('cam_usage'),note='后驱动轮参与相位与里程；后从动轮直径在当前模型中不改变凸轮相位，不推荐用它解决相位分配。'),
                boundary='当前计算的二维理论与有限采样诊断；可能原因尚待试算/实物核对，不是加工放行或实车验证。')


def diagnosis_text(data: dict[str,Any]) -> str:
    lines=['当前项目检查诊断（未授权也可完整查看）']
    if not data['problems']:lines.append('当前主检查没有失败项。采样稳定性：'+str(data['precision'].get('stable','未完成'))+'；仍须最终DXF独立检验和实物适配。')
    def position(row: dict[str,Any]) -> str:
        point=row.get('point');xy='无比赛路线对应位置' if point is None else f"({point[0]:.3f}, {point[1]:.3f}) mm"
        pose=row.get('pose') or row;center=pose.get('follower_center_mm');pivot=pose.get('pivot_mm')
        vector=lambda v:'未提供' if v is None else f'({v[0]:.3f}, {v[1]:.3f}) mm'
        steer=pose.get('steer_deg')
        return f"{row.get('scope')}；凸轮角{row.get('angle_deg')}°；路线位置{xy}；前轮角{steer}°；转轴{vector(pivot)}；接触中心{vector(center)}（有限采样姿态）"
    for item in data['problems']:
        d=item['diagnostic'];lines+=['',f"【{item['name']}】",'已确认事实：',*item['confirmed'],
            '最坏采样位置：'+position(d),
            '待验证原因（不是故障根因结论）：',*item['hypotheses'],
            '可授权试算的因素：'+('、'.join(BY_KEY[k].name for k in item['parameters']) or '暂无自动试算因素'),item['next_action']]
        m=d['measurements']
        if 'minimum_location' in m:
            lines+=['负向端位置：'+position(m['minimum_location']),'正向端位置：'+position(m['maximum_location'])]
    if data['correlation']:lines.append(data['correlation']['note'])
    lines+=['','单参数无明显影响不能排除组合有效；少量局部样本不能证明全局单调。',data['transmission']['note'],'下一步：确认哪些部件固定、哪些可调整安装/改尺寸及是否允许调整路线；高级设置可细调探索范围。',data['boundary']]
    return '\n'.join(lines)


def rule_directions(result: Result | None) -> str:
    return '基线尚未得到可用结果，先读取当前输入并计算诊断；不提供未经计算的尺寸。' if result is None else diagnosis_text(diagnose(result))


@dataclass
class Candidate:
    number: int
    project: Project
    screening: dict[str, Any]
    result: Result | None = None
    audit: VerificationResult | None = None
    dxf: bytes = b""
    validation_error: str = ""
    comparison: dict[str, Any] = field(default_factory=dict)
    validation_seconds: float = 0.

    @property
    def independent_pass(self) -> bool:
        return (self.audit is not None and self.audit.kinematic_pass and bool(self.dxf)
                and self.audit.sha256 == hashlib.sha256(self.dxf).hexdigest()
                and self.audit.inputs.project.to_dict() == self.project.to_dict())

    @property
    def status(self) -> str:
        if self.result is None: return "未完成验证（仅粗筛）"
        if software_pass(self.result):
            return "独立检验通过" if self.independent_pass else "指定软件检查通过；独立验证未通过/未完成"
        if self.comparison.get("resolved") or self.comparison.get("improved_metrics"):
            return "试算有改善；未全部通过"
        return "未确认改善；未全部通过"


def clean(value_: Any) -> Any:
    if isinstance(value_,float) and not math.isfinite(value_): return "无穷" if value_>0 else "非有限值"
    if isinstance(value_,dict): return {k:clean(v) for k,v in value_.items()}
    if isinstance(value_,(list,tuple)): return [clean(v) for v in value_]
    return value_


def audit_error(exc: OSError | ProjectError) -> str:
    if isinstance(exc,OSError):
        return f"独立审计文件读写失败（{type(exc).__name__}，错误码{exc.errno}）；请核对临时目录权限/磁盘空间，未完成验证。"
    text=str(exc);directory=Path(tempfile.gettempdir())
    return text.replace(str(directory),"<临时目录>").replace(directory.as_posix(),"<临时目录>")


@dataclass
class ImprovementOutcome:
    original_project: Project
    settings: TrialSettings
    baseline: Result | None
    baseline_error: str
    trials: list[dict[str, Any]]
    candidates: list[Candidate]
    elapsed: float
    stop_reason: str

    def influence(self) -> list[dict[str,Any]]:
        groups=[]
        for a in self.settings.adjustments:
            samples=[]
            for row in self.trials:
                changes=row['modifications']
                if row['phase']=='单参数试算' and len(changes)==1 and changes[0]['parameter']==a.key:
                    samples.append(dict(value=changes[0]['after'],metrics=row.get('stats'),error=row.get('error'),
                                        failures=[c['name'] for c in row.get('checks',[]) if c['status']=='fail'],effect=row.get('effect')))
            pressure=[(x['value'],x['metrics']['max_pressure']) for x in samples if x['metrics'] is not None and 'max_pressure' in x['metrics']]
            if self.baseline is not None and 'max_pressure' in self.baseline.stats:
                pressure.append((value(self.original_project,a.key),self.baseline.stats['max_pressure']))
            pressure.sort();steps=[b[1]-a_[1] for a_,b in zip(pressure,pressure[1:])]
            nonmonotone=any(x>1e-7 for x in steps) and any(x < -1e-7 for x in steps)
            groups.append(dict(parameter=a.key,name=BY_KEY[a.key].name,unit=BY_KEY[a.key].unit,samples=samples,
                pressure_observation="压力角样本非单调" if nonmonotone else "样本未显示压力角反向变化；不足以证明单调性或全局趋势"))
        return groups

    def report(self, candidate: Candidate | None = None) -> str:
        rows = ["# 设计改进助手 · 实际试算方案", f"软件{VERSION}；原案保留；非加工放行或实车认证。",
                f"已试算{len(self.trials)}/{self.settings.evaluations}次；总耗时{self.elapsed:.2f}s；{self.stop_reason}。",
                "整体预算包含基线、粗筛、复算和DXF审计，在计算取消检查点生效；不是硬实时截止。",
                "基线错误："+self.baseline_error if self.baseline_error else "基线已使用完整检查及采样复算。",
                rule_directions(self.baseline), ""]
        rows += [f"授权范围：{BY_KEY[a.key].name} {a.lower:g}～{a.upper:g}{BY_KEY[a.key].unit}；来源：{self.settings.range_sources.get(a.key,'用户给定，未核实实物限制')}" for a in self.settings.adjustments]
        rows += ["## 单参数局部影响（仅粗筛，最终结论见候选）"]
        for group in self.influence():
            rows.append(f"- {group['name']}：{group['pressure_observation']}")
            for sample in group['samples']:
                stats=sample['metrics']
                rows.append(f"  - 实际试算值{sample['value']:g} {group['unit']}："+(
                    "无解/未完成："+str(sample['error']) if stats is None else
                    f"压力角{stats.get('max_pressure')}°，运行段最大前轮角{stats.get('max_steer')}°；粗筛失败项{sample['failures']}"))
        for c in ([candidate] if candidate is not None else self.candidates):
            rows += [f"## 候选{c.number}：{c.status}","### 修改清单"]
            rows += [f"- {d['part']} / {d['name']}：{d['before']:g} → {d['after']:g} {d['unit']}，变化{d['change']:+g}；{d['purpose']}"
                     for d in modifications(self.original_project,c.project)]
            reasons=diagnose(self.baseline)['recommended'] if self.baseline is not None else {}
            for d in modifications(self.original_project,c.project):
                rows.append(f"为什么比较{d['name']}：涉及基线检查{reasons.get(d['parameter'],['用户自行授权的探索因素'])}；此关联不是根因结论。")
            rows.append(f"支持证据：实际试算编号{c.number}，完整候选重新计算；单参数局部影响见上方。仅在本次预算/范围内比较，非全局单调结论。")
            rows += [f"本候选复算/审计用时{c.validation_seconds:.2f}s。验证提示："+(c.validation_error or "见逐项结论"),
                     "粗筛不等于最终通过。" if c.result is None else "以下为完整候选重新计算结果，与基线使用相同设置、目标及门槛。"]
            if c.comparison:
                info=c.comparison
                rows += [f"已解决主检查：{info['resolved']}；新增主失败：{info['new_failures']}；剩余主失败：{info['remaining']}",
                         "改善指标："+json.dumps(clean(info['improved_metrics']),ensure_ascii=False),
                         "恶化指标："+json.dumps(clean(info['worsened_metrics']),ensure_ascii=False),
                         "### 关键检查（位置、机构姿态与调整依据见方案JSON）",
                         "|项目|原案实际 / 限制 / 状态|候选实际 / 限制 / 状态|", "|---|---|---|"]
                for item in info['checks']:
                    def cell(d: dict[str, Any] | None) -> str:
                        return "未计算" if d is None else f"{d['actual']} / {d['limit']} / {d['status']}".replace("|","/")
                    rows.append(f"|{item['name']}|{cell(item['before'])}|{cell(item['after'])}|")
                rows += ["### 候选失败位置与机构姿态"]
                for item in info['checks']:
                    d=item['after']
                    if d is not None and d['status']=='fail':
                        rows.append(f"- {d['name']}：{d['scope']}；凸轮角{d['angle_deg']}°；场地位置{d['point']} mm；机构采样姿态{json.dumps(d['pose'],ensure_ascii=False)}；调整方向：{d['advice']}")
                rows += ["### 实际代价与隐含安装变化", "```json",json.dumps(clean(dict(before=info['baseline_cost'],after=info['candidate_cost'])),ensure_ascii=False,indent=2),"```",
                         "基圆或臂/角/偏置变化会联动安装位置；须按以上坐标重新核对装配，不能描述为其余硬件完全不动。",
                         "### 采样记录", "```json",json.dumps(clean(dict(before=info['baseline_sampling'],after=info['candidate_sampling'])),ensure_ascii=False,indent=2),"```"]
            rows += ["最终DXF SHA-256："+(c.audit.sha256 if c.audit is not None else "未完成审计"),
                     "独立运动学："+("通过" if c.independent_pass else "不通过或未完成；不能据此宣布通过"),""]
            rows += ["### 独立检验的逐项结论", "基线本轮未做独立DXF审计，不把以下失败伪称为相对基线新增或已解决；与主检查分别展示。"]
            if c.audit is not None:
                rows += [f"- [{check.status}] {check.name}：{check.detail}" for check in c.audit.checks]
                rows += [f"- 独立问题：凸轮角{issue['angle_deg']}°；位置{issue['position_mm']} mm；{issue['reason']}" for issue in c.audit.issues[:20]]
                if len(c.audit.issues)>20:rows.append("更多独立问题见导出的完整独立检验报告。")
            else:rows.append("未完成独立检验，不推断其余检查状态。")
        if not self.candidates: rows.append("在本次范围和预算内未找到可展示方案，不代表不存在可行设计。")
        rows += ["## 边界", "确定性有限试算，没有运行时大模型；无随机过程。两条求解链可能共享错误的机构假设。",
                 "二维理想模型、有限采样及理论误差不能证明连续几何、实车精度或制造可行；弹簧、驱动力矩、摩擦、空间接触等仍须验证。"]
        return "\n".join(rows)+"\n"

    def export(self, folder: Path, cancelled: Callable[[], bool] | None = None) -> None:
        """Publish a new readable plan, preserving exact audited DXF bytes."""
        def stop() -> None:
            if cancelled and cancelled(): raise ProjectError("方案导出已取消。")
        folder=Path(folder)
        if folder.exists() and any(folder.iterdir()): raise ProjectError("请选择新目录或空目录，不能覆盖原项目/报告。")
        folder.parent.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=".solarcam-plan-",dir=folder.parent) as temp:
            stage=Path(temp)/"plan"; stage.mkdir()
            self.original_project.save(stage/"baseline.cam.json")
            (stage/"修改方案.md").write_text(self.report(),encoding="utf-8")
            records=[]
            for c in self.candidates:
                stop(); assert_permissions(self.original_project,c.project,self.settings)
                out=stage/f"candidate-{c.number}"; out.mkdir(); c.project.save(out/"candidate.cam.json")
                if c.result is not None and len(c.result.profile):
                    files=export_design(c.result,out/"design",cancelled=cancelled)
                    if c.audit is not None:
                        if not c.dxf or c.audit.sha256!=hashlib.sha256(c.dxf).hexdigest() or c.audit.inputs.project.to_dict()!=c.project.to_dict():
                            raise ProjectError("候选参数、受检DXF字节或哈希不一致，不能导出审计证据。")
                        files['dxf'].write_bytes(c.dxf)
                        audit=copy.deepcopy(c.audit);audit.filename="cam.dxf"
                        export_verification(audit,out/"independent")
                records.append(dict(number=c.number,status=c.status,project=c.project.to_dict(),
                    modifications=modifications(self.original_project,c.project),comparison=c.comparison,
                    screening=c.screening,validation_error=c.validation_error,
                    validation_seconds=c.validation_seconds,
                    independent_checks=None if c.audit is None else [asdict(check) for check in c.audit.checks],
                    independent_pass=c.independent_pass,sha256=None if c.audit is None else c.audit.sha256))
            data=dict(software_version=VERSION,method="确定性单参数试算及有限组合；无随机过程",random_seed=None,
                      settings=asdict(self.settings),elapsed_seconds=self.elapsed,stop_reason=self.stop_reason,
                      baseline_error=self.baseline_error,baseline=self.original_project.to_dict(),
                      baseline_diagnosis=None if self.baseline is None else diagnose(self.baseline),
                      local_influence=self.influence(),trials=self.trials,candidates=records)
            (stage/"方案与试算记录.json").write_text(json.dumps(clean(data),ensure_ascii=False,indent=2,allow_nan=False),encoding="utf-8")
            stop()
            if folder.exists(): folder.rmdir()  # Only the checked empty target; refuses concurrent additions.
            os.replace(stage,folder)


def run_improvement(project: Project, settings: TrialSettings,
                    cancelled: Callable[[], bool] | None = None,
                    progress: Callable[[str], None] | None = None) -> ImprovementOutcome:
    settings=copy.deepcopy(settings); project=copy.deepcopy(project); settings.validate(project)
    started=time.monotonic(); deadline=started+settings.seconds
    def user_stop() -> None:
        if cancelled and cancelled(): raise ProjectError("设计改进试算已取消；原案未修改。")
    def stop() -> bool:
        return bool(cancelled and cancelled()) or time.monotonic()>=deadline
    def say(text: str) -> None:
        if progress: progress(text)
    user_stop(); say("阶段1：完整计算基线及采样复算；尚未试算候选，可停止。")
    baseline=None; baseline_error=""
    try: baseline=calculate(project,stop)
    except ProjectError as exc:
        user_stop(); baseline_error="基线："+str(exc)
    trials: list[dict[str,Any]]=[]; pool: list[tuple[tuple[float,...],Project,dict[str,Any]]]=[]
    seen={json.dumps(project.to_dict(),sort_keys=True)}
    promising: dict[str,tuple[tuple[float,...],float]]={}
    singles=[]
    for a in settings.adjustments:
        probes=sorted(set((a.lower,(a.lower+a.upper)/2,a.upper)),key=lambda x:abs(x-value(project,a.key)))
        singles.append([(a.key,x) for x in probes if x!=value(project,a.key)])
    def screen_key(r: Result, q: Project) -> tuple[float,...]:
        rnk=engine_rank(r)
        stroke=max(0.,r.stats.get('stroke_max',0)-q.mechanism.max_stroke)+max(0.,q.mechanism.min_stroke-r.stats.get('stroke_min',0))
        changes=modifications(project,q)
        cost=sum(abs(d['change'])/max(1.,abs(d['before'])) for d in changes)
        return (rnk[0],rnk[1]+stroke/max(1.,q.mechanism.arm),float(len(changes)),cost,rnk[2])
    base_rank=None if baseline is None else screen_key(baseline,project)
    def trial(q: Project, phase: str, key: str | None = None) -> None:
        user_stop()
        token=json.dumps(q.to_dict(),sort_keys=True)
        if token in seen or len(trials)>=settings.evaluations or time.monotonic()>=deadline: return
        seen.add(token);assert_permissions(project,q,settings)
        number=len(trials)+1; tick=time.monotonic()
        row: dict[str,Any]=dict(number=number,phase=phase,project=q.to_dict(),modifications=modifications(project,q),
                               validation="粗筛，未完成采样复算或独立检验")
        say(f"阶段2：{phase} · 已完成{len(trials)}次 / 预算{settings.evaluations}次；当前试算{number}，可停止。")
        try:
            r=calculate(q,stop,precision=False); ranking=screen_key(r,q)
            row.update(stats=r.stats,checks=[asdict(c) for c in r.checks],sampling=r.sampling,rank=ranking)
            signs={'max_pressure':-1,'max_steer':-1,'checkpoint_error':-1,'replay_error':-1,'min_contact_gap':1,'undercut_margin':1}
            changes=[dict(metric=k,before=baseline.stats[k],after=r.stats[k],delta=r.stats[k]-baseline.stats[k],
                          observation='改善' if sign*(r.stats[k]-baseline.stats[k])>1e-7 else ('恶化' if sign*(r.stats[k]-baseline.stats[k]) < -1e-7 else '无明显影响'))
                     for k,sign in signs.items() if baseline is not None and k in baseline.stats and k in r.stats]
            row['effect']=dict(metrics=changes,resolved=sorted(failures(baseline)-failures(r)),new_failures=sorted(failures(r)-failures(baseline)),
                               remaining=sorted(failures(r)),validation='单参数/组合粗筛对比，不是最终通过；无影响不能排除其他组合')
            pool.append((ranking,copy.deepcopy(q),row));pool.sort(key=lambda x:x[0]);del pool[max(6,settings.audit_top*3):]
            if key is not None and (base_rank is None or ranking[:2]<base_rank[:2]):
                if key not in promising or ranking<promising[key][0]: promising[key]=(ranking,value(q,key))
        except ProjectError as exc:
            user_stop();row['error']=str(exc)
        row['seconds']=time.monotonic()-tick;trials.append(row)
    # Round robin prevents early parameters from consuming the entire budget.
    for round_ in itertools.zip_longest(*singles):
        for item in round_:
            if item is not None:
                key,number=item;q=copy.deepcopy(project);set_value(q,key,number);trial(q,"单参数试算",key)
    if len(promising)>1:
        keys=list(promising)
        for flags in itertools.product((False,True),repeat=len(keys)):
            if sum(flags)<2: continue
            q=copy.deepcopy(project)
            for key,enabled in zip(keys,flags):
                if enabled: set_value(q,key,promising[key][1])
            trial(q,"有局部改善参数的有限组合")
            if len(trials)>=settings.evaluations or time.monotonic()>=deadline: break
    candidates=[]
    for _,q,row in pool[:settings.audit_top]:
        user_stop();c=Candidate(row['number'],q,row);candidates.append(c)
        if time.monotonic()>=deadline:
            c.validation_error="整体时间预算用尽，只有粗筛结果，禁止采用。";continue
        say(f"阶段3：候选{c.number}完整复算及最终DXF独立审计；已完成试算{len(trials)}次。")
        validation_started=time.monotonic()
        try:
            c.result=calculate(q,stop);assert_permissions(project,c.result.project,settings)
            c.comparison=compare(baseline,c.result)
            if len(c.result.profile): c.audit,c.dxf=audit_candidate(c.result,stop)
            else: c.validation_error="没有最终轮廓，未完成独立审计。"
        except (ProjectError,OSError) as exc:
            user_stop();c.validation_error=audit_error(exc)
        c.validation_seconds=time.monotonic()-validation_started
    def final_key(c: Candidate) -> tuple[float,...]:
        if c.result is None: return (3.,)
        ranking=screen_key(c.result,c.project)
        if software_pass(c.result): return (0. if c.independent_pass else 1.,*ranking[2:4],ranking[1],ranking[4])
        return (2.,ranking[0],float(len(c.comparison.get('new_failures',[]))),ranking[1],*ranking[2:])
    candidates.sort(key=final_key);user_stop()
    reason="整体时间预算用尽；未完成项均保留" if time.monotonic()>=deadline else (
        "候选次数预算用尽" if len(trials)>=settings.evaluations else "本轮有限单参数/组合试算完成")
    if not any(software_pass(c.result) and c.independent_pass for c in candidates):
        reason+="；在本次范围和预算内未找到全部通过方案，不代表不存在可行设计"
    if baseline is not None and candidates and not any(c.comparison.get('resolved') or c.comparison.get('improved_metrics') for c in candidates):
        reason+="；未确认指标改善，原案保留"
    return ImprovementOutcome(project,settings,baseline,baseline_error,trials,candidates,time.monotonic()-started,reason)
