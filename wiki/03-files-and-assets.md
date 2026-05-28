# 文件与资产说明

## 1. 顶层文件

### [main.py](/Users/apple/Desktop/workspace/edge2/ocr/main.py)

主程序。功能包括：

- GUI 渲染
- 摄像头切换
- 输入校验
- OCR 调用
- CSV 读写
- OSS 下载/上传

### [main.py.bak](/Users/apple/Desktop/workspace/edge2/ocr/main.py.bak)

历史备份版本。可以作为功能演进参考，但不应视为当前运行入口。

### [test.py](/Users/apple/Desktop/workspace/edge2/ocr/test.py)

本地摄像头测试脚本。功能非常单一：

- 打开默认摄像头
- 显示实时画面
- 按 `p` 保存图片
- 按 `q` 退出

它更像调试工具，不参与正式业务流程。

### [results.csv](/Users/apple/Desktop/workspace/edge2/ocr/results.csv)

识别记录文件。现场运行时会：

- 先从 OSS 下载
- 再追加写入
- 再上传回 OSS

这意味着它承担了“轻量共享数据库”的角色。

### [text.txt](/Users/apple/Desktop/workspace/edge2/ocr/text.txt)

OCR 二进制输出的文本结果文件，Python 依赖它做二次解析。

### [out.jpg](/Users/apple/Desktop/workspace/edge2/ocr/out.jpg)

OCR 二进制生成的结果图，通常用于可视化或调试。

### [rknn_ppocr_system_demo](/Users/apple/Desktop/workspace/edge2/ocr/rknn_ppocr_system_demo)

RKNN OCR 可执行文件。通过 `strings` 可见其参数格式为：

```text
<det_model_path> <rec_model_path> <image_path>
```

同时可以确认：

- 会写出 `text.txt`
- 会写出 `./out.jpg`
- 内部存在 `ppocr_system` 相关符号

## 2. `lib/`

### [lib/librga.so](/Users/apple/Desktop/workspace/edge2/ocr/lib/librga.so)

RGA 相关动态库，通常用于图像处理加速。

### [lib/librknnrt.so](/Users/apple/Desktop/workspace/edge2/ocr/lib/librknnrt.so)

RKNN Runtime 动态库，是执行 `.rknn` 模型的核心运行时依赖。

## 3. `model/`

### [model/ppocrv4_det.rknn](/Users/apple/Desktop/workspace/edge2/ocr/model/ppocrv4_det.rknn)

文本检测模型。

### [model/ppocrv4_rec.rknn](/Users/apple/Desktop/workspace/edge2/ocr/model/ppocrv4_rec.rknn)

文本识别模型。

## 4. 仓库形态判断

从当前目录内容看，这里不包含完整 OCR C/C++ 源码，也没有 Python 包结构、依赖声明或构建脚本。

更准确地说，这个目录是：

- 一个可直接部署运行的工具目录
- 一套 Python 前端脚本
- 一套预编译 ARM OCR 推理资产

而不是一个能完整重编译全部组件的标准源码仓库。
