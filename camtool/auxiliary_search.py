"""Bounded whole-route soft-guide search with immutable hardware and DXF audit."""
from __future__ import annotations
import copy
import json
import math
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
import numpy as np
from .engine import Result, calculate, validate
from .export import export_design
from .project import Project, ProjectError
from .route_shape import required_nodes
from .verification import VerificationResult, export_verification
from .version import VERSION
from .candidate_audit import audit_candidate
from .workflow import candidate_explanation, candidate_changes


@dataclass
class SearchSettings:
    radius: float = 120.
    maximum_points: int = 6
    evaluations: int = 36
    seconds: float = 60.
    first_segment: int = 1
    last_segment: int = 0  # 0 = all remaining effective race segments

    def validate(self, count: int) -> None:
        nums = vars(self)
        if not all(isinstance(v,(int,float)) and math.isfinite(v) for v in nums.values()):
            raise ProjectError("辅助搜索设置必须为有限数字。")
        if not 0 < self.radius <= 2000 or not 1 <= self.seconds <= 600:
            raise ProjectError("搜索半径 0～2000 mm；候选搜索预算 1～600 秒。")
        for name in ("maximum_points","evaluations","first_segment","last_segment"):
            if int(nums[name])!=nums[name]: raise ProjectError("辅助点上限、候选预算和段号必须为整数。")
        if not 0 <= self.maximum_points <= 40 or not 1 <= self.evaluations <= 500:
            raise ProjectError("辅助点上限 0～40；候选预算 1～500。")
        end=self.last_segment or count
        if not 1 <= self.first_segment <= end <= count: raise ProjectError("搜索比赛段范围无效。")


def rank(r: Result) -> tuple[float, ...]:
    s,p=r.stats,r.project
    failures=sum(c.status=="fail" for c in r.checks)
    violation=(max(0.,s.get("max_pressure",180)/p.cam.pressure_limit-1)
               +max(0.,-s.get("undercut_margin",-1e6))/p.mechanism.roller
               +max(0.,-s.get("min_contact_gap",-1e6)-max(.02,p.mechanism.roller*.01))/p.mechanism.roller
               +max(0.,s["max_steer"]/p.vehicle.max_steer-1)
               +max(0.,s["checkpoint_error"]/max(.1,p.route.tolerance)-1)
               +max(0.,s["max_steer_rate"]/p.route.steer_rate_limit-1))
    smooth=float(np.sum(np.abs(np.diff(r.delta))))
    return (float(failures),violation,s.get("max_pressure",180)/90+smooth*.1+s["sensor_distance"]/10000)


@dataclass
class SearchOutcome:
    original_project: Project
    original: Result | None
    candidate: Result
    settings: SearchSettings
    trials: list[dict[str,Any]]
    elapsed: float
    improved: bool
    verification: VerificationResult | None = None
    verification_error: str = ""
    dxf: bytes = b""
    status: str = ""
    stop_reason: str = ""
    search_elapsed: float = 0.0

    def export(self, folder: Path) -> None:
        folder=Path(folder)
        if folder.exists() and any(folder.iterdir()): raise ProjectError("请选择空目录，避免覆盖项目或报告。")
        folder.mkdir(parents=True,exist_ok=True)
        self.original_project.save(folder/"原方案.cam.json")
        files=export_design(self.candidate,folder/"候选方案")
        if self.dxf:
            if self.verification is None or self.verification.inputs.project.to_dict()!=self.candidate.project.to_dict():
                raise ProjectError("候选参数与检验对象不一致，须重新检验。")
            # DXF writers generate fresh handles/timestamps on each save. Deliver
            # the exact bytes inspected, not a semantically identical re-export.
            files["dxf"].write_bytes(self.dxf)
        if self.verification is not None:
            audit=copy.deepcopy(self.verification); audit.filename=str(files["dxf"])
            export_verification(audit,folder/"候选DXF独立二次检验")
        data=dict(software_version=VERSION,status=self.status,settings=vars(self.settings),elapsed_seconds=self.elapsed,
                  search_elapsed_seconds=self.search_elapsed,stop_reason=self.stop_reason,
                  method="确定性分段轮询与局部数值搜索；无随机过程，无大模型调用",random_seed=None,
                  changed_parameters=candidate_changes(self.original_project,self.candidate.project),
                  explanation=candidate_explanation(self.original_project,self.candidate),
                  original_precision=None if self.original is None else self.original.precision,
                  original_sampling=None if self.original is None else self.original.sampling,
                  candidate_sampling=self.candidate.sampling,
                  remaining_failures=[c.name for c in self.candidate.checks if c.status=="fail"],
                  improved=self.improved,original_metrics=None if self.original is None else self.original.stats,
                  candidate_metrics=self.candidate.stats,precision=self.candidate.precision,
                  auxiliary_points=self.candidate.project.route.auxiliary_points,trials=self.trials,
                  independent_verified=self.verification is not None and self.verification.kinematic_pass,
                  verification_error=self.verification_error,
                  limitation="理论局部搜索；预算覆盖候选迭代，最终精度与 DXF 检验另计；无动力学或实车保证。")
        # Curvature can be +infinity for a straight line: serialize explicitly.
        def clean(v: Any) -> Any:
            if isinstance(v,float) and not math.isfinite(v): return "无穷" if v>0 else "非有限值"
            if isinstance(v,dict): return {k:clean(x) for k,x in v.items()}
            if isinstance(v,(tuple,list)): return [clean(x) for x in v]
            return v
        (folder/"辅助点搜索记录.json").write_text(json.dumps(clean(data),ensure_ascii=False,indent=2,allow_nan=False),encoding="utf-8")


