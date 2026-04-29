"""Anti-pattern validator for composed messages.

Validates composed messages against known anti-patterns that reduce scores,
and provides auto-fix capabilities for minor violations.
"""

from __future__ import annotations

import re
from dataclasses import replace
from typing import Any

from src.models import ComposedMessage


# ---------------------------------------------------------------------------
# Patterns
# ---------------------------------------------------------------------------

# CTA indicator phrases (case-insensitive)
_CTA_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\breply\b", re.IGNORECASE),
    re.compile(r"\bwant me to\b", re.IGNORECASE),
    re.compile(r"\bshould I\b", re.IGNORECASE),
]

# URL patterns
_URL_PATTERN = re.compile(r"https?://|www\.", re.IGNORECASE)

# Long preamble phrases (case-insensitive)
_PREAMBLE_PHRASES: list[str] = [
    "I hope you're doing well",
    "I hope you are doing well",
    "Good morning",
    "Good afternoon",
    "Good evening",
    "I'm reaching out today",
    "I am reaching out today",
]

# Compiled preamble patterns — match at sentence start (possibly after whitespace)
_PREAMBLE_PATTERNS: list[re.Pattern[str]] = [
    re.compile(
        r"(?:^|\.\s*)" + re.escape(phrase) + r"[^.]*\.?\s*",
        re.IGNORECASE,
    )
    for phrase in _PREAMBLE_PHRASES
]

# Generic discount patterns like "flat X% off" or "X% discount"
_GENERIC_DISCOUNT_PATTERN = re.compile(
    r"\bflat\s+\d+%\s+off\b|\b\d+%\s+discount\b", re.IGNORECASE
)


def _split_sentences(text: str) -> list[str]:
    """Split text into sentences on period, exclamation, question mark, or newline."""
    parts = re.split(r"(?<=[.!?])\s+|\n+", text.strip())
    return [s.strip() for s in parts if s.strip()]


def _has_service_at_price(category: dict[str, Any]) -> bool:
    """Check if the category has any service_at_price offers in its offer_catalog."""
    offer_catalog = category.get("offer_catalog", [])
    return any(
        offer.get("type") == "service_at_price" for offer in offer_catalog
    )


class AntiPatternValidator:
    """Validates composed messages against known anti-patterns."""

    def validate(
        self,
        message: ComposedMessage,
        category: dict[str, Any],
        sent_bodies: set[str],
    ) -> list[str]:
        """Return a list of violation descriptions. Empty list means valid.

        Checks:
        1. Taboo vocabulary from category.voice.vocab_taboo
        2. Multiple CTAs detected in body
        3. CTA not at end of message
        4. URLs in body (http://, https://, www.)
        5. Long preambles
        6. Body matches a previously sent body in sent_bodies
        7. Generic discount format when service-at-price is available
        """
        violations: list[str] = []
        body = message.body

        # Check 1 — taboo vocabulary (case-insensitive substring match)
        voice = category.get("voice", {})
        taboo_list: list[str] = voice.get("vocab_taboo", [])
        body_lower = body.lower()
        for taboo in taboo_list:
            if taboo.lower() in body_lower:
                violations.append(f"taboo_vocabulary:{taboo}")

        # Check 2 — multiple CTAs
        cta_count = sum(
            1 for pat in _CTA_PATTERNS if pat.search(body)
        )
        if cta_count > 1:
            violations.append("multiple_ctas")

        # Check 3 — CTA not at end of message
        sentences = _split_sentences(body)
        if sentences:
            last_sentence = sentences[-1]
            has_cta_at_end = any(
                pat.search(last_sentence) for pat in _CTA_PATTERNS
            )
            has_cta_anywhere = any(pat.search(body) for pat in _CTA_PATTERNS)
            if has_cta_anywhere and not has_cta_at_end:
                violations.append("cta_not_at_end")

        # Check 4 — URLs in body
        if _URL_PATTERN.search(body):
            violations.append("url_in_body")

        # Check 5 — long preambles
        for phrase in _PREAMBLE_PHRASES:
            if phrase.lower() in body_lower:
                violations.append(f"long_preamble:{phrase}")
                break  # one preamble violation is enough

        # Check 6 — duplicate body
        if body in sent_bodies:
            violations.append("duplicate_body")

        # Check 7 — generic discount when service-at-price is available
        if _GENERIC_DISCOUNT_PATTERN.search(body) and _has_service_at_price(category):
            violations.append("generic_discount")

        return violations

    def fix(
        self,
        message: ComposedMessage,
        violations: list[str],
    ) -> ComposedMessage:
        """Attempt to auto-fix minor violations. Returns a new ComposedMessage.

        Fixable violations:
        - url_in_body: remove URLs from body
        - long_preamble: trim preamble sentences

        Unfixable violations are left as-is (caller decides whether to skip).
        """
        body = message.body

        for violation in violations:
            if violation == "url_in_body":
                # Remove full URLs (http://..., https://..., www....)
                body = re.sub(
                    r"https?://\S+|www\.\S+", "", body
                )
                # Clean up extra whitespace left behind
                body = re.sub(r"  +", " ", body).strip()

            elif violation.startswith("long_preamble:"):
                phrase = violation.split(":", 1)[1]
                # Remove the preamble sentence containing the phrase
                for pat in _PREAMBLE_PATTERNS:
                    body = pat.sub("", body)
                body = body.strip()
                # If the body now starts with a period or comma, strip it
                body = body.lstrip(".,;: ")

        if body != message.body:
            return replace(message, body=body)
        return message
