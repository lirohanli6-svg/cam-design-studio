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


def configure_tcl_runtime() -> None:
    """Embedded Python may have _tkinter but no default Tcl data location.

    Fail before publishing instead of letting PyInstaller silently exclude the
    complete tkinter package. Full CPython installations use their normal data.
    """
    candidates=[(Path(os.environ.get('TCL_LIBRARY','')),Path(os.environ.get('TK_LIBRARY',''))),
                (ROOT/'build-tools/tcl-runtime/tcl8.6',ROOT/'build-tools/tcl-runtime/tk8.6'),
                (Path(sys.base_prefix)/'tcl/tcl8.6',Path(sys.base_prefix)/'tcl/tk8.6'),
                (ROOT/'dist/SolarCam/_internal/_tcl_data',ROOT/'dist/SolarCam/_internal/_tk_data')]
    for tcl,tk in candidates:
        if (tcl/'init.tcl').is_file() and (tk/'tk.tcl').is_file():
            env={**os.environ,'TCL_LIBRARY':str(tcl.resolve()),'TK_LIBRARY':str(tk.resolve())}
            probe=subprocess.run([sys.executable,'-c','import tkinter; tkinter.Tcl().eval("info library")'],
                                 env=env,capture_output=True,timeout=15,
                                 creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
            if probe.returncode==0:
                os.environ['TCL_LIBRARY']=env['TCL_LIBRARY'];os.environ['TK_LIBRARY']=env['TK_LIBRARY']
                break
    from PyInstaller.utils.hooks.tcl_tk import tcltk_info  # type: ignore[import-untyped]
    if not tcltk_info.available or not tcltk_info.data_files or not tcltk_info.tkinter_fully_usable:
        raise RuntimeError('构建缺少可用Tcl/Tk，拒绝生成无法启动的发布包。请配置TCL_LIBRARY/TK_LIBRARY或安装完整Python Tcl/Tk。')


def populate(folder: Path) -> None:
    Project.demo().save(folder/'演示项目.cam.json')
    cases=folder/'可复现理论案例'; cases.mkdir(exist_ok=True)
    demo_project().save(cases/'正常方案.cam.json')
    demo_project(True).save(cases/'问题方案.cam.json')
    assistant_case=demo_project(); assistant_case.cam.base_radius=25
    assistant_case.save(cases/'改进助手_压力角理论案例.cam.json')
    shutil.copytree(ROOT/'examples/辅助点理论案例',cases/'辅助点理论案例',dirs_exist_ok=True)
    (folder/'实测轨迹格式模板_无采样点.csv').write_text(TRACK_TEMPLATE,encoding='utf-8-sig')
    docs = {'README.md':'使用说明.md','CHANGELOG.md':'CHANGELOG.md','docs/MODEL.md':'计算模型与验证.md',
            'docs/REVIEW.md':'历史代码审查记录.md','docs/VERIFICATION.md':'独立二次检验说明.md',
            'docs/VERIFICATION_VALIDATION.md':'二次检验历史验证记录_v1.1.0.md','docs/VERSIONING.md':'版本维护说明.md',
            'docs/WORKBENCH.md':'演示与方案分析操作说明.md','docs/WORKBENCH_VALIDATION.md':'新版功能验证记录.md',
            'docs/WORKBENCH_VALIDATION_v1.2.0.md':'功能历史验证记录_v1.2.0.md',
            'docs/WORKBENCH_VALIDATION_v1.3.0.md':'功能历史验证记录_v1.3.0.md',
            'docs/WORKBENCH_VALIDATION_v1.4.0.md':'功能历史验证记录_v1.4.0.md',
            'docs/AUXILIARY.md':'辅助点搜索与凸轮几何说明.md',
            'docs/WORKBENCH_VALIDATION_v1.5.0.md':'功能历史验证记录_v1.5.0.md',
            'docs/REQUIRED_POINTS.md':'必过点与回算偏差说明.md',
            'docs/POINT_EDITING.md':'打卡点与辅助点编辑说明.md',
            'docs/VIEW_CONTROLS.md':'缩放平移与拖动说明.md',
            'docs/IMPROVEMENT_ASSISTANT.md':'设计改进助手操作说明.md',
            'docs/WORKBENCH_VALIDATION_v1.7.0.md':'功能历史验证记录_v1.7.0.md',
            'docs/WORKBENCH_VALIDATION_v1.8.0.md':'功能历史验证记录_v1.8.0.md',
            'docs/WORKBENCH_VALIDATION_v1.6.0.md':'功能历史验证记录_v1.6.0.md',
            'docs/WORKBENCH_VALIDATION_v1.5.1.md':'功能历史验证记录_v1.5.1.md',
            'examples/waypoints.csv':'打卡点示例.csv'}
    for source,name in docs.items():
        shutil.copy2(ROOT/source,folder/name)
    (folder/'软件版本.json').write_text(json.dumps({'software':APP_NAME,'version':VERSION,
        'release_date':RELEASE_DATE,'project_schema_version':1},ensure_ascii=False,indent=2),encoding='utf-8')
    (folder/'先看这里_启动说明.txt').write_text(f'''{APP_NAME} {VERSION}
发布日期：{RELEASE_DATE}

完整保留此文件夹，双击同目录的 SolarCam.exe。如收到ZIP请先完整解压。适用于 64 位 Windows；无需 Python、MATLAB 或 API 密钥，可离线使用。
_internal 是全部运行依赖，licenses 是许可证；保留整个文件夹结构。移动或分享时保留整个文件夹，不要只复制exe。

新功能：检查报告 → 设计改进助手（或顶栏改进助手），打开自动诊断当前输入，无需先勾选参数。先读已确认事实、待验证原因和相关部件。
体验：打开“可复现理论案例/改进助手_压力角理论案例.cam.json” → 改进助手 → 当前问题与可能原因 → 确认可改部件：允许凸轮尺寸修改，并明确允许中位高度/安装坐标联动 → 确认授权 → 开始实际试算 → 查看候选及完整检查 → 显式采用或保留原案。默认范围只用于探索，不是机械安全范围；高级页可细调范围与预算。
保存：采用候选只产生未保存草稿；点击另存为选择您自己的目录。导出方案选择新的空目录，包含实际审计DXF与哈希。不覆盖案例或原项目。
边界：有限采样的二维理论模型；独立检验通过不等于加工放行或实车通过。详见设计改进助手操作说明.md。

设计：打开项目 / 演示项目 → 核对实车参数 → 计算设计 → 导出最终 cam.dxf。
界面：所有功能页签统一位于顶部一排；“同步演示 / 问题定位 / 优化对比 / 实测与扰动”各保留一处入口。点“场地与试跑”返回设计输入。
细节：场地/凸轮图滚轮以光标为中心缩放，中键平移；提供放大、缩小、适应全部、恢复视图及缩放比例。“放大查看”独立窗口可调整/最大化，数据、选择和播放同步，观察视图各自保留。问题定位后的“定位并放大”须明确点击。
场地模式：浏览模式左拖/中拖平移不改点，编辑点位模式左键添加/拖点；点位类型另选打卡点/辅助点。拖动不反复清空整图、不启动工程计算，只更新对应行；一次拖动一条历史，松开保留最终坐标。详见“缩放平移与拖动说明.md”。
编辑已有打卡点：单击表格中的 X/Y 单元格，黄色格表示编辑中；Enter 提交、Esc 取消，Tab/Shift+Tab 校验并保存当前格后切换。非法输入保留原坐标及草稿，在表格下方就近提示；无数值变化不记历史。
新增点位：只使用独立“新增点位”的 X、Y 和“添加”。选行或改已有格不会填入或覆盖新增输入。相邻重合提示但保留，首尾同点可表示返回起点，不自动删除。
批量操作：编号列选行，Ctrl 多选、Shift 连选，或“全选”/Ctrl+A → “删除”或 Delete。选择/排序/撤销/重做在表格旁；CSV/地图另成组。每次有效提交、新增或一次拖动支持一条撤销记录，修改后须重新计算。
同步演示：顶端“同步演示” → 正常案例 / 问题案例 → 播放、暂停或拖动统一时间轴。可“返回原项目”，不会写入原文件。
问题定位：点击检查行，查看路线、凸轮、曲线位置及实际值、限制、依据与建议。
优化对比：运行本地优化后，原方案仍有效；在对比页自行选择保留原方案或采用候选，或保存两套项目。没有改善会明确显示。
辅助点：在“辅助”页单击 X/Y/所属区间直接编辑，Enter提交、Esc取消、Tab/Shift+Tab换格；“新增辅助点”独立填写 X/Y、所属区间及添加。Ctrl/Shift多选、批量删除、上/下移、撤销重做各自管理；切页保留草稿，不提交另一类点。排序只改显示顺序，同区间均值引导，不要求穿过、不计入打卡。详见“打卡点与辅助点编辑说明.md”。场地页可填写起跑前轮角和转向变化速度规划限值。
自动搜索：优化对比页设置半径、比赛段范围、点数上限和次数/秒预算 → 搜索辅助点 → 查看真实8项指标及粗细采样稳定性 → 显式选择原方案或候选。搜索结束的最终DXF会独立反算接触、转角与轨迹，失败或未验证如实显示。“保存搜索及检验”选择空目录保存完整证据，另有专用停止搜索按钮。搜索预算不含最终复算和独立检验；它们也可取消。
必过点回算：轮廓点数作为起始精度；漏点或回算误差过大时自动加密至最多4000点。报告显示请求/实际点数和每次误差。检查报告内“逐点打卡偏差”可查看每点容差与状态，点击行定位地图；红色漏点不代表已通过。名义打卡通过仍须单独检查几何及最终DXF。
问题接触图：点击压力角/过切/干涉行，绿圆为滚子/球，红粗线为最近/侵入轮廓段，显示间隙；回程不映射虚构比赛位置。
地图编辑：地图上方明确选择“打卡点/辅助点”模式，蓝圆为打卡、紫菱“辅N·段N”为辅助；点击选择或空白添加当前类型、拖动当前类型，表格同步高亮编号。辅助地图新增使用新增区里的所属区间，不覆盖 X/Y 输入。一拖一条历史；Ctrl+Z/Y及按钮按当前类型撤销重做。任一点变化后必须重新计算，旧导出及过期后台结果失效。
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


def copy_release_folder(source: Path) -> Path:
    folder=ROOT.parent/f'太阳能小车凸轮设计工作台_v{VERSION}'
    if folder.exists() or folder.with_name(folder.name+'.zip').exists():
        raise RuntimeError(f'保留已有交付，拒绝覆盖：{folder}')
    shutil.copytree(source,folder)
    return folder


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
    delivery=parser.add_mutually_exclusive_group()
    delivery.add_argument('--package',action='store_true',help='额外创建完整分享文件夹及 ZIP，不覆盖旧包')
    delivery.add_argument('--folder-only',action='store_true',help='只创建独立运行文件夹，不生成ZIP或上传')
    args=parser.parse_args()
    configure_tcl_runtime()
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
    subprocess.run([sys.executable,'-m','PyInstaller','--clean','--noconfirm','--distpath',str(destination),
                    '--workpath',str(ROOT/'build'/f'v{VERSION}'),'SolarCam.spec'],cwd=ROOT,check=True)
    folder=destination/'SolarCam'; populate(folder)
    print(f'Ready {VERSION}: {folder/"SolarCam.exe"}')
    if args.package: print(package_release(folder))
    elif args.folder_only: print(copy_release_folder(folder))


if __name__=='__main__': main()
