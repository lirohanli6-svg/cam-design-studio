"""Versioned Windows build/release; preserve older share packages."""
from __future__ import annotations
import argparse
import importlib.metadata
import json
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TOOLS = ROOT/'build-tools'
if TOOLS.exists():
    sys.path.insert(0,str(TOOLS))
    os.environ['PYTHONPATH'] = str(TOOLS)
from camtool.project import Project
from camtool.version import APP_NAME, RELEASE_DATE, VERSION
from camtool.workbench import TRACK_TEMPLATE, demo_project


def populate(folder: Path) -> None:
    Project.demo().save(folder/'演示项目.cam.json')
    cases=folder/'可复现理论案例'; cases.mkdir(exist_ok=True)
    demo_project().save(cases/'正常方案.cam.json')
    demo_project(True).save(cases/'问题方案.cam.json')
    (folder/'实测轨迹格式模板_无采样点.csv').write_text(TRACK_TEMPLATE,encoding='utf-8-sig')
    docs = {'README.md':'使用说明.md','CHANGELOG.md':'CHANGELOG.md','docs/MODEL.md':'计算模型与验证.md',
            'docs/REVIEW.md':'历史代码审查记录.md','docs/VERIFICATION.md':'独立二次检验说明.md',
            'docs/VERIFICATION_VALIDATION.md':'二次检验历史验证记录_v1.1.0.md','docs/VERSIONING.md':'版本维护说明.md',
            'docs/WORKBENCH.md':'演示与方案分析操作说明.md','docs/WORKBENCH_VALIDATION.md':'新版功能验证记录.md',
            'docs/WORKBENCH_VALIDATION_v1.2.0.md':'功能历史验证记录_v1.2.0.md',
            'examples/waypoints.csv':'打卡点示例.csv'}
    for source,name in docs.items():
        shutil.copy2(ROOT/source,folder/name)
    (folder/'软件版本.json').write_text(json.dumps({'software':APP_NAME,'version':VERSION,
        'release_date':RELEASE_DATE,'project_schema_version':1},ensure_ascii=False,indent=2),encoding='utf-8')
    (folder/'先看这里_启动说明.txt').write_text(f'''{APP_NAME} {VERSION}
发布日期：{RELEASE_DATE}

请完整解压 ZIP，再双击同目录的 SolarCam.exe。适用于 64 位 Windows；无需 Python、MATLAB 或 API 密钥，可离线使用。
_internal 是全部运行依赖，licenses 是许可证；保留整个文件夹结构。分享时发送整个 ZIP，不要只发 exe。

设计：打开项目 / 演示项目 → 核对实车参数 → 计算设计 → 导出最终 cam.dxf。
界面：所有功能页签统一位于顶部一排；“同步演示 / 问题定位 / 优化对比 / 实测与扰动”各保留一处入口。点“场地与试跑”返回设计输入。
批量删除打卡点：在“打卡点”表格中按住 Ctrl 多选、Shift 连选，或点“全选”/Ctrl+A → 点“删除所选”或 Delete。可一次撤销整批删除，也可重做；改后请重新计算。
同步演示：顶端“同步演示” → 正常案例 / 问题案例 → 播放、暂停或拖动统一时间轴。可“返回原项目”，不会写入原文件。
问题定位：点击检查行，查看路线、凸轮、曲线位置及实际值、限制、依据与建议。
优化对比：运行本地优化后，原方案仍有效；在对比页自行选择保留原方案或采用候选，或保存两套项目。没有改善会明确显示。
地图编辑：设计页“场地与试跑”点击空白添加打卡点、拖动已有点；打卡点页可撤销 / 重做。改参后必须重新计算。
参数配置：车辆与机构页点击输入框高亮尺寸示意；保存 / 加载带来源的参数配置。来源未经软件核实。
实测与扰动：导入声明单位和坐标约定的传感器 CSV；本包只提供空格式模板，没有实测数据。固定原最终凸轮，按自定范围扫描驱动轮径 / 相位，结果属于理论预测。
计算 / 优化可点顶端“停止计算”，扰动点“停止分析”；编辑输入会使旧结果失效，过期后台结果不会覆盖新输入。
出发：红色 S0 是出发接触点，橙色箭头是正对本图一面观察的旋向；机构运动进度条最左可查看起跑装配姿态。
二次检验：点“二次检验” → 选择最终 DXF → 复制设计参数或独立填写实测值 → 核定来源、装配及计划区间 → 开始检验 → 逐项查看及导出报告。
读取项目参数时默认计划段为 0～240°，必须核定；复制已计算的设计可填入设计计划区间，但仍需核定实车。
检验整圈核查机构，只回算计划段比赛轨迹。名义数据仅用于比较，不驱动独立运动。
参数改变后，旧检验结果失效。“未验证”不可当成“通过”。几何、采样稳定及动画不保证实车性能。

版本及更新：软件内“版本与更新日志”，或本目录 CHANGELOG.md。
详细说明：演示与方案分析操作说明.md、独立二次检验说明.md；实际验证：新版功能验证记录.md、发布验证.json。
未完成弹簧/接触力、摩擦/力矩、轮胎打滑、车壳空间包络及实车试验。
''',encoding='utf-8-sig')
    licenses=folder/'licenses'; licenses.mkdir(exist_ok=True)
    python_license=Path(sys.base_prefix)/'LICENSE.txt'
    if python_license.is_file(): shutil.copy2(python_license,licenses/'Python.txt')
    for name in ('numpy','ezdxf','fonttools','pyparsing','charset-normalizer','typing-extensions','pyinstaller','packaging'):
        try: distribution=importlib.metadata.distribution(name)
        except importlib.metadata.PackageNotFoundError: continue
        for relative in distribution.files or []:
            if relative.name.lower().startswith(('license','copying','notice')):
                license_source=Path(str(distribution.locate_file(relative)))
                if license_source.is_file():
                    target=licenses/name/str(relative); target.parent.mkdir(parents=True,exist_ok=True)
                    shutil.copy2(license_source,target)
    for terms_source,name in [(folder/'_internal'/'_tcl_data'/'license.terms','Tcl.txt'),
                        (folder/'_internal'/'_tk_data'/'license.terms','Tk.txt')]:
        if terms_source.is_file(): shutil.copy2(terms_source,licenses/name)
    (licenses/'阅读说明.txt').write_text('Python.txt 包含 Python 及其随附 Tcl/Tk 等组件的原文条款。各依赖的目录保留许可证原相对路径，避免多个同名 LICENSE 相互覆盖。NumPy 及其捆绑组件原文也随 _internal/numpy-*.dist-info/licenses 提供。PyInstaller 引导程序例外条款见 pyinstaller 目录。',encoding='utf-8-sig')


