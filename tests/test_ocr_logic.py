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

    def test_map_roi_to_frame_scales_local_image_coordinates(self):
        actual_roi, display_roi = ocr_main.map_roi_to_frame(
            {"enabled": True, "x": 320, "y": 240, "w": 160, "h": 120},
            (960, 1280, 3),
        )
        self.assertEqual(display_roi["x"], 320)
        self.assertEqual(actual_roi["x"], 640)
        self.assertEqual(actual_roi["y"], 480)
        self.assertEqual(actual_roi["w"], 320)
        self.assertEqual(actual_roi["h"], 240)

    def test_duplicate_pass_restores_last_pass_time_info(self):
        ocr_main.state.mark_seen_sn("SN001", "pass")
        ocr_main.state.set_last_detection(
            {
                "sn": "SN001",
                "time_info": {"download": 0.1, "detect": 0.2, "upload": 0.3},
            }
        )
        with self.assertRaises(ocr_main.DuplicateSNError):
            ocr_main.ensure_not_duplicate_sn("SN001", force_upload=False)
        snapshot = ocr_main.state.snapshot()
        self.assertEqual(snapshot["time_info"]["download"], 0.1)
        self.assertTrue(snapshot["result_text"].startswith("PASS"))


if __name__ == "__main__":
    unittest.main()
