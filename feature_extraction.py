"""URL validation and deterministic feature extraction for training and prediction."""

from __future__ import annotations

import ipaddress
import re
from typing import Any
from urllib.parse import parse_qsl, urlsplit


FEATURE_NAMES = (
    "url_length",
    "dot_count",
    "hyphen_count",
    "special_character_count",
    "has_at_symbol",
    "is_ip_address",
    "uses_https",
    "subdomain_count",
    "query_parameter_count",
    "suspicious_keyword_count",
    "uses_shortener",
    "digit_count",
    "letter_count",
    "domain_length",
    "domain_has_digits",
    "path_depth",
    "has_nonstandard_port",
    "fragment_present",
)

SUSPICIOUS_KEYWORDS = (
    "account",
    "banking",
    "confirm",
    "login",
    "password",
    "payment",
    "secure",
    "signin",
    "update",
    "verify",
    "wallet",
)

SHORTENER_DOMAINS = frozenset(
    {
        "bit.ly",
        "goo.gl",
        "is.gd",
        "ow.ly",
        "rebrand.ly",
        "shorturl.at",
        "tiny.cc",
        "tinyurl.com",
        "t.co",
        "trib.al",
        "urlz.fr",
    }
)

_DOMAIN_LABEL = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$", re.IGNORECASE)


def normalize_url(value: str) -> str:
    """Validate an HTTP(S) URL without ever requesting it from the network."""
    if not isinstance(value, str):
        raise ValueError("URL must be text.")

    value = value.strip()
    if not value:
        raise ValueError("Enter a URL to scan.")
    if len(value) > 2048:
        raise ValueError("URL must be 2,048 characters or fewer.")
    if any(ord(character) < 32 or character.isspace() for character in value):
        raise ValueError("URL cannot contain spaces or control characters.")

    candidate = value if "://" in value else f"https://{value}"
    try:
        parts = urlsplit(candidate)
        hostname = parts.hostname
        parts.port
    except ValueError as error:
        raise ValueError("Enter a valid HTTP or HTTPS URL.") from error

    if parts.scheme.lower() not in {"http", "https"} or not parts.netloc or not hostname:
        raise ValueError("Only valid HTTP and HTTPS URLs can be scanned.")

    try:
        ascii_hostname = hostname.encode("idna").decode("ascii").lower()
    except UnicodeError as error:
        raise ValueError("The URL contains an invalid domain name.") from error

    try:
        ipaddress.ip_address(ascii_hostname)
    except ValueError:
        labels = ascii_hostname.rstrip(".").split(".")
        if not ascii_hostname or len(ascii_hostname) > 253 or any(
            not _DOMAIN_LABEL.fullmatch(label) for label in labels
        ):
            raise ValueError("Enter a valid domain name or IP address.")

    return candidate


def extract_url_features(value: str) -> dict[str, int | float]:
    """Return features in FEATURE_NAMES order for a validated URL."""
    normalized = normalize_url(value)
    parts = urlsplit(normalized)
    hostname = (parts.hostname or "").lower().rstrip(".")
    try:
        ipaddress.ip_address(hostname)
        is_ip_address = 1
    except ValueError:
        is_ip_address = 0

    labels = hostname.split(".") if hostname else []
    domain_text = f"{hostname}{parts.path}{parts.query}".lower()
    special_characters = re.findall(r"[^A-Za-z0-9:/?&=#._%-]", normalized)
    port = parts.port
    default_port = 443 if parts.scheme.lower() == "https" else 80

    features: dict[str, int | float] = {
        "url_length": len(normalized),
        "dot_count": normalized.count("."),
        "hyphen_count": normalized.count("-"),
        "special_character_count": len(special_characters),
        "has_at_symbol": int("@" in normalized),
        "is_ip_address": is_ip_address,
        "uses_https": int(parts.scheme.lower() == "https"),
        "subdomain_count": max(len(labels) - 2, 0) if not is_ip_address else 0,
        "query_parameter_count": len(parse_qsl(parts.query, keep_blank_values=True)),
        "suspicious_keyword_count": sum(
            keyword in domain_text for keyword in SUSPICIOUS_KEYWORDS
        ),
        "uses_shortener": int(hostname in SHORTENER_DOMAINS),
        "digit_count": sum(character.isdigit() for character in normalized),
        "letter_count": sum(character.isalpha() for character in normalized),
        "domain_length": len(hostname),
        "domain_has_digits": int(any(character.isdigit() for character in hostname)),
        "path_depth": len([part for part in parts.path.split("/") if part]),
        "has_nonstandard_port": int(port is not None and port != default_port),
        "fragment_present": int(bool(parts.fragment)),
    }
    return {name: features[name] for name in FEATURE_NAMES}


