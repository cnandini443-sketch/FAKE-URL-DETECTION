# AI-Powered Fake URL Detection and Phishing Website Identification System

A locally run web application for inspecting URL structure with a Random Forest classifier trained from a real, user-supplied CSV dataset. The scanner never opens, fetches, or executes submitted URLs. It stores successful predictions in a local SQLite database.

> **Model requirement:** This repository deliberately contains no generated labels, sample predictions, or claimed accuracy. Add a real dataset to `dataset/urls.csv` and train the model before scans can return a classification. Until then, the site, database, dashboard, and history work, while `/api/predict` reports that the model is unavailable.

## Features

- Shared, ordered URL feature extraction for both training and prediction.
- Random Forest training with held-out accuracy, precision, recall, F1-score, and confusion matrix.
- Risk score combining model probability with documented, bounded structural indicators.
- Flask JSON API and responsive HTML/CSS/JavaScript interface.
- SQLite-backed scan history, aggregate dashboard statistics, and clear-history action.
- Basic HTTP(S) URL validation; no server-side website requests.
- Dynamic English and Telugu spoken scan explanations; Telugu uses local browser-side Piper inference when no native Telugu voice is installed.
- QR image/camera scanning is not included. It would require an additional image-decoding library and is not necessary for the core workflow.

## Technologies

Python, Flask, Pandas, NumPy, scikit-learn, joblib, SQLite, HTML5, CSS3, and browser JavaScript/WebAssembly. The Python dependencies are listed in `requirements.txt`; the SQLite engine is included with Python.

## Folder structure

```text
Fake-URL-Detection/
├── app.py
├── database.py
├── feature_extraction.py
├── prediction_service.py
├── train_model.py
├── requirements.txt
├── README.md
├── dataset/
│   └── urls.csv
├── model/
│   └── random_forest.pkl       # created by training; not committed/generated
├── database/
│   └── scans.db                # created automatically when the app starts
├── templates/
│   └── index.html
├── static/
│   ├── css/style.css
│   ├── js/script.js
│   └── vendor/                  # local Piper/ONNX WebAssembly runtime
└── tests/
    └── test_app.py
```

## Dataset format

Replace the header-only `dataset/urls.csv` template with a real, labelled dataset containing these required column names:

```csv
url,label
https://www.example.org,legitimate
http://example-login.invalid/account/verify,phishing
```

The two example rows above illustrate the format only; they are **not** bundled as training data and must not be used to claim model quality. Provide real observations in both classes. Accepted legitimate labels: `legitimate`, `legit`, `benign`, `safe`, `good`, or `0`. Accepted phishing labels: `phishing`, `phish`, `malicious`, `malware`, `bad`, or `1` (case-insensitive). Each class needs at least five valid, unique URLs for the stratified 80/20 split. Use a larger, representative, appropriately licensed dataset for meaningful evaluation; avoid duplicates and test-set leakage.

Training drops blank rows and duplicate URLs, normalizes valid URLs, and rejects unrecognized labels. Features are numerical and generated in the `FEATURE_NAMES` order declared in `feature_extraction.py`; that exact order is saved with the model and checked when it is loaded.

## Installation

From the project root in PowerShell, create the virtual environment under LocalAppData. This workspace is in OneDrive, where Windows Application Control blocked scikit-learn's compiled extension from a workspace-local environment during verification.

```powershell
$venv = Join-Path $env:LOCALAPPDATA 'FakeUrlDetection\.venv'
py -3.14 -m venv $venv
& "$venv\Scripts\python.exe" -m pip install --upgrade pip
& "$venv\Scripts\python.exe" -m pip install -r requirements.txt
```

Use that interpreter for the remaining commands:

```powershell
& "$venv\Scripts\python.exe" train_model.py
& "$venv\Scripts\python.exe" app.py
```

## Train the model

Place the real dataset at `dataset/urls.csv`, activate the virtual environment, and run:

```powershell
& "$venv\Scripts\python.exe" train_model.py
```

Training metrics are computed from the actual held-out split and printed to the terminal. The trained artifact is saved to `model/random_forest.pkl`. To use alternate paths:

```powershell
& "$venv\Scripts\python.exe" train_model.py --dataset path\to\urls.csv --model-output path\to\random_forest.pkl
```

