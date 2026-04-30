"""Composer Engine for Vera Merchant Bot.

LLM-powered 4-context message composer with trigger-kind dispatch,
anti-pattern validation, and auto-fix capabilities. This is the core
competitive differentiator — the system prompt is optimized for the
5-dimension scoring rubric.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from src.context_store import ContextStore
from src.llm_client import LLMClient
from src.models import ComposedMessage, MessageClassification
from src.prompts import (
    BASE_SYSTEM_PROMPT,
    CUSTOMER_SCOPED_KINDS,
    PROMPT_REGISTRY,
    REPLY_INTENT_COMMITTED_INSTRUCTIONS,
    REPLY_NORMAL_INSTRUCTIONS,
    REPLY_SYSTEM_PROMPT,
    get_trigger_instruction,
)
from src.validators import AntiPatternValidator

logger = logging.getLogger(__name__)


class Composer:
    """LLM-powered 4-context message composer with trigger-kind dispatch."""

    def __init__(
        self,
        llm_client: LLMClient,
        context_store: ContextStore,
    ) -> None:
        self._llm = llm_client
        self._store = context_store
        self._prompt_registry: dict[str, str] = dict(PROMPT_REGISTRY)
        self._validator = AntiPatternValidator()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def compose(
        self,
        category: dict[str, Any],
        merchant: dict[str, Any],
        trigger: dict[str, Any],
        customer: dict[str, Any] | None = None,
        conversation_history: list[Any] | None = None,
        sent_bodies: set[str] | None = None,
    ) -> ComposedMessage:
        """Compose a message using the 4-context framework.

        Steps:
        1. Select prompt variant by trigger kind
        2. Build structured context block
        3. Call LLM via complete_with_fallback
        4. Parse JSON response into ComposedMessage
        5. Run AntiPatternValidator, auto-fix minor violations
        6. Return ComposedMessage
        """
        if sent_bodies is None:
            sent_bodies = set()

        trigger_kind = trigger.get("kind", "")

        # 1. Select prompt
        system_prompt = self._select_prompt(trigger_kind, category)

        # 2. Build context block
        context_block = self._build_context_block(
            category, merchant, trigger, customer
        )

        # 3. Build user prompt
        user_prompt = f"Compose a message for this context:\n\n{context_block}"
        if conversation_history:
            history_text = self._format_conversation_history(conversation_history)
            user_prompt += f"\n\nCONVERSATION HISTORY:\n{history_text}"
        if sent_bodies:
            user_prompt += (
                f"\n\nPREVIOUSLY SENT (do NOT repeat):\n"
                + "\n".join(f"- {b}" for b in sent_bodies)
            )

        # 4. Call LLM
        fallback_json = self._build_fallback_response(trigger, merchant, customer)
        raw = await self._llm.complete_with_fallback(
            system_prompt, user_prompt, fallback_json
        )

        # 5. Parse response
        message = self._parse_llm_response(raw, trigger, merchant, customer)

        # 6. Validate and fix
        message = self._validate_and_fix(message, category, sent_bodies, merchant=merchant)

        return message

    async def compose_reply(
        self,
        conversation_history: list[Any],
        classification: MessageClassification,
        sent_bodies: set[str],
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Compose a reply for an ongoing conversation.

        For INTENT_COMMITTED: instruct LLM to produce action-mode output.
        For NORMAL: instruct LLM to continue conversation naturally.

        Optional kwargs:
        - merchant: dict — merchant context payload for richer replies
        - category: dict — category context payload for voice matching
        """
        # Format conversation history
        history_text = self._format_conversation_history(conversation_history)

        # Select reply instructions based on classification
        if classification == MessageClassification.INTENT_COMMITTED:
            reply_instructions = REPLY_INTENT_COMMITTED_INSTRUCTIONS
        else:
            reply_instructions = REPLY_NORMAL_INSTRUCTIONS

        # Format sent bodies
        sent_bodies_text = (
            "\n".join(f"- {b}" for b in sent_bodies) if sent_bodies else "(none)"
        )

        # Build system prompt
        system_prompt = REPLY_SYSTEM_PROMPT.format(
            conversation_history=history_text,
            classification=classification.value,
            reply_instructions=reply_instructions,
            sent_bodies=sent_bodies_text,
        )

        # Enrich system prompt with merchant/category context if available
        merchant = kwargs.get("merchant")
        category = kwargs.get("category")
        context_parts: list[str] = []
        if merchant is not None:
            context_parts.append(self._build_merchant_block(merchant))
        if category is not None:
            context_parts.append(self._build_category_block(category))
        if context_parts:
            system_prompt += "\n\nADDITIONAL CONTEXT:\n" + "\n\n".join(context_parts)

        user_prompt = "Compose the next reply in this conversation."

        # Fallback response
        if classification == MessageClassification.INTENT_COMMITTED:
            fallback = json.dumps({
                "action": "send",
                "body": "Great, let me get started on that right away.",
                "cta": "open_ended",
                "rationale": "Intent committed — switching to action mode.",
            })
        else:
            fallback = json.dumps({
                "action": "send",
                "body": "Thanks for your message! Let me look into that for you.",
                "cta": "open_ended",
                "rationale": "Normal reply — continuing conversation.",
            })

        raw = await self._llm.complete_with_fallback(
            system_prompt, user_prompt, fallback
        )

        # Parse reply response
        return self._parse_reply_response(raw)

    # ------------------------------------------------------------------
    # Prompt selection
    # ------------------------------------------------------------------

    def _select_prompt(
        self,
        trigger_kind: str,
        category: dict[str, Any],
    ) -> str:
        """Return the full system prompt for the given trigger kind.

        Assembles the base prompt with voice rules from the category
        and trigger-specific instructions from the registry.
        """
        trigger_instruction = get_trigger_instruction(trigger_kind)

        voice = category.get("voice", {})
        tone = voice.get("tone", "professional")
        vocab_allowed = ", ".join(voice.get("vocab_allowed", []))
        vocab_taboo = ", ".join(voice.get("vocab_taboo", []))

        system_prompt = BASE_SYSTEM_PROMPT.format(
            tone=tone,
            vocab_allowed=vocab_allowed or "(none specified)",
            vocab_taboo=vocab_taboo or "(none specified)",
            trigger_instructions=trigger_instruction,
            trigger_kind=trigger_kind or "generic",
        )

        return system_prompt

    # ------------------------------------------------------------------
    # Context block building
    # ------------------------------------------------------------------

    def _build_context_block(
        self,
        category: dict[str, Any],
        merchant: dict[str, Any],
        trigger: dict[str, Any],
        customer: dict[str, Any] | None = None,
    ) -> str:
        """Serialize contexts into a structured text block for the LLM."""
        parts: list[str] = []

        # === CATEGORY ===
        parts.append(self._build_category_block(category))

        # === MERCHANT ===
        parts.append(self._build_merchant_block(merchant))

        # === TRIGGER ===
        parts.append(self._build_trigger_block(trigger))

        # === CUSTOMER (if present) ===
        if customer is not None:
            parts.append(self._build_customer_block(customer))

        return "\n\n".join(parts)

    @staticmethod
    def _build_category_block(category: dict[str, Any]) -> str:
        """Build the category section of the context block."""
        lines = ["=== CATEGORY ==="]
        lines.append(f"Slug: {category.get('slug', 'unknown')}")

        voice = category.get("voice", {})
        lines.append(f"Voice tone: {voice.get('tone', 'professional')}")
        taboo = voice.get("vocab_taboo", [])
        lines.append(f"Taboo words: {', '.join(taboo) if taboo else '(none)'}")

        peer_stats = category.get("peer_stats", {})
        lines.append(
            f"Peer stats: avg_rating={peer_stats.get('avg_rating', 'N/A')}, "
            f"avg_ctr={peer_stats.get('avg_ctr', 'N/A')}"
        )

        digest = category.get("digest", [])
        if digest:
            digest_items = []
            for item in digest[:5]:
                title = item.get("title", "")
                source = item.get("source", "")
                digest_items.append(f"  - {title} ({source})")
            lines.append("Digest items:\n" + "\n".join(digest_items))

        seasonal = category.get("seasonal_beats", [])
        if seasonal:
            beats = [
                f"  - {b.get('month_range', '')}: {b.get('note', '')}"
                for b in seasonal
            ]
            lines.append("Seasonal beats:\n" + "\n".join(beats))

        trends = category.get("trend_signals", [])
        if trends:
            trend_items = [
                f"  - {t.get('query', '')}: {t.get('delta_yoy', 0):+.0%} YoY"
                for t in trends
            ]
            lines.append("Trend signals:\n" + "\n".join(trend_items))

        return "\n".join(lines)

    @staticmethod
    def _build_merchant_block(merchant: dict[str, Any]) -> str:
        """Build the merchant section of the context block."""
        lines = ["=== MERCHANT ==="]

        identity = merchant.get("identity", {})
        lines.append(f"Name: {identity.get('name', 'Unknown')}")
        lines.append(f"Owner: {identity.get('owner_first_name', 'Unknown')}")
        lines.append(
            f"City/Locality: {identity.get('city', '')}, "
            f"{identity.get('locality', '')}"
        )
        languages = identity.get("languages", [])
        lines.append(f"Languages: {', '.join(languages) if languages else 'en'}")

        sub = merchant.get("subscription", {})
        lines.append(
            f"Subscription: {sub.get('status', 'unknown')}, "
            f"{sub.get('plan', 'N/A')}, "
            f"{sub.get('days_remaining', 'N/A')}d remaining"
        )

        perf = merchant.get("performance", {})
        lines.append(
            f"Performance (30d): views={perf.get('views', 0)}, "
            f"calls={perf.get('calls', 0)}, "
            f"ctr={perf.get('ctr', 0)}"
        )
        delta = perf.get("delta_7d", {})
        if delta:
            views_pct = delta.get("views_pct", 0)
            calls_pct = delta.get("calls_pct", 0)
            lines.append(
                f"7d deltas: views {views_pct:+.0%}, calls {calls_pct:+.0%}"
            )

        offers = merchant.get("offers", [])
        if offers:
            offer_items = [
                f"  - {o.get('title', '')} [{o.get('status', '')}]"
                for o in offers
            ]
            lines.append("Active offers:\n" + "\n".join(offer_items))
        else:
            lines.append("Active offers: (none)")

        signals = merchant.get("signals", [])
        if signals:
            lines.append(f"Signals: {', '.join(signals)}")

        cust_agg = merchant.get("customer_aggregate", {})
        if cust_agg:
            lines.append(f"Customer aggregate: {json.dumps(cust_agg)}")

        review_themes = merchant.get("review_themes", [])
        if review_themes:
            theme_items = [
                f"  - {t.get('theme', '')}: {t.get('sentiment', '')} "
                f"({t.get('occurrences_30d', 0)}x) "
                f"\"{t.get('common_quote', '')}\""
                for t in review_themes
            ]
            lines.append("Review themes:\n" + "\n".join(theme_items))

        conv_history = merchant.get("conversation_history", [])
        if conv_history:
            recent = conv_history[-3:]
            history_items = [
                f"  - [{h.get('from', '')}] {h.get('body', '')}"
                for h in recent
            ]
            lines.append("Conversation history:\n" + "\n".join(history_items))

        return "\n".join(lines)

    @staticmethod
    def _build_trigger_block(trigger: dict[str, Any]) -> str:
        """Build the trigger section of the context block."""
        lines = ["=== TRIGGER ==="]
        lines.append(f"Kind: {trigger.get('kind', 'unknown')}")
        lines.append(f"Scope: {trigger.get('scope', 'merchant')}")
        lines.append(f"Source: {trigger.get('source', 'internal')}")
        lines.append(f"Urgency: {trigger.get('urgency', 1)}")

        payload = {
            k: v
            for k, v in trigger.items()
            if k not in ("kind", "scope", "source", "urgency", "suppression_key",
                         "expires_at", "merchant_id", "customer_id", "id")
        }
        if payload:
            lines.append(f"Payload: {json.dumps(payload, default=str)}")

        lines.append(
            f"Suppression key: {trigger.get('suppression_key', 'none')}"
        )

        return "\n".join(lines)

    @staticmethod
    def _build_customer_block(customer: dict[str, Any]) -> str:
        """Build the customer section of the context block."""
        lines = ["=== CUSTOMER ==="]

        identity = customer.get("identity", {})
        lines.append(f"Name: {identity.get('name', 'Customer')}")
        lines.append(
            f"Language pref: {identity.get('language_pref', 'english')}"
        )
        lines.append(f"Age band: {identity.get('age_band', 'unknown')}")

        lines.append(f"State: {customer.get('state', 'unknown')}")

        rel = customer.get("relationship", {})
        lines.append(
            f"Relationship: {rel.get('visits_total', 0)} visits, "
            f"last visit {rel.get('last_visit', 'N/A')}"
        )
        services = rel.get("services_received", [])
        if services:
            lines.append(f"Services: {', '.join(services[:10])}")

        prefs = customer.get("preferences", {})
        if prefs:
            lines.append(f"Preferences: {json.dumps(prefs)}")

        consent = customer.get("consent", {})
        lines.append(f"Consent scope: {consent.get('scope', [])}")

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # LLM response parsing
    # ------------------------------------------------------------------

    def _parse_llm_response(
        self,
        raw: str,
        trigger: dict[str, Any],
        merchant: dict[str, Any],
        customer: dict[str, Any] | None,
    ) -> ComposedMessage:
        """Extract JSON from LLM output and build a ComposedMessage.

        Handles:
        - Clean JSON responses
        - JSON wrapped in markdown code blocks
        - Malformed responses (falls back to defaults)
        """
        parsed = self._extract_json(raw)

        if parsed is None:
            logger.warning("Failed to parse LLM response, using fallback")
            return self._fallback_composed_message(trigger, merchant, customer)

        trigger_kind = trigger.get("kind", "generic")
        suppression_key = trigger.get("suppression_key", "")

        # Determine send_as
        send_as = parsed.get("send_as", "vera")
        if trigger_kind in CUSTOMER_SCOPED_KINDS and customer is not None:
            send_as = "merchant_on_behalf"

        return ComposedMessage(
            body=parsed.get("body", ""),
            cta=parsed.get("cta", "open_ended"),
            send_as=send_as,
            suppression_key=parsed.get("suppression_key", suppression_key),
            rationale=parsed.get("rationale", ""),
            template_name=parsed.get(
                "template_name", f"vera_{trigger_kind}_v1"
            ),
            template_params=parsed.get("template_params", []),
        )

    @staticmethod
    def _extract_json(raw: str) -> dict[str, Any] | None:
        """Try to extract a JSON object from raw LLM output."""
        # Try direct parse
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            pass

        # Try extracting from markdown code block
        code_block = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", raw, re.DOTALL)
        if code_block:
            try:
                return json.loads(code_block.group(1))
            except (json.JSONDecodeError, TypeError):
                pass

        # Try finding JSON object in the text
        brace_match = re.search(r"\{.*\}", raw, re.DOTALL)
        if brace_match:
            try:
                return json.loads(brace_match.group(0))
            except (json.JSONDecodeError, TypeError):
                pass

        return None

    def _parse_reply_response(self, raw: str) -> dict[str, Any]:
        """Parse LLM reply response into an action dict."""
        parsed = self._extract_json(raw)

        if parsed is None:
            return {
                "action": "send",
                "body": "Thanks for your message! Let me look into that for you.",
                "cta": "open_ended",
                "rationale": "Normal reply — continuing conversation.",
            }

        return {
            "action": parsed.get("action", "send"),
            "body": parsed.get("body", ""),
            "cta": parsed.get("cta", "open_ended"),
            "rationale": parsed.get("rationale", ""),
        }

    # ------------------------------------------------------------------
    # Validation and auto-fix
    # ------------------------------------------------------------------

    def _validate_and_fix(
        self,
        message: ComposedMessage,
        category: dict[str, Any],
        sent_bodies: set[str],
        merchant: dict[str, Any] | None = None,
    ) -> ComposedMessage:
        """Run anti-pattern validation and auto-fix minor violations."""
        violations = self._validator.validate(
            message, category, sent_bodies, merchant=merchant
        )

        if violations:
            logger.info("Violations found: %s — attempting auto-fix", violations)
            # For price hallucinations, we can't auto-fix — log a warning
            price_violations = [v for v in violations if v.startswith("price_hallucination")]
            if price_violations:
                logger.warning("PRICE HALLUCINATION detected: %s", price_violations)
            message = self._validator.fix(message, violations)

        return message

    # ------------------------------------------------------------------
    # Fallback responses
    # ------------------------------------------------------------------

    def _build_fallback_response(
        self,
        trigger: dict[str, Any],
        merchant: dict[str, Any],
        customer: dict[str, Any] | None,
    ) -> str:
        """Build a fallback JSON string for when LLM fails."""
        msg = self._fallback_composed_message(trigger, merchant, customer)
        return json.dumps({
            "body": msg.body,
            "cta": msg.cta,
            "send_as": msg.send_as,
            "suppression_key": msg.suppression_key,
            "rationale": msg.rationale,
            "template_name": msg.template_name,
            "template_params": msg.template_params,
        })

    @staticmethod
    def _fallback_composed_message(
        trigger: dict[str, Any],
        merchant: dict[str, Any],
        customer: dict[str, Any] | None,
    ) -> ComposedMessage:
        """Create a minimal valid ComposedMessage as fallback."""
        trigger_kind = trigger.get("kind", "generic")
        owner = merchant.get("identity", {}).get("owner_first_name", "")
        suppression_key = trigger.get("suppression_key", "")

        send_as = "vera"
        if trigger_kind in CUSTOMER_SCOPED_KINDS and customer is not None:
            send_as = "merchant_on_behalf"

        if send_as == "merchant_on_behalf" and customer:
            cust_name = customer.get("identity", {}).get("name", "")
            body = (
                f"Hi {cust_name}, "
                f"{merchant.get('identity', {}).get('name', '')} here. "
                f"We have an update for you. Reply YES to learn more."
            )
        else:
            body = (
                f"{owner}, quick update from Vera regarding your business. "
                f"Reply YES if you'd like details."
            )

        return ComposedMessage(
            body=body,
            cta="binary_yes_no",
            send_as=send_as,
            suppression_key=suppression_key,
            rationale=f"Fallback response for {trigger_kind} trigger.",
            template_name=f"vera_{trigger_kind}_v1",
            template_params=[],
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _format_conversation_history(turns: list[Any]) -> str:
        """Format conversation turns into readable text."""
        lines = []
        for turn in turns:
            if hasattr(turn, "role"):
                role = turn.role
                message = turn.message
            elif isinstance(turn, dict):
                role = turn.get("role", turn.get("from", "unknown"))
                message = turn.get("message", turn.get("body", ""))
            else:
                continue
            lines.append(f"[{role.upper()}] {message}")
        return "\n".join(lines) if lines else "(no history)"