def get_risk_indicators(value: str) -> list[tuple[str, float]]:
    """Return deterministic explanations and their bounded risk contributions."""
    normalized = normalize_url(value)
    features = extract_url_features(normalized)
    parts = urlsplit(normalized)
    hostname = (parts.hostname or "").lower().rstrip(".")
    domain_text = f"{hostname}{parts.path}{parts.query}".lower()
    indicators: list[tuple[str, float]] = []

    if features["is_ip_address"]:
        indicators.append(("The host is an IP address instead of a domain name.", 0.30))
    if features["has_at_symbol"]:
        indicators.append(("The URL contains an @ symbol that can disguise its destination.", 0.25))
    if not features["uses_https"]:
        indicators.append(("The URL does not use HTTPS.", 0.15))
    if features["url_length"] > 120:
        indicators.append(("The URL is unusually long (> 120 characters).", 0.15))
    elif features["url_length"] > 75:
        indicators.append(("The URL is unusually long (> 75 characters).", 0.08))
    if features["subdomain_count"] >= 3:
        indicators.append(("The URL contains excessive subdomains (3 or more).", 0.15))
    elif features["subdomain_count"] == 2:
        indicators.append(("The URL contains multiple subdomains.", 0.08))
    if features["suspicious_keyword_count"]:
        matched_kws = [kw for kw in SUSPICIOUS_KEYWORDS if kw in domain_text]
        kw_str = ", ".join(matched_kws) if matched_kws else "credential lure"
        indicators.append((f"The URL contains suspicious keywords ({kw_str}).", min(0.30, 0.15 * features["suspicious_keyword_count"])))
    if features["uses_shortener"]:
        indicators.append(("The domain is a known URL-shortening service.", 0.20))
    if features["special_character_count"] >= 5:
        indicators.append(("The URL contains too many special characters (5 or more).", 0.12))
    elif features["special_character_count"] >= 3:
        indicators.append(("The URL contains several unusual special characters.", 0.08))
    if features["domain_has_digits"]:
        indicators.append(("The domain contains numeric characters.", 0.08))
    if features["path_depth"] >= 4:
        indicators.append(("The URL has a suspiciously deep path structure.", 0.08))
    if features["has_nonstandard_port"]:
        indicators.append(("The URL uses a non-standard web port.", 0.12))

    return indicators


