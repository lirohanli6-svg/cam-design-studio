"""User-facing workflow facts; no numerical solver or GUI side effects."""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from .project import Project

if TYPE_CHECKING:
    from .engine import Result
    from .verification import VerificationResult


@dataclass(frozen=True)
class WorkflowState:
    stages: str
    next_step: str
    action: str


def workflow_state(result: Result | None, exported_hash: str = "",
                   verification: VerificationResult | None = None) -> WorkflowState:
    """A DXF verdict applies only to the same bytes, inputs and race interval."""
    if result is None:
        return WorkflowState("名义打卡：未计算 / 已失效 ｜ 完整设计：未计算 ｜ 最终 DXF：未关联 ｜ 实车：未验证",
                             "① 设置打卡点和实测参数 → ② 计算设计", "calculate")
    nominal = bool(len(result.profile) and result.checkpoint_rows)
    visits = sum(bool(row["passed"]) for row in result.checkpoint_rows) if nominal else 0
    checkpoints = f"{visits}/{len(result.checkpoint_rows)}" if nominal else "未验证"
    failures = sum(c.status == "fail" for c in result.checks)
    design = f"{failures} 项不满足" if failures else (
        "约束及采样复算通过" if result.precision.get("stable") else "约束通过·采样未确认")
    dxf, action, step = "未检验", "export", "④ 导出分析文件 → ⑤ 核对独立参数并检验最终 DXF"
    if failures or not len(result.profile):
        action, step = "issues", "③ 点击问题定位 → 按依据调整 → 重新计算（不保证通过）"
    if exported_hash:
        action, step = "verify", "⑤ 检验本次导出：先核对独立参数与来源，再开始检验"
        if verification is not None:
            q = verification.inputs
            same_project = q.project.to_dict() == result.project.to_dict()
            same_run = abs(q.run_start) < 1e-9 and abs(q.run_end-result.stats["cam_usage"]*360) < 1e-6
            if verification.sha256 != exported_hash:
                dxf = "另份文件·本次未检验"
            elif not same_project or not same_run:
                dxf = "另组参数/区间·详见检验页"
            else:
                dxf = "独立运动学通过" if verification.kinematic_pass else "不通过 / 有未验证项"
                if verification.kinematic_pass and not failures and result.precision.get("stable"):
                    action, step = "limits", "⑥ 理论检查完成；下一步核对实际机构、制造装配与实车采集"
    return WorkflowState(f"名义打卡：{checkpoints} ｜ 完整设计：{design} ｜ 最终 DXF：{dxf} ｜ 实车：未验证", step, action)


def candidate_changes(original: Project, candidate: Project, *, compact: bool = False) -> str:
    """State exact changed planning parameters, without inventing a benefit."""
    changes = []
    before_fixed, after_fixed = original.to_dict(), candidate.to_dict()
    for key in ("tension", "tangent_offsets", "auxiliary_points"):
        before_fixed["route"].pop(key)
        after_fixed["route"].pop(key)
    if before_fixed != after_fixed:
        changes.append("比较条件发生变化，不可按相同硬件/目标/容差判定改善")
    for key, title in (("tension", "路线张力"), ("tangent_offsets", "切线偏置 °"),
                       ("auxiliary_points", "辅助点 [段索引,X,Y mm]")):
        before, after = getattr(original.route, key), getattr(candidate.route, key)
        if before != after:
            def display(value: object) -> str:
                if compact and isinstance(value, list) and len(value)>4:
                    return f"{len(value)} 项，前3项 {value[:3]}（完整值见导出项目/搜索记录）"
                return str(value)
            changes.append(f"{title}：{display(before)} → {display(after)}")
    return "；".join(changes) if changes else "规划参数未改变"


def candidate_explanation(original: Project, candidate: Result) -> str:
    failed = [c.name for c in candidate.checks if c.status == "fail"]
    return (candidate_changes(original, candidate.project, compact=True)
            + "；剩余失败项：" + ("、".join(failed) or "无主设计失败项")
            + ("；主设计采样复算通过" if candidate.precision.get("stable") else "；主设计采样稳定性未确认")
            + "；实车未验证。")
