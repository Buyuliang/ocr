import fcntl
import glob
import json
import os
import re
import subprocess
import threading
import time
from collections import deque
from datetime import datetime
from urllib import error as urllib_error
from urllib import request as urllib_request

import cv2
import numpy as np
from flask import Flask, Response, jsonify, render_template, request


class UploadError(RuntimeError):
    pass


APP_HOST = os.environ.get("OCR_WEB_HOST", "0.0.0.0")
APP_PORT = int(os.environ.get("OCR_WEB_PORT", "5000"))
API_BASE = os.environ.get("OCR_SN_API_BASE", "http://192.168.202.203:5000").rstrip("/")
LOCK_PATH = os.path.expanduser("~/.cache/ocr_web.lock")
SAVE_DIR = os.path.expanduser("~/ocr")
os.makedirs(SAVE_DIR, exist_ok=True)
OCR_DEMO = "./rknn_ppocrv5_demo"
OCR_DET_MODEL = "model/PP-OCRv5_mobile_det.rknn"
OCR_REC_MODEL = "model/PP-OCRv5_mobile_rec.rknn"
LOGIN_URL = f"{API_BASE}/api/login"
PRODUCT_URL = f"{API_BASE}/api/product"
LOGIN_USERNAME = "mixtile"
LOGIN_PASSWORD = "rK#nH6wea]h<PP%]_EdA"
CPU_MODELS = ["RK3288"]
DEFAULT_CPU_MODEL = "RK3288"
DISPLAY_FRAME_WIDTH = 640
DISPLAY_FRAME_HEIGHT = 480


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
        self.upload_logs = deque(maxlen=200)
        self.result_text = ""
        self.time_info = {"download": None, "detect": None, "upload": None}
        self.detecting = False
        self.selected_device = None
        self.devices = []
        self.last_detection = None
        self.last_error = None
        self.last_seen_sn = None
        self.last_result_status = "idle"
        self.source_mode = "camera"
        self.operation_mode = "server"
        self.local_image_name = None
        self.overlays = {"cpu": None, "qr": None}

    def append_log(self, message):
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self.lock:
            self.logs.append(f"[{timestamp}] {message}")

    def append_upload_log(self, title, payload):
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        if isinstance(payload, str):
            text = payload
        else:
            text = json.dumps(payload, ensure_ascii=False, indent=2)
        with self.lock:
            self.upload_logs.appendleft({"timestamp": timestamp, "title": title, "text": text})

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

    def set_detecting(self, value):
        with self.lock:
            self.detecting = value

    def set_source_mode(self, mode, image_name=None):
        with self.lock:
            self.source_mode = mode
            self.local_image_name = image_name

    def set_operation_mode(self, mode):
        with self.lock:
            self.operation_mode = mode

    def mark_seen_sn(self, sn, status="processing"):
        with self.lock:
            self.last_seen_sn = sn
            self.last_result_status = status

    def mark_result_status(self, status, error=None):
        with self.lock:
            self.last_result_status = status
            self.last_error = error

    def set_last_detection(self, payload):
        with self.lock:
            self.last_detection = payload

    def set_overlay(self, target, payload):
        with self.lock:
            self.overlays[target] = payload

    def clear_overlays(self):
        with self.lock:
            self.overlays = {"cpu": None, "qr": None}

    def clear_logs(self):
        with self.lock:
            self.logs.clear()

    def upload_log_snapshot(self):
        with self.lock:
            return list(self.upload_logs)

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
                "last_seen_sn": self.last_seen_sn,
                "last_result_status": self.last_result_status,
                "last_uploaded_sn": self.last_detection["sn"] if self.last_detection else None,
                "source_mode": self.source_mode,
                "operation_mode": self.operation_mode,
                "local_image_name": self.local_image_name,
                "overlays": dict(self.overlays),
            }