def get_explainable_features(value: str, features: dict[str, int | float] | None = None) -> dict[str, Any]:
    """Generate structured Explainable AI details from actual extracted features."""
    normalized = normalize_url(value)
    if features is None:
        features = extract_url_features(normalized)
    parts = urlsplit(normalized)
    hostname = (parts.hostname or "").lower().rstrip(".")
    domain_text = f"{hostname}{parts.path}{parts.query}".lower()

    suspicious: list[dict[str, Any]] = []
    safe: list[dict[str, Any]] = []
    detected_reasons: list[str] = []
    f_risk = 0.0

    if features["is_ip_address"]:
        suspicious.append({
            "name": "IP Address Host",
            "reason": "IP address used instead of domain",
            "detail": f"Host '{hostname}' is an IP address instead of a domain name.",
            "severity": "high",
            "weight": 0.30,
        })
        detected_reasons.append("IP address used instead of domain")
        f_risk += 0.30
    else:
        safe.append({
            "name": "Domain Format",
            "detail": "Uses a standard registered domain name.",
        })

    if features["has_at_symbol"]:
        suspicious.append({
            "name": "At Symbol Present",
            "reason": "URL contains an @ symbol",
            "detail": "Contains '@' character which can disguise destination credentials.",
            "severity": "high",
            "weight": 0.25,
        })
        detected_reasons.append("Contains @ symbol disguise")
        f_risk += 0.25

    if not features["uses_https"]:
        suspicious.append({
            "name": "Missing HTTPS",
            "reason": "Missing HTTPS",
            "detail": "The URL uses unencrypted HTTP protocol.",
            "severity": "medium",
            "weight": 0.15,
        })
        detected_reasons.append("Missing HTTPS encryption")
        f_risk += 0.15
    else:
        safe.append({
            "name": "Encrypted Transport",
            "detail": "Uses secure HTTPS connection.",
        })

    if features["url_length"] > 120:
        suspicious.append({
            "name": "Unusually Long URL",
            "reason": "URL is unusually long",
            "detail": f"Total length is {int(features['url_length'])} characters (exceeds 120).",
            "severity": "medium",
            "weight": 0.15,
        })
        detected_reasons.append("URL is unusually long")
        f_risk += 0.15
    elif features["url_length"] > 75:
        suspicious.append({
            "name": "Elevated Length",
            "reason": "URL length is elevated",
            "detail": f"Total length is {int(features['url_length'])} characters.",
            "severity": "low",
            "weight": 0.08,
        })
        detected_reasons.append("Elevated URL length")
        f_risk += 0.08
    else:
        safe.append({
            "name": "URL Length",
            "detail": f"Standard compact length ({int(features['url_length'])} characters).",
        })

    if features["subdomain_count"] >= 3:
        suspicious.append({
            "name": "Excessive Subdomains",
            "reason": "Excessive subdomains",
            "detail": f"Contains {int(features['subdomain_count'])} subdomain levels.",
            "severity": "high",
            "weight": 0.15,
        })
        detected_reasons.append("Excessive subdomains")
        f_risk += 0.15
    elif features["subdomain_count"] == 2:
        suspicious.append({
            "name": "Multiple Subdomains",
            "reason": "Multiple subdomains",
            "detail": "Contains 2 subdomain levels.",
            "severity": "medium",
            "weight": 0.08,
        })
        detected_reasons.append("Multiple subdomains")
        f_risk += 0.08
    else:
        safe.append({
            "name": "Subdomain Hierarchy",
            "detail": "Clean, standard domain hierarchy.",
        })

    if features["suspicious_keyword_count"]:
        matched_kws = [kw for kw in SUSPICIOUS_KEYWORDS if kw in domain_text]
        kw_str = ", ".join(matched_kws) if matched_kws else "credential lure"
        weight = min(0.30, 0.15 * features["suspicious_keyword_count"])
        suspicious.append({
            "name": "Suspicious Keywords",
            "reason": "Suspicious keywords detected",
            "detail": f"Detected credential lure keywords: {kw_str}.",
            "severity": "high" if weight >= 0.20 else "medium",
            "weight": weight,
        })
        detected_reasons.append(f"Suspicious keywords detected ({kw_str})")
        f_risk += weight
    else:
        safe.append({
            "name": "Keyword Safety",
            "detail": "No suspicious phishing or banking lures found.",
        })

    if features["uses_shortener"]:
        suspicious.append({
            "name": "URL Shortener",
            "reason": "URL shortening pattern",
            "detail": f"Domain '{hostname}' is a known URL shortening service.",
            "severity": "high",
            "weight": 0.20,
        })
        detected_reasons.append("URL shortening pattern")
        f_risk += 0.20

    if features["special_character_count"] >= 5:
        suspicious.append({
            "name": "Special Characters",
            "reason": "Too many special characters",
            "detail": f"Contains {int(features['special_character_count'])} special characters.",
            "severity": "medium",
            "weight": 0.12,
        })
        detected_reasons.append("Too many special characters")
        f_risk += 0.12
    elif features["special_character_count"] >= 3:
        suspicious.append({
            "name": "Special Characters",
            "reason": "Special characters present",
            "detail": f"Contains {int(features['special_character_count'])} special characters.",
            "severity": "low",
            "weight": 0.08,
        })
        detected_reasons.append("Unusual special characters")
        f_risk += 0.08

    if features["domain_has_digits"]:
        suspicious.append({
            "name": "Domain Digits",
            "reason": "Unusual domain structure",
            "detail": "Domain contains numeric characters.",
            "severity": "low",
            "weight": 0.08,
        })
        detected_reasons.append("Unusual domain structure (contains digits)")
        f_risk += 0.08

    if features["path_depth"] >= 4:
        suspicious.append({
            "name": "Deep Path Structure",
            "reason": "Suspicious path structure",
            "detail": f"Path is nested {int(features['path_depth'])} levels deep.",
            "severity": "medium",
            "weight": 0.08,
        })
        detected_reasons.append("Suspicious deep path structure")
        f_risk += 0.08

    if features["has_nonstandard_port"]:
        suspicious.append({
            "name": "Non-Standard Port",
            "reason": "Non-standard port",
            "detail": f"Connects to explicit non-standard port ({parts.port}).",
            "severity": "medium",
            "weight": 0.12,
        })
        detected_reasons.append("Non-standard web port")
        f_risk += 0.12

    bounded_feature_risk = min(1.0, f_risk)

    return {
        "detected_reasons": detected_reasons,
        "suspicious_features": suspicious,
        "safe_features": safe,
        "feature_risk_score": round(bounded_feature_risk * 100, 1),
        "feature_risk_weight": bounded_feature_risk,
    }
