import csv
import fcntl
import glob
import os
import re
import subprocess
import threading
import time
from collections import deque
from datetime import datetime

import cv2
from flask import Flask, Response, jsonify, render_template, request


APP_HOST = os.environ.get("OCR_WEB_HOST", "0.0.0.0")
APP_PORT = int(os.environ.get("OCR_WEB_PORT", "5000"))
LOCK_PATH = os.path.expanduser("~/.cache/ocr_web.lock")
SAVE_DIR = os.path.expanduser("~/ocr")
os.makedirs(SAVE_DIR, exist_ok=True)


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


class AppState:
    def __init__(self):
        self.lock = threading.Lock()
        self.logs = deque(maxlen=400)
        self.result_text = ""
        self.time_info = {"download": None, "detect": None, "upload": None}
        self.detecting = False
        self.selected_device = None
        self.devices = []
        self.last_detection = None
        self.last_error = None

    def append_log(self, message):
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self.lock:
            self.logs.append(f"[{timestamp}] {message}")

    def set_result(self, text):
        with self.lock:
            self.result_text = text

    def set_time_info(self, download=None, detect=None, upload=None):
        with self.lock:
            self.time_info = {"download": download, "detect": detect, "upload": upload}

    def set_devices(self, devices, selected_device):
        with self.lock:
            self.devices = devices
            self.selected_device = selected_device

    def snapshot(self):
        with self.lock:
            return {
                "logs": list(self.logs),
                "result_text": self.result_text,
                "time_info": dict(self.time_info),
                "detecting": self.detecting,
                "selected_device": self.selected_device,
                "devices": list(self.devices),
                "last_detection": self.last_detection,
                "last_error": self.last_error,
            }


class CameraManager:
    def __init__(self, state):
        self.state = state
        self.lock = threading.Lock()
        self.cap = None
        self.current_frame = None
        self.current_device = None
        self.stop_event = threading.Event()
        self.worker = threading.Thread(target=self.capture_loop, daemon=True)
        self.worker.start()

    def open_device(self, device):
        with self.lock:
            self._release_locked()
            if not device:
                self.current_device = None
                self.current_frame = None
                return False

            open_targets = []
            match = re.fullmatch(r"/dev/video(\d+)", device)
            if match:
                open_targets.append((int(match.group(1)), cv2.CAP_V4L2))
            open_targets.append((device, cv2.CAP_V4L2))
            open_targets.append((device, cv2.CAP_ANY))

            for target, backend in open_targets:
                cap = cv2.VideoCapture(target, backend)
                if not cap.isOpened():
                    cap.release()
                    continue
                cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
                cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
                cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"YUYV"))
                self.cap = cap
                self.current_device = device
                self.current_frame = None
                self.state.append_log(f"已切换到设备: {device}")
                return True

            self.current_device = None
            self.current_frame = None
            self.state.append_log(f"无法打开设备: {device}，可能已被其他进程占用。")
            return False

    def _release_locked(self):
        if self.cap:
            self.cap.release()
            self.cap = None

    def release(self):
        with self.lock:
            self._release_locked()
            self.current_device = None
            self.current_frame = None

    def capture_loop(self):
        while not self.stop_event.is_set():
            with self.lock:
                cap = self.cap
            if cap and cap.isOpened():
                ok, frame = cap.read()
                if ok:
                    with self.lock:
                        self.current_frame = frame.copy()
                else:
                    time.sleep(0.05)
            else:
                time.sleep(0.1)

    def get_frame_copy(self):
        with self.lock:
            if self.current_frame is None:
                return None
            return self.current_frame.copy()

    def get_jpeg(self):
        frame = self.get_frame_copy()
        if frame is None:
            frame = self.build_placeholder()
        ok, encoded = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
        if not ok:
            return b""
        return encoded.tobytes()

    @staticmethod
    def build_placeholder():
        frame = 255 * (cv2.UMat(480, 640, cv2.CV_8UC3).get() * 0)
        cv2.putText(frame, "Camera Preview", (180, 220), cv2.FONT_HERSHEY_SIMPLEX, 1, (180, 180, 180), 2)
        cv2.putText(frame, "No frame available", (170, 270), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (140, 140, 140), 2)
        return frame


