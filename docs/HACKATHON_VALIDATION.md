# 2026-10-22 路演与核心流程验收

对象为当前工作树，在原 v1.7.0 源码上完善流程、案例与可靠性；版本号未改，未打包或发布。本次结果不代表历史 EXE 已包含改动。输入均为内置理论案例或测试构造数据，未覆盖、同步或上传用户项目。

## 实际改动

| 文件 | 改动 |
|---|---|
| `camtool/workflow.py`（新增）、`ui.py` | 四层状态、下一步操作、导出对象关联、后台导出、取消后队列结果隔离、示例入口 |
| `camtool/workbench.py`、`workbench_ui.py` | 案例来源与采样设置；诊断超限影响；候选参数解释；小窗口滚动详情及图形空间 |
| `camtool/auxiliary_search.py`、`auxiliary_ui.py` | 原案/候选同精度复算；终止原因、耗时、逐候选输入/指标变化/失败项和采样记录；明确粗筛与最终审计 |
| `camtool/export.py` | 五文件先暂存、再替换；失败/取消回滚；回滚失败保留备份 |
| `camtool/verification_ui.py`、`verification.py` | 取消与过期结果拦截、来源快照；公开报告文件名及自动名义 CSV 来源去除机器目录，保留 DXF 哈希 |
| `camtool/plotting.py` | 图上采样状态使用实际结果，避免已复算后仍显示未确认 |
| `tests/test_readiness.py`（新增） | 状态关联、诊断、同条件搜索、导出失败与取消/回滚恢复、路径隐私回归 |
| `tools/readiness_ui_check.py`（新增） | 显式真实 Tk 闭环验收；常规启动不执行 |
| `tools/workbench_ui_check.py`、`auxiliary_ui_check.py`、`verification_ui_check.py` | 可传独立证据目录；取消不再要求弹错误框，改验收停止状态且无结果 |
| `README.md`、`AGENTS.md`、`PROJECT_STATUS.md` | 定位/安装/AI/边界说明、模块索引和交接 |
| 本文、`HACKATHON_READINESS.md`、`HACKATHON_EVIDENCE.json`（新增） | 评分映射、90 秒操作稿、实际结果与来源/参数/源文件指纹 |

## 实际检查结果

- 全量单元/工作流：**103 项通过，239.550 s**。包括原有解析直线/圆弧/圆形凸轮、异常输入、名义/独立回算、有限采样与候选审计回归，没有修改数学公式或判定门槛。
- 后续补充逐候选记录后，定向 `test_readiness.py`：**10 项通过，32.805 s**；包含原案与候选精度、固定条件和审计字节检查。后续显示布局另由真实 GUI 回归覆盖。
- mypy：**29 个源文件通过**，范围 `camtool main.py build_app.py --check-untyped-defs`。
- 四套真实 Tk 检查全部通过：`readiness_ui_check.py`、`auxiliary_ui_check.py`、`workbench_ui_check.py`、`verification_ui_check.py`。新流程覆盖常规启动无自动任务、正常/问题案例、联动图形、实际参数调整后重算、约束/失败项保留、后台五文件导出、最终 DXF 二次检验、迟到结果取消、重复点击、输入失效与原项目恢复。
- 原有 GUI 回归覆盖实际后台搜索、显式采用候选/恢复原案、真实取消、忽略取消的迟到任务隔离、实测格式/理论扰动、独立参数编辑、报告导出及页面切换。
- 主工作树 `git diff --check` 通过；原有 LF/CRLF 警告保留，不作无关格式化。新增文件另检查尾随空白、文档相对链接和证据源文件指纹。

过程中发现并修复了取消后排队结果仍可能接收、小窗口下一步按钮被挤出、问题说明遮挡图形、候选长说明挤掉路线图。最终 1000×640 问题页与优化对比页验收通过；优化图实际高度98 px，详细指标/解释可滚动。建议路演用更大分辨率。截图验收显式等待绘制并使测试窗口置前，避免空白/遮挡截图被误作结果；部分历史脚本截图仍可能被其他窗口遮挡，因此另取最终无遮挡截图。

## 两个案例的真实状态

