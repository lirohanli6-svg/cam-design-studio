# 项目状态与会话交接

更新日期：2026-10-21。本文件是当前开发状态的唯一详细摘要；开发规则、模块定位和测试选择见 [AGENTS.md](AGENTS.md)。功能清单来自当前源码和现有说明，是否通过必须另看验证记录。

## 当前基线

- 主仓库 `E:\太阳能小车\cam-desktop`，入口 `main.py → camtool.ui.App`；父目录是交付/设计资料，其他源码副本不是默认修改对象。
- 当前源码版本 **1.7.0 / 2026-10-21**，schema保持1。本任务已完成发布门禁；源码、窗口/EXE属性、报告、说明及包名均为1.7.0。最终程序和分享包见最近交接。
- 已核实上一版 **v1.6.0** 确已发布：完整目录/ZIP、版本文件和 `build/final-release-check-v1.6.0.json` 对齐，85测试/25类型文件/7真实GUI及解压EXE记录见 `docs/WORKBENCH_VALIDATION_v1.6.0.md`。原文档对1.6.0“版本未对齐”的摘要已过时，现更正。
- 开始前有大量已有未提交/未跟踪实现，继续保留；本次快照与旧版/用户文件指纹在 `build/v1.7.0-baseline/`。

## 已实现功能与关键文件

| 功能 | 入口/关键文件 |
|---|---|
| 项目 JSON 保存/重开、点位 CSV 导入、参数与旧 schema | `camtool/project.py`；公开入口 `camtool/__init__.py` |
| 必过点顺序路线、整体样条、软辅助形状、传感器/三轮车运动 | `camtool/route_shape.py`、`camtool/route_metrics.py`、`camtool/engine.py` |
| 平推连杆与摆臂顶球两种理想机构、凸轮/回程、压力/过切/接触检查 | `camtool/engine.py`、`camtool/cam_geometry.py` |
| 名义回算、自动加密、逐点打卡偏差、粗细采样稳定性 | `camtool/engine.py`、`camtool/route_metrics.py`、`tests/test_required_points.py` |
| 本地路线优化、有限预算辅助搜索、原案/候选保留与 DXF 审计 | `camtool/engine.py:optimize`、`camtool/auxiliary_search.py`、`camtool/candidate_audit.py` |
| 打卡/辅助点表格、单元格草稿、地图编辑、批量操作、撤销重做 | `camtool/waypoints.py`、`camtool/auxiliary_editor.py`、`camtool/auxiliary_ui.py`、`camtool/ui.py` |
| 两类点表统一原生验收入口 | `camtool/editor_smoke.py`、`tools/dual_point_ui_check.py` |
| 光标缩放/等比例平移、保留绘制、独立窗口、增量拖动 | `camtool/viewport.py`、`camtool/plot_views.py`、`camtool/ui.py:map_* / flush_drag`、`camtool/view_smoke.py` |
| 图表、机构运动、同步时间轴、诊断、优化对比 | `camtool/plotting.py`、`camtool/dimensions.py`、`camtool/workbench.py`、`camtool/workbench_ui.py` |
| 参数配置、带来源与单位的实测轨迹、固定最终 DXF 的扰动分析 | `camtool/workbench.py`、`camtool/workbench_ui.py` |
| DXF/CSV/检查报告/项目快照、出发点与旋向参考 | `camtool/export.py`、`camtool/markings.py` |
| 最终 DXF 独立接触/转角/轨迹检验，来源、哈希与报告 | `camtool/verification.py`、`camtool/verification_ui.py`、`camtool/project_validation.py` |
| 桌面启动、自动冒烟入口、Windows 构建与版本化交付 | `main.py`、`build_app.py`、`SolarCam.spec`、`tools/release_smoke.py`、`camtool/version.py` |

主依赖链为 `main → ui → engine → project/route_shape/route_metrics/cam_geometry`；`Result → export → DXF`，再经 `candidate_audit → verification` 独立检验。界面与 `workbench` 消费结果；`verification` 的运动计算不能依赖生成引擎。详细依赖和测试对应表保存在 `AGENTS.md`，不在此重复。

按需阅读：领域用语 [CONTEXT.md](CONTEXT.md)，数学模型 [docs/MODEL.md](docs/MODEL.md)，辅助点 [docs/AUXILIARY.md](docs/AUXILIARY.md)，必过点 [docs/REQUIRED_POINTS.md](docs/REQUIRED_POINTS.md)，独立检验 [docs/VERIFICATION.md](docs/VERIFICATION.md)，工作台 [docs/WORKBENCH.md](docs/WORKBENCH.md)，发布 [docs/VERSIONING.md](docs/VERSIONING.md)。

