# Implementation Plan: Vera Merchant Bot

## Overview

Build a stateful HTTP bot server (Python/FastAPI) implementing the magicpin AI Challenge judge harness contract. The implementation follows an incremental approach: foundational data models and context store first, then trigger evaluation and conversation management, then LLM-powered composition with anti-pattern validation, and finally submission generation and integration wiring.

## Tasks

- [x] 1. Set up project structure, dependencies, and core data models
  - [x] 1.1 Create project directory structure and install dependencies
    - Create `src/` directory with `__init__.py`
    - Create `pyproject.toml` or `requirements.txt` with: `fastapi`, `uvicorn`, `pydantic`, `httpx`, `hypothesis`, `pytest`, `pytest-asyncio`
    - Create `src/config.py` with team metadata, LLM provider config (provider, api_key, model, timeout), and timeout budgets (healthz: 2s, metadata: 2s, context: 5s, tick: 10s, reply: 10s)
    - _Requirements: 18.1, 18.2, 18.3, 18.4, 18.5_

  - [x] 1.2 Implement Pydantic request/response models (`src/models.py`)
    - Define `ContextPushRequest`, `TickRequest`, `ReplyRequest` request models
    - Define `HealthResponse`, `MetadataResponse`, `ContextAcceptedResponse`, `ContextRejectedResponse` response models
    - Define `TickAction`, `TickResponse`, `SendAction`, `WaitAction`, `EndAction` response models
    - Define internal dataclasses: `StoredContext`, `ConversationState`, `Turn`, `ComposedMessage`, `MessageClassification` enum
    - _Requirements: 1.1, 2.1, 3.1, 4.3, 5.3_

  - [ ]* 1.3 Write unit tests for data models
    - Test Pydantic model validation (valid and invalid inputs)
    - Test serialization/deserialization round-trips
    - _Requirements: 1.1, 3.1, 4.3, 5.3_

- [x] 2. Implement Context Store module
  - [x] 2.1 Implement `ContextStore` class (`src/context_store.py`)
    - Implement `__init__` with `_store: dict[tuple[str, str], StoredContext]` and `asyncio.Lock`
    - Implement `put(scope, context_id, version, payload, delivered_at) -> PutResult` with atomic version comparison (strict greater-than)
    - Implement `get(scope, context_id) -> StoredContext | None` for lock-free reads
    - Implement `get_all_by_scope(scope) -> list[StoredContext]`
    - Implement `count_by_scope() -> dict[str, int]` returning counts per scope
    - Validate scope against `{"category", "merchant", "customer", "trigger"}` — reject invalid scopes
    - _Requirements: 3.2, 3.3, 3.4, 3.5, 12.1_

  - [ ]* 2.2 Write property tests for context store version ordering
    - **Property 1: Context store version ordering**
    - **Validates: Requirements 3.2, 3.5, 12.1**

  - [ ]* 2.3 Write property tests for stale version rejection
    - **Property 2: Stale version rejection**
    - **Validates: Requirements 3.3**

  - [ ]* 2.4 Write property tests for invalid scope rejection
    - **Property 3: Invalid scope rejection**
    - **Validates: Requirements 3.4**

  - [ ]* 2.5 Write property tests for context count accuracy
    - **Property 4: Context count accuracy**
    - **Validates: Requirements 1.2**

- [x] 3. Implement Bot Server endpoints (healthz, metadata, context)
  - [x] 3.1 Create FastAPI application and router (`src/bot.py`)
    - Initialize FastAPI app with startup time tracking
    - Instantiate `ContextStore` as app-level dependency
    - Implement `GET /v1/healthz` returning `HealthResponse` with uptime and context counts
    - Implement `GET /v1/metadata` returning team metadata from config
    - Implement `POST /v1/context` with version conflict handling (200 accepted, 409 stale, 400 invalid scope)
    - _Requirements: 1.1, 1.2, 1.3, 2.1, 2.2, 3.1, 3.2, 3.3, 3.4, 3.5, 3.6_

  - [ ]* 3.2 Write unit tests for healthz, metadata, and context endpoints
    - Test healthz returns correct counts after context pushes
    - Test context push accepts higher version, rejects stale, rejects invalid scope
    - Test idempotency: re-pushing same version returns 409
    - _Requirements: 1.1, 1.2, 3.2, 3.3, 3.4_