def package_release(source: Path) -> tuple[Path,Path]:
    folder=ROOT.parent/f'太阳能小车凸轮设计工作台_v{VERSION}'
    archive=folder.parent/(folder.name+'.zip')
    if folder.exists() or archive.exists():
        raise RuntimeError(f'为保留已有交付拒绝覆盖：{folder}；新发布请按版本流程处理。')
    shutil.copytree(source,folder)
    with zipfile.ZipFile(archive,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=6) as package:
        for path in sorted(folder.rglob('*')):
            if path.is_file(): package.write(path,path.relative_to(folder.parent))
    with zipfile.ZipFile(archive) as package:
        if package.testzip() is not None: raise RuntimeError('分享 ZIP 完整性检查失败。')
    return folder,archive


def main() -> None:
    parser=argparse.ArgumentParser()
    parser.add_argument('--package',action='store_true',help='额外创建完整分享文件夹及 ZIP，不覆盖旧包')
    args=parser.parse_args()
    destination=ROOT/'dist'/f'v{VERSION}'
    version_tuple=tuple(int(x) for x in VERSION.split('.'))+(0,)
    resource=ROOT/'build'/'windows-version.txt'; resource.parent.mkdir(exist_ok=True)
    resource.write_text(f'''VSVersionInfo(ffi=FixedFileInfo(filevers={version_tuple!r}, prodvers={version_tuple!r}, mask=0x3f,
flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0,0)),
kids=[StringFileInfo([StringTable('080404B0',[
StringStruct('FileDescription','太阳能小车凸轮设计工作台'),StringStruct('FileVersion','{VERSION}'),
StringStruct('ProductName','太阳能小车凸轮设计工作台'),StringStruct('ProductVersion','{VERSION}'),
StringStruct('OriginalFilename','SolarCam.exe')])]),VarFileInfo([VarStruct('Translation',[2052,1200])])])
''',encoding='utf-8')
    subprocess.run([sys.executable,'-m','PyInstaller','--noconfirm','--distpath',str(destination),
                    '--workpath',str(ROOT/'build'/f'v{VERSION}'),'SolarCam.spec'],cwd=ROOT,check=True)
    folder=destination/'SolarCam'; populate(folder)
    print(f'Ready {VERSION}: {folder/"SolarCam.exe"}')
    if args.package: print(package_release(folder))


if __name__=='__main__': main()
