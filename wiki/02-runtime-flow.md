# 运行流程

## 1. 入口

当前唯一明确入口是 [main.py](/Users/apple/Desktop/workspace/edge2/ocr/main.py)。

程序启动后会立即做几件事：

- 创建保存目录 `~/ocr`
- 扫描 `/dev/video*`
- 初始化第一个可用摄像头
- 构建 Tkinter 窗口
- 启动实时视频刷新循环

## 2. 主流程拆解

### 2.1 摄像头管理

- `get_video_devices()`
  - 枚举 `/dev/video*`
- `extract_video_index()`
  - 按设备编号排序，避免 `video10` 排在 `video2` 前面
- `init_camera(device)`
  - 用 `cv2.VideoCapture(device)` 打开设备
- `refresh_device_list()`
  - 动态刷新设备菜单

### 2.2 检测动作

用户点击“开始检测”时，`start_detection()` 会执行：

1. 清空结果区
2. 关闭“编辑前三码/后四码”勾选
3. 从 OSS 下载历史 `results.csv`
4. 读取当前视频帧
5. 读取 SN、vendor、model
6. 保存当前照片到 `~/ocr`
7. 设置 `LD_LIBRARY_PATH=./lib`
8. 执行外部命令：

```bash
./rknn_ppocr_system_demo model/ppocrv4_det.rknn model/ppocrv4_rec.rknn <photo_path>
```

### 2.3 OCR 结果解析

Python 不直接读取标准输出作为结构化结果，而是依赖 OCR 二进制落盘生成的 `text.txt`。

已观察到的 `text.txt` 示例：

```text
Rackchip
RK3288
NAAKY35033W 2542
```

解析策略：

- 查找包含 `RK3288` 的行
- 读取下一行
- 去掉空白字符
- 使用：
  - `next_line[:3]` 对比 `vendor`
  - `next_line[-4:]` 对比 `model`

如果不匹配，立即判定为 `FAIL`。

### 2.4 结果持久化

匹配成功时写入 `results.csv`，字段如下：

- `SN号`
- `content`
- `Vendor`
- `Model`
- `date`

随后调用：

```bash
ossutil cp -f results.csv oss://az05/checkCpu/
```

### 2.5 界面反馈

- `result_text`
  - 用于显示伪进度条和最终 PASS/FAIL
- `log_text`
  - 用于显示拍照、切换设备、OCR 输出、异常信息

## 3. 实际调用链

```text
Tkinter UI
  -> OpenCV 读取摄像头
  -> main.py 保存抓拍图
  -> rknn_ppocr_system_demo 执行 RKNN OCR
  -> 产出 text.txt / out.jpg
  -> main.py 解析 text.txt
  -> main.py 更新 results.csv
  -> ossutil 上传 results.csv
```

## 4. 关键隐式约束

- OCR 二进制必须在当前工作目录下执行。
- `lib/`、`model/`、`text.txt`、`out.jpg` 都假设使用相对路径。
- `ossutil` 必须预先安装并配置好认证。
- 运行环境必须是能执行 ARM64 ELF 的 Linux 系统。
