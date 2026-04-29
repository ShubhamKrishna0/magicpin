# Requirements Document

## Introduction

Vera Merchant Bot is a production-quality HTTP bot server that implements the magicpin AI Challenge judge harness contract. The bot acts as "Vera," an AI merchant assistant that engages merchants and their customers on WhatsApp. It exposes 5 HTTP endpoints, persists pushed context across calls, composes high-quality messages using a 4-context framework (Category, Merchant, Trigger, Customer), handles multi-turn conversations with intent detection and graceful exits, and generates a `submission.jsonl` for the 30 canonical test pairs. The bot uses an LLM for message composition, scoring on 5 dimensions: Specificity, Category Fit, Merchant Fit, Trigger Relevance, and Engagement Compulsion.

## Glossary

- **Bot_Server**: The HTTP server application exposing the 5 judge-harness endpoints
- **Context_Store**: The in-memory data store that persists category, merchant, customer, and trigger contexts across calls, keyed by `(scope, context_id)` with version tracking
- **Composer**: The LLM-powered module that takes up to 4 context objects and produces a composed WhatsApp message with body, CTA, send_as, suppression_key, and rationale
- **Conversation_Manager**: The module that tracks active conversations, detects auto-replies, handles intent transitions, manages graceful exits, and prevents repetition
- **Trigger_Evaluator**: The module that, on each tick, ranks available triggers by urgency and relevance, decides which to act on, and which to suppress
- **Submission_Generator**: The module that reads the expanded dataset's 30 test pairs and produces `submission.jsonl` by invoking the Composer for each pair
- **Judge_Harness**: The external magicpin test system that calls the Bot_Server endpoints, plays merchant/customer roles, and scores responses
- **CategoryContext**: Slow-changing knowledge pack per business vertical (dentists, salons, restaurants, gyms, pharmacies) including voice profile, offer catalog, peer stats, digest items, seasonal beats, and trend signals
- **MerchantContext**: Per-merchant state including identity, subscription, performance metrics, active offers, conversation history, customer aggregates, and derived signals
- **TriggerContext**: The event prompting a specific message, with kind, scope, urgency, payload, suppression key, and expiry
- **CustomerContext**: Per-customer state with a specific merchant, including identity, relationship history, lapse state, preferences, and consent
- **Suppression_Key**: A deduplication identifier that prevents the same trigger from generating duplicate messages
- **Auto_Reply**: A canned WhatsApp Business auto-response from a merchant (e.g., "Thank you for contacting…") that is not a genuine human reply
- **Intent_Transition**: The moment a merchant shifts from qualification/questioning to explicit commitment (e.g., "let's do it"), requiring the bot to switch from qualifying to action mode
- **Compulsion_Lever**: A persuasion technique used in message composition — specificity, loss aversion, social proof, effort externalization, curiosity, reciprocity, asking-the-merchant, or single binary commitment

## Requirements

### Requirement 1: Health Check Endpoint

**User Story:** As the Judge_Harness, I want to probe the bot's liveness and context load state, so that I can verify the bot is operational and has ingested the expected dataset before scoring begins.

#### Acceptance Criteria

1. THE Bot_Server SHALL expose a `GET /v1/healthz` endpoint that returns HTTP 200 with a JSON body containing `status`, `uptime_seconds`, and `contexts_loaded`
2. THE Bot_Server SHALL return `contexts_loaded` as an object with keys `category`, `merchant`, `customer`, and `trigger`, each holding the count of distinct context_ids stored for that scope
3. WHEN the Bot_Server receives a `GET /v1/healthz` request, THE Bot_Server SHALL respond within 2 seconds

### Requirement 2: Metadata Endpoint

**User Story:** As the Judge_Harness, I want to retrieve the bot's identity and approach description, so that I can log team information and model choice for scoring records.

#### Acceptance Criteria

