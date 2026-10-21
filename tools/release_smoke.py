"""Test the actual extracted ZIP in a clean runtime environment; attach proof."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
sys.path.insert(0,str(ROOT/'build-tools'))
from camtool.version import VERSION


def main() -> None:
    parser=argparse.ArgumentParser()
    parser.add_argument('--attach-proof',action='store_true')
    parser.add_argument('--project',type=Path)
    args=parser.parse_args()
    folder=ROOT.parent/f'太阳能小车凸轮设计工作台_v{VERSION}'
    archive=folder.parent/(folder.name+'.zip')
    qa=ROOT/'build'/'visual-qa'
    qa.mkdir(parents=True,exist_ok=True)
    output=Path(tempfile.mkdtemp(prefix=f'release-v{VERSION}-',dir=qa))
    extracted=output/'extracted'
    print('Testing release ZIP:',archive,flush=True)
    with zipfile.ZipFile(archive) as package:
        assert package.testzip() is None
        package.extractall(extracted)
    runtime=extracted/folder.name
    exe=runtime/'SolarCam.exe'
    assert json.loads((runtime/'软件版本.json').read_text(encoding='utf-8'))['version']==VERSION
    assert (runtime/'_internal'/'CHANGELOG.md').read_bytes()==(runtime/'CHANGELOG.md').read_bytes()
    digest=hashlib.sha256(exe.read_bytes()).hexdigest()
    assert digest==hashlib.sha256((folder/'SolarCam.exe').read_bytes()).hexdigest()
    env={k:v for k,v in os.environ.items() if k.upper() not in ('PYTHONPATH','PYTHONHOME','TCL_LIBRARY','TK_LIBRARY')}
    windows=Path(env.get('SystemRoot','C:/Windows'))
    env['PATH']=str(windows/'System32')+os.pathsep+str(windows)
    startup=subprocess.STARTUPINFO()
    startup.dwFlags|=subprocess.STARTF_USESHOWWINDOW
    startup.wShowWindow=subprocess.SW_HIDE
    smoke=output/'packaged-smoke'
    command=[str(exe),'--smoke-dir',str(smoke)]
    if args.project is not None:command.extend(['--smoke-project',str(args.project.resolve())])
    completed=subprocess.run(command,cwd=runtime,env=env,
                              startupinfo=startup,creationflags=subprocess.CREATE_NO_WINDOW,timeout=180)
    error=smoke/'smoke-error.txt'
    if error.exists():
        raise RuntimeError(error.read_text(encoding='utf-8'))
    assert completed.returncode==0,completed.returncode
    data=json.loads((smoke/'smoke.json').read_text(encoding='utf-8'))
    for name in ('ok','valid','frozen','project_roundtrip','dxf_audit','independent_verification','verification_report_export',
                 'synchronized_demo','problem_location','optimization_comparison','synthetic_csv_distinguished','fixed_dxf_prediction','analysis_export',
                 'bulk_waypoint_delete_undo','single_navigation','inline_waypoint_edit','isolated_waypoint_add','waypoint_validation','auxiliary_background_search','auxiliary_final_dxf_verification'):
        assert data[name],name
    assert data['software_version']==VERSION and data['independent_path_points']>10
    assert data['dual_point_editor']['callback_errors']==[]
    assert all(value is True for name,value in data['dual_point_editor'].items() if name!='callback_errors')
    assert data['auxiliary_candidate_explicit_adoption'] is True
    assert data['figure_views']['callback_errors']==[]
    assert all(value is True for name,value in data['figure_views'].items() if name not in ('callback_errors','drag_measurement'))
    result=json.loads((smoke/'二次检验'/'二次检验结果.json').read_text(encoding='utf-8'))
    assert result['software_version']==VERSION
    assert result['sha256']==hashlib.sha256((smoke/'cam.dxf').read_bytes()).hexdigest()
    assert not any(c['status']=='不通过' for c in result['checks'])
    candidate_folder=smoke/'辅助搜索与最终DXF检验'
    candidate=json.loads((candidate_folder/'候选DXF独立二次检验/二次检验结果.json').read_text(encoding='utf-8'))
    assert candidate['software_version']==VERSION
    assert candidate['sha256']==hashlib.sha256((candidate_folder/'候选方案/cam.dxf').read_bytes()).hexdigest()
    assert not any(c['status']=='不通过' for c in candidate['checks'])
    assert candidate['nominal_comparison_only']==[]
    proof={**data,'executable_sha256':digest,'clean_python_environment':True,
           'system_only_path':True,'extracted_zip_runtime':True,'exit_code':completed.returncode,
           'verification_input_sha256':result['sha256'],'verification_metrics':result['metrics'],
           'checks':result['checks'],'validation_directory':str(output)}
    proof['valid_scope']='内置正常理论样例；用户项目单独记录在 user_project_regression'
    proof['auxiliary_candidate_sha256']=candidate['sha256']
    proof['auxiliary_candidate_checks']=candidate['checks']
    proof['auxiliary_candidate_metrics']=candidate['metrics']
    proof['auxiliary_candidate_has_no_nominal_motion_input']=True
    baseline=ROOT/'build'/f'v{VERSION}-baseline'/'preserved-v1.1.0.json'
    if baseline.is_file():
        saved=json.loads(baseline.read_text(encoding='utf-8-sig'))
        saved=[saved] if isinstance(saved,dict) else saved
        for old in saved:
            assert hashlib.sha256(Path(old['Path']).read_bytes()).hexdigest().upper()==old['Hash'].upper()
        proof['previous_release_files_unchanged']=saved
    preserved=ROOT/'build'/f'v{VERSION}-baseline'/'preserved-releases.json'
    if preserved.is_file():
        saved=json.loads(preserved.read_text(encoding='utf-8'))
        missing=[]
        verified=[]
        for old_folder in saved['folders']:
            previous=Path(old_folder['path'])
            if not previous.is_dir():
                missing.append(str(previous));continue
            paths=sorted(p for p in previous.rglob('*') if p.is_file())
            manifest='\n'.join(p.relative_to(previous).as_posix()+' '+hashlib.sha256(p.read_bytes()).hexdigest() for p in paths)
            assert len(paths)==old_folder['files'] and hashlib.sha256(manifest.encode('utf-8')).hexdigest()==old_folder['tree_sha256']
            verified.append(str(previous))
        for old_file in saved['files']:
            if not Path(old_file['path']).is_file():
                missing.append(old_file['path']);continue
            assert hashlib.sha256(Path(old_file['path']).read_bytes()).hexdigest()==old_file['sha256']
            verified.append(old_file['path'])
        proof['previous_releases_preserved']=saved
        proof['previous_release_preservation_status']='部分未验证：基线路径缺失' if missing else '通过'
        proof['previous_releases_verified_unchanged']=verified
        proof['missing_previous_baselines']=missing
    proof_path=output/'发布验证.json'
    proof_path.write_text(json.dumps(proof,ensure_ascii=False,indent=2),encoding='utf-8')
    if args.project is not None:
        assert data['user_auxiliary_editor_compatibility'] is True
        assert data['user_search_candidate_editor']['explicit_adopt_restore'] is True
        assert data['user_search_candidate_editor']['automatic_current_input_replacement'] is False
        assert data['user_search_candidate_editor']['controls']
        user=data['user_project_regression']
        assert user['source_sha256']==hashlib.sha256(args.project.read_bytes()).hexdigest()
        assert user['nominal_pass'] and user['parameters_unchanged']
        assert user['independent_sha256']==hashlib.sha256((smoke/'初试项目验证/设计计算与最终DXF/cam.dxf').read_bytes()).hexdigest()
        proof['user_project_regression']=user
        proof_path.write_text(json.dumps(proof,ensure_ascii=False,indent=2),encoding='utf-8')
    if args.attach_proof:
        if args.project is not None:
            shutil.copytree(smoke/'初试项目验证',folder/'初试项目验证',dirs_exist_ok=True)
        shutil.copy2(proof_path,folder/proof_path.name)
        record=ROOT/'docs'/'WORKBENCH_VALIDATION.md'
        text=record.read_text(encoding='utf-8')
        addition=(f'\n已在实际 ZIP 解压目录完成：独立 EXE 退出码 0；内置正常理论样例的设计计算、最终DXF独立检验及报告导出流程通过。'
                  f'清除 Python/Tcl 环境变量，PATH 仅含 Windows 系统目录；独立轨迹 {data["independent_path_points"]} 个点。'
                  f'同步演示、问题定位、优化对比、合成 CSV 分类、固定 DXF 扰动与分析导出均通过。'
                  f'本版单元格提交/非法输入、独立新增、批量删除/撤销与统一页签切换通过；现存旧版指纹核对未改变。'
                  f'基线缺失路径 {proof.get("missing_previous_baselines",[])}，这些保留项未验证，见凭证。'
                  f'辅助点真实后台搜索、最终候选DXF独立检验和完整报告导出通过。'
                  f'完整执行凭证见 `发布验证.json`。这仍是本机软件验证，不是另一台电脑或实车试验。\n')
        if args.project is not None:
            user=proof['user_project_regression']
            addition += (f"\n用户提供项目的解压EXE实际结果：名义打卡 {sum(row['passed'] for row in user['checkpoint_rows'])}/{len(user['checkpoint_rows'])}，最大偏差 {user['nominal_max_checkpoint_mm']:.4f} mm；"
                         f"设计整体通过={user['main_design_pass']}，最终DXF独立通过={user['independent_pass']}，独立轨迹{user['independent_path_points']}点。"
                         f"具体失败原因与哈希见交付文件夹「初试项目验证」，不得把流程执行成功理解成用户项目几何或实车通过。\n")
        if '已在实际 ZIP 解压目录完成' not in text:
            text=text.replace('\n## 未覆盖\n',addition+'\n## 未覆盖\n')
            record.write_text(text,encoding='utf-8')
        for target in (folder/'新版功能验证记录.md', ROOT/'dist'/f'v{VERSION}'/'SolarCam'/'新版功能验证记录.md'):
            shutil.copy2(record,target)
        staging=archive.with_name(archive.name+'.tmp')
        with zipfile.ZipFile(staging,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=6) as package:
            for path in sorted(folder.rglob('*')):
                if path.is_file():
                    package.write(path,path.relative_to(folder.parent))
        with zipfile.ZipFile(staging) as package:
            assert package.testzip() is None
            assert hashlib.sha256(package.read(folder.name+'/SolarCam.exe')).hexdigest()==digest
        staging.replace(archive)
    print('PASS: extracted executable starts, calculates, independently verifies DXF, and exports reports.',flush=True)
    print('Proof:',proof_path,flush=True)
    print('ZIP MB:',round(archive.stat().st_size/1024/1024,1),flush=True)


if __name__=='__main__':
    main()
