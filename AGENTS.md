# 开发入口与工作约定

## 快速恢复

1. 首先读本文件及 `PROJECT_STATUS.md`，检查 `git status --short`。默认以工作树源码为准，不能把 HEAD、旧交付包或历史验证记录视为当前状态。
2. 从下表选择任务模块，先用 `rg -n` 查符号/调用点，再读取相关函数、直接调用者和对应测试。只有出现依赖证据时才扩大阅读范围。
3. 新任务如涉及未提交改动，先检查相关文件的局部 diff；不得还原、覆盖、清理或顺手重构用户改动。未跟踪源码也可能是有效实现。
4. 完成最小修改，检查差异，执行相应验证，更新必要状态，然后交接实际结果及未覆盖项。

## 架构与定位表

Windows 离线桌面程序；Python 3.12、Tkinter、NumPy、ezdxf；没有服务端。对外工作流由 `camtool/__init__.py` 导出 `Project`、`calculate`、`optimize`、`Result`、`export_design`。

| 任务 | 主要文件（位于 camtool/，除非另注） | 优先测试文件（位于 tests/） |
|---|---|---|
| 项目参数、JSON/CSV、旧 schema | `project.py`，`engine.py:validate` | `test_workflow.py`、`test_safety.py`；兼容性补看 `test_workbench.py` |
| 路线样条、必过点、辅助点形状 | `route_shape.py`、`route_metrics.py`、`engine.py:_route/_motion/_replay` | `test_auxiliary.py`、`test_required_points.py` |
| 车辆/机构运动、凸轮、采样、优化 | `engine.py`、`cam_geometry.py` | `test_workflow.py`、`test_safety.py`、`test_required_points.py`；按影响补 `test_auxiliary.py` |
| 主窗口、后台任务、结果失效 | `ui.py` | 按功能选单元测试及 `tools/ui_check.py` |
| 顶部流程/四层状态、路演案例、候选解释与可靠导出 | `workflow.py`、`ui.py`、`workbench.py`、`workbench_ui.py`、`export.py` | `test_readiness.py`；`tools/readiness_ui_check.py`；评分/路演见 `docs/HACKATHON_READINESS.md` |
| 打卡点表格、坐标草稿、历史 | `waypoints.py`、`ui.py`、`workbench.py:RouteHistory` | `test_waypoints.py`；`tools/editing_ui_check.py` |
| 辅助点编辑与地图联动 | `auxiliary_editor.py`、`auxiliary_ui.py`、`ui.py` | `test_auxiliary_editor.py`；`tools/auxiliary_ui_check.py` |
| 两类点表统一验收 | `editor_smoke.py`（被显式冒烟入口调用） | `tools/dual_point_ui_check.py`，配合两类编辑单元测试 |
| 辅助搜索、排名、候选独立审计 | `auxiliary_search.py`、`candidate_audit.py`、`auxiliary_ui.py` | `test_auxiliary.py`；`tools/auxiliary_ui_check.py` |
| 设计改进助手、参数授权、结构试算、候选采用/撤销 | `improvement.py`、`improvement_ui.py`；`ui.py` 生命周期 | `test_improvement.py`、`test_improvement_diagnosis.py`；`tools/diagnosis_ui_check.py`、`tools/improvement_ui_check.py`；说明 `docs/IMPROVEMENT_ASSISTANT.md` |
| 绘图、尺寸、起点/旋向标记 | `plotting.py`、`dimensions.py`、`markings.py` | `test_cam_markings.py` 及相关界面检查 |
| 主图/放大窗口缩放、平移、保留绘制和拖动性能 | `viewport.py`、`plot_views.py`、`ui.py:map_* / flush_drag / render`；`plotting.py:Plot` | `test_viewport.py`；`tools/views_ui_check.py`、`tools/drag_probe.py` |
| DXF/CSV/报告导出 | `export.py`、`markings.py`、`project_validation.py` | `test_workflow.py`、`test_cam_markings.py`、`test_required_points.py` |
| 最终 DXF 独立检验 | `verification.py`、`verification_ui.py` | `test_verification.py`；`tools/verification_ui_check.py` |
| 时间轴、问题定位、对比、实测/扰动 | `workbench.py`、`workbench_ui.py` | `test_workbench.py`；`tools/workbench_ui_check.py` |
| 必过点偏差展示 | `route_metrics.py`、`engine.py`、`ui.py` | `test_required_points.py`；`tools/required_points_ui_check.py` |
| 版本、打包、发布 | `version.py`；根目录 `main.py`、`build_app.py`、`SolarCam.spec` | `docs/VERSIONING.md`、`tools/release_smoke.py` |