- [x] 4. Checkpoint — Verify foundational modules
  - Ensure all tests pass, ask the user if questions arise.

- [x] 5. Implement Trigger Evaluator module
  - [x] 5.1 Implement `TriggerEvaluator` class (`src/trigger_evaluator.py`)
    - Implement `__init__` with `_fired_suppression_keys: set[str]` and `_suppressed_conversations: set[str]`
    - Implement `evaluate(available_trigger_ids, now, composer) -> list[TickAction]`
    - Filter out triggers with already-fired suppression keys
    - Filter out triggers whose `expires_at < now`
    - Filter out customer-scoped triggers where message type not in customer's `consent.scope`
    - Sort remaining triggers by `urgency` descending
    - Compose messages for top triggers (up to 20), stopping when time budget is exhausted
    - Record suppression keys after successful composition
    - Implement `record_suppression(key)`, `is_suppressed(key)`, `suppress_conversation(conversation_id)`
    - _Requirements: 4.2, 4.4, 4.5, 4.7, 17.1, 17.2, 17.3_

  - [ ]* 5.2 Write property tests for suppression key deduplication
    - **Property 5: Suppression key deduplication**
    - **Validates: Requirements 4.5, 17.1, 17.2**

  - [ ]* 5.3 Write property tests for expired trigger filtering
    - **Property 6: Expired trigger filtering**
    - **Validates: Requirements 17.3**

  - [ ]* 5.4 Write property tests for tick action count cap
    - **Property 7: Tick action count cap**
    - **Validates: Requirements 4.7**

  - [ ]* 5.5 Write property test for consent scope filtering
    - **Property 21: Consent scope filtering**
    - **Validates: Requirements 16.4**

- [x] 6. Implement Conversation Manager module
  - [x] 6.1 Implement `ConversationManager` class (`src/conversation_manager.py`)
    - Implement `__init__` with `_conversations: dict[str, ConversationState]`
    - Implement `handle_reply(conversation_id, merchant_id, customer_id, from_role, message, received_at, turn_number, composer) -> ReplyAction`
    - Append incoming message to conversation history
    - Classify message using `classify_message(message, conversation)`
    - Route to appropriate handler based on classification
    - Track `auto_reply_streak` per conversation
    - Track `sent_bodies` per conversation for anti-repetition
    - Track conversation `phase` transitions
    - Implement `get_history(conversation_id) -> list[Turn]`
    - _Requirements: 5.1, 5.2, 5.3, 13.1, 13.2, 13.4_

  - [x] 6.2 Implement message classification logic
    - Implement auto-reply detection: pattern matching against "Thank you for contacting", "Our team will respond shortly", "automated assistant", "will get back to you", exact-match consecutive detection
    - Implement intent commitment detection: "let's do it", "yes go ahead", "ok what's next", "sounds good", "I'm in", "go ahead", "yes please"
    - Implement hostile/opt-out detection: "stop messaging", "not interested", "unsubscribe", "leave me alone", profanity, "useless", "spam", "bothering me"
    - Implement off-topic detection for requests outside Vera's scope
    - Return `MessageClassification` enum value
    - _Requirements: 6.1, 7.1, 8.1, 8.3_

  - [x] 6.3 Implement auto-reply escalation logic
    - 1st auto-reply: respond with `action: "send"` containing brief acknowledgment
    - 2nd consecutive auto-reply: respond with `action: "wait"` with `wait_seconds >= 14400` (4 hours)
    - 3rd+ consecutive auto-reply: respond with `action: "end"`
    - Reset `auto_reply_streak` on any non-auto-reply message
    - _Requirements: 6.2, 6.3, 6.4_

  - [x] 6.4 Implement intent transition and hostile exit handlers
    - On `intent_committed`: set phase to `action_committed`, instruct composer to produce action-mode output
    - On `hostile_optout`: respond with `action: "end"` (optionally with brief apology), suppress conversation
    - On `off_topic`: politely decline, redirect to original trigger topic
    - _Requirements: 7.1, 7.2, 8.1, 8.2, 8.3_

  - [ ]* 6.5 Write property tests for conversation history completeness
    - **Property 8: Conversation history completeness**
    - **Validates: Requirements 5.2, 13.1**

  - [ ]* 6.6 Write property tests for reply action validity
    - **Property 9: Reply action validity**
    - **Validates: Requirements 5.3**

  - [ ]* 6.7 Write property tests for conversation phase validity
    - **Property 10: Conversation phase validity**
    - **Validates: Requirements 13.2**

  - [ ]* 6.8 Write property tests for auto-reply detection
    - **Property 11: Auto-reply pattern detection**
    - **Validates: Requirements 6.1**

  - [ ]* 6.9 Write property tests for auto-reply escalation
    - **Property 12: Auto-reply escalation to end**
    - **Validates: Requirements 6.4**

  - [ ]* 6.10 Write property tests for intent commitment detection
    - **Property 13: Intent commitment detection**
    - **Validates: Requirements 7.1**

  - [ ]* 6.11 Write property tests for opt-out detection
    - **Property 14: Opt-out detection triggers end**
    - **Validates: Requirements 8.1**

  - [ ]* 6.12 Write property tests for hostile exit suppression
    - **Property 15: Hostile exit suppresses conversation**
    - **Validates: Requirements 8.2, 13.4**

  - [ ]* 6.13 Write property test for no duplicate bodies
    - **Property 17: No duplicate bodies in conversation**
    - **Validates: Requirements 11.5**

