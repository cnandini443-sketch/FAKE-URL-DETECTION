import io
import tempfile
import unittest
from pathlib import Path

import database
from app import create_app
from feature_extraction import (
    FEATURE_NAMES,
    extract_url_features,
    get_explainable_features,
    get_risk_indicators,
    normalize_url,
)
from prediction_service import DEFAULT_MODEL_PATH, predict_url
from train_model import load_dataset


class FeatureExtractionTests(unittest.TestCase):
    def test_features_are_ordered_and_capture_url_signals(self) -> None:
        url = "http://192.168.1.2/login?next=account"
        features = extract_url_features(url)
        self.assertEqual(tuple(features), FEATURE_NAMES)
        self.assertEqual(features["is_ip_address"], 1)
        self.assertEqual(features["uses_https"], 0)
        self.assertEqual(features["query_parameter_count"], 1)
        self.assertGreater(features["suspicious_keyword_count"], 0)
        self.assertTrue(get_risk_indicators(url))

    def test_domain_query_port_fragment_and_shortener_features(self) -> None:
        features = extract_url_features(
            "http://verify-login.mail.example.com:8080/account/path?q=1&next=2#section"
        )
        self.assertEqual(features["subdomain_count"], 2)
        self.assertEqual(features["query_parameter_count"], 2)
        self.assertEqual(features["has_nonstandard_port"], 1)
        self.assertEqual(features["fragment_present"], 1)
        self.assertEqual(extract_url_features("https://bit.ly/path")["uses_shortener"], 1)

    def test_rejects_unsafe_or_malformed_input(self) -> None:
        for value in ("", None, 123, "javascript:alert(1)", "https://", "https://bad host/path"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                normalize_url(value)

    def test_missing_dataset_explains_required_columns(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            missing = Path(temporary_directory) / "urls.csv"
            with self.assertRaisesRegex(FileNotFoundError, "url.*label"):
                load_dataset(missing)


class ApiAndDatabaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        root = Path(self.temporary_directory.name)
        self.database_path = root / "test-scans.db"
        self.app = create_app(
            {
                "TESTING": True,
                "DATABASE": str(self.database_path),
                "MODEL_PATH": str(root / "missing-random-forest.pkl"),
            }
        )
        self.client = self.app.test_client()

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_home_page_and_api_routes(self) -> None:
        self.assertEqual(self.client.get("/").status_code, 200)
        self.assertEqual(self.client.get("/api/status").json["model_ready"], False)
        self.assertEqual(self.client.get("/api/history").json["scans"], [])
        self.assertEqual(self.client.get("/api/dashboard").json["total_scans"], 0)

    def test_invalid_input_and_missing_model_never_save_a_result(self) -> None:
        self.assertEqual(self.client.post("/api/predict", json={"url": "javascript:alert(1)"}).status_code, 400)
        self.assertEqual(self.client.post("/api/predict", data="not json").status_code, 400)
        response = self.client.post("/api/predict", json={"url": "https://example.com"})
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json["code"], "model_unavailable")
        self.assertEqual(self.client.get("/api/history").json["scans"], [])

    def test_database_failure_returns_json_error(self) -> None:
        self.app.config["DATABASE"] = str(Path(self.temporary_directory.name))
        response = self.client.get("/api/dashboard")
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.json["error"], "Database operation failed.")

    def test_database_statistics_history_and_clear(self) -> None:
        database.add_scan("https://example.test", "Legitimate", 4.2, "Low", self.database_path)
        database.add_scan("http://192.0.2.3/login", "Phishing", 91.5, "High", self.database_path)
        stats = self.client.get("/api/dashboard").json
        self.assertEqual(stats["total_scans"], 2)
        self.assertEqual(stats["legitimate_count"], 1)
        self.assertEqual(stats["phishing_count"], 1)
        self.assertEqual(len(self.client.get("/api/history").json["scans"]), 2)
        cleared = self.client.delete("/api/history")
        self.assertEqual(cleared.status_code, 200)
        self.assertEqual(cleared.json["deleted"], 2)
        self.assertEqual(self.client.get("/api/dashboard").json["total_scans"], 0)