def search_auxiliaries(project: Project, settings: SearchSettings,
                       cancelled: Callable[[],bool] | None = None,
                       progress: Callable[[str],None] | None = None) -> SearchOutcome:
    validate(project)
    original_project=copy.deepcopy(project); nodes=required_nodes(project)
    count=len(nodes)-1; settings.validate(count)
    if len(project.route.auxiliary_points)>settings.maximum_points:
        raise ProjectError("辅助点上限小于现有辅助点数量，请先手动减少或提高上限。")
    started=time.monotonic(); trials: list[dict[str,Any]]=[]
    def stop() -> None:
        if cancelled and cancelled(): raise ProjectError("辅助点搜索已取消；原方案未替换。")
    stop()
    try: original=calculate(project,cancelled,precision=False)
    except ProjectError:
        stop(); original=None
    best=original
    end=settings.last_segment or count
    segments=list(range(settings.first_segment-1,end))
    # Prioritize actual race-sector pressure/clearance peaks, then cover every
    # requested segment once per round. Return peaks are deliberately excluded.
    if original is not None and len(original.pressure):
        priorities=[]
        indices=[int(np.argmax(original.pressure)),int(original.geometry.get("index",0))]
        drive_angles=original.drive_s/(math.pi*project.vehicle.drive_diameter*project.cam.ratio)*360
        for index in indices:
            angle=float(np.degrees(original.cam_angle[index]))
            if angle>drive_angles[-1]+1e-9: continue
            position=original.target_path[int(np.argmin(abs(drive_angles-angle)))]
            edges=np.diff(nodes,axis=0)
            t=np.clip(np.sum((position-nodes[:-1])*edges,axis=1)/np.sum(edges**2,axis=1),0,1)
            seg=int(np.argmin(np.linalg.norm(nodes[:-1]+t[:,None]*edges-position,axis=1)))
            if seg in segments and seg not in priorities: priorities.append(seg)
        segments=priorities+[i for i in segments if i not in priorities]
    # Full route round-robin: no first-eight truncation. A second pass focuses
    # on the best candidate. Remove/move as well as insert soft guides.
    attempts=0
    while attempts<settings.evaluations and time.monotonic()-started<settings.seconds:
        round_index=attempts//max(1,len(segments))
        seg=segments[attempts%len(segments)]
        candidate=copy.deepcopy(best.project if best else project)
        guides=candidate.route.auxiliary_points
        direction=(-1 if round_index%2==0 else 1)
        chord=nodes[seg+1]-nodes[seg]; normal=np.array([-chord[1],chord[0]])/np.linalg.norm(chord)
        midpoint=(nodes[seg+1]+nodes[seg])/2
        # Radius is measured from the required-node chord midpoint, including
        # moved guides; it never edits actual hardware or limits.
        displacement=direction*settings.radius/(1+round_index//2)
        xy=midpoint+normal*displacement
        xmin,xmax,ymin,ymax=project.route.field
        matching=[j for j,row in enumerate(guides) if int(row[0])==seg]
        operation="添加"
        if matching and round_index%3==2:
            del guides[matching[-1]]; operation="减少"
        elif matching:
            guides[matching[0]]=[float(seg),float(xy[0]),float(xy[1])]; operation="移动"
        elif len(guides)<settings.maximum_points and xmin<=xy[0]<=xmax and ymin<=xy[1]<=ymax:
            guides.append([float(seg),float(xy[0]),float(xy[1])])
        else:
            # Alternate all-node tangent and global tension exploration when
            # the guide cap is reached, while leaving required visits intact.
            knob=min(seg,max(0,len(nodes)-3))
            while len(candidate.route.tangent_offsets)<len(nodes)-2: candidate.route.tangent_offsets.append(0.)
            if candidate.route.tangent_offsets:
                candidate.route.tangent_offsets[knob]=max(-50.,min(50.,candidate.route.tangent_offsets[knob]+direction*5))
            candidate.route.tension=max(.2,min(1.5,candidate.route.tension+direction*.08))
            operation="切向/张力"
        attempts+=1; stop()
        if progress: progress(f"辅助搜索 {attempts}/{settings.evaluations} · 比赛段 {seg+1}/{count} · {operation}")
        trial_started=time.monotonic()
        row: dict[str,Any]=dict(number=attempts,segment=seg+1,operation=operation,accepted=False,
            changed_parameters=candidate_changes(original_project,candidate),project=candidate.to_dict(),
            validation_scope="粗采样筛选，未独立审计；仅最终候选执行精度复算及最终 DXF 审计")
        try:
            result=calculate(candidate,cancelled,precision=False)
            row.update(metrics=result.stats,rank=rank(result),sampling=result.sampling,
                       remaining_failures=[c.name for c in result.checks if c.status=="fail"],
                       metric_changes_from_original=None if original is None else {
                           key:result.stats[key]-original.stats[key] for key in ("max_pressure","max_steer","checkpoint_error","min_contact_gap")
                           if key in result.stats and key in original.stats})
            if len(guides)<=settings.maximum_points and (best is None or rank(result)<rank(best)):
                best=result; row["accepted"]=True
        except ProjectError as exc:
            stop(); row["error"]=str(exc)
        row["calculation_seconds"]=time.monotonic()-trial_started
        trials.append(row)
    stop()
    if best is None: raise ProjectError("预算内未找到可计算候选。原方案已保留；请核对起跑、必过点与硬件装配。")
    search_elapsed=time.monotonic()-started
    stop_reason="候选预算用尽" if attempts>=settings.evaluations else "时间预算用尽（单次计算在取消检查点结束）"
    if progress: progress("候选搜索结束；正在粗细采样复算及最终 DXF 独立接触—转角—轨迹检验（另计时间，可取消）。")
    best=calculate(best.project,cancelled)
    # Compare equally validated baselines, including precision-induced failures.
    if original is not None:
        original=calculate(original_project,cancelled)
    improved=original is not None and rank(best)<rank(original)
    outcome=SearchOutcome(original_project,original,best,copy.deepcopy(settings),trials,time.monotonic()-started,improved)
    outcome.stop_reason=stop_reason; outcome.search_elapsed=search_elapsed
    if len(best.profile):
        try:
            outcome.verification, outcome.dxf = audit_candidate(best,cancelled)
        except (OSError,ProjectError) as exc:
            stop(); outcome.verification_error=str(exc)
    else: outcome.verification_error="未生成最终凸轮，未验证。"
    stop(); outcome.elapsed=time.monotonic()-started
    feasibility="主设计几何通过" if best.valid else "预算内未找到满足全部主设计约束的方案"
    audit="DXF独立运动学通过" if outcome.verification and outcome.verification.kinematic_pass else "DXF独立检验不通过或未完成"
    stable="主设计采样稳定" if best.precision.get("stable") else "主设计采样稳定性未确认"
    outcome.status=f"{'有排名改善' if improved else '未确认改善'}；{feasibility}；{stable}；{audit}。{stop_reason}，搜索 {search_elapsed:.2f}s / 总耗时 {outcome.elapsed:.2f}s。候选仅供比较，原方案已保留。"
    return outcome