class CameraManager:
    def __init__(self, state):
        self.state = state
        self.lock = threading.Lock()
        self.cap = None
        self.current_frame = None
        self.uploaded_frame = None
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
            if self.uploaded_frame is not None:
                return self.uploaded_frame.copy()
            if self.current_frame is None:
                return None
            return self.current_frame.copy()

    def has_uploaded_frame(self):
        with self.lock:
            return self.uploaded_frame is not None

    def enter_local_mode(self):
        self.state.set_operation_mode("local")
        self.state.set_source_mode("local_image", self.state.snapshot().get("local_image_name"))
        self.state.append_log("已进入本地图片模式。")

    def set_uploaded_frame(self, frame, image_name):
        with self.lock:
            self.uploaded_frame = frame.copy()
        self.state.set_operation_mode("local")
        self.state.set_source_mode("local_image", image_name)
        self.state.append_log(f"已切换到本地图片模式: {image_name}")

    def clear_uploaded_frame(self, exit_mode=True):
        with self.lock:
            self.uploaded_frame = None
        if exit_mode:
            self.state.set_operation_mode("server")
            self.state.set_source_mode("camera", None)
        else:
            self.state.set_operation_mode("local")
            self.state.set_source_mode("local_image", None)

    def get_jpeg(self):
        frame = self.get_frame_copy()
        if frame is None:
            if self.state.snapshot().get("operation_mode") == "local":
                frame = self.build_placeholder("Local Image Mode", "Double-click OCR to choose image")
            else:
                frame = self.build_placeholder("Camera Preview", "No frame available")
        elif frame.shape[1] != 640 or frame.shape[0] != 480:
            frame = cv2.resize(frame, (640, 480))
        ok, encoded = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
        if not ok:
            return b""
        return encoded.tobytes()

    @staticmethod
    def build_placeholder(title, subtitle):
        frame = 255 * (cv2.UMat(480, 640, cv2.CV_8UC3).get() * 0)
        cv2.putText(frame, title, (120, 220), cv2.FONT_HERSHEY_SIMPLEX, 1, (180, 180, 180), 2)
        cv2.putText(frame, subtitle, (80, 270), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (140, 140, 140), 2)
        return frame


state = AppState()
camera = CameraManager(state)
app = Flask(__name__)
auth_lock = threading.Lock()
auth_token = None


def build_state_response(payload=None, status=200):
    body = dict(payload or {})
    body["state"] = state.snapshot()
    return jsonify(body), status


def format_elapsed(value):
    if value is None:
        return None
    return round(value, 3)


def decode_uploaded_image(file_storage):
    data = file_storage.read()
    if not data:
        raise RuntimeError("上传的图片为空。")
    image_array = np.frombuffer(data, dtype=np.uint8)
    frame = cv2.imdecode(image_array, cv2.IMREAD_COLOR)
    if frame is None:
        raise RuntimeError("无法解析上传的图片。")
    return frame


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


def get_display_shape():
    return (DISPLAY_FRAME_HEIGHT, DISPLAY_FRAME_WIDTH, 3)

def map_points_to_display(points, frame_shape):
    if points is None:
        return None
    array = np.array(points, dtype=float).reshape(-1, 2)
    frame_height, frame_width = frame_shape[:2]
    if not frame_width or not frame_height:
        return None
    array[:, 0] = array[:, 0] * DISPLAY_FRAME_WIDTH / frame_width
    array[:, 1] = array[:, 1] * DISPLAY_FRAME_HEIGHT / frame_height
    return array.tolist()


def points_to_rect(points):
    if not points:
        return None
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    return {
        "x": max(0, int(round(min(xs)))),
        "y": max(0, int(round(min(ys)))),
        "w": max(1, int(round(max(xs) - min(xs)))),
        "h": max(1, int(round(max(ys) - min(ys)))),
    }


def build_overlay_payload(label, rect, text, points=None):
    if not rect:
        return None
    return {
        "label": label,
        "rect": rect,
        "text": text,
        "points": points or [],
    }