class ComprehensiveSystemTests(unittest.TestCase):
    """Verifies all 9 test scenarios required by the specification."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.has_model = DEFAULT_MODEL_PATH.is_file()

    def setUp(self) -> None:
        if not self.has_model:
            self.skipTest("Trained model random_forest.pkl required for end-to-end tests.")
        self.temporary_directory = tempfile.TemporaryDirectory()
        root = Path(self.temporary_directory.name)
        self.database_path = root / "test-full-scans.db"
        self.app = create_app(
            {
                "TESTING": True,
                "DATABASE": str(self.database_path),
                "MODEL_PATH": str(DEFAULT_MODEL_PATH),
            }
        )
        self.client = self.app.test_client()

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def _generate_qr_png_bytes(self, text: str) -> bytes:
        import qrcode
        qr = qrcode.QRCode()
        qr.add_data(text)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()

    def test_1_legitimate_url(self) -> None:
        url = "https://www.google.com"
        res = self.client.post("/api/predict", json={"url": url})
        self.assertEqual(res.status_code, 200)
        data = res.json
        self.assertEqual(data["prediction"], "Legitimate")
        self.assertEqual(data["risk_level"], "Low")
        self.assertIsInstance(data["risk_score"], (int, float))
        self.assertLessEqual(data["risk_score"], 30.0)
        self.assertEqual(data["warning"], "Low Risk — No major suspicious indicators detected.")
        self.assertIn("en", data["voice_narration"])
        self.assertIn("low risk", data["voice_narration"]["en"].lower())
        self.assertIn("confidence is", data["voice_narration"]["en"].lower())
        self.assertIn("te", data["voice_narration"])
        self.assertIn("సురక్షితమైనదిగా", data["voice_narration"]["te"])
        self.assertIn("100లో", data["voice_narration"]["te"])
        self.assertIn("శాతం", data["voice_narration"]["te"])
        self.assertNotIn("Risk Score", data["voice_narration"]["te"])
        self.assertNotIn("out of 100", data["voice_narration"]["te"])
        self.assertNotIn("URL", data["voice_narration"]["te"])
        self.assertNotIn("HTTPS", data["voice_narration"]["te"])
        self.assertNotIn("IP", data["voice_narration"]["te"])
        self.assertTrue(len(data["explanation"]["safe_features"]) > 0)

    def test_2_suspicious_url(self) -> None:
        url = "https://www.amazon.com/dp/B08N5WRWNW"
        res = self.client.post("/api/predict", json={"url": url})
        self.assertEqual(res.status_code, 200)
        data = res.json
        self.assertEqual(data["prediction"], "Suspicious")
        self.assertEqual(data["risk_level"], "Medium")
        self.assertGreater(data["risk_score"], 30.0)
        self.assertLessEqual(data["risk_score"], 70.0)
        self.assertEqual(
            data["warning"],
            "Warning — This URL has suspicious characteristics. Verify the website before providing personal information.",
        )
        self.assertIn("medium risk", data["voice_narration"]["en"].lower())
        self.assertIn("అనుమానాస్పద", data["voice_narration"]["te"])

    def test_3_high_risk_url(self) -> None:
        url = "http://192.168.1.1/login-bank-account-verify"
        res = self.client.post("/api/predict", json={"url": url})
        self.assertEqual(res.status_code, 200)
        data = res.json
        self.assertEqual(data["prediction"], "Phishing")
        self.assertEqual(data["risk_level"], "High")
        self.assertGreater(data["risk_score"], 70.0)
        self.assertEqual(
            data["warning"],
            "⚠️ HIGH RISK ALERT — This URL shows strong phishing characteristics. Do not enter passwords, OTPs, banking details, or other sensitive information.",
        )
        self.assertEqual(data["alert"]["title"], "⚠️ PHISHING ALERT")
        self.assertIn("high risk", data["voice_narration"]["en"].lower())
        self.assertIn("హెచ్చరిక", data["voice_narration"]["te"])
        self.assertTrue(len(data["explanation"]["detected_reasons"]) > 0)

    def test_4_qr_code_containing_legitimate_url(self) -> None:
        url = "https://www.google.com"
        qr_bytes = self._generate_qr_png_bytes(url)
        # Decode via QR endpoint
        decode_res = self.client.post(
            "/api/qr/decode",
            data={"file": (io.BytesIO(qr_bytes), "qr.png")},
            content_type="multipart/form-data",
        )
        self.assertEqual(decode_res.status_code, 200)
        self.assertEqual(decode_res.json["url"], url)

        # Run through same prediction pipeline with source tag
        scan_res = self.client.post("/api/predict", json={"url": decode_res.json["url"], "source": "QR Code"})
        self.assertEqual(scan_res.status_code, 200)
        scan_result = scan_res.json
        self.assertEqual(scan_result["source"], "QR Code")
        self.assertEqual(scan_result["risk_level"], "Low")
        self.assertIsInstance(scan_result["risk_score"], float)
        self.assertIn("confidence is", scan_result["voice_narration"]["en"].lower())
        self.assertIn("రిస్క్ స్కోరు", scan_result["voice_narration"]["te"])
        self.assertNotIn("out of 100", scan_result["voice_narration"]["te"])

        # Verify scan history records QR Code source
        history = self.client.get("/api/history").json["scans"]
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["source"], "QR Code")
        self.assertEqual(history[0]["risk_score"], scan_result["risk_score"])

    def test_5_qr_code_containing_suspicious_url(self) -> None:
        url = "http://192.168.1.1/login-bank-account-verify"
        qr_bytes = self._generate_qr_png_bytes(url)
        decode_res = self.client.post(
            "/api/qr/decode",
            data={"file": (io.BytesIO(qr_bytes), "qr_phish.png")},
            content_type="multipart/form-data",
        )
        self.assertEqual(decode_res.status_code, 200)
        self.assertEqual(decode_res.json["url"], url)

        scan_res = self.client.post("/api/predict", json={"url": decode_res.json["url"], "source": "QR Code"})
        self.assertEqual(scan_res.status_code, 200)
        self.assertEqual(scan_res.json["source"], "QR Code")
        self.assertEqual(scan_res.json["risk_level"], "High")

        dashboard = self.client.get("/api/dashboard").json
        self.assertGreaterEqual(dashboard["qr_scans_count"], 1)

    def test_6_empty_url(self) -> None:
        res = self.client.post("/api/predict", json={"url": ""})
        self.assertEqual(res.status_code, 400)
        self.assertIn("error", res.json)

    def test_7_invalid_url(self) -> None:
        for bad_url in ("javascript:alert('xss')", "not a url", "ftp://invalid-scheme.com"):
            with self.subTest(bad_url=bad_url):
                res = self.client.post("/api/predict", json={"url": bad_url})
                self.assertEqual(res.status_code, 400)
                self.assertIn("error", res.json)

    def test_8_http_url(self) -> None:
        url = "http://www.gnu.org"
        res = self.client.post("/api/predict", json={"url": url})
        self.assertEqual(res.status_code, 200)
        data = res.json
        # Check HTTP is flagged in extracted features and explainability
        self.assertEqual(data["features"]["uses_https"], 0)
        reasons_text = " ".join(data["reasons"])
        self.assertIn("HTTPS", reasons_text)

    def test_9_https_url(self) -> None:
        url = "https://www.wikipedia.org"
        res = self.client.post("/api/predict", json={"url": url})
        self.assertEqual(res.status_code, 200)
        data = res.json
        self.assertEqual(data["features"]["uses_https"], 1)
        self.assertEqual(data["risk_level"], "Low")


if __name__ == "__main__":
    unittest.main()