1. THE Bot_Server SHALL expose a `GET /v1/metadata` endpoint that returns HTTP 200 with a JSON body containing `team_name`, `team_members`, `model`, `approach`, `contact_email`, `version`, and `submitted_at`
2. WHEN the Bot_Server receives a `GET /v1/metadata` request, THE Bot_Server SHALL respond within 2 seconds

### Requirement 3: Context Ingestion Endpoint

**User Story:** As the Judge_Harness, I want to push category, merchant, customer, and trigger contexts to the bot incrementally, so that the bot has the data it needs to compose contextually relevant messages.

#### Acceptance Criteria

1. THE Bot_Server SHALL expose a `POST /v1/context` endpoint that accepts a JSON body with fields `scope`, `context_id`, `version`, `payload`, and `delivered_at`
2. WHEN the Bot_Server receives a context push with a `version` higher than the currently stored version for the same `(scope, context_id)`, THE Context_Store SHALL atomically replace the prior version with the new payload and return HTTP 200 with `{"accepted": true, "ack_id": "...", "stored_at": "..."}`
3. WHEN the Bot_Server receives a context push with a `version` equal to or lower than the currently stored version for the same `(scope, context_id)`, THE Bot_Server SHALL return HTTP 409 with `{"accepted": false, "reason": "stale_version", "current_version": <current>}`
4. WHEN the Bot_Server receives a context push with an invalid or missing `scope` field, THE Bot_Server SHALL return HTTP 400 with `{"accepted": false, "reason": "invalid_scope"}`
5. THE Context_Store SHALL persist all accepted contexts in memory for the duration of the test run without data loss between endpoint calls
6. WHEN the Bot_Server receives a context push, THE Bot_Server SHALL respond within 5 seconds

### Requirement 4: Tick Endpoint — Proactive Message Initiation

**User Story:** As the Judge_Harness, I want to wake the bot periodically with a list of active triggers, so that the bot can decide which merchants or customers to proactively message.

#### Acceptance Criteria

1. THE Bot_Server SHALL expose a `POST /v1/tick` endpoint that accepts a JSON body with fields `now` (ISO timestamp) and `available_triggers` (list of trigger context_ids)
2. WHEN the Bot_Server receives a tick, THE Trigger_Evaluator SHALL inspect each available trigger, retrieve the associated merchant, category, and optionally customer contexts from the Context_Store, and decide whether to compose a message
3. THE Bot_Server SHALL return HTTP 200 with `{"actions": [...]}` where each action contains `conversation_id`, `merchant_id`, `customer_id`, `send_as`, `trigger_id`, `template_name`, `template_params`, `body`, `cta`, `suppression_key`, and `rationale`
4. WHEN no triggers warrant action, THE Bot_Server SHALL return `{"actions": []}` rather than fabricating messages
5. THE Trigger_Evaluator SHALL skip triggers whose `suppression_key` matches a previously sent message's suppression_key within the same test run
6. WHEN the Bot_Server receives a tick, THE Bot_Server SHALL respond within 10 seconds
7. THE Bot_Server SHALL return at most 20 actions per tick response

### Requirement 5: Reply Endpoint — Multi-Turn Conversation Handling

**User Story:** As the Judge_Harness, I want to send merchant or customer replies to the bot, so that the bot can continue multi-turn conversations with contextually appropriate follow-ups.

#### Acceptance Criteria

1. THE Bot_Server SHALL expose a `POST /v1/reply` endpoint that accepts a JSON body with fields `conversation_id`, `merchant_id`, `customer_id`, `from_role`, `message`, `received_at`, and `turn_number`
2. WHEN the Bot_Server receives a reply, THE Conversation_Manager SHALL append the incoming message to the conversation history for the given `conversation_id`
3. THE Bot_Server SHALL return HTTP 200 with one of three action types: `{"action": "send", "body": "...", "cta": "...", "rationale": "..."}`, `{"action": "wait", "wait_seconds": N, "rationale": "..."}`, or `{"action": "end", "rationale": "..."}`
4. WHEN the Bot_Server receives a reply, THE Bot_Server SHALL respond within 10 seconds