- [x] 7. Checkpoint — Verify trigger evaluator and conversation manager
  - Ensure all tests pass, ask the user if questions arise.

- [x] 8. Implement LLM Client module
  - [x] 8.1 Implement `LLMClient` class (`src/llm_client.py`)
    - Implement `__init__` with provider, api_key, model, timeout (default 8.0s)
    - Implement `complete(system_prompt, user_prompt) -> str` with async HTTP call and timeout
    - Support providers: openai, anthropic (via httpx async client)
    - Implement `complete_with_fallback(system_prompt, user_prompt, fallback_response) -> str` for graceful degradation
    - Handle timeout errors, API errors, and rate limits gracefully
    - _Requirements: 9.1, 18.4, 18.5, 18.6_

  - [ ]* 8.2 Write unit tests for LLM client timeout and fallback behavior
    - Test that timeout returns fallback response
    - Test that API errors return fallback response
    - _Requirements: 18.6_

- [x] 9. Implement Anti-Pattern Validator module
  - [x] 9.1 Implement `AntiPatternValidator` class (`src/validators.py`)
    - Implement `validate(message, category, sent_bodies) -> list[str]` returning violation list
    - Check 1: Taboo vocabulary from `category.voice.vocab_taboo`
    - Check 2: Multiple CTAs detected in body
    - Check 3: CTA not at end of message
    - Check 4: URLs in body (`http://`, `https://`, `www.`)
    - Check 5: Long preambles ("I hope you're doing well", "Good morning")
    - Check 6: Body matches a previously sent body in `sent_bodies`
    - Check 7: Generic discount format when service-at-price is available
    - Implement `fix(message, violations) -> ComposedMessage` for auto-fixing minor violations (remove URLs, trim preambles)
    - _Requirements: 11.1, 11.2, 11.3, 11.4, 11.5, 11.6, 11.7_

  - [ ]* 9.2 Write property tests for no taboo vocabulary
    - **Property 16: No taboo vocabulary in composed messages**
    - **Validates: Requirements 9.2, 11.4**

  - [ ]* 9.3 Write property tests for no URLs in message body
    - **Property 18: No URLs in message body**
    - **Validates: Requirements 11.7**

