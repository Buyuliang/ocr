# OCR Project Wiki

这个目录整理了 `/Users/apple/Desktop/workspace/edge2/ocr` 的现状分析，目标是让后续维护者可以快速回答三个问题：

1. 这个项目是干什么的
2. 它现在怎么跑起来
3. 哪些地方有明显风险

建议阅读顺序：

1. [01-overview.md](/Users/apple/Desktop/workspace/edge2/ocr/wiki/01-overview.md)
2. [02-runtime-flow.md](/Users/apple/Desktop/workspace/edge2/ocr/wiki/02-runtime-flow.md)
3. [03-files-and-assets.md](/Users/apple/Desktop/workspace/edge2/ocr/wiki/03-files-and-assets.md)
4. [04-dependencies-and-environment.md](/Users/apple/Desktop/workspace/edge2/ocr/wiki/04-dependencies-and-environment.md)
5. [05-risks-and-improvements.md](/Users/apple/Desktop/workspace/edge2/ocr/wiki/05-risks-and-improvements.md)

当前结论摘要：

- 这是一个运行在 Linux ARM64 环境上的 OCR 检测工具，GUI 用 `Tkinter`，摄像头采集用 `OpenCV`。
- 核心 OCR 并不在 Python 内部完成，而是通过外部二进制 `rknn_ppocr_system_demo` 调用 RKNN 模型完成。
- Python 主要负责设备选择、拍照、参数输入、结果校验、CSV 记录和 OSS 上传。
- 目录内的二进制和 `.so` 文件是运行关键资产，当前仓库更像“可运行包”而不是“完整源码仓库”。