## 已知问题、风险与待办

1. **视图/性能边界**：主图和独立窗口使用保留绘制，拖动合并约16ms事件、一次失效、单行预览、松开一条历史；没有新增工程数据采样。高密图形、多窗口、不同显卡/远程桌面仍可能增加耗时，不承诺固定帧率。
2. **历史用户方案仍失败**：1.5.1 的“初试”案例在原 20 mm 容差下名义打卡 10/10、最大约 13.639 mm，但转角/压力/行程/接触等约束不通过，最终 DXF 自交、独立轨迹未生成。它不是可直接制造的通过方案。来源：[docs/WORKBENCH_VALIDATION_v1.5.1.md](docs/WORKBENCH_VALIDATION_v1.5.1.md)；输入回归副本 `tests/fixtures/required_loop.cam.json`。本版已只读重跑原文件，仍为上述失败；当前实际证据见本版验证记录和发布凭证。
3. **采样与搜索边界**：实际凸轮点数上限 4000；触顶、粗细差异或有限预算搜索不能证明连续几何稳定或全局最优。比赛/回程接缝不保证二阶变化率完全匹配，详见模型和辅助点文档。
4. **实物边界**：两种理想平面模型尚不能视为用户 YDI 装配认证；弹簧、脱离接触、摩擦、驱动力矩、打滑、空间包络、制造误差、实车轨迹及另一台干净 Windows 机器未获本次验证。
5. **本机开发环境需显式选择**：`py -0p` 指向的 `E:\python\python.exe` 本次不可用；工具提供的 Python 3.12.14 可用，但默认没有 ezdxf/mypy。仓库已有 `build-tools/` 本地依赖，可按需仅向检查进程注入模块路径。不要把机器路径写入应用逻辑，也不要默认所有新环境可直接执行 `python`。

## 验证基线

- **历史 1.5.1**：文档记录 79 项自动测试、23 个源文件类型检查、六套真实 Tk 检查与 ZIP 解压 EXE 验证。仅适用于该历史版本，详见上述带版本验证文件。
- **当前测试分布**：`test_workflow.py`、`test_safety.py`、`test_verification.py`、`test_workbench.py`、`test_waypoints.py`、`test_cam_markings.py`、`test_auxiliary.py`、`test_required_points.py`、`test_auxiliary_editor.py`、`test_viewport.py`；按模块选择，执行命令见 `AGENTS.md`。
- 本次功能任务已实际运行93项自动测试（184.775秒）、28源文件类型检查及相关真实GUI；正式门禁结果与详细证据更新在最近交接及本版验证记录。旧文档初建验证仍作为历史保留。

## 历史流程记录

原文档初建与个人Skill安装记录保存在 [本次基线快照](build/v1.7.0-baseline/PROJECT_STATUS.md)，当时仅文档检查及14项点文档测试，不代表任何版本完整发布通过。开发规则仍是AGENTS.md，Skill只有被加载时才生效。

## 最近功能发布交接（2026-10-21：1.7.0已完成）