The app reads the default model path. Do not distribute a model artifact trained on a dataset whose provenance or license is unknown.

## Run the application

```powershell
& "$venv\Scripts\python.exe" app.py
```

Open <http://127.0.0.1:5000/>. The app creates `database/scans.db` and its `scan_history` table automatically. Stop the server with Ctrl+C.

## Use

1. Train the model with your labelled CSV.
2. Start Flask and open the local URL above.
3. Enter an HTTP(S) URL (the scheme may be omitted and defaults to HTTPS), then select **Analyze link**.
4. Review the model classification, risk score, risk level, and URL-feature explanations.
5. Use **Dashboard** or **Scan history** to inspect saved scans. Clear history requires confirmation.

A successful scan is saved only after a model prediction succeeds. An absent/incompatible model or invalid input does not create a history entry.

## Telugu speech

When the browser does not expose a Telugu SpeechSynthesis voice, the Telugu button uses the bundled Piper WebAssembly runtime and the `te_IN-maya-medium` voice in a dedicated worker. The first Telugu playback downloads the approximately 63 MB voice model from Hugging Face and caches it in browser CacheStorage; the Telugu narration text is synthesized locally and is not sent to a TTS API. Preparation and synthesis have deadlines, and a stalled worker is terminated with a clear error instead of blocking the page. After a successful download, audio generation works offline unless browser storage is cleared or evicted. English continues to use the browser's native SpeechSynthesis voice.

The model is pinned to a fixed Piper voice-repository revision. Its model card identifies IIT Madras IndicTTS as its source dataset and links that dataset's license; review those terms before redistributing the voice model or using it commercially. Vendored runtime sources and license references are listed in `static/vendor/README.md`.

## Risk-score calculation

Let $p$ be the Random Forest probability for the `Phishing` class. Each detected structural indicator contributes a documented weight (for example, IP host `0.30`, `@` symbol `0.25`, missing HTTPS `0.10`, and shortener domain `0.20`). Let $i$ be the sum of detected weights, capped at `0.35`. The final score is:

$$
\text{risk} = \operatorname{round}\left(100\left[1 - (1-p)(1-i)\right]\right)
$$

This combines model probability with bounded indicator evidence, is clamped to 0–100, and is not random. Risk levels are `0–30` Low, `31–70` Medium, and `71–100` High. Prediction labels are derived from model probability and risk: `Phishing` at probability >= 0.70; otherwise `Suspicious` at probability >= 0.35 or risk > 30; otherwise `Legitimate`. These thresholds are configurable in `prediction_service.py` and are not a substitute for dataset-specific calibration.

## API endpoints

- `GET /` — serve the web application.
- `GET /api/status` — report whether a compatible trained model is available.
- `POST /api/predict` — accept `{"url":"https://example.org"}` and return prediction, score, risk level, probability, and explanations; `400` for invalid input, `503` when no model is available.
- `GET /api/history` — return up to the latest 100 saved scans.
- `DELETE /api/history` — clear all scan records.
- `GET /api/dashboard` — return counts by prediction and the five latest scans.

## Tests

Run the focused standard-library test suite:

```powershell
& "$venv\Scripts\python.exe" -m unittest discover -s tests -v
```

The tests cover URL validation and features, Flask routes and error responses, model-unavailable behavior, dataset-missing feedback, and SQLite history/statistics/clearing. End-to-end classification and meaningful model metrics require a real training dataset.

## Limitations

URL structure alone cannot determine whether a website is safe, and phishing techniques change over time. Model quality depends on dataset size, label quality, representativeness, source, and train/test separation. Risk indicators and probability thresholds are simple and not calibrated. No page content, domain reputation, DNS, TLS certificate, or live threat-intelligence lookup is performed. The app does not visit URLs, and QR scanning is not implemented. Run it on a trusted local machine and protect/handle datasets and scan history appropriately.

## Future enhancements

- Add a provenance-tracked, licensed benchmark dataset and calibration/temporal evaluation.
- Add optional QR image decoding with a maintained decoder and safe upload limits.
- Evaluate feature importance and per-class metrics across multiple splits.
- Add model-version metadata and database export/retention controls.
