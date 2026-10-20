# 凸轮设计台 / SolarCam

武汉1024AI黑客松参赛作品，作者：余灿。太阳能小车路线、转向机构与凸轮轮廓的离线设计工作台，当前版本 v1.3.0。

在线演示：[凸轮设计台](https://cam-design-studio-yucan.ampletoad4.chatgpt.site)

[作品介绍、视频与 PPT](https://cam-design-studio-yucan.ampletoad4.chatgpt.site/#materials)

[Windows 发布文件](https://github.com/lirohanli6-svg/cam-design-studio/releases)

## 功能
打卡路线设计、两种理想平面机构、三视图同步演示、问题定位、本地优化对比、DXF/CSV 导出、最终 DXF 独立反算，以及实测轨迹导入与单因素扰动分析。

## 开发运行
使用 Python 3.12，安装 Tkinter 支持。
```sh
python -m pip install -r requirements.txt
python main.py
```
## 验证
```sh
python -m pip install -r requirements-dev.txt
python -m unittest discover -s tests -v
```
图形界面检查需要可见 Windows 桌面。版本记录和发布方式见 CHANGELOG.md 与 docs/VERSIONING.md。

## 使用边界
演示尺寸需替换为实测值。计算属于理想几何与运动学，不保证实车性能。尚未实施实车验证，未验证摩擦、打滑、弹簧力和完整动力学。在线演示如有提供，仅播放来自桌面计算引擎的预计算理论案例。

## 代码使用
本仓库未声明开源许可证。公开可读不等于授权任意复制、修改或分发；许可证由作者另行确定。第三方依赖遵循各自许可。


## 网页理论案例
`web-demo/` 包含预计算理论案例的播放器，正常/问题数据直接由桌面版计算引擎生成。网页不执行任意新参数设计，不包含实车测试数据。
