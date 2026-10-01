"""Flask application for local, offline URL classification."""

from __future__ import annotations

import base64
import logging
import os
import sqlite3
from pathlib import Path
from typing import Any

import cv2
from flask import Flask, jsonify, render_template, request
import numpy as np

import database
from feature_extraction import normalize_url
from prediction_service import ModelUnavailableError, load_model, predict_url

PROJECT_ROOT = Path(__file__).resolve().parent


def decode_qr_image_bytes(image_bytes: bytes) -> str | None:
    """Safely decode QR code text from raw image bytes without network activity."""
    if not image_bytes:
        return None
    try:
        np_arr = np.frombuffer(image_bytes, np.uint8)
        img = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)
        if img is None:
            return None
        detector = cv2.QRCodeDetector()
        decoded_text, _, _ = detector.detectAndDecode(img)
        if decoded_text and decoded_text.strip():
            return decoded_text.strip()

        # Try with grayscale and Otsu thresholding as fallback
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        decoded_text, _, _ = detector.detectAndDecode(gray)
        if decoded_text and decoded_text.strip():
            return decoded_text.strip()

        _, thresh = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)
        decoded_text, _, _ = detector.detectAndDecode(thresh)
        if decoded_text and decoded_text.strip():
            return decoded_text.strip()
    except Exception:
        pass
    return None


def create_app(test_config: dict[str, Any] | None = None) -> Flask:
    app = Flask(__name__)
    app.config.from_mapping(
        DATABASE=str(database.DEFAULT_DATABASE_PATH),
        MODEL_PATH=str(PROJECT_ROOT / "model" / "random_forest.pkl"),
        MAX_CONTENT_LENGTH=16 * 1024 * 1024,
    )
    if test_config:
        app.config.update(test_config)
    database.initialize_database(app.config["DATABASE"])

    @app.get("/")
    def home() -> str:
        return render_template("index.html")

    @app.post("/api/predict")
    def predict() -> tuple[Any, int] | Any:
        if not request.is_json:
            return jsonify(error="Send the URL as a JSON object with a 'url' field."), 400
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return jsonify(error="Request body must be a JSON object."), 400
        source = str(payload.get("source", "Manual")).strip() or "Manual"
        try:
            url = normalize_url(payload.get("url"))
            result = predict_url(url, app.config["MODEL_PATH"])
            result["source"] = source
        except ValueError as error:
            return jsonify(error=str(error)), 400
        except ModelUnavailableError as error:
            return jsonify(error=str(error), code="model_unavailable"), 503
        except Exception:
            app.logger.exception("URL prediction failed")
            return jsonify(error="Prediction failed. Check the model and server logs."), 500

        try:
            database.add_scan(
                url,
                result["prediction"],
                result["risk_score"],
                result["risk_level"],
                source=source,
                reasons=result.get("reasons"),
                database_path=app.config["DATABASE"],
            )
        except Exception:
            app.logger.exception("Could not save the completed scan")
            return jsonify(error="Prediction succeeded, but its history could not be saved."), 500
        return jsonify(result), 200

    @app.post("/api/qr/decode")
    def qr_decode() -> tuple[Any, int] | Any:
        image_bytes: bytes | None = None
        if "file" in request.files:
            file = request.files["file"]
            if file.filename:
                image_bytes = file.read()
        elif request.is_json:
            payload = request.get_json(silent=True) or {}
            image_b64 = payload.get("image", "")
            if "," in image_b64:
                image_b64 = image_b64.split(",", 1)[1]
            if image_b64:
                try:
                    image_bytes = base64.b64decode(image_b64)
                except Exception:
                    return jsonify(error="Invalid base64 image data."), 400

        if not image_bytes:
            return jsonify(error="No QR code image provided. Upload an image file."), 400

        decoded_text = decode_qr_image_bytes(image_bytes)
        if not decoded_text:
            return jsonify(error="No QR code could be detected in the provided image. Please ensure the QR code is clearly visible and well-lit."), 400

        return jsonify(success=True, url=decoded_text), 200


    @app.get("/api/history")
    def history() -> Any:
        return jsonify(scans=database.get_history(app.config["DATABASE"]))

    @app.delete("/api/history")
    def delete_history() -> Any:
        deleted = database.clear_history(app.config["DATABASE"])
        return jsonify(deleted=deleted, message="Scan history cleared.")

    @app.get("/api/dashboard")
    def dashboard() -> Any:
        return jsonify(database.get_dashboard_stats(app.config["DATABASE"]))

    @app.get("/api/status")
    def status() -> Any:
        try:
            load_model(app.config["MODEL_PATH"])
        except ModelUnavailableError as error:
            return jsonify(model_ready=False, message=str(error))
        return jsonify(model_ready=True, message="Trained model ready")

    @app.errorhandler(413)
    def request_too_large(_error: Exception) -> tuple[Any, int]:
        return jsonify(error="Request body is too large."), 413

    @app.errorhandler(sqlite3.Error)
    def database_error(error: sqlite3.Error) -> tuple[Any, int]:
        app.logger.exception("Database operation failed", exc_info=error)
        return jsonify(error="Database operation failed."), 500

    return app


app = create_app()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    app.run(host=os.environ.get("FLASK_HOST", "127.0.0.1"), port=int(os.environ.get("PORT", "5000")), debug=False)