state = AppState()
camera = CameraManager(state)
app = Flask(__name__)


def format_elapsed(value):
    if value is None:
        return None
    return round(value, 3)


def refresh_devices():
    devices = get_video_devices()
    selected = state.snapshot()["selected_device"]
    if devices:
        if selected not in devices:
            selected = devices[0]
            camera.open_device(selected)
        elif camera.current_device != selected:
            camera.open_device(selected)
    else:
        selected = None
        camera.release()
        state.append_log("没有检测到可用设备！")
    state.set_devices(devices, selected)
    return devices, selected


def run_command(command):
    return subprocess.run(command, capture_output=True, text=True, check=True)


def download_log(oss_path, local_file_path):
    started_at = time.perf_counter()
    try:
        result = subprocess.run(
            f"ossutil cp -f {local_file_path} {oss_path}" if False else f"ossutil cp -f {oss_path} {local_file_path}",
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=True,
        )
        elapsed = time.perf_counter() - started_at
        state.append_log(f"下载日志完成，耗时 {elapsed:.3f}s")
        return elapsed, result.stdout
    except subprocess.CalledProcessError as exc:
        elapsed = time.perf_counter() - started_at
        state.append_log(f"下载日志失败，耗时 {elapsed:.3f}s")
        raise RuntimeError(exc.stderr.strip() or "下载日志失败") from exc


def upload_log(local_file_path, oss_path):
    started_at = time.perf_counter()
    try:
        result = subprocess.run(
            f"ossutil cp -f {local_file_path} {oss_path}",
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=True,
        )
        elapsed = time.perf_counter() - started_at
        state.append_log(f"上传日志完成，耗时 {elapsed:.3f}s")
        return elapsed, result.stdout
    except subprocess.CalledProcessError as exc:
        elapsed = time.perf_counter() - started_at
        state.append_log(f"上传日志失败，耗时 {elapsed:.3f}s")
        raise RuntimeError(exc.stderr.strip() or "上传日志失败") from exc


def perform_detection(sn, vendor, model, vendor_enabled):
    frame = camera.get_frame_copy()
    if frame is None:
        raise RuntimeError("摄像头未打开或当前没有有效画面。")

    download_cost, _ = download_log("oss://az05/checkCpu/results.csv", "results.csv")
    state.set_time_info(download=download_cost, detect=None, upload=None)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    photo_path = os.path.join(SAVE_DIR, f"{sn}_{timestamp}.jpg")
    cv2.imwrite(photo_path, frame)
    state.append_log(f"照片已保存到: {photo_path}")

    detect_started_at = time.perf_counter()
    try:
        result = run_command(["./rknn_ppocr_system_demo", "model/ppocrv4_det.rknn", "model/ppocrv4_rec.rknn", photo_path])
    finally:
        if os.path.exists(photo_path):
            os.remove(photo_path)
            state.append_log(f"已删除图片: {photo_path}")
    detect_cost = time.perf_counter() - detect_started_at
    state.append_log(f"识别完成，耗时 {detect_cost:.3f}s")
    state.append_log("检测结果:\n" + result.stdout.strip())

    if not os.path.exists("text.txt"):
        state.set_time_info(download=download_cost, detect=detect_cost, upload=None)
        raise RuntimeError("检测失败：未生成 text.txt。")

    with open("text.txt", "r", encoding="utf-8", errors="ignore") as file_obj:
        lines = file_obj.readlines()

    for index, line in enumerate(lines):
        if "RK3288" not in line:
            continue
        if index + 1 >= len(lines):
            break

        next_line = "".join(lines[index + 1].split())
        if vendor_enabled and (next_line[:3] != vendor or next_line[-4:] != model):
            state.set_time_info(download=download_cost, detect=detect_cost, upload=None)
            raise RuntimeError("检测失败：OCR 结果中，'RK3288' 下一行字符与输入不匹配。")

        file_exists = os.path.exists("results.csv")
        with open("results.csv", "a", newline="", encoding="utf-8") as csv_file:
            writer = csv.writer(csv_file)
            if not file_exists:
                writer.writerow(["SN号", "content", "Vendor", "Model", "date"])
            writer.writerow([sn, next_line, vendor, model, timestamp])
        state.append_log("CSV 文件已保存到本地: results.csv")

        upload_cost, _ = upload_log("results.csv", "oss://az05/checkCpu/")
        state.set_time_info(download=download_cost, detect=detect_cost, upload=upload_cost)
        state.set_result(f"PASS\n{next_line}")
        state.last_detection = {
            "sn": sn,
            "ocr_text": next_line,
            "timestamp": timestamp,
        }
        return

    state.set_time_info(download=download_cost, detect=detect_cost, upload=None)
    raise RuntimeError("检测失败：OCR 结果中未找到 'RK3288'。")


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/status")
def api_status():
    return jsonify(state.snapshot())