- [x] 10. Implement Composer Engine module
  - [x] 10.1 Implement `Composer` class (`src/composer.py`)
    - Implement `__init__` with `LLMClient`, `ContextStore`, and prompt registry
    - Implement `compose(category, merchant, trigger, customer?, conversation_history?, sent_bodies?) -> ComposedMessage`
    - Implement `compose_reply(category, merchant, trigger?, customer?, conversation_history, classification, sent_bodies) -> ReplyAction`
    - Implement `_select_prompt(trigger_kind) -> PromptTemplate` for trigger-kind dispatch
    - Implement `_build_context_block(category, merchant, trigger, customer?) -> str` for structured context serialization
    - Implement `_validate_and_fix(message, contexts) -> ComposedMessage` using AntiPatternValidator
    - Parse LLM JSON output into `ComposedMessage` with error handling
    - _Requirements: 9.1, 9.2, 9.3, 9.4, 9.5, 9.6, 9.7, 9.8, 9.9, 9.10, 10.1, 12.2, 12.3_

  - [x] 10.2 Create trigger-kind-specific prompt templates (`src/prompts/`)
    - Create `src/prompts/__init__.py` with prompt registry
    - Create `prompt_research.py` — research_digest: source citation, trial size, patient segment, peer tone
    - Create `prompt_recall.py` — recall_due: specific slots, service price, customer recall window, language pref
    - Create `prompt_perf.py` — perf_dip / seasonal_perf_dip: metric delta, contextualization, concrete next step
    - Create `prompt_planning.py` — active_planning_intent: complete drafted artifact, no qualifying questions
    - Create `prompt_alert.py` — supply_alert: batch numbers, manufacturer, affected customer count
    - Create `prompt_renewal.py` — renewal_due: days remaining, plan value, performance context
    - Create `prompt_competitor.py` — competitor_opened: competitor details, differentiation strategy
    - Create `prompt_review.py` — review_theme_emerged: theme, occurrence count, action plan
    - Create `prompt_milestone.py` — milestone_reached: metric, current value, celebration + next goal
    - Create `prompt_event.py` — ipl_match_today: match details, data-informed recommendation
    - Create `prompt_winback.py` — customer_lapsed_*: days since last visit, no-shame framing
    - Create `prompt_refill.py` — chronic_refill_due: molecule list, stock-out date, senior-friendly
    - Create `prompt_trial.py` — trial_followup: trial date, next session options
    - Create `prompt_festival.py` — festival_upcoming: festival name, days until, category relevance
    - Create `prompt_reengagement.py` — dormant_with_vera: days since last message, fresh hook
    - Create `prompt_generic.py` — default fallback for unknown trigger kinds
    - Each prompt includes: system prompt with voice rules, composition rules, compulsion lever instructions, and JSON output format
    - _Requirements: 10.1, 10.2, 10.3, 10.4, 10.5, 10.6_

  - [ ]* 10.3 Write property tests for trigger-kind prompt dispatch
    - **Property 19: Trigger-kind prompt dispatch**
    - **Validates: Requirements 10.1**

  - [ ]* 10.4 Write property tests for customer-scoped trigger routing
    - **Property 20: Customer-scoped trigger routing**
    - **Validates: Requirements 16.1**

- [x] 11. Checkpoint — Verify composition pipeline
  - Ensure all tests pass, ask the user if questions arise.

