# GainMapHDR_to_PQHDR

这是一个基于 Python的图形界面工具，主要用于解析包含增益图 (Gain Map) 的 Ultra HDR 图像，并将其通过的光电转换计算，渲染为标准的 ST.2084 (PQ) HDR 图像文件。

本项目支持自动提取 Android Ultra HDR 格式的内嵌图层与元数据，也支持手动分别导入 SDR 基础层与 Gain Map 层。最终可输出Windows11支持的 16-bit 无损 PNG （Edge浏览器支持）或 10-bit AVIF （自带照片app支持）格式。

## ✨ 主要特性 (Features)

- **一键提取与分离**：自动读取 Ultra HDR (JPEG) 内嵌的 XMP 元数据（如 Gamma、Offset、Capacity 等），并无损分离 SDR 基础层与 Gain Map 图层。
- **自定义色彩空间**：支持 sRGB 和 Display P3 色域输入，并允许自定义 SDR EOTF (sRGB 或 Gamma 2.2)。
- **全像素浮点渲染**：在浮点空间内将 SDR 像素依据增益图和亮度锚点（默认 100 nits）重新映射到高动态范围线性光空间。
- **标准化 PQ 编码输出**：
  - **16-bit PNG**: 采用 OpenCV 渲染，并在文件头强行注入标准 cICP 标签 (Color Primaries=9, Transfer=16, Matrix=0)。
  - **10-bit AVIF**: 采用 Imagecodecs 编码，直接封装 10-bit 深度与 ST.2084/Rec.2020 元数据。
- **中间图层留存**：提供选项，可一键保存分解出来的底图与增益图，方便后期分析与二次处理。

## 🛠️ 依赖安装 (Prerequisites)

请确保你的运行环境为 Python 3.7+。运行前需要安装以下依赖包：

```bash
pip install Pillow numpy opencv-python imagecodecs