@app.route("/api/devices")
def api_devices():
    devices, selected = refresh_devices()
    return jsonify({"devices": devices, "selected_device": selected})


@app.route("/api/select-device", methods=["POST"])
def api_select_device():
    payload = request.get_json(force=True, silent=True) or {}
    device = payload.get("device")
    devices = get_video_devices()
    if device not in devices:
        return jsonify({"ok": False, "error": "设备不存在。"}), 400
    ok = camera.open_device(device)
    state.set_devices(devices, device if ok else None)
    return jsonify({"ok": ok, "selected_device": state.snapshot()["selected_device"]})


@app.route("/api/detect", methods=["POST"])
def api_detect():
    payload = request.get_json(force=True, silent=True) or {}
    sn = (payload.get("sn") or "").strip()
    vendor = (payload.get("vendor") or "").strip()
    model = (payload.get("model") or "").strip()
    vendor_enabled = bool(payload.get("vendor_enabled"))

    if not sn:
        return jsonify({"ok": False, "error": "SN 不能为空。"}), 400
    if vendor_enabled and (not vendor or not model):
        return jsonify({"ok": False, "error": "前三码和后四码不能为空。"}), 400

    snapshot = state.snapshot()
    if snapshot["detecting"]:
        return jsonify({"ok": False, "error": "检测进行中，请稍后。"}), 409

    state.detecting = True
    state.set_result("处理中...")
    state.set_time_info(download=None, detect=None, upload=None)
    try:
        perform_detection(sn, vendor, model, vendor_enabled)
        return jsonify({"ok": True, "state": state.snapshot()})
    except RuntimeError as exc:
        state.last_error = str(exc)
        state.set_result("FAIL")
        state.append_log(str(exc))
        return jsonify({"ok": False, "error": str(exc), "state": state.snapshot()}), 500
    finally:
        state.detecting = False


@app.route("/video_feed")
def video_feed():
    def generate():
        while True:
            frame = camera.get_jpeg()
            yield (
                b"--frame\r\n"
                b"Content-Type: image/jpeg\r\n\r\n" + frame + b"\r\n"
            )
            time.sleep(0.05)

    return Response(generate(), mimetype="multipart/x-mixed-replace; boundary=frame")


def acquire_lock():
    os.makedirs(os.path.expanduser("~/.cache"), exist_ok=True)
    lock_file = open(LOCK_PATH, "w")
    fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    return lock_file


def main():
    try:
        lock_file = acquire_lock()
    except OSError as exc:
        raise RuntimeError("OCR Web 服务已经在运行。") from exc

    state.append_log("OCR Web 服务启动。")
    refresh_devices()
    app.config["LOCK_FILE"] = lock_file
    app.run(host=APP_HOST, port=APP_PORT, debug=False, threaded=True)


if __name__ == "__main__":
    main()