### Requirement 6: Auto-Reply Detection

**User Story:** As a merchant engagement system, I want the bot to detect WhatsApp Business canned auto-replies, so that it does not waste turns engaging with automated responses.

#### Acceptance Criteria

1. WHEN the Conversation_Manager receives a merchant message that matches common auto-reply patterns (e.g., "Thank you for contacting", "Our team will respond shortly"), THE Conversation_Manager SHALL classify the message as an Auto_Reply
2. WHEN the Conversation_Manager detects a first Auto_Reply in a conversation, THE Bot_Server SHALL respond with `action: "send"` containing a brief acknowledgment that flags the message for the owner
3. WHEN the Conversation_Manager detects a second consecutive Auto_Reply in the same conversation, THE Bot_Server SHALL respond with `action: "wait"` with a wait period of at least 4 hours
4. WHEN the Conversation_Manager detects a third or subsequent consecutive Auto_Reply in the same conversation, THE Bot_Server SHALL respond with `action: "end"`

### Requirement 7: Intent Transition Detection

**User Story:** As a merchant engagement system, I want the bot to detect when a merchant explicitly commits to an action, so that it immediately switches from qualifying to executing.

#### Acceptance Criteria

1. WHEN the Conversation_Manager receives a merchant message containing explicit commitment language (e.g., "let's do it", "yes go ahead", "ok what's next"), THE Conversation_Manager SHALL classify the intent as `action_committed`
2. WHEN the intent is classified as `action_committed`, THE Composer SHALL produce a response that executes the next concrete step rather than asking further qualifying questions
3. THE Composer SHALL include measurable scope or deliverables in the action response (e.g., "drafting for 40 patients", "live in 10 min")

### Requirement 8: Hostile and Opt-Out Handling

**User Story:** As a merchant engagement system, I want the bot to gracefully exit when a merchant expresses frustration or explicitly opts out, so that the bot respects merchant boundaries and avoids negative brand impact.

#### Acceptance Criteria

1. WHEN the Conversation_Manager receives a merchant message containing explicit opt-out language (e.g., "stop messaging me", "not interested", "unsubscribe"), THE Bot_Server SHALL respond with `action: "end"` or a brief apology followed by `action: "end"`
2. WHEN a conversation is ended due to hostile or opt-out signals, THE Conversation_Manager SHALL suppress all future triggers for that merchant's conversation_id
3. WHEN the Conversation_Manager receives an off-topic request (e.g., "help me with GST filing"), THE Composer SHALL politely decline the off-topic ask and redirect back to the original trigger topic

### Requirement 9: LLM-Powered Message Composition

**User Story:** As a merchant engagement system, I want the bot to compose WhatsApp messages using an LLM with the 4-context framework, so that messages are specific, category-appropriate, merchant-personalized, trigger-relevant, and compulsion-driven.

#### Acceptance Criteria

1. THE Composer SHALL accept CategoryContext, MerchantContext, TriggerContext, and optionally CustomerContext as structured inputs and produce a ComposedMessage containing `body`, `cta`, `send_as`, `suppression_key`, and `rationale`
2. THE Composer SHALL use the CategoryContext voice profile to match tone, vocabulary, and taboos for the merchant's business vertical
3. THE Composer SHALL reference verifiable facts from the provided contexts (numbers, dates, source citations, peer stats) and avoid fabricating data not present in the contexts
4. THE Composer SHALL personalize messages using the merchant's owner first name, locality, active offers, performance metrics, and derived signals from MerchantContext
5. THE Composer SHALL explicitly connect the message to the triggering event from TriggerContext, making clear why the message is being sent now
6. THE Composer SHALL employ at least one Compulsion_Lever per message (specificity, loss aversion, social proof, effort externalization, curiosity, reciprocity, asking-the-merchant, or single binary commitment)
7. THE Composer SHALL include a single primary CTA positioned at the end of the message body
8. WHEN `send_as` is `"vera"`, THE Composer SHALL use Vera's peer/colleague voice addressing the merchant directly
9. WHEN `send_as` is `"merchant_on_behalf"`, THE Composer SHALL use the merchant's business name and warm customer-facing voice, honoring the customer's language preference
10. THE Composer SHALL honor the merchant's `identity.languages` field and the customer's `identity.language_pref` field, supporting Hindi-English code-mix where indicated