- **目标/改动**：场地/凸轮光标锚点缩放、中键平移、等比例网格/比例尺、独立窗口、浏览编辑隔离、播放/选择保留视图及显式定位；保留绘制、16ms合并拖动、单行预览、一次失效和一次历史。关键文件 `viewport.py`、`plot_views.py`、`ui.py:map_* / flush_drag / render`、`plotting.Plot`、`workbench_ui.py`；未改核心数学、硬件、schema、独立求解器或导出尺寸。
- **发布**：`E:\太阳能小车\太阳能小车凸轮设计工作台_v1.7.0\SolarCam.exe`；同级 `太阳能小车凸轮设计工作台_v1.7.0.zip`。EXE SHA256 `b50a97d4bec84a4403fed95a39b73d97b877223061d0c30c546dc56432937cdc`。旧版/原文件指纹基线 `build/v1.7.0-baseline`。
- **命令/环境/结果**：本机可用Python 3.12、`PYTHONPATH=build-tools`，GUI使用该目录匹配Tcl/Tk；`python -m unittest discover -s tests -q` **93通过/184.775s**，mypy **28源文件通过**；8个GUI脚本通过。`main.py --smoke-dir`源码链及`tools/release_smoke.py --attach-proof --project <初试原文件>`真实ZIP解压EXE链均通过。后者清除Python/Tcl环境变量、PATH仅Windows系统目录。具体命令/证据见 [本版验证记录](docs/WORKBENCH_VALIDATION.md)。
- **闪屏/性能**：80事件原110全清/80表格重建/2254.37ms，最终0全清/0重建/0计算/27.32ms批量。原生EXE连续12次刷新中位1.051ms、最大2.936ms；主图与放大窗口真实鼠标拖出松开、放大拖动、首尾同点、撤销/重做、同步及相机保持均通过。仅本机软件测量，不保证不同负载/显卡固定帧率。输入NaN/Inf/反向边界保护也通过。
- **真实项目**：初试原文件哈希仍为`91afc6089f26787de2193f241cc92a47ff69608cbfa73422e3a64470dee247d7`，名义10/10、最大13.638777mm；整体设计及最终DXF独立检验均失败/独立轨迹0点，仍不是可制造或实车通过方案。搜索候选只显式采用测试后恢复，不自动替换或更改原文件。
- **证据**：[最终发布凭证](../太阳能小车凸轮设计工作台_v1.7.0/发布验证.json)、`build/final-release-check-v1.7.0.json`、`build/visual-qa/views-v1.7.0`；打包内有说明、源/解压EXE界面验收JSON、截图及初试完整报告。旧版四个目录/三个ZIP和用户原文件最终再核对不变。
- **未覆盖/限制**：无第二台机器、其他显卡/远程桌面、长时压力、制造或实车/动力学认证；显示不增加工程采样，多窗口/高密图形仍增绘制成本，极限帧率未保证。既有辅助40点、各100条会话历史和采样/搜索限制保留。
- **下一步/最短恢复**：本需求已完成，无待发布步骤。新任务先读AGENTS对应模块行、当前Git状态，再按`FigurePanel`→`Viewport`或`ui.map_*`定向修改。若有新的闪屏环境，先运行`tools/drag_probe.py <新日志>`及`tools/views_ui_check.py`，保留新证据，勿直接沿用此版本通过结论。

## 最近交接（2026-10-21：GitHub v1.7.0 源码同步）

- **目标与结果**：将当前主工作树的 v1.7.0 同步到公开仓库 [lirohanli6-svg/cam-design-studio](https://github.com/lirohanli6-svg/cam-design-studio) 的 `main`。源码提交为 [`407a48fe8808c7afd4b6f5607a9a28b4173fc85d`](https://github.com/lirohanli6-svg/cam-design-studio/commit/407a48fe8808c7afd4b6f5607a9a28b4173fc85d)，保留远端原提交历史，未强制推送。
- **范围**：84 个源码、测试、示例和文档文件，其中 71 个新增或更新，未删除远端文件；保留原 `web-demo/` 四个文件及 `作品简介.md`。GitHub README 在本地使用说明前保留作品入口，并明确当前源码为 v1.7.0、在线视频与 PPT 为早期 v1.3.0 材料。生成的公开 README 和同步脚本仅位于忽略的 `build/github-sync-v1.7.0/`，没有覆盖本地 README 或其他提交副本。
- **实际检查**：在独立源码快照运行 `python -m unittest discover -s tests -q`，93 项通过，测试运行时间 180.183 秒；`python -m mypy camtool main.py build_app.py --check-untyped-defs`，28 个源文件通过。首轮类型检查未把本地 ezdxf 路径传给检查子进程，补齐该进程的 `PYTHONPATH=build-tools` 后仅重跑类型检查通过，未改程序来规避错误。检查时源文件相对快照无变化，`git diff --check` 通过。
- **远端复核**：重新 fetch `main`，84 个文件的 Git blob 哈希与待上传快照全部一致，5 个保留材料文件的哈希保持不变；版本文件再次读取为 `1.7.0 / 2026-10-21`。清单、测试/类型日志和复核记录分别为 `build/github-sync-v1.7.0/manifest.json`、`validation.json`、`remote-verification.json`。
- **本地状态**：本次不修改软件运行逻辑、不创建本地主仓库提交、不暂存或清理已有改动；本地主仓库仅追加本节状态。用户原有未提交和未跟踪实现保留，未同步父目录交付副本或参赛材料副本。
- **未执行与后续**：本次是 GitHub 源码同步，没有重新执行真实 GUI/EXE 门禁、打包、上传新版二进制 Release 或更新在线演示；既有 v1.7.0 本机发布验证见上一节。后续发布 GitHub 下载包时核对准确版本及用户文件范围，不能把历史 v1.3.0 附件当成 v1.7.0。