依赖方向和边界：

- `main.py → ui.App`；界面编排计算、编辑器、绘图和各结果页。搜索后台由 `auxiliary_ui.py` 编排。
- `ui → plot_views → plotting/viewport` 是主图/放大窗口的运行依赖；`plotting.Plot` 仅对带相机的Canvas使用显示变换。`view_smoke` 在显式冒烟入口延迟导入，常规启动不运行鼠标/验收动作。
- `engine → project / route_shape / route_metrics / cam_geometry`，生成 `Result`；`export → Result / markings / version` 输出交付文件。
- `workflow` 只汇总状态与参数变化，由界面和辅助搜索复用；不调用求解器。顶部 DXF 结论绑定当前导出哈希、项目和计划区间，不替代独立参数核对。验收脚本可显式传输出目录，不覆盖历史发布证据。
- `engine.optimize`、辅助搜索和 `project_validation` 经 `candidate_audit → export → 最终DXF → verification` 审计候选。此链有函数内延迟导入，修改 import 时核对循环依赖。
- `Diagnostic.measurements/pose` 从当前计算数组生成行程跨度/两端越界/姿态；`improvement.diagnose` 区分事实与依赖假设，不解析中文报告判根因。助手默认诊断页后台计算/整理，无授权也可看；部件授权后复用原试算。结构试算须明确允许隐含安装位置联动。`ProjectError.fields` 可选字段键用于输入定位，保留原异常类型。
- `improvement → engine / auxiliary_search.rank / workbench.diagnostics / candidate_audit` 复用数值与独立审计；变量只在注册允许列表内，未授权字段、采样和验收门槛逐字段不变。`improvement_ui` 后台队列只传数据；预览不修改项目，采用历史复用RouteHistory快照并保留点表历史。当前输入改变时使助手候选/采用历史失效。
- `verification` 的运动计算只依赖项目数据、版本和 NumPy/ezdxf，自行从轮廓求接触、转角和轨迹；不得改为调用主生成引擎来证明自己正确。`reference`/名义 CSV 只用于比较。
- `workbench → Result / route_metrics`，扰动分析在函数内调用独立检验。`waypoints → workbench.RouteHistory`；`auxiliary_editor` 复用点表基类，但必须维护各自的坐标和历史。
- `tests/` 是单元与工作流测试，`tools/` 是真实界面、案例与发布验证脚本，`examples/` 与 `tests/fixtures/` 是明确来源的输入；`docs/` 为按需阅读的模型/操作/验证记录。

## 定向读取与局部修改

- 在仓库目录搜索，不跨整个父工作区。列源码可用 `rg --files camtool tests tools docs examples`；不要把 `build-tools/` 放入文件清单。
- 默认排除 `.git/`、`__pycache__/`、`.mypy_cache/`、`build/`、`dist/`、`build-tools/`、ZIP/二进制，以及父目录的历史交付、参赛副本、临时渲染和 CAD/Office 文件。确需日志时先定位具体文件并只取相关段。
- 示例：`rg -n 'ordered_deviations|checkpoint_rows' camtool/route_metrics.py camtool/engine.py tests/test_required_points.py`。没有命中时先换符号或检查直接依赖，再逐步扩大范围。
- 大文件按符号和行段阅读；不要默认整读 `ui.py`、`engine.py`、`verification.py`、全部测试或全部发布历史。`CONTEXT.md` 是领域术语表，非第二份项目交接状态。
- 仅修改本任务需要的函数及直接关联测试/文档；保留公开接口、JSON 兼容性、默认参数和既有行为。不要混入格式化、重命名、依赖升级或无关架构调整。
- 沿用周边 Python 写法和类型注解，用户提示用中文；输入错误使用 `ProjectError`，有限数字、尺寸和几何约束要在正确边界验证，避免吞掉异常。
- Tk 控件在主线程操作；后台计算支持取消，改参后旧结果和旧导出必须失效，拒绝过期后台结果。
- `FigurePanel` 主图/独立窗口使用各自的 `Viewport`，仅改变显示。保留绘制帧内不得全图清空；拖动在 `ui.flush_drag` 合并、只改当前行，首次有效位移做一次结果失效，松开处理最终坐标并提交一条历史。独立窗口共用数据/选择/进度，观察相机各自保存；播放和问题选择不得隐式重置视图。`view_smoke.py` 由显式发布冒烟入口调用，不在常规启动时执行。