### Requirement 10: Trigger-Kind Routing and Prompt Dispatch

**User Story:** As a message composition system, I want different trigger kinds to use specialized prompt variants, so that research digests, recall reminders, performance alerts, and planning intents each produce appropriately shaped messages.

#### Acceptance Criteria

1. THE Composer SHALL dispatch to a trigger-kind-specific prompt variant based on the `TriggerContext.kind` field
2. WHEN the trigger kind is `research_digest`, THE Composer SHALL include source citation, trial size, and patient segment relevance in the message body
3. WHEN the trigger kind is `recall_due`, THE Composer SHALL include specific appointment slot options, service price from the offer catalog, and the customer's recall window
4. WHEN the trigger kind is `perf_dip` or `seasonal_perf_dip`, THE Composer SHALL include the specific metric delta, contextualize whether the dip is expected or concerning, and propose a concrete next step
5. WHEN the trigger kind is `active_planning_intent`, THE Composer SHALL produce a complete drafted artifact (e.g., pricing tiers, program structure) rather than asking more qualifying questions
6. WHEN the trigger kind is `supply_alert`, THE Composer SHALL include affected batch numbers, manufacturer name, and the count of affected customers derived from merchant data

### Requirement 11: Anti-Pattern Prevention

**User Story:** As a message quality system, I want the bot to avoid known anti-patterns that reduce scores, so that every composed message meets the judge's quality floor.

#### Acceptance Criteria

1. THE Composer SHALL use service-at-price format (e.g., "Dental Cleaning @ ₹299") instead of generic discount format (e.g., "Flat 30% off") when offers are available in the context
2. THE Composer SHALL include at most one primary CTA per message
3. THE Composer SHALL position the CTA in the final sentence of the message body
4. THE Composer SHALL avoid vocabulary listed in `CategoryContext.voice.vocab_taboo`
5. THE Conversation_Manager SHALL prevent sending the same `body` text verbatim within the same `conversation_id`
6. THE Composer SHALL avoid long preambles (e.g., "I hope you're doing well") and re-introductions after the first message in a conversation
7. THE Composer SHALL avoid including URLs in message bodies

### Requirement 12: Context Versioning and Adaptive Composition

**User Story:** As a merchant engagement system, I want the bot to use the latest version of each context when composing messages, so that mid-test context injections (new digest items, updated performance, new triggers) are reflected in subsequent compositions.

#### Acceptance Criteria

1. WHEN the Context_Store receives a higher-version context for an existing `context_id`, THE Context_Store SHALL replace the old version atomically before the next composition uses that context
2. WHEN the Composer composes a message, THE Composer SHALL retrieve the latest version of each relevant context from the Context_Store
3. THE Composer SHALL incorporate newly pushed digest items, updated performance metrics, and new triggers in subsequent messages without requiring a restart

### Requirement 13: Conversation State Persistence

**User Story:** As a multi-turn conversation system, I want the bot to maintain conversation state across reply calls, so that follow-up messages are coherent and non-repetitive.

#### Acceptance Criteria

1. THE Conversation_Manager SHALL maintain a per-conversation_id history of all turns (both bot-sent and merchant/customer-received messages)
2. THE Conversation_Manager SHALL track the current conversation phase (initiating, qualifying, action_committed, waiting, ended) for each conversation_id
3. WHEN composing a follow-up message, THE Composer SHALL receive the full conversation history to avoid repeating previous content and to maintain thread coherence
4. THE Conversation_Manager SHALL track suppressed conversation_ids and prevent new messages from being sent on ended conversations

