import importlib.util
import pathlib
import unittest


MODULE_PATH = pathlib.Path("/opt/ocr/main.py")
if not MODULE_PATH.exists():
    MODULE_PATH = pathlib.Path("/Users/apple/Desktop/codex/.remote_work/main.py")
SPEC = importlib.util.spec_from_file_location("ocr_main", MODULE_PATH)
ocr_main = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ocr_main)


class OcrLogicTests(unittest.TestCase):
    def setUp(self):
        ocr_main.state = ocr_main.AppState()

    def test_normalize_manual_fragment_removes_spaces_and_uppercases(self):
        self.assertEqual(ocr_main.normalize_manual_fragment(" na a "), "NAA")
        self.assertEqual(ocr_main.normalize_manual_fragment(" 25 18 "), "2518")

    def test_build_overlay_payload_uses_given_rect(self):
        payload = ocr_main.build_overlay_payload("CPU", {"x": 1, "y": 2, "w": 3, "h": 4}, "ABC1234")
        self.assertEqual(payload["label"], "CPU")
        self.assertEqual(payload["rect"]["w"], 3)
        self.assertEqual(payload["text"], "ABC1234")

    def test_duplicate_pass_restores_last_pass_time_info(self):
        ocr_main.state.mark_seen_sn("SN001", "pass")
        ocr_main.state.set_last_detection(
            {
                "sn": "SN001",
                "time_info": {"download": 0.1, "detect": 0.2, "upload": 0.3},
            }
        )
        with self.assertRaises(RuntimeError):
            ocr_main.ensure_not_duplicate_sn("SN001")
        snapshot = ocr_main.state.snapshot()
        self.assertEqual(snapshot["time_info"]["download"], 0.1)
        self.assertTrue(snapshot["result_text"].startswith("PASS"))

    def test_build_pass_result_text_contains_wait_message(self):
        text = ocr_main.build_pass_result_text("SN001", "ABC1234")
        self.assertIn("PASS", text)
        self.assertIn("等待下一个设备", text)
        self.assertIn("SN: SN001", text)
        self.assertIn("CPU: ABC1234", text)

    def test_get_detection_frame_requires_local_image_in_local_mode(self):
        ocr_main.state.set_operation_mode("local")
        ocr_main.camera = type(
            "CameraStub",
            (),
            {
                "has_uploaded_frame": staticmethod(lambda: False),
                "get_frame_copy": staticmethod(lambda: None),
            },
        )()
        with self.assertRaises(RuntimeError) as ctx:
            ocr_main.get_detection_frame()
        self.assertIn("本地图片模式", str(ctx.exception))

    def test_client_result_api_updates_server_side_fail_state(self):
        client = ocr_main.app.test_client()
        response = client.post(
            "/api/client-result",
            json={
                "result_text": "FAIL\nSN不能为空。",
                "status": "fail",
                "error": "SN不能为空。",
            },
        )
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["state"]["result_text"], "FAIL\nSN不能为空。")
        self.assertEqual(payload["state"]["last_result_status"], "fail")
        self.assertEqual(payload["state"]["last_error"], "SN不能为空。")


if __name__ == "__main__":
    unittest.main()
