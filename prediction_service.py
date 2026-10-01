"""Load a trained Random Forest model and produce transparent risk estimates."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import joblib
import pandas as pd

from feature_extraction import (
    FEATURE_NAMES,
    extract_url_features,
    get_explainable_features,
    get_risk_indicators,
)

PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_MODEL_PATH = PROJECT_ROOT / "model" / "random_forest.pkl"


class ModelUnavailableError(RuntimeError):
    """Raised when the real trained model is absent or incompatible."""


def load_model(model_path: str | Path = DEFAULT_MODEL_PATH) -> dict[str, Any]:
    path = Path(model_path)
    if not path.is_file():
        raise ModelUnavailableError(
            "No trained model is available. Add a real dataset at dataset/urls.csv, "
            "then run: python train_model.py"
        )
    try:
        bundle = joblib.load(path)
    except (OSError, ValueError, EOFError) as error:
        raise ModelUnavailableError("The saved model could not be loaded. Retrain it with python train_model.py.") from error

    classifier = bundle.get("model") if isinstance(bundle, dict) else None
    if (
        classifier is None
        or bundle.get("feature_names") != list(FEATURE_NAMES)
        or not hasattr(classifier, "predict_proba")
        or not hasattr(classifier, "classes_")
    ):
        raise ModelUnavailableError("The saved model is incompatible. Retrain it with python train_model.py.")
    return bundle


def _build_voice_narrations(
    risk_level: str,
    risk_score: float,
    phishing_probability: float,
    detected_reasons: list[str],
) -> dict[str, str]:
    """Build separate English and Telugu narrations from the current scan result."""
    english_level = {"High": "high risk", "Medium": "medium risk", "Low": "low risk"}[risk_level]
    english_reasons = "; ".join(detected_reasons[:3])
    english_detail = (
        f"Suspicious indicators detected in the URL include {english_reasons}."
        if english_reasons
        else "No major suspicious indicators were detected in the URL."
    )
    en_text = (
        f"This URL is classified as {english_level}. "
        f"The risk score is {risk_score:.1f} out of 100. "
        f"The model's phishing confidence is {phishing_probability * 100:.1f} percent. "
        f"{english_detail}"
    )

    te_reason_map = {
        "IP address used instead of domain": "డొమైన్‌కు బదులుగా ఐపీ చిరునామా ఉపయోగించబడింది",
        "Contains @ symbol disguise": "అట్ గుర్తుతో అసలు గమ్యాన్ని దాచే అవకాశం ఉంది",
        "Missing HTTPS encryption": "హెచ్‌టిటిపిఎస్ గుప్తీకరణ లేదు",
        "URL is unusually long": "యూఆర్ఎల్ అసాధారణంగా పొడవుగా ఉంది",
        "Elevated URL length": "యూఆర్ఎల్ సాధారణం కంటే పొడవుగా ఉంది",
        "Excessive subdomains": "అధిక ఉపడొమైన్‌లు ఉన్నాయి",
        "Multiple subdomains": "బహుళ ఉపడొమైన్‌లు ఉన్నాయి",
        "Suspicious keywords detected": "అనుమానాస్పద పదాలు గుర్తించబడ్డాయి",
        "URL shortening pattern": "యూఆర్ఎల్ కుదింపు సేవ ఉపయోగించబడింది",
        "Too many special characters": "అధిక ప్రత్యేక అక్షరాలు ఉన్నాయి",
        "Unusual special characters": "అసాధారణ ప్రత్యేక అక్షరాలు ఉన్నాయి",
        "Unusual domain structure (contains digits)": "డొమైన్‌లో అంకెలు ఉన్నాయి",
        "Suspicious deep path structure": "యూఆర్ఎల్ మార్గం అసాధారణంగా లోతుగా ఉంది",
        "Non-standard web port": "ప్రామాణికం కాని వెబ్ పోర్ట్ ఉపయోగించబడింది",
    }
    telugu_reasons: list[str] = []
    for reason in detected_reasons:
        translated = next(
            (value for pattern, value in te_reason_map.items() if reason.startswith(pattern)),
            None,
        )
        if translated and translated not in telugu_reasons:
            telugu_reasons.append(translated)

    telugu_level = {
        "High": "అధిక ప్రమాదంగా",
        "Medium": "మధ్యస్థ ప్రమాదంగా",
        "Low": "తక్కువ ప్రమాదంగా",
    }[risk_level]
    te_opening = "హెచ్చరిక. " if risk_level == "High" else "జాగ్రత్త. " if risk_level == "Medium" else ""
    te_text = (
        f"{te_opening}ఈ యూఆర్ఎల్ {telugu_level} గుర్తించబడింది. "
        f"దీని రిస్క్ స్కోరు 100లో {risk_score:.1f}. "
        f"మోడల్ అంచనా ప్రకారం ఈ యూఆర్ఎల్ మోసపూరితమయ్యే అవకాశం {phishing_probability * 100:.1f} శాతం. "
    )
    if telugu_reasons:
        te_text += f"ఈ యూఆర్ఎల్‌లో {', '.join(telugu_reasons[:3])}."
    elif risk_level == "Low":
        te_text += "ఈ యూఆర్ఎల్ సురక్షితమైనదిగా గుర్తించబడింది. ప్రధాన అనుమానాస్పద లక్షణాలు కనిపించలేదు."
    else:
        te_text += "ఈ యూఆర్ఎల్‌లో అనుమానాస్పద లక్షణాలు గుర్తించబడలేదు."

    return {"en": en_text, "te": te_text}


def predict_url(url: str, model_path: str | Path = DEFAULT_MODEL_PATH) -> dict[str, Any]:
    bundle = load_model(model_path)
    classifier = bundle["model"]
    features = extract_url_features(url)
    feature_frame = pd.DataFrame([[features[name] for name in FEATURE_NAMES]], columns=FEATURE_NAMES)
    probabilities = classifier.predict_proba(feature_frame)[0]
    classes = list(classifier.classes_)
    if "Phishing" not in classes or "Legitimate" not in classes:
        raise ModelUnavailableError("The trained model must contain both required URL classes.")

    phishing_probability = float(probabilities[classes.index("Phishing")])
    indicators = get_risk_indicators(url)
    explainable = get_explainable_features(url, features)
    feature_risk_weight = explainable["feature_risk_weight"]

    # Calculate deterministic decimal risk score from actual model probability and feature indicators
    # Combines model probability (65%) and legitimate feature-based risk indicators (35%)
    combined = 0.65 * phishing_probability + 0.35 * feature_risk_weight
    risk_score = round(max(0.0, min(100.0, combined * 100.0)), 1)

    # Risk level classification strictly following specifications:
    # 0.0 - 30.0: LOW RISK
    # 30.1 - 70.0: MEDIUM RISK
    # 70.1 - 100.0: HIGH RISK
    if risk_score <= 30.0:
        risk_level = "Low"
        prediction = "Legitimate"
        warning = "Low Risk — No major suspicious indicators detected."
        alert = {
            "type": "safe",
            "title": "✓ LOW RISK",
            "message": "No major suspicious indicators detected.",
        }
    elif risk_score <= 70.0:
        risk_level = "Medium"
        prediction = "Suspicious"
        warning = "Warning — This URL has suspicious characteristics. Verify the website before providing personal information."
        alert = {
            "type": "warning",
            "title": "⚠️ SUSPICIOUS URL",
            "message": "This URL contains suspicious characteristics. Proceed carefully.",
        }
    else:
        risk_level = "High"
        prediction = "Phishing"
        warning = "⚠️ HIGH RISK ALERT — This URL shows strong phishing characteristics. Do not enter passwords, OTPs, banking details, or other sensitive information."
        alert = {
            "type": "danger",
            "title": "⚠️ PHISHING ALERT",
            "message": "This URL has been identified as high risk. Do not enter passwords, OTPs, card details or other sensitive information.",
        }

    # Build human-readable reasons from actual model output and detected features
    reasons = [reason for reason, _ in indicators]
    if phishing_probability >= 0.70:
        reasons.insert(0, f"The trained Random Forest model assigns a high phishing probability ({phishing_probability * 100:.1f}%) to this URL pattern.")
    elif phishing_probability >= 0.35:
        reasons.insert(0, f"The trained Random Forest model identifies suspicious phishing-like URL patterns ({phishing_probability * 100:.1f}% probability).")
    else:
        reasons.insert(0, f"The trained Random Forest model assigns a low phishing probability ({phishing_probability * 100:.1f}%) to this URL pattern.")

    if not indicators:
        reasons.append("No configured structural risk indicators were detected.")

    voice_narration = _build_voice_narrations(
        risk_level,
        risk_score,
        phishing_probability,
        explainable["detected_reasons"],
    )

    return {
        "url": url,
        "prediction": prediction,
        "risk_score": risk_score,
        "risk_level": risk_level,
        "phishing_probability": round(phishing_probability, 4),
        "reasons": reasons,
        "warning": warning,
        "alert": alert,
        "explanation": {
            "model_probability": round(phishing_probability * 100, 1),
            "feature_risk_score": explainable["feature_risk_score"],
            "detected_reasons": explainable["detected_reasons"],
            "suspicious_features": explainable["suspicious_features"],
            "safe_features": explainable["safe_features"],
        },
        "voice_narration": voice_narration,
        "features": features,
    }