### Requirement 14: Submission Generation

**User Story:** As a challenge participant, I want the bot to generate a `submission.jsonl` file for the 30 canonical test pairs, so that I can submit scored compositions to the judge.

#### Acceptance Criteria

1. THE Submission_Generator SHALL read the expanded dataset's `test_pairs.json` file containing 30 `(merchant_id, trigger_id)` pairs
2. FOR EACH test pair, THE Submission_Generator SHALL retrieve the associated category, merchant, trigger, and optionally customer contexts, invoke the Composer, and produce a JSON line with `test_id`, `body`, `cta`, `send_as`, `suppression_key`, and `rationale`
3. THE Submission_Generator SHALL write all 30 lines to `submission.jsonl` in the project root
4. THE Submission_Generator SHALL complete composition for all 30 pairs within 15 minutes total

### Requirement 15: Dataset Expansion

**User Story:** As a challenge participant, I want to expand the seed dataset to the full 50 merchants, 200 customers, and 100 triggers, so that the bot can be tested against the complete dataset the judge expects.

#### Acceptance Criteria

1. WHEN the dataset expansion script is executed, THE Submission_Generator SHALL produce 50 merchant files, 200 customer files, 100 trigger files, and 30 test pairs from the seed data
2. THE expanded dataset SHALL be deterministic (same output for the same seed value) so that all participants work with identical data

### Requirement 16: Customer-Facing Message Composition

**User Story:** As a merchant engagement system, I want the bot to compose messages sent on behalf of the merchant to their customers, so that recall reminders, lapse winbacks, and refill notifications reach customers in the merchant's voice.

#### Acceptance Criteria

1. WHEN a trigger has `scope: "customer"` and a populated `customer_id`, THE Composer SHALL set `send_as` to `"merchant_on_behalf"` and compose the message in the merchant's business voice
2. THE Composer SHALL honor the CustomerContext's `identity.language_pref` field for language and code-mix selection
3. THE Composer SHALL reference the customer's relationship history (visit count, last visit date, services received) to personalize the message
4. THE Composer SHALL respect the CustomerContext's `consent.scope` field and only send message types the customer has opted into
5. WHEN composing for a senior citizen customer (age_band containing "65" or higher), THE Composer SHALL use respectful salutations and offer multiple response channels (reply or call)

### Requirement 17: Suppression and Deduplication

**User Story:** As a message quality system, I want the bot to prevent duplicate messages for the same trigger event, so that merchants and customers are not spammed with repeated content.

#### Acceptance Criteria

1. THE Trigger_Evaluator SHALL maintain a set of fired suppression_keys for the duration of the test run
2. WHEN a trigger's `suppression_key` matches an already-fired key, THE Trigger_Evaluator SHALL skip that trigger during tick processing
3. WHEN a trigger's `expires_at` timestamp has passed relative to the tick's `now` timestamp, THE Trigger_Evaluator SHALL skip that trigger

### Requirement 18: Response Time Compliance

**User Story:** As a challenge participant, I want the bot to respond within the judge's timeout budgets, so that no responses are dropped due to latency.

#### Acceptance Criteria

1. THE Bot_Server SHALL respond to `GET /v1/healthz` within 2 seconds
2. THE Bot_Server SHALL respond to `GET /v1/metadata` within 2 seconds
3. THE Bot_Server SHALL respond to `POST /v1/context` within 5 seconds
4. THE Bot_Server SHALL respond to `POST /v1/tick` within 10 seconds
5. THE Bot_Server SHALL respond to `POST /v1/reply` within 10 seconds
6. IF the Composer cannot complete LLM composition within the timeout budget, THEN THE Bot_Server SHALL return a valid empty or minimal response rather than timing out
