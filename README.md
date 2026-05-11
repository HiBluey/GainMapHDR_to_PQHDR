# GainMapHDR_to_PQHDR

这是一个基于 Python的图形界面工具，可以将包含增益图（Gain Map）的 Ultra HDR JPEG 转化为标准 Rec.2020 PQ HDR 图像，并提供实时预览。

本项目支持自动提取 Android Ultra HDR 格式的内嵌图层与元数据，也支持手动分别导入 SDR 基础层与 Gain Map 层。最终可输出10-bit AVIF格式。

​🛠️ 快速上手

​1. 安装依赖
pip install Pillow numpy opencv-python imagecodecs

2. 获取渲染引擎
​从 mpv.io 下载最新的 Windows 版 mpv.exe，并将其直接丢入本脚本所在的文件夹。

​3. 运行程序

📖 操作指南

1.​ 一键加载：点击 自动提取 Ultra HDR，程序将自动剥离 SDR 基础层与 Gain Map 元数据。  

​2. 动态调节：通过左侧的滑块或gain map层元数据输入框微调参数，右侧视窗将实时刷新预览。  

​3. 导出：调整满意后，点击底部导出按钮生成基于PQ的高动态范围图像。
