# pip install opencv-python
# sudo apt install python3-tk -y
# pip install --upgrade Pillow

import cv2
import csv
import os
import glob
import time
import numpy as np
from tkinter import Tk, Label, Button, Entry, Text, StringVar, Frame, OptionMenu, IntVar, Radiobutton, END, Checkbutton
from PIL import Image, ImageTk
from datetime import datetime
import subprocess

# 保存目录
save_dir = os.path.expanduser("~/ocr")
os.makedirs(save_dir, exist_ok=True)


def upload_log(local_file_path, oss_path, result_text, start_progress):
    """
    上传文件到 OSS
    Args:
        local_file_path: 本地文件路径
        oss_path: OSS 目标路径
        result_text: tkinter 文本组件，用于更新状态
        start_progress: 用于更新进度的函数
    """
    try:
        command = f"ossutil cp -f {local_file_path} {oss_path}"
        result = subprocess.run(command, shell=True, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        print("Upload successful:", result.stdout)
        # 成功时更新状态为 PASS
        result_text.delete(1.0, END)
        start_progress()
        result_text.insert("end", " ")
        result_text.insert(END, "PASS")
    except subprocess.CalledProcessError as e:
        print("Error during upload:", e.stderr)
        # 失败时更新状态为 FAIL
        result_text.delete(1.0, END)
        start_progress()
        result_text.insert("end", " ")
        result_text.insert(END, "FAIL")

def download_log(oss_path, local_file_path):
    """
    从 OSS 下载文件
    Args:
        oss_path: OSS 文件路径
        local_file_path: 本地保存路径
    """
    try:
        command = f"ossutil cp -f {oss_path} {local_file_path}"
        result = subprocess.run(command, shell=True, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        print("Download successful:", result.stdout)
    except subprocess.CalledProcessError as e:
        print("Error during download:", e.stderr)

# 刷新设备列表的函数
def refresh_device_list():
    global devices
    current_devices = get_video_devices()
    if current_devices != devices:  # 只有设备列表有变化时才更新
        devices = current_devices
        menu = device_menu["menu"]
        menu.delete(0, "end")  # 清空现有选项
        if devices:
            # 添加新的设备到菜单
            for device in devices:
                menu.add_command(label=device, command=lambda value=device: device_var.set(value))
            if device_var.get() not in devices:
                device_var.set(devices[0])  # 更新为第一个设备
                init_camera(devices[0])  # 初始化摄像头
                log_text.insert(END, f"设备已切换到: {devices[0]}\n")
                log_text.see(END)
        else:
            # 没有可用设备时显示提示
            menu.add_command(label="无可用设备", command=lambda: device_var.set("无可用设备"))
            device_var.set("无可用设备")
            log_text.insert(END, "没有检测到可用设备！\n")
            log_text.see(END)

def extract_video_index(device):
    """
    提取 /dev/video 后的数字索引。
    如果无法提取数字，则返回一个较大的值用于排序。
    """
    try:
        # 分割并提取数字部分
        suffix = device.split('video')[-1]
        if suffix.isdigit():
            return int(suffix)
    except Exception as e:
        print(f"Error processing device name {device}: {e}")
    # 如果无法提取有效数字，返回一个很大的值（放到排序末尾）
    return float('inf')

# 获取所有可用的摄像头设备
def get_video_devices():
    devices = sorted(glob.glob('/dev/video*'), key=extract_video_index) # 匹配所有 /dev/video* 设备
    return devices

# 初始化摄像头
cap = None

def init_camera(device):
    global cap
    if cap:
        cap.release()
    cap = cv2.VideoCapture(device)
    if not cap.isOpened():
        return None
    return cap

# 开始检测按钮逻辑
def start_detection():
    global cap
    result_text.delete(1.0, "end")
    vendor_editable.set(0)  # 取消选中 Checkbutton
    toggle_vendor_model()   # 更新输入框状态
    download_log("oss://az05/checkCpu/results.csv", "results.csv")
    if cap and cap.isOpened():
        ret, frame = cap.read()
        if ret:
            # 保存当前帧
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

            # 获取输入数据
            sn = sn_input.get().strip()
            vendor = vendor_input.get().strip()
            model = model_input.get().strip()
            # 清空 SN 号输入框，并将光标停在 SN 号输入框
            sn_input.delete(0, END)
            sn_input.focus()
            if not sn or (vendor_editable.get() == 1 and (not vendor or not model)):
                log_text.insert(END, "输入项不能为空，请检查输入！\n")
                log_text.see(END)
                return

            photo_path = os.path.join(save_dir, f"{sn}_{timestamp}.jpg")
            cv2.imwrite(photo_path, frame)
            log_text.insert(END, f"照片已保存到: {photo_path}\n")
            log_text.see(END)

            # 调用 OCR 推理
            # 设置 LD_LIBRARY_PATH 环境变量
            os.environ["LD_LIBRARY_PATH"] = "./lib"  # 修改为你的 lib 路径
            command = ["./rknn_ppocr_system_demo", "model/ppocrv4_det.rknn", "model/ppocrv4_rec.rknn", photo_path]
            try:
                result = subprocess.run(command, capture_output=True, text=True, check=True)
                log_text.insert(END, f"检测结果:\n{result.stdout}\n")
                log_text.see(END)
                results = []
                # 识别完成后删除图片
                if os.path.exists(photo_path):
                    os.remove(photo_path)
                    log_text.insert(END, f"已删除图片: {photo_path}\n")
                    log_text.see(END)
                # 显示 OCR 结果
                if os.path.exists("text.txt"):
                    with open("text.txt", "r") as file:
                        lines = file.readlines()

                    # 查找包含 'RK3288' 的行
                    for i, line in enumerate(lines):
                        if "RK3288" in line:
                            if i + 1 < len(lines):  # 确保有下一行
                                next_line = "".join(lines[i + 1].split())
                                # print("next_line")
                                # print(next_line)
                                # print(next_line[:3])
                                # print(next_line[-5:])
                                # 检查字符匹配
                                if not (next_line[:3] == vendor and next_line[-4:] == model):
                                    log_text.insert(
                                        END,
                                        "检测失败：OCR 结果中，'RK3288' 下一行字符与输入不匹配。\n"
                                    )
                                    log_text.see(END)
                                    result_text.delete(1.0, END)
                                    start_progress()
                                    result_text.insert("end", " ")
                                    result_text.insert(END, "FAIL")
                                    return

                                # # 更新结果显示
                                # result_text.delete(1.0, END)
                                # start_progress()
                                # result_text.insert("end", " ")
                                # result_text.insert(END, "PASS")
                                # 保存数据到列表
                                results.append([sn, next_line, vendor, model, timestamp])
                
                                # 检查 CSV 文件是否存在
                                file_exists = os.path.exists("results.csv")

                                # 打开或创建 CSV 文件进行写入
                                with open("results.csv", 'a', newline='', encoding='utf-8') as csv_file:
                                    writer = csv.writer(csv_file)

                                    # 如果文件不存在，写入标题行
                                    if not file_exists:
                                        writer.writerow(["SN号", "content", "Vendor", "Model", "date"])
                                    
                                    # 写入数据
                                    writer.writerows(results)
                                # print(f"CSV 文件已保存到: {csv_output_path}")
                                log_text.insert(END, "CSV 文件已保存到本地: results.csv\n")
                                log_text.see(END)
                                upload_log("results.csv", "oss://az05/checkCpu/", result_text, start_progress)
                                break
                    else:
                        log_text.insert(END, "检测失败：OCR 结果中未找到 'RK3288'。\n")
                        log_text.see(END)
                        result_text.delete(1.0, END)
                        start_progress()
                        result_text.insert("end", " ")
                        result_text.insert(END, "FAIL")
                else:
                    # print("no found test.txt")
                    log_text.insert(END, f"no found test.txt: {e.stderr}\n")
                    log_text.see(END)
            except subprocess.CalledProcessError as e:
                log_text.insert(END, f"检测失败: {e.stderr}\n")
                log_text.see(END)
        else:
            log_text.insert(END, "拍照失败！\n")
            log_text.see(END)
    else:
        log_text.insert(END, "摄像头未打开，请先选择设备！\n")
        log_text.see(END)

# 更新摄像头画面
def update_video():
    global cap
    if cap and cap.isOpened():
        ret, frame = cap.read()
        if ret:
            frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            img = ImageTk.PhotoImage(Image.fromarray(frame_rgb))
            video_label.imgtk = img
            video_label.configure(image=img)
        else:
            video_label.imgtk = black_image_tk
            video_label.configure(image=black_image_tk)
    else:
        video_label.imgtk = black_image_tk
        video_label.configure(image=black_image_tk)
    root.after(10, update_video)

# 摄像头设备切换逻辑
def on_device_change(*args):
    device = device_var.get()
    if device != "无可用设备":
        if init_camera(device):
            log_text.insert(END, f"已切换到设备: {device}\n")
            log_text.see(END)
        else:
            log_text.insert(END, f"无法打开设备: {device}\n")
            log_text.see(END)

# 控制前三码和后四码文本框是否可编辑和显示
def toggle_vendor_model():
    if vendor_editable.get() == 1:
        state = "normal"
    else:
        state = "disabled"
    vendor_input.config(state=state)
    model_input.config(state=state)

# 初始化 Tkinter 界面
root = Tk()
root.title("OCR 识别工具")
root.geometry("1200x750")

# 左侧摄像头区域
frame = Frame(root, width=640, height=480)
frame.grid(row=0, column=0, rowspan=6, padx=10, pady=10)
video_label = Label(frame)
video_label.pack()

# 创建一个黑色画布
black_image = np.zeros((480, 640, 3), dtype=np.uint8)
black_image_tk = ImageTk.PhotoImage(Image.fromarray(black_image))

# 右侧控件区域
right_frame = Frame(root, width=360)
right_frame.grid(row=0, column=1, rowspan=6, padx=10, pady=10, sticky="n")

# 设备选择
device_var = StringVar(root)
device_var.set("选择设备")
devices = get_video_devices()
if devices:
    device_menu = OptionMenu(right_frame, device_var, *devices)
    device_var.set(devices[0])
    init_camera(devices[0])  # 初始化第一个设备
else:
    device_menu = OptionMenu(right_frame, device_var, "无可用设备")

device_menu.grid(row=0, column=0, pady=5, padx=(5, 15), sticky="w")  # 添加适当的右侧间距
device_menu.config(width=8)  # 设置宽度为 20 个字符的长度
# device_menu.grid(row=0, column=0, columnspan=2, pady=5, sticky="w")
device_var.trace("w", on_device_change)

# 添加刷新按钮
refresh_button = Button(right_frame, text="刷新设备", command=refresh_device_list, relief="raised")
refresh_button.grid(row=0, column=1, pady=5, padx=5, sticky="w")  # 保持与设备菜单的对齐

# 输入 SN 号
sn_label = Label(right_frame, text="输入 SN 号:")
sn_label.grid(row=1, column=0, padx=5, pady=5, sticky="w")
sn_input = Entry(right_frame, width=30)
sn_input.grid(row=1, column=1, padx=5, pady=5)

# 输入厂商前三码
vendor_label = Label(right_frame, text="前三码:")
vendor_label.grid(row=2, column=0, padx=5, pady=5, sticky="w")
vendor_input = Entry(right_frame, width=30, state="disabled")  # 默认不可编辑
vendor_input.grid(row=2, column=1, padx=5, pady=5)

# 输入生产标号后四码
model_label = Label(right_frame, text="后四码:")
model_label.grid(row=3, column=0, padx=5, pady=5, sticky="w")
model_input = Entry(right_frame, width=30, state="disabled")  # 默认不可编辑
model_input.grid(row=3, column=1, padx=5, pady=5)

# 添加单选按钮
vendor_editable = IntVar(value=0)  # 默认不选中
toggle_button = Checkbutton(right_frame, text="编辑前三码/后四码", variable=vendor_editable, command=toggle_vendor_model)
toggle_button.grid(row=2, column=2, padx=5, pady=5, sticky="w")

# 识别结果文本框
result_label = Label(right_frame, text="识别结果:")
result_label.grid(row=4, column=0, padx=5, pady=5, sticky="w")
result_text = Text(right_frame, width=40, height=10, wrap="word", bd=2, relief="sunken")
result_text.grid(row=5, column=0, columnspan=2, padx=5, pady=5)

# 模拟进度条的字符
def update_progress_bar(progress):
    # 创建进度条的长度
    max_progress = 10  # 进度条最大长度
    bar_length = int(progress * max_progress / 100)  # 根据进度百分比更新进度条的长度
    progress_bar = "#" * bar_length + "-" * (max_progress - bar_length)  # 使用 "#" 和 "-" 来模拟进度条
    
    result_text.delete(1.0, "end")  # 清空文本框内容
    result_text.insert("end", f"进度: [{progress_bar}] {progress}%")  # 显示进度条和百分比
    result_text.see("end")  # 滚动到文本框底部

# 更新进度的函数
def start_progress():
    for progress in range(0, 101, 50):
        update_progress_bar(progress)  # 更新进度条显示
        time.sleep(0.01)  # 模拟延时（例如计算过程）
        root.update()  # 更新 Tkinter 界面

# 日志框
log_label = Label(right_frame, text="日志输出:")
log_label.grid(row=6, column=0, padx=5, pady=5, sticky="w")
log_text = Text(right_frame, width=40, height=10, wrap="word", bd=2, relief="sunken")
log_text.grid(row=7, column=0, columnspan=2, padx=5, pady=5)

# 开始检测按钮
start_button = Button(right_frame, text="开始检测", command=start_detection, relief="raised", width=20, height=2)
start_button.grid(row=8, column=0, columnspan=2, padx=5, pady=10)

# download_log("oss://az05/checkCpu/results.csv", "results.csv")
# 更新摄像头画面
update_video()

# 窗口关闭时释放资源
def on_close():
    global cap
    if cap:
        cap.release()
    root.destroy()

root.protocol("WM_DELETE_WINDOW", on_close)

# 启动 Tkinter 主循环
root.mainloop()
