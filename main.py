import csv
import glob
import os
import subprocess
import threading
import time
from datetime import datetime
import re

import cv2
import gi
import numpy as np

gi.require_version("GdkPixbuf", "2.0")
gi.require_version("Gtk", "3.0")
from gi.repository import GdkPixbuf, GLib, Gtk


save_dir = os.path.expanduser("~/ocr")
os.makedirs(save_dir, exist_ok=True)


def extract_video_index(device):
    try:
        suffix = device.split("video")[-1]
        if suffix.isdigit():
            return int(suffix)
    except Exception:
        pass
    return float("inf")


def get_video_devices():
    all_devices = glob.glob("/dev/video*")
    capture_devices = [device for device in all_devices if re.fullmatch(r"/dev/video\d+", device)]
    if capture_devices:
        return sorted(capture_devices, key=extract_video_index)
    return sorted(all_devices, key=extract_video_index)


class OCRApp:
    def __init__(self):
        self.cap = None
        self.devices = []
        self.current_frame = None
        self.is_detecting = False
        self.placeholder_pixbuf = self.create_black_pixbuf()
        self.latest_frame_bytes = None

        self.window = Gtk.Window(title="OCR 识别工具")
        self.window.set_default_size(1200, 750)
        self.window.set_border_width(12)
        self.window.connect("destroy", self.on_close)

        self.build_ui()
        self.refresh_device_list(initial=True)

        GLib.timeout_add(33, self.update_video)
        for delay in (200, 1000, 3000):
            GLib.timeout_add(delay, self.present_window)

    def build_ui(self):
        root_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        self.window.add(root_box)

        left_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        root_box.pack_start(left_box, True, True, 0)

        self.video_image = Gtk.Image()
        self.video_image.set_from_pixbuf(self.placeholder_pixbuf)
        video_frame = Gtk.Frame()
        video_frame.add(self.video_image)
        left_box.pack_start(video_frame, True, True, 0)

        right_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        right_box.set_size_request(420, -1)
        root_box.pack_start(right_box, False, False, 0)

        device_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        right_box.pack_start(device_row, False, False, 0)

        self.device_combo = Gtk.ComboBoxText()
        self.device_combo.connect("changed", self.on_device_change)
        device_row.pack_start(self.device_combo, True, True, 0)

        refresh_button = Gtk.Button(label="刷新设备")
        refresh_button.connect("clicked", self.on_refresh_clicked)
        device_row.pack_start(refresh_button, False, False, 0)

        form_grid = Gtk.Grid(column_spacing=8, row_spacing=8)
        right_box.pack_start(form_grid, False, False, 0)

        sn_label = Gtk.Label(label="输入 SN 号:")
        sn_label.set_xalign(0)
        form_grid.attach(sn_label, 0, 0, 1, 1)
        self.sn_entry = Gtk.Entry()
        form_grid.attach(self.sn_entry, 1, 0, 2, 1)

        vendor_label = Gtk.Label(label="前三码:")
        vendor_label.set_xalign(0)
        form_grid.attach(vendor_label, 0, 1, 1, 1)
        self.vendor_entry = Gtk.Entry()
        self.vendor_entry.set_sensitive(False)
        form_grid.attach(self.vendor_entry, 1, 1, 1, 1)

        model_label = Gtk.Label(label="后四码:")
        model_label.set_xalign(0)
        form_grid.attach(model_label, 0, 2, 1, 1)
        self.model_entry = Gtk.Entry()
        self.model_entry.set_sensitive(False)
        form_grid.attach(self.model_entry, 1, 2, 1, 1)

        self.vendor_check = Gtk.CheckButton(label="编辑前三码/后四码")
        self.vendor_check.connect("toggled", self.on_vendor_toggled)
        form_grid.attach(self.vendor_check, 2, 1, 1, 2)

        result_label = Gtk.Label(label="识别结果:")
        result_label.set_xalign(0)
        right_box.pack_start(result_label, False, False, 0)

        self.result_view = self.create_text_view(height=120)
        right_box.pack_start(self.result_view["scrolled"], False, False, 0)

        self.time_info_label = Gtk.Label(label="下载: - | 识别: - | 上传: -")
        self.time_info_label.set_xalign(0)
        right_box.pack_start(self.time_info_label, False, False, 0)

        log_label = Gtk.Label(label="日志输出:")
        log_label.set_xalign(0)
        right_box.pack_start(log_label, False, False, 0)

        self.log_view = self.create_text_view(height=220)
        right_box.pack_start(self.log_view["scrolled"], True, True, 0)

        self.start_button = Gtk.Button(label="开始检测")
        self.start_button.set_size_request(-1, 48)
        self.start_button.connect("clicked", self.on_start_clicked)
        right_box.pack_start(self.start_button, False, False, 0)

    def create_text_view(self, height):
        text_view = Gtk.TextView()
        text_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        text_view.set_editable(False)
        text_view.set_cursor_visible(False)

        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_size_request(-1, height)
        scrolled.add(text_view)
        return {"view": text_view, "buffer": text_view.get_buffer(), "scrolled": scrolled}

    def create_black_pixbuf(self):
        black_image = np.zeros((480, 640, 3), dtype=np.uint8)
        return self.frame_to_pixbuf(black_image)

    def frame_to_pixbuf(self, frame):
        if frame.shape[:2] != (480, 640):
            frame = cv2.resize(frame, (640, 480))
        if frame.ndim != 3 or frame.shape[2] != 3:
            return None
        frame = np.ascontiguousarray(frame)
        self.latest_frame_bytes = GLib.Bytes.new(frame.tobytes())
        return GdkPixbuf.Pixbuf.new_from_bytes(
            self.latest_frame_bytes,
            GdkPixbuf.Colorspace.RGB,
            False,
            8,
            frame.shape[1],
            frame.shape[0],
            frame.shape[1] * 3,
        )

    def present_window(self):
        self.window.show_all()
        self.window.deiconify()
        self.window.present()
        return False

    def set_text_buffer(self, buffer_wrapper, text):
        buffer_wrapper["buffer"].set_text(text)
        mark = buffer_wrapper["buffer"].get_insert()
        buffer_wrapper["view"].scroll_mark_onscreen(mark)

    def append_text_buffer(self, buffer_wrapper, text):
        buffer_obj = buffer_wrapper["buffer"]
        end_iter = buffer_obj.get_end_iter()
        buffer_obj.insert(end_iter, text)
        mark = buffer_obj.get_insert()
        buffer_wrapper["view"].scroll_mark_onscreen(mark)

    def set_result_text(self, text):
        self.set_text_buffer(self.result_view, text)

    def append_log(self, text):
        self.append_text_buffer(self.log_view, text)

    def log_with_time(self, text):
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        GLib.idle_add(self.append_log, f"[{timestamp}] {text}\n")

    def set_time_info(self, download_cost=None, detect_cost=None, upload_cost=None):
        def format_cost(value):
            if value is None:
                return "-"
            return f"{value:.3f}s"

        self.time_info_label.set_text(
            f"下载: {format_cost(download_cost)} | 识别: {format_cost(detect_cost)} | 上传: {format_cost(upload_cost)}"
        )

    def set_busy_state(self, busy):
        self.is_detecting = busy
        self.start_button.set_sensitive(not busy)
        self.device_combo.set_sensitive(not busy)

    def upload_log(self, local_file_path, oss_path):
        started_at = time.perf_counter()
        try:
            command = f"ossutil cp -f {local_file_path} {oss_path}"
            result = subprocess.run(
                command,
                shell=True,
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            print("Upload successful:", result.stdout)
            elapsed = time.perf_counter() - started_at
            GLib.idle_add(self.start_progress)
            GLib.idle_add(self.set_result_text, " PASS")
            self.log_with_time(f"上传日志完成，耗时 {elapsed:.3f}s")
            return elapsed
        except subprocess.CalledProcessError as exc:
            print("Error during upload:", exc.stderr)
            elapsed = time.perf_counter() - started_at
            GLib.idle_add(self.start_progress)
            GLib.idle_add(self.set_result_text, " FAIL")
            self.log_with_time(f"上传日志失败，耗时 {elapsed:.3f}s")
            return elapsed

    def download_log(self, oss_path, local_file_path):
        started_at = time.perf_counter()
        try:
            command = f"ossutil cp -f {oss_path} {local_file_path}"
            result = subprocess.run(
                command,
                shell=True,
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            print("Download successful:", result.stdout)
            elapsed = time.perf_counter() - started_at
            self.log_with_time(f"下载日志完成，耗时 {elapsed:.3f}s")
            return elapsed
        except subprocess.CalledProcessError as exc:
            print("Error during download:", exc.stderr)
            elapsed = time.perf_counter() - started_at
            self.log_with_time(f"下载日志失败，耗时 {elapsed:.3f}s")
            return elapsed

    def update_progress_bar(self, progress):
        max_progress = 10
        bar_length = int(progress * max_progress / 100)
        progress_bar = "#" * bar_length + "-" * (max_progress - bar_length)
        self.set_result_text(f"进度: [{progress_bar}] {progress}%")

    def start_progress(self):
        while Gtk.events_pending():
            Gtk.main_iteration_do(False)
        for progress in range(0, 101, 50):
            self.update_progress_bar(progress)
            time.sleep(0.01)
            while Gtk.events_pending():
                Gtk.main_iteration_do(False)

    def on_refresh_clicked(self, _button):
        self.refresh_device_list()

    def refresh_device_list(self, initial=False):
        current_devices = get_video_devices()
        if not initial and current_devices == self.devices:
            return

        previous_device = self.device_combo.get_active_text()
        self.devices = current_devices
        self.device_combo.remove_all()

        if self.devices:
            for device in self.devices:
                self.device_combo.append_text(device)
            if previous_device in self.devices:
                self.device_combo.set_active(self.devices.index(previous_device))
            else:
                self.device_combo.set_active(0)
                self.append_log(f"设备已切换到: {self.devices[0]}\n")
        else:
            self.device_combo.append_text("无可用设备")
            self.device_combo.set_active(0)
            self.append_log("没有检测到可用设备！\n")

    def on_device_change(self, combo):
        device = combo.get_active_text()
        if not device or device == "无可用设备":
            if self.cap:
                self.cap.release()
                self.cap = None
            return

        if self.init_camera(device):
            self.append_log(f"已切换到设备: {device}\n")
        else:
            self.append_log(f"无法打开设备: {device}\n")

    def on_vendor_toggled(self, button):
        editable = button.get_active()
        self.vendor_entry.set_sensitive(editable)
        self.model_entry.set_sensitive(editable)

    def init_camera(self, device):
        if self.cap:
            self.cap.release()
        self.cap = cv2.VideoCapture(device)
        if not self.cap.isOpened():
            self.cap = None
            return None
        return self.cap

    def update_video(self):
        frame = None
        if self.cap and self.cap.isOpened():
            ret, frame = self.cap.read()
            if ret:
                self.current_frame = frame.copy()
                frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                pixbuf = self.frame_to_pixbuf(frame_rgb)
                if pixbuf is not None:
                    self.video_image.set_from_pixbuf(pixbuf)
            else:
                self.current_frame = None
        if frame is None:
            self.video_image.set_from_pixbuf(self.placeholder_pixbuf)
        return True

    def on_start_clicked(self, _button):
        if self.is_detecting:
            return
        self.set_busy_state(True)
        self.set_result_text("处理中...")
        self.set_time_info()
        frame_snapshot = None if self.current_frame is None else self.current_frame.copy()
        form_data = {
            "sn": self.sn_entry.get_text().strip(),
            "vendor": self.vendor_entry.get_text().strip(),
            "model": self.model_entry.get_text().strip(),
            "vendor_enabled": self.vendor_check.get_active(),
            "frame": frame_snapshot,
        }
        worker = threading.Thread(target=self.start_detection, args=(form_data,), daemon=True)
        worker.start()

    def start_detection(self, form_data):
        try:
            GLib.idle_add(self.vendor_check.set_active, False)
            download_cost = self.download_log("oss://az05/checkCpu/results.csv", "results.csv")

            frame = form_data["frame"]
            if frame is None:
                self.log_with_time("摄像头未打开，请先选择设备！")
                GLib.idle_add(self.set_result_text, " FAIL")
                return

            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            sn = form_data["sn"]
            vendor = form_data["vendor"]
            model = form_data["model"]
            vendor_enabled = form_data["vendor_enabled"]

            GLib.idle_add(self.sn_entry.set_text, "")
            GLib.idle_add(self.sn_entry.grab_focus)

            if not sn or (vendor_enabled and (not vendor or not model)):
                self.log_with_time("输入项不能为空，请检查输入！")
                GLib.idle_add(self.set_result_text, " FAIL")
                return

            photo_path = os.path.join(save_dir, f"{sn}_{timestamp}.jpg")
            cv2.imwrite(photo_path, frame)
            self.log_with_time(f"照片已保存到: {photo_path}")

            command = ["./rknn_ppocr_system_demo", "model/ppocrv4_det.rknn", "model/ppocrv4_rec.rknn", photo_path]
            detect_started_at = time.perf_counter()
            result = subprocess.run(command, capture_output=True, text=True, check=True)
            detect_cost = time.perf_counter() - detect_started_at
            self.log_with_time(f"识别完成，耗时 {detect_cost:.3f}s")
            GLib.idle_add(self.append_log, f"检测结果:\n{result.stdout}\n")

            results = []
            upload_cost = None
            if os.path.exists(photo_path):
                os.remove(photo_path)
                self.log_with_time(f"已删除图片: {photo_path}")

            if not os.path.exists("text.txt"):
                self.log_with_time("检测失败：未生成 text.txt。")
                GLib.idle_add(self.set_time_info, download_cost, detect_cost, upload_cost)
                return

            with open("text.txt", "r", encoding="utf-8", errors="ignore") as file_obj:
                lines = file_obj.readlines()

            for index, line in enumerate(lines):
                if "RK3288" not in line:
                    continue
                if index + 1 >= len(lines):
                    break

                next_line = "".join(lines[index + 1].split())
                if vendor and model and (next_line[:3] != vendor or next_line[-4:] != model):
                    self.log_with_time("检测失败：OCR 结果中，'RK3288' 下一行字符与输入不匹配。")
                    GLib.idle_add(self.start_progress)
                    GLib.idle_add(self.set_result_text, " FAIL")
                    GLib.idle_add(self.set_time_info, download_cost, detect_cost, upload_cost)
                    return

                results.append([sn, next_line, vendor, model, timestamp])
                file_exists = os.path.exists("results.csv")
                with open("results.csv", "a", newline="", encoding="utf-8") as csv_file:
                    writer = csv.writer(csv_file)
                    if not file_exists:
                        writer.writerow(["SN号", "content", "Vendor", "Model", "date"])
                    writer.writerows(results)

                self.log_with_time("CSV 文件已保存到本地: results.csv")
                upload_cost = self.upload_log("results.csv", "oss://az05/checkCpu/")
                GLib.idle_add(self.set_time_info, download_cost, detect_cost, upload_cost)
                return

            self.log_with_time("检测失败：OCR 结果中未找到 'RK3288'。")
            GLib.idle_add(self.start_progress)
            GLib.idle_add(self.set_result_text, " FAIL")
            GLib.idle_add(self.set_time_info, download_cost, detect_cost, upload_cost)
        except subprocess.CalledProcessError as exc:
            self.log_with_time(f"检测失败: {exc.stderr.strip()}")
            GLib.idle_add(self.set_result_text, " FAIL")
        finally:
            GLib.idle_add(self.set_busy_state, False)

    def on_close(self, *_args):
        if self.cap:
            self.cap.release()
            self.cap = None
        Gtk.main_quit()


def main():
    app = OCRApp()
    app.window.show_all()
    Gtk.main()


if __name__ == "__main__":
    main()
