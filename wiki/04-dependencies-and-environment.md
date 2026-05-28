# 依赖与运行环境

## 1. Python 侧依赖

从 [main.py](/Users/apple/Desktop/workspace/edge2/ocr/main.py) 可直接识别出的依赖：

- `opencv-python`
- `Pillow`
- `tkinter`
- `numpy`

代码顶部还留下了安装提示：

```python
# pip install opencv-python
# sudo apt install python3-tk -y
# pip install --upgrade Pillow
```

`csv`、`glob`、`os`、`time`、`datetime`、`subprocess` 属于标准库。

## 2. 系统级依赖

### 摄像头

- 依赖 Linux 下的 `/dev/video*`
- 默认通过 V4L2 路径暴露设备

### OSS 工具

- 依赖命令行工具 `ossutil`
- 要求本机已完成 OSS 凭证配置

### RKNN 运行时

- 依赖 `lib/librknnrt.so`
- 依赖 `lib/librga.so`
- 运行时通过 `LD_LIBRARY_PATH=./lib` 让二进制找到这些库

## 3. CPU/架构要求

当前二进制文件被识别为：

- `ELF 64-bit`
- `ARM aarch64`
- `GNU/Linux`

因此这个项目至少要求：

- Linux
- ARM64 / AArch64

这也解释了为什么它不适合直接在 macOS 或普通 x86 Linux 上本地运行。

## 4. 运行目录要求

项目强依赖“从仓库根目录启动”这一前提，因为多处路径都是相对路径：

- `./lib`
- `./rknn_ppocr_system_demo`
- `model/ppocrv4_det.rknn`
- `model/ppocrv4_rec.rknn`
- `results.csv`
- `text.txt`

如果从别的工作目录调用 `python main.py`，大概率会出现路径错误。

## 5. 建议的最小运行前提

```text
Linux ARM64
Python 3
opencv-python
Pillow
numpy
python3-tk
ossutil
/dev/video* 可访问
当前目录包含 lib/ model/ 和 rknn_ppocr_system_demo
```