def get_detection_frame():
    snapshot = state.snapshot()
    operation_mode = snapshot.get("operation_mode")
    if operation_mode == "local" and not camera.has_uploaded_frame():
        raise RuntimeError("本地图片模式下请先双击 OCR 选择本地图片。")
    frame = camera.get_frame_copy()
    if frame is None:
        if operation_mode == "local":
            raise RuntimeError("本地图片模式下当前没有可识别的图片。")
        raise RuntimeError("摄像头未打开或当前没有有效画面。")
    return frame


def run_command(command):
    return subprocess.run(command, capture_output=True, text=True, check=True)


def http_json_request(url, payload, headers=None):
    request_headers = {"Content-Type": "application/json"}
    if headers:
        request_headers.update(headers)
    req = urllib_request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=request_headers,
    )
    started_at = time.perf_counter()
    try:
        with urllib_request.urlopen(req, timeout=10) as response:
            response_text = response.read().decode("utf-8", "ignore")
            response_json = json.loads(response_text) if response_text else {}
            return format_elapsed(time.perf_counter() - started_at), response_json
    except urllib_error.HTTPError as exc:
        response_text = exc.read().decode("utf-8", "ignore")
        raise UploadError(response_text or f"HTTP {exc.code}") from exc
    except (urllib_error.URLError, TimeoutError) as exc:
        raise UploadError(f"接口请求失败: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise UploadError("接口返回了无法解析的 JSON。") from exc


def login_and_get_token():
    global auth_token
    with auth_lock:
        if auth_token:
            return 0.0, auth_token
        login_cost, response_json = http_json_request(
            LOGIN_URL,
            {"username": LOGIN_USERNAME, "password": LOGIN_PASSWORD},
        )
        token = response_json.get("access_token")
        if not token:
            raise UploadError("登录接口未返回 access_token。")
        auth_token = token
        return login_cost, token


def upload_product(payload):
    global auth_token
    state.append_upload_log("上传请求 JSON", payload)
    print("[UPLOAD REQUEST]", json.dumps(payload, ensure_ascii=False))
    login_cost, token = login_and_get_token()
    headers = {"Authorization": f"Bearer {token}"}
    try:
        upload_cost, response_json = http_json_request(PRODUCT_URL, payload, headers=headers)
        state.append_upload_log("上传返回 JSON", response_json)
        print("[UPLOAD RESPONSE]", json.dumps(response_json, ensure_ascii=False))
        return login_cost, upload_cost, response_json
    except UploadError as exc:
        if "401" not in str(exc) and "UNAUTHORIZED" not in str(exc).upper():
            raise
    with auth_lock:
        auth_token = None
    login_cost, token = login_and_get_token()
    headers = {"Authorization": f"Bearer {token}"}
    upload_cost, response_json = http_json_request(PRODUCT_URL, payload, headers=headers)
    state.append_upload_log("上传返回 JSON", response_json)
    print("[UPLOAD RESPONSE]", json.dumps(response_json, ensure_ascii=False))
    return login_cost, upload_cost, response_json


def translate_points(points, offset_x, offset_y):
    if points is None:
        return None
    array = np.array(points, dtype=float).reshape(-1, 2)
    array[:, 0] += offset_x
    array[:, 1] += offset_y
    return array


def decode_qr_codes(frame):
    detect_frame = frame
    detector = cv2.QRCodeDetector()
    variants = [detect_frame]

    gray = cv2.cvtColor(detect_frame, cv2.COLOR_BGR2GRAY)
    variants.append(gray)
    variants.append(cv2.equalizeHist(gray))
    _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    variants.append(binary)

    seen = set()
    decoded = []
    overlay_payload = None
    for candidate in variants:
        try:
            ok, texts, points, _ = detector.detectAndDecodeMulti(candidate)
        except Exception:
            ok, texts, points = False, [], None
        if ok and texts:
            for index, text in enumerate(texts):
                value = (text or '').strip()
                if value and value not in seen:
                    seen.add(value)
                    decoded.append(value)
                    candidate_points = None
                    if points is not None and len(points) > index:
                        display_points = map_points_to_display(points[index], frame.shape)
                        overlay_payload = build_overlay_payload(
                            "QR",
                            points_to_rect(display_points),
                            value,
                            display_points,
                        )
        if decoded:
            return decoded, overlay_payload

        try:
            text, points, _ = detector.detectAndDecode(candidate)
        except Exception:
            text, points = '', None
        value = (text or '').strip()
        if value and value not in seen:
            seen.add(value)
            decoded.append(value)
            display_points = map_points_to_display(points, frame.shape)
            overlay_payload = build_overlay_payload(
                "QR",
                points_to_rect(display_points),
                value,
                display_points,
            )
            return decoded, overlay_payload

    return decoded, None


def detect_qr_codes(update_result=True):
    frame = get_detection_frame()
    decoded, overlay_payload = decode_qr_codes(frame)
    state.append_log('二维码识别使用全图。')

    if not decoded:
        state.set_overlay("qr", None)
        raise RuntimeError('未识别到二维码。')

    state.append_log('二维码识别结果: ' + ' | '.join(decoded))
    if update_result:
        state.set_result('QR\n' + '\n'.join(decoded))
    state.set_overlay("qr", overlay_payload)
    state.mark_result_status(state.snapshot().get("last_result_status") or "idle", None)
    return decoded


def perform_qr_detection():
    decoded = detect_qr_codes(update_result=True)
    state.set_time_info(download=None, detect=None, upload=None)
    return decoded


def analyze_ocr_lines(lines, cpu_model):
    compact_lines = [line.strip() for line in lines if line.strip()]
    normalized_cpu = cpu_model.replace(" ", "").upper()

    has_cpu_model = False
    candidates = []

    for line in compact_lines:
        normalized_line = "".join(line.split()).upper()
        if normalized_cpu in normalized_line:
            has_cpu_model = True
            continue

        candidate = "".join(line.split())
        if len(candidate) < 7:
            continue
        if not re.search(r"[A-Za-z]", candidate):
            continue
        if not re.search(r"\d", candidate):
            continue
        candidates.append(candidate)

    if not has_cpu_model:
        raise RuntimeError(f"检测失败：OCR 结果中未找到 '{cpu_model}'。")
    if not candidates:
        raise RuntimeError("检测失败：OCR 结果中未找到可用丝印。")
    return candidates


def build_cpu_overlay_text(lines, cpu_model):
    compact_lines = [line.strip() for line in lines if line.strip()]
    normalized_cpu = cpu_model.replace(" ", "").upper()
    preferred = []
    fallback = []

    for line in compact_lines:
        normalized_line = "".join(line.split()).upper()
        compact = "".join(line.split())
        if not compact:
            continue
        if normalized_cpu and normalized_cpu in normalized_line:
            continue
        if re.search(r"[A-Za-z]", compact) and re.search(r"\d", compact):
            preferred.append(compact)
        else:
            fallback.append(compact)

    display_lines = preferred or fallback or compact_lines
    return "\n".join(display_lines[:3])


def normalize_manual_fragment(value):
    return re.sub(r"\s+", "", (value or "").strip()).upper()


def require_non_empty_fields(work_order_number, sn, vendor, model):
    missing = []
    if not work_order_number:
        missing.append("工单号")
    if not sn:
        missing.append("SN")
    if not vendor:
        missing.append("前三码")
    if not model:
        missing.append("后四码")
    if missing:
        raise RuntimeError("、".join(missing) + "不能为空。")


def build_pass_result_text(sn, silkscreen=None):
    lines = ["PASS", "等待下一个设备"]
    if sn:
        lines.append(f"SN: {sn}")
    if silkscreen:
        lines.append(f"CPU: {silkscreen}")
    return "\n".join(lines)


def select_silkscreen(candidates, vendor, model):
    if vendor or model:
        for candidate in candidates:
            normalized_candidate = candidate.upper()
            if vendor and normalized_candidate[:3] != vendor:
                continue
            if model and normalized_candidate[-4:] != model:
                continue
            return candidate
    return candidates[0]


def ensure_not_duplicate_sn(sn):
    snapshot = state.snapshot()
    last_sn = snapshot.get('last_seen_sn')
    last_result_status = snapshot.get('last_result_status')
    if not last_sn:
        return
    if last_sn == sn and last_result_status == 'pass':
        last_detection = snapshot.get("last_detection") or {}
        last_time_info = last_detection.get("time_info")
        if isinstance(last_time_info, dict):
            state.set_time_info(
                download=last_time_info.get("download"),
                detect=last_time_info.get("detect"),
                upload=last_time_info.get("upload"),
            )
        state.set_result(f'PASS\n等待下一个设备\nSN: {sn}')
        raise RuntimeError(f'SN {sn} 已经 PASS，请更换下一台设备后再检测。')


def resolve_detection_sn(sn):
    normalized_sn = (sn or "").strip()
    if not normalized_sn:
        raise RuntimeError("SN不能为空。")
    state.append_log(f"本次检测使用手动 SN: {normalized_sn}")
    return normalized_sn


def perform_detection(sn, vendor, model, work_order_number, cpu_model):
    state.set_overlay("cpu", None)
    state.set_overlay("qr", None)
    sn = resolve_detection_sn(sn)
    require_non_empty_fields(work_order_number, sn, vendor, model)
    ensure_not_duplicate_sn(sn)
    state.mark_seen_sn(sn, "processing")

    frame = get_detection_frame()
    state.set_time_info(download=None, detect=None, upload=None)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    photo_path = os.path.join(SAVE_DIR, f"{sn}_{timestamp}.jpg")
    detect_frame = frame
    cv2.imwrite(photo_path, detect_frame)
    state.append_log(f"照片已保存到: {photo_path}")
    state.append_log("CPU 识别使用全图。")

    detect_started_at = time.perf_counter()
    try:
        result = run_command([OCR_DEMO, OCR_DET_MODEL, OCR_REC_MODEL, photo_path])
    finally:
        if os.path.exists(photo_path):
            os.remove(photo_path)
            state.append_log(f"已删除图片: {photo_path}")
    detect_cost = time.perf_counter() - detect_started_at
    state.append_log(f"识别完成，耗时 {detect_cost:.3f}s")
    state.append_log("检测结果:\n" + result.stdout.strip())

    if not os.path.exists("text.txt"):
        state.set_time_info(download=None, detect=detect_cost, upload=None)
        raise RuntimeError("检测失败：未生成 text.txt。")

    with open("text.txt", "r", encoding="utf-8", errors="ignore") as file_obj:
        lines = file_obj.readlines()

    cpu_overlay_text = build_cpu_overlay_text(lines, cpu_model)
    if cpu_overlay_text:
        state.set_overlay(
            "cpu",
            build_overlay_payload(
                "CPU",
                {"x": 0, "y": 0, "w": DISPLAY_FRAME_WIDTH, "h": DISPLAY_FRAME_HEIGHT},
                cpu_overlay_text,
            ),
        )

    candidates = analyze_ocr_lines(lines, cpu_model)
    silkscreen = select_silkscreen(candidates, vendor, model)
    state.set_overlay(
        "cpu",
        build_overlay_payload(
            "CPU",
            {"x": 0, "y": 0, "w": DISPLAY_FRAME_WIDTH, "h": DISPLAY_FRAME_HEIGHT},
            silkscreen,
        ),
    )

    normalized_silkscreen = re.sub(r"\s+", "", silkscreen)
    normalized_silkscreen_upper = normalized_silkscreen.upper()
    if normalized_silkscreen_upper[:3] != vendor or normalized_silkscreen_upper[-4:] != model:
        state.set_time_info(download=None, detect=detect_cost, upload=None)
        raise RuntimeError(
            f"检测失败：丝印 '{silkscreen}' 与输入不匹配，需要前三码 '{vendor}' 和后四码 '{model}' 一致。"
        )

    login_cost, upload_cost, upload_response = upload_product(
        {
            "cpu_model": cpu_model,
            "cpu_silkscreen": normalized_silkscreen,
            "work_order_number": work_order_number,
            "serial_number": sn,
        }
    )
    state.set_time_info(download=login_cost, detect=detect_cost, upload=upload_cost)
    state.set_result(build_pass_result_text(sn, silkscreen))
    state.mark_result_status("pass", None)
    detection_payload = {
        "sn": sn,
        "ocr_text": silkscreen,
        "timestamp": timestamp,
        "cpu_model": cpu_model,
        "work_order_number": work_order_number,
        "server_response": upload_response,
        "time_info": {
            "download": login_cost,
            "detect": detect_cost,
            "upload": upload_cost,
        },
    }
    state.set_last_detection(detection_payload)
    state.append_log("丝印服务返回:\n" + json.dumps(upload_response, ensure_ascii=False))
    return detection_payload


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/log")
def log_page():
    return render_template("log.html", entries=state.upload_log_snapshot())


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
    if ok:
        camera.clear_uploaded_frame()
    state.set_devices(devices, device if ok else None)
    return jsonify({"ok": ok, "selected_device": state.snapshot()["selected_device"]})


@app.route("/api/local-image", methods=["POST"])
def api_local_image():
    image_file = request.files.get("image")
    if image_file is None or not image_file.filename:
        return jsonify({"ok": False, "error": "请选择本地图片。"}), 400
    try:
        frame = decode_uploaded_image(image_file)
    except RuntimeError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    camera.set_uploaded_frame(frame, os.path.basename(image_file.filename))
    state.set_result("已加载本地图片")
    return build_state_response({"ok": True})


@app.route("/api/clear-local-image", methods=["POST"])
def api_clear_local_image():
    camera.clear_uploaded_frame(exit_mode=True)
    state.set_result("已切回摄像头")
    state.append_log("已退出本地图片模式，切回摄像头。")
    return build_state_response({"ok": True})


@app.route("/api/local-mode", methods=["POST"])
def api_local_mode():
    payload = request.get_json(force=True, silent=True) or {}
    action = (payload.get("action") or "").strip().lower()
    if action == "enter":
        camera.enter_local_mode()
        state.set_result("已进入本地图片模式")
        return build_state_response({"ok": True})
    if action == "exit":
        camera.clear_uploaded_frame(exit_mode=True)
        state.set_result("已切回服务器模式")
        state.append_log("已退出本地图片模式，切回服务器模式。")
        return build_state_response({"ok": True})
    return jsonify({"ok": False, "error": "未知本地模式动作。"}), 400


@app.route("/api/logs/clear", methods=["POST"])
def api_clear_logs():
    state.clear_logs()
    state.append_log("日志已清空。")
    return build_state_response({"ok": True})


@app.route("/api/client-result", methods=["POST"])
def api_client_result():
    payload = request.get_json(force=True, silent=True) or {}
    result_text = (payload.get("result_text") or "").strip()
    status = (payload.get("status") or "idle").strip().lower()
    error_text = (payload.get("error") or "").strip()

    if status not in {"idle", "processing", "pass", "fail"}:
        return jsonify({"ok": False, "error": "未知状态。"}), 400
    if not result_text:
        return jsonify({"ok": False, "error": "结果文本不能为空。"}), 400

    state.set_result(result_text)
    state.mark_result_status(status, error_text or None)
    return build_state_response({"ok": True})


@app.route("/api/detect", methods=["POST"])
def api_detect():
    payload = request.get_json(force=True, silent=True) or {}
    sn = (payload.get("sn") or "").strip()
    vendor = normalize_manual_fragment(payload.get("vendor") or "")
    model = normalize_manual_fragment(payload.get("model") or "")
    work_order_number = (payload.get("work_order_number") or "").strip()
    cpu_model = DEFAULT_CPU_MODEL

    if not work_order_number:
        return jsonify({"ok": False, "error": "工单号不能为空。"}), 400
    if not sn:
        return jsonify({"ok": False, "error": "SN不能为空。"}), 400
    if not vendor:
        return jsonify({"ok": False, "error": "前三码不能为空。"}), 400
    if not model:
        return jsonify({"ok": False, "error": "后四码不能为空。"}), 400
    if len(vendor) != 3:
        return jsonify({"ok": False, "error": "前三码必须为 3 位。"}), 400
    if len(model) != 4:
        return jsonify({"ok": False, "error": "后四码必须为 4 位。"}), 400

    snapshot = state.snapshot()
    if snapshot["detecting"]:
        return jsonify({"ok": False, "error": "检测进行中，请稍后。"}), 409

    state.set_detecting(True)
    state.set_result("处理中...")
    state.set_time_info(download=None, detect=None, upload=None)
    state.clear_overlays()
    response_payload = None
    response_status = 200
    try:
        detection = perform_detection(
            sn,
            vendor,
            model,
            work_order_number,
            cpu_model,
        )
        response_payload = {"ok": True, "sn": detection["sn"]}
    except RuntimeError as exc:
        snapshot = state.snapshot()
        if snapshot.get("last_result_status") == "pass" and "已经 PASS" in str(exc):
            state.set_result(build_pass_result_text(snapshot.get('last_seen_sn') or ""))
            state.append_log(str(exc))
            response_payload = {"ok": False, "error": str(exc), "sn": snapshot.get("last_seen_sn") or ""}
            response_status = 409
        elif snapshot.get("last_result_status") == "processing":
            state.mark_result_status("fail", str(exc))
            state.set_result(f"FAIL\n{exc}")
            state.append_log(str(exc))
            response_payload = {"ok": False, "error": str(exc), "sn": snapshot.get("last_seen_sn") or sn}
            response_status = 500
        else:
            state.set_result(f"FAIL\n{exc}")
            state.append_log(str(exc))
            response_payload = {"ok": False, "error": str(exc), "sn": snapshot.get("last_seen_sn") or sn}
            response_status = 500
    except Exception as exc:
        if state.snapshot().get("last_result_status") == "processing":
            state.mark_result_status("fail", str(exc))
        state.set_result(f"FAIL\n{exc}")
        state.append_log(f"未处理异常: {exc}")
        response_payload = {"ok": False, "error": str(exc), "sn": state.snapshot().get("last_seen_sn") or sn}
        response_status = 500
    finally:
        state.set_detecting(False)
    return build_state_response(response_payload, response_status)

@app.route("/api/qr-detect", methods=["POST"])
def api_qr_detect():
    snapshot = state.snapshot()
    if snapshot["detecting"]:
        return jsonify({"ok": False, "error": "检测进行中，请稍后。"}), 409

    state.set_detecting(True)
    state.set_result("二维码识别中...")
    state.set_time_info(download=None, detect=None, upload=None)
    state.clear_overlays()
    started_at = time.perf_counter()
    response_payload = None
    response_status = 200
    try:
        decoded = perform_qr_detection()
        state.set_time_info(download=None, detect=time.perf_counter() - started_at, upload=None)
        response_payload = {"ok": True, "decoded": decoded}
    except RuntimeError as exc:
        state.clear_overlays()
        state.set_result(f"FAIL\n{exc}")
        state.append_log(str(exc))
        state.set_time_info(download=None, detect=time.perf_counter() - started_at, upload=None)
        response_payload = {"ok": False, "error": str(exc)}
        response_status = 500
    finally:
        state.set_detecting(False)
    return build_state_response(response_payload, response_status)

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