唯一输入工厂为 `demo_project(False/True)`。正常例在旧 `Project.demo()` 上仅调整采样：路线步长1.25 mm、凸轮起始2000点；粗细比较为路线1.25→1 mm、凸轮2000→4000点。硬件、目标点、容差、压力角门槛及路线形状未变。初始 `Project.demo()` 仍保留旧5 mm/1200点输入，其采样未确认回归没有被删除。

正常例名义打卡 **5/5**，主约束及主设计采样复算通过；实际最大压力角粗/细 **41.224371° / 41.615952°**，差 **0.391581° < 0.5°**；最小凸曲率半径差 **0.020854 mm < 0.1 mm**；最小接触间隙差 **0.000318 mm < 0.02 mm**。

正常例从实际导出 DXF 再独立检验，几何运动学通过：角步长0.5→0.25°、接触容差0.01→0.002 mm，独立轨迹粗细差 **0.425407 mm / 1 mm**；与设计参考路线最大差 **0.122080 mm**，只用于理论比较，**不是实车精度**。本次受检实际字节：

```text
SHA-256 13ac8d25da452947e56f857e3ff53dbaa9491daf58620a5b2f58065ff0e8f556
```

该哈希已再次从实际文件只读复核。重新导出时 DXF 元数据会变化，新的文件必须使用自己的哈希和独立检验，不能照抄本次通过结论。证据中的来源均标为设计继承/未实测，没有把双求解流程当作真值。

问题例实际保留四个失败项：**完整周期转向、压力角、圆形障碍物、前轮转角**。名义打卡仍是5/5，明确演示“打卡通过不代表完整设计通过”。GUI 中调整路线张力到0.8并重算，仍保留实际失败，不放宽原限位和障碍约束，不伪造优化成功。

可移交的机器可读摘要见 [HACKATHON_EVIDENCE.json](HACKATHON_EVIDENCE.json)，含完整示例输入、检查状态、采样数据、独立检验指标、哈希与运行代码指纹。原始日志与当前截图在本地忽略目录 `build/hackathon-readiness-20261022/`，未随 Git 跟踪；可重新运行以下命令生成。

## 复现命令

先按 README 安装 Python 3.12、项目/开发依赖及 GUI 验收用 Pillow，在仓库目录执行：

```powershell
python -m unittest discover -s tests -q
python -m unittest discover -s tests -p test_readiness.py -q
python -m mypy camtool main.py build_app.py --check-untyped-defs
python tools/readiness_ui_check.py build/hackathon-readiness-20261022/gui
python tools/auxiliary_ui_check.py build/hackathon-readiness-20261022/auxiliary
python tools/workbench_ui_check.py build/hackathon-readiness-20261022/workbench
python tools/verification_ui_check.py build/hackathon-readiness-20261022/verification
git diff --check
```

本机实际使用工具提供的 Python 3.12.14，并仅向验证进程注入现有 `build-tools` 的 `PYTHONPATH`，Tk 使用其中匹配的 Tcl/Tk8.6.12。系统旧 `py` 路径不可作为本次解释器证据。受限进程无法截图时在实际桌面进程运行 GUI；验证逻辑仍调用真实 Tk 和工作树源码，没有用截图替代计算检查。

## 未覆盖与恢复入口

- 无真实机构测量、制造装配、动力学/空间接触、实车采集或另一台干净机器验证；没有效率对照数据、用户访谈或商业收入。边界和落地路径见 [路演说明](HACKATHON_READINESS.md)。
- 未接入运行时大模型、未新增优化器或大规模重构；保留确定性数值计算与独立 DXF 求解原则。有限搜索不保证全局最优；时间预算在计算检查点生效，最终复算与审计另计。
- 本次五文件回滚保护用于主设计导出；独立检验报告的多文件导出、参数配置等没有统一升级为同一事务机制。它们仍应导出到新目录。主项目保存沿用原子替换；即使主导出也不保证突然断电的跨文件原子性。
- 没有改版本号、正式门禁/EXE、安装包、历史交付、参赛副本、用户项目、Git 提交/推送或公开站点。体验本次改动需启动源码。

恢复：先读 `AGENTS.md` 对应模块与 `PROJECT_STATUS.md`，再查看本报告和显式检查脚本。长日志、开始时原文件快照和本次局部差异在 `build/hackathon-readiness-20261022/`；不要用历史发布凭证声明新改动已发布。