## 数学与工程约定

- 长度为 mm；后轴中点是车体原点，前向 +X、左向 +Y；车头方向以场地 +X 为 0°、逆时针为正。区分界面角度与函数内部单位，不凭名称猜单位。
- 车上传感器安装偏移与传感器场地起点是不同参数；安装 X > 0。传动比是驱动轮圈数/凸轮圈数，后从动轮直径不应改变驱动里程与凸轮相位。
- 比赛必过点按顺序访问；辅助控制点只引导形状；首尾同点可以表达返回起点，不能擅自删除。名义打卡、完整几何合格、最终 DXF 独立检验、实车验证分别判定。
- 公式/积分/插值/采样更改必须检查单位、坐标符号、左右置、旋向、起始相位、比赛/回程边界、退化输入和收敛性；按影响用直线、已知圆弧、圆形凸轮等解析案例验证。
- 触及实际采样上限不等于稳定；不放宽容差、改硬件、吸附目标或复用生成器结果来掩盖失败。独立检验必须对应实际导出的 DXF 字节与哈希。
- 数学细节按需查 `docs/MODEL.md`，辅助点查 `docs/AUXILIARY.md`，必过点查 `docs/REQUIRED_POINTS.md`，二次检验查 `docs/VERIFICATION.md`。有限采样不是连续几何证明，理想运动学不是实车性能保证。

## 验证与日志

命令均在本仓库执行；`python` 指已经确认可用的 Python 3.12 解释器。先确认解释器和依赖，不盲信系统别名或旧 `py` 注册路径；依赖见 `requirements.txt`、`requirements-dev.txt`。本机现存 `build-tools/` 是本地依赖目录，可按需注入当前进程的模块路径，不能当作源码修改或提交。GUI 还需要可用的 Tcl/Tk，截图脚本使用 Pillow；不要为仅文档任务安装或升级依赖。

```powershell
# 按文件选择，避免默认输出全部用例名称
python -m unittest discover -s tests -p 'test_waypoints.py' -q
python -m unittest discover -s tests -p 'test_auxiliary_editor.py' -q
# 按具体用例进一步缩小（-k 匹配实际测试名）
python -m unittest discover -s tests -p 'test_required_points.py' -k distance_uses_ordered_segments -q
```

- 从定位表选择本次相关测试，不固定只测以上示例。算法、schema、导出或跨模块更改应覆盖直接消费者和相关工作流；核心广泛影响或正式发布才运行完整测试：`python -m unittest discover -s tests -q`。
- Python 代码更改按影响运行类型检查；公共数据结构/接口或发布使用 `python -m mypy camtool main.py build_app.py --check-untyped-defs`，不要用仅检查调用者遗漏的片段代替接口验证。
- GUI 编辑、线程/取消、焦点或页面布局更改需运行对应 `python tools/<相关检查脚本>.py`，单元测试不能代替真实界面验证。
- 文档更改检查命令、引用路径、架构描述和 diff 即可；若改变了公式或行为约定，仍需核对实现及必要数学/测试证据。缩小范围不等于跳过必要检查。
- 默认用 `-q`；长日志写入 `build/<任务名>/` 下独立文件，确认退出码后只汇报数量、结论和关键失败；失败再读取相关段，不能用截断输出掩盖错误。
- 不为非发布任务运行 `build_app.py --package` 或写发布凭证。发布遵循 `docs/VERSIONING.md`，验证真正从 ZIP 解压的 EXE；保留旧发布和用户文件，不能以源码启动替代发布验证。

## 任务结束与交接

- 检查本次文件 diff、`git diff --check` 和新增文件，明确哪些改动是本次所做；不提交或清理无关改动。
- 每次结束更新 `PROJECT_STATUS.md` 中受影响的功能/问题及“最近交接”：日期、目标、实际文件、已执行的命令与结果、未测范围、具体下一步。未完成任务也记录阻塞与恢复入口。
- 状态写可验证事实，不复制整段源码、日志或对话；替换过期状态，详细证据链接到具体记录。只有架构、定位入口或开发规则变化才更新本文件；正式发布再同步版本/日志/发布验证。
- 没有运行的检查标明未执行及原因；历史验证必须标版本，不能写成当前工作树通过。节省 Token 不得省略必要代码检查、数学验证和测试。
