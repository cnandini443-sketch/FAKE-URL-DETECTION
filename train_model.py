"""Train and evaluate the phishing classifier from dataset/urls.csv."""

from __future__ import annotations

import argparse
from pathlib import Path

import joblib
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import (
    accuracy_score,
    confusion_matrix,
    f1_score,
    precision_score,
    recall_score,
)
from sklearn.model_selection import train_test_split

from feature_extraction import FEATURE_NAMES, extract_url_features, normalize_url

PROJECT_ROOT = Path(__file__).resolve().parent
LABELS = {
    "0": "Legitimate",
    "benign": "Legitimate",
    "good": "Legitimate",
    "legitimate": "Legitimate",
    "legit": "Legitimate",
    "safe": "Legitimate",
    "1": "Phishing",
    "bad": "Phishing",
    "malicious": "Phishing",
    "malware": "Phishing",
    "phish": "Phishing",
    "phishing": "Phishing",
}


def load_dataset(csv_path: Path) -> tuple[pd.DataFrame, int, int]:
    if not csv_path.is_file():
        raise FileNotFoundError(
            f"Dataset not found: {csv_path}\n"
            "Place a real CSV at dataset/urls.csv with required columns 'url' and 'label'. "
            "Labels must identify legitimate/benign URLs and phishing/malicious URLs."
        )

    data = pd.read_csv(csv_path)
    missing = {"url", "label"} - set(data.columns.str.strip().str.lower())
    if missing:
        raise ValueError(
            f"Dataset is missing required column(s): {', '.join(sorted(missing))}. "
            "Required columns are 'url' and 'label'."
        )
    data.columns = data.columns.str.strip().str.lower()
    data = data[["url", "label"]].dropna().copy()
    data["url"] = data["url"].astype(str).str.strip()
    raw_labels = data["label"].astype(str).str.strip().str.lower()
    normalized_labels = raw_labels.map(LABELS)
    if normalized_labels.isna().any():
        unknown = sorted(raw_labels.loc[normalized_labels.isna()].unique())
        raise ValueError(f"Unrecognized dataset label(s): {unknown}")
    data["label"] = normalized_labels

    original_count = len(data)
    valid_rows: list[int] = []
    for index, url in data["url"].items():
        try:
            data.at[index, "url"] = normalize_url(url)
            valid_rows.append(index)
        except ValueError:
            continue
    data = data.loc[valid_rows].reset_index(drop=True)
    conflicting_labels = data.groupby("url")["label"].nunique()
    if (conflicting_labels > 1).any():
        raise ValueError("The same URL has conflicting labels in the dataset.")
    data = data.drop_duplicates(subset="url").reset_index(drop=True)
    invalid_count = original_count - len(data)

    if data.empty or set(data["label"]) != {"Legitimate", "Phishing"}:
        raise ValueError("After cleaning, the dataset must contain both legitimate and phishing URLs.")
    if data["label"].value_counts().min() < 5:
        raise ValueError("Each label needs at least five valid, unique URLs for a stratified 80/20 split.")

    return data, invalid_count, original_count


def train(dataset_path: Path, model_path: Path) -> dict[str, object]:
    data, removed_count, original_count = load_dataset(dataset_path)
    feature_frame = pd.DataFrame(
        [extract_url_features(url) for url in data["url"]], columns=FEATURE_NAMES
    )
    x_train, x_test, y_train, y_test = train_test_split(
        feature_frame,
        data["label"],
        test_size=0.2,
        random_state=42,
        stratify=data["label"],
    )

    classifier = RandomForestClassifier(
        n_estimators=300,
        class_weight="balanced_subsample",
        random_state=42,
        n_jobs=-1,
    )
    classifier.fit(x_train, y_train)
    predictions = classifier.predict(x_test)

    results: dict[str, object] = {
        "accuracy": accuracy_score(y_test, predictions),
        "precision": precision_score(y_test, predictions, pos_label="Phishing", zero_division=0),
        "recall": recall_score(y_test, predictions, pos_label="Phishing", zero_division=0),
        "f1_score": f1_score(y_test, predictions, pos_label="Phishing", zero_division=0),
        "confusion_matrix": confusion_matrix(
            y_test, predictions, labels=["Legitimate", "Phishing"]
        ).tolist(),
    }
    model_path.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(
        {
            "model": classifier,
            "feature_names": list(FEATURE_NAMES),
            "labels": ["Legitimate", "Phishing"],
        },
        model_path,
    )

    print(f"Dataset rows loaded: {original_count}")
    print(f"Invalid or duplicate rows removed: {removed_count}")
    print(f"Rows used: {len(data)} (train: {len(x_train)}, test: {len(x_test)})")
    print(f"Accuracy: {results['accuracy']:.4f}")
    print(f"Precision (Phishing): {results['precision']:.4f}")
    print(f"Recall (Phishing): {results['recall']:.4f}")
    print(f"F1-score (Phishing): {results['f1_score']:.4f}")
    print("Confusion matrix (rows=true, columns=predicted; Legitimate, Phishing):")
    for row in results["confusion_matrix"]:
        print(row)
    print(f"Trained model saved to: {model_path}")
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=PROJECT_ROOT / "dataset" / "urls.csv")
    parser.add_argument("--model-output", type=Path, default=PROJECT_ROOT / "model" / "random_forest.pkl")
    arguments = parser.parse_args()
    try:
        train(arguments.dataset, arguments.model_output)
    except (FileNotFoundError, ValueError, pd.errors.ParserError) as error:
        parser.exit(status=1, message=f"Training error: {error}\n")


if __name__ == "__main__":
    main()