- [x] 12. Wire tick and reply endpoints with full composition pipeline
  - [x] 12.1 Implement `POST /v1/tick` endpoint with full pipeline
    - Receive tick request, pass `available_triggers` to `TriggerEvaluator.evaluate()`
    - TriggerEvaluator retrieves contexts from store, calls Composer for each valid trigger
    - Implement time-budget management: track elapsed time, stop composing when < 2s remaining
    - Return composed actions (up to 20) or empty list
    - Register new conversations in ConversationManager for each action sent
    - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5, 4.6, 4.7_

  - [x] 12.2 Implement `POST /v1/reply` endpoint with full pipeline
    - Receive reply request, pass to `ConversationManager.handle_reply()`
    - ConversationManager classifies message, routes to handler
    - For `normal` and `off_topic` classifications: call Composer with conversation history
    - For `auto_reply`: apply escalation logic (send/wait/end)
    - For `intent_committed`: call Composer in action mode
    - For `hostile_optout`: return end action, suppress conversation
    - Implement time-budget management with fallback on timeout
    - _Requirements: 5.1, 5.2, 5.3, 5.4, 6.1, 6.2, 6.3, 6.4, 7.1, 7.2, 7.3, 8.1, 8.2, 8.3_

  - [x] 12.3 Implement customer-facing message composition
    - When trigger has `scope: "customer"` and `customer_id` is populated, set `send_as: "merchant_on_behalf"`
    - Retrieve CustomerContext from store, pass to Composer
    - Honor customer's `language_pref`, `consent.scope`, and `preferences`
    - Use merchant's business name and warm customer-facing voice
    - For senior citizens (age_band "65-75" or higher): use respectful salutations, offer call option
    - _Requirements: 16.1, 16.2, 16.3, 16.4, 16.5_

  - [ ]* 12.4 Write integration tests for tick → reply cycle
    - Test full warmup flow: push contexts, verify healthz counts
    - Test tick with triggers: verify actions returned with correct fields
    - Test reply cycle: send reply, verify follow-up response
    - Test auto-reply hell scenario (4 consecutive auto-replies)
    - Test intent transition scenario
    - Test hostile exit scenario
    - Test mid-test context update: push v2, verify new data used in next composition
    - _Requirements: 4.1, 5.1, 6.1, 7.1, 8.1, 12.1, 12.2, 12.3_

- [x] 13. Implement Submission Generator
  - [x] 13.1 Implement dataset expansion integration
    - Verify `dataset/generate_dataset.py` runs correctly to produce expanded dataset
    - Load expanded `test_pairs.json` with 30 canonical (merchant_id, trigger_id) pairs
    - _Requirements: 15.1, 15.2_

  - [x] 13.2 Implement `SubmissionGenerator` class (`src/submission.py`)
    - Implement `generate(test_pairs_path, output_path) -> None`
    - For each test pair: retrieve category, merchant, trigger, and optionally customer contexts from store
    - Invoke Composer for each pair
    - Write JSON line with `test_id`, `body`, `cta`, `send_as`, `suppression_key`, `rationale` to `submission.jsonl`
    - Implement progress tracking and timeout management (complete within 15 minutes)
    - _Requirements: 14.1, 14.2, 14.3, 14.4_

  - [ ]* 13.3 Write property tests for submission line completeness
    - **Property 22: Submission line completeness**
    - **Validates: Requirements 14.2**

  - [ ]* 13.4 Write property test for dataset expansion determinism
    - **Property 23: Dataset expansion determinism**
    - **Validates: Requirements 15.2**

- [x] 14. Final integration and entry point
  - [x] 14.1 Create main entry point and CLI
    - Create `main.py` with uvicorn server startup (host=0.0.0.0, port=8080)
    - Add CLI flag `--generate-submission` to run SubmissionGenerator instead of server
    - Add CLI flag `--expand-dataset` to run dataset expansion
    - Wire all modules together: ContextStore → TriggerEvaluator → ConversationManager → Composer → LLMClient
    - Add environment variable support for LLM_PROVIDER, LLM_API_KEY, LLM_MODEL
    - _Requirements: 1.1, 2.1, 3.1, 4.1, 5.1, 14.1_

  - [x] 14.2 Add error handling and logging
    - Add structured logging throughout all modules
    - Add global exception handler in FastAPI to always return valid JSON
    - Add request timing middleware to log latency per endpoint
    - Ensure no endpoint ever returns malformed JSON or times out without a response
    - _Requirements: 18.1, 18.2, 18.3, 18.4, 18.5, 18.6_

  - [ ]* 14.3 Run full integration test suite against local bot
    - Start bot server locally
    - Run `judge_simulator.py` against the bot
    - Verify all scenarios pass (warmup, phase2, auto-reply, intent, hostile)
    - Fix any issues found
    - _Requirements: 1.1, 2.1, 3.1, 4.1, 5.1, 6.1, 7.1, 8.1_

- [x] 15. Final checkpoint — Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Each task references specific requirements for traceability
- Checkpoints ensure incremental validation
- Property tests validate universal correctness properties from the design document
- Unit tests validate specific examples and edge cases
- The implementation language is Python with FastAPI, matching the design document
- All prompt templates follow the 4-context composition framework optimized for the 5-dimension scoring rubric
