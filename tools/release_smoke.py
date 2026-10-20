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
    completed=subprocess.run([str(exe),'--smoke-dir',str(smoke)],cwd=runtime,env=env,
                              startupinfo=startup,creationflags=subprocess.CREATE_NO_WINDOW,timeout=180)
    error=smoke/'smoke-error.txt'
    if error.exists():
        raise RuntimeError(error.read_text(encoding='utf-8'))
    assert completed.returncode==0,completed.returncode
    data=json.loads((smoke/'smoke.json').read_text(encoding='utf-8'))
    for name in ('ok','valid','frozen','project_roundtrip','dxf_audit','independent_verification','verification_report_export',
                 'synchronized_demo','problem_location','optimization_comparison','synthetic_csv_distinguished','fixed_dxf_prediction','analysis_export',
                 'bulk_waypoint_delete_undo','single_navigation'):
        assert data[name],name
    assert data['software_version']==VERSION and data['independent_path_points']>10
    result=json.loads((smoke/'二次检验'/'二次检验结果.json').read_text(encoding='utf-8'))
    assert result['software_version']==VERSION
    assert result['sha256']==hashlib.sha256((smoke/'cam.dxf').read_bytes()).hexdigest()
    assert not any(c['status']=='不通过' for c in result['checks'])
    proof={**data,'executable_sha256':digest,'clean_python_environment':True,
           'system_only_path':True,'extracted_zip_runtime':True,'exit_code':completed.returncode,
           'verification_input_sha256':result['sha256'],'verification_metrics':result['metrics'],
           'checks':result['checks'],'validation_directory':str(output)}
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
        for old_folder in saved['folders']:
            previous=Path(old_folder['path'])
            paths=sorted(p for p in previous.rglob('*') if p.is_file())
            manifest='\n'.join(p.relative_to(previous).as_posix()+' '+hashlib.sha256(p.read_bytes()).hexdigest() for p in paths)
            assert len(paths)==old_folder['files'] and hashlib.sha256(manifest.encode('utf-8')).hexdigest()==old_folder['tree_sha256']
        for old_file in saved['files']:
            assert hashlib.sha256(Path(old_file['path']).read_bytes()).hexdigest()==old_file['sha256']
        proof['previous_releases_preserved']=saved
    proof_path=output/'发布验证.json'
    proof_path.write_text(json.dumps(proof,ensure_ascii=False,indent=2),encoding='utf-8')
    if args.attach_proof:
        shutil.copy2(proof_path,folder/proof_path.name)
        record=ROOT/'docs'/'WORKBENCH_VALIDATION.md'
        text=record.read_text(encoding='utf-8')
        addition=(f'\n已在实际 ZIP 解压目录完成：独立 EXE 退出码 0；设计计算、最终 DXF 导出、独立二次检验及报告导出通过。'
                  f'清除 Python/Tcl 环境变量，PATH 仅含 Windows 系统目录；独立轨迹 {data["independent_path_points"]} 个点。'
                  f'同步演示、问题定位、优化对比、合成 CSV 分类、固定 DXF 扰动与分析导出均通过。'
                  f'本版批量删除/撤销与统一页签切换通过；旧版目录及 ZIP 指纹核对未改变。'
                  f'完整执行凭证见 `发布验证.json`。这仍是本机软件验证，不是另一台电脑或实车试验。\n')
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
