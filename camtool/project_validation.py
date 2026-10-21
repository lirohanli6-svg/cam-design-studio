"""Read-only input validation evidence, including the actual final DXF."""
from __future__ import annotations
import csv
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from .engine import Result, calculate
from .project import Project, ProjectError
from .export import export_design
from .candidate_audit import audit_candidate
from .verification import VerificationResult, export_verification
from .version import VERSION


@dataclass
class ProjectValidation:
    result: Result
    audit: VerificationResult
    summary: dict[str,Any]


def validate_project_file(source: Path, output: Path,
                          cancelled: Callable[[],bool] | None = None) -> ProjectValidation:
    source,output=Path(source),Path(output)
    if output.exists() and any(output.iterdir()):raise ProjectError("验证输出请选择空目录，避免覆盖项目或结果。")
    raw=source.read_bytes();digest=hashlib.sha256(raw).hexdigest()
    project=Project.load(source)
    result=calculate(project,cancelled)
    if result.project.to_dict()!=project.to_dict():raise ProjectError("验证过程修改了输入参数，结果不可采用。")
    output.mkdir(parents=True,exist_ok=True)
    files=export_design(result,output/'设计计算与最终DXF')
    audit,blob=audit_candidate(result,cancelled)
    # Preserve exactly the inspected file, including DXF metadata, not a re-save.
    files['dxf'].write_bytes(blob);audit.filename=str(files['dxf'])
    export_verification(audit,output/'最终DXF独立二次检验')
    (output/'初试_原文件只读快照.cam.json').write_bytes(raw)
    summary=dict(software_version=VERSION,source_file=str(source),source_sha256=digest,
                 kind='user_supplied_design_not_measured',parameters_unchanged=True,
                 nominal_pass=all(row['passed'] for row in result.checkpoint_rows),
                 nominal_max_checkpoint_mm=result.stats['checkpoint_error'],tolerance_mm=project.route.tolerance,
                 sampling=result.sampling,checkpoint_rows=result.checkpoint_rows,precision=result.precision,
                 main_design_pass=result.valid,failed_design_checks=[vars(c) for c in result.checks if c.status=='fail'],
                 independent_pass=audit.kinematic_pass,independent_path_points=len(audit.path),
                 independent_sha256=audit.sha256,
                 independent_failed_checks=[vars(c) for c in audit.checks if c.status=='不通过'],
                 limits='名义回算在容差内，不表示轮廓接触、机构或实车通过；未验证动力学。')
    if source.read_bytes()!=raw:raise ProjectError("验证期间原文件被其他操作更改，当前证据不能对应新的输入；未覆盖原文件。")
    (output/'初试验证结果.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
    with (output/'逐点打卡偏差.csv').open('w',encoding='utf-8-sig',newline='') as stream:
        writer=csv.writer(stream);writer.writerow(['点号','目标X_mm','目标Y_mm','名义回算偏差_mm','容差_mm','状态'])
        for row in result.checkpoint_rows:
            writer.writerow([row['number'],*row['target'],row['error_mm'],project.route.tolerance,'通过' if row['passed'] else '不通过'])
    rows=[f'# 初试项目实际软件验证 · {VERSION}',f'原文件：{source}',f'SHA256：{digest}',
          '本报告使用用户提供的设计参数，不是真实测量或实车试验。原文件与参数未修改。',
          f"名义回算：{'通过' if summary['nominal_pass'] else '不通过'}；最大打卡偏差 {result.stats['checkpoint_error']:.4f} mm / {project.route.tolerance:g} mm。",
          f"采样 {result.sampling['requested']} → {result.sampling['used']} 点；实际加密记录见结果JSON。",
          f"主设计整体：{'通过' if result.valid else '不通过'}；主设计采样稳定性：{'确认' if result.precision.get('stable') else '未确认'}。",
          f"最终DXF独立检验：{'通过' if audit.kinematic_pass else '不通过'}；独立轨迹 {len(audit.path)} 点。未求出轨迹时不能声称独立打卡通过。",'','仍不满足的设计项：']
    rows += ['- '+c.name+'：'+c.detail for c in result.checks if c.status=='fail']
    rows += ['','独立检验不通过项：']+['- '+c.name+'：'+c.detail for c in audit.checks if c.status=='不通过']
    (output/'初试验证说明.md').write_text('\n\n'.join(rows)+'\n',encoding='utf-8')
    return ProjectValidation(result,audit,summary)
