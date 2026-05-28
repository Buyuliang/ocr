# 项目概览

## 1. 项目用途

这个项目用于对接摄像头拍摄到的设备标签进行 OCR 识别，并结合人工输入的 SN、厂商前三码、型号后四码做简单校验。

识别成功后，结果会：

- 写入本地 `results.csv`
- 上传到 OSS 路径 `oss://az05/checkCpu/`

## 2. 当前实现形态

项目不是通用 Python OCR 工程，而是一个“Python GUI + 外部 ARM OCR 可执行文件”的封装。

职责拆分如下：

- `main.py`
  - GUI
  - 摄像头管理
  - 输入校验
  - 调用 OCR 二进制
  - 解析 `text.txt`
  - 写入 CSV
  - 上传/下载 OSS 文件
- `rknn_ppocr_system_demo`
  - 真正执行 OCR 推理
  - 读取 `.rknn` 模型
  - 产出 `text.txt`
  - 产出 `out.jpg`
- `lib/*.so`
  - RKNN/RGA 运行时依赖
- `model/*.rknn`
  - 检测模型与识别模型

## 3. 典型业务流程

1. 用户选择 `/dev/video*` 摄像头。
2. 输入 SN。
3. 如果勾选“编辑前三码/后四码”，再输入 `vendor` 和 `model`。
4. 点击“开始检测”。
5. 程序抓取当前摄像头画面并保存到 `~/ocr/<sn>_<timestamp>.jpg`。
6. 调用 `./rknn_ppocr_system_demo det_model rec_model image_path`。
7. 从 `text.txt` 中查找 `RK3288`，取下一行作为关键识别内容。
8. 校验该行的前三字符和后四字符是否与输入一致。
9. 成功则追加写入 `results.csv` 并上传 OSS，失败则在 UI 中显示 `FAIL`。

## 4. 代码演进线索

从 [main.py.bak](/Users/apple/Desktop/workspace/edge2/ocr/main.py.bak) 看，项目最早更接近“拍照后显示 OCR 文本”的简单版本，后续在 [main.py](/Users/apple/Desktop/workspace/edge2/ocr/main.py) 中新增了：

- 多摄像头设备切换
- CSV 持久化
- OSS 下载/上传
- `vendor/model` 规则校验
- 结果 PASS/FAIL 展示

说明这个工具已经从“实验脚本”演进成“现场操作工具”。
