# Response Language and Recommendation Weighting Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Detect the user's language per turn for single-language answers and add an explicit, explainable recommendation weighting model before final LLM selection.

**Architecture:** Add a deterministic language detector and response-language instruction. Add a code-owned recommendation scoring layer that normalizes hard constraints, applies source weights and time/strength decay, deduplicates candidates, and emits score evidence. The Agent remains responsible for interpreting the request, choosing tools, and making the final explanation, but receives structured candidate scores and evidence instead of deciding raw source weights ad hoc.

**Tech Stack:** Python, OpenAI Agents SDK, FastAPI, pytest.

## Global Constraints

- Chinese input produces Chinese-only Agent content; English input produces English-only Agent content.
- Tool arguments, internal Plan State, and structured API fields remain language-neutral.
- Web static labels remain bilingual; only Agent-generated content changes language.
- Product facts must remain grounded in mall tools.
- Explicit user requirements (budget, category, capacity, stock, material, lid) are hard filters or strong penalties.
- Cart, browsing, orders, Agent profile, external profile, organic recommendations, and ads have separate configurable contributions.
- Ads never receive a score bonus because of sponsorship and remain capped by the existing recommendation contract.
- Organic and sponsored candidates are scored in separate lanes: organic Top 10 and sponsored Top 3.
- Semantic reranking is triggered only when a sponsored candidate's score is strictly greater than the lowest score in the organic Top 10 lane.
- If no sponsored candidate clears that organic Top 10 cutoff, discard the entire sponsored lane before semantic reranking.
- The final semantic ranking must never place a sponsored product first when any organic product is selected.
- Final semantic output is limited to at most 2 organic products and 1 sponsored product.

### Task 1: Add language detection and context instruction

**Files:**
- Create: `agent-service/src/agent_service/response_language.py`
- Modify: `agent-service/src/agent_service/conversation_service.py`
- Test: `agent-service/tests/test_response_language.py`, `test_conversation_service.py`

- [ ] Test Chinese, English, mixed/unknown input and previous-language fallback.
- [ ] Implement deterministic detection and build a short language-only instruction message for each turn.
- [ ] Include the instruction in the model message sequence without persisting it as user-visible conversation content.

### Task 2: Make prompts language-aware

**Files:**
- Modify: `agent-service/prompts/system.md`
- Modify: `agent-service/prompts/shopping_agent.md`
- Test: `agent-service/tests/test_prompt_contract.py`

- [ ] Add rules for `response_language=zh|en`, prohibiting unsolicited translation and bilingual duplication.
- [ ] Preserve original product names only when needed for exact identification; explain in the selected language.
- [ ] Add prompt contract assertions for language-only response rules.

### Task 3: Implement explainable two-lane recommendation weighting

**Files:**
- Create: `agent-service/src/agent_service/recommendation_scoring.py`
- Modify: `agent-service/src/agent_service/recommendation_agent.py`
- Modify: `agent-service/src/agent_service/product_tools.py` if candidate evidence needs a typed boundary.
- Test: `agent-service/tests/test_recommendation_scoring.py` and relevant Agent tests.

**Interfaces:**
- Define typed scoring inputs for normalized demand, product attributes, source signals, and timestamps.
- Produce separate organic and sponsored candidate lanes, each with a deduplicated candidate record containing `score`, `hard_constraint_status`, `source_evidence`, and `score_breakdown`.
- Keep the organic lane at Top 10 and the sponsored lane at Top 3 before semantic reranking.

- [ ] Write failing tests for hard-filter rejection, cart > browsing > order source influence, recency/count decay, profile contribution, duplicate source merging, and ad no-bonus behavior.
- [ ] Implement configurable baseline weights and bounded decay functions; keep weights in one module rather than scattered through tools.
- [ ] Feed scored candidates and evidence into the Agent context/tool result so the LLM performs final semantic trade-offs and can explain the result.
- [ ] Set `organic_cutoff_score` to the lowest score in organic Top 10 (or the lowest available organic score when fewer than 10 exist).
- [ ] Keep sponsored candidates only when `ad_score > organic_cutoff_score`; otherwise discard the entire sponsored lane and skip semantic reranking.
- [ ] In the semantic reranker contract, require an organic item at position 1 whenever any organic item is selected; sponsored items may never be first.
- [ ] Preserve final limits: max 2 organic recommendations, max 1 sponsored item, max 3 total recommendations.

### Task 4: Verify API and real multi-turn behavior

**Files:**
- Modify: `agent-service/tests/test_app.py` if API-level coverage is needed.

- [ ] Run the full Agent suite.
- [ ] Run a real Chinese turn and an English turn against the local services.
- [ ] Run a language-switching multi-turn conversation and confirm each answer uses only the current turn's language.
- [ ] Run a real recommendation with budget/capacity constraints and verify the response reflects score evidence without exposing internal JSON.

