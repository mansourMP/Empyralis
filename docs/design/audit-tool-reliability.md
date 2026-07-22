# Tool Reliability Audit — "Are We SURE the Model Will Use Our Tools Correctly, 100%?"

**Scope:** REPORT-ONLY. No code changed. Every "our reality" claim below is
file:line-cited against `/Users/mansur/empyralis` as checked out on
2026-07-22 (branch `main`). Official guidance is fresh-fetched from
`platform.claude.com` and `anthropic.com/engineering` on 2026-07-22 — not
recalled from training — with source URLs on every claim so it can be
re-verified as the docs change.

**Short answer to the founder's question: No, we are not sure.** The
two-tier architecture (small always-on set + on-demand discovery) is the
right shape and matches Anthropic's own recommended pattern. But the
discovery layer — the only path to the other ~40+ tools — is plain
token-overlap string matching with no semantic understanding, and it
**empirically fails on realistic paraphrases** (reproduced below, not
theorized). A user request that doesn't happen to share word-stems with a
tool's own name/description text gets **zero tools back**, silently. That is
the single biggest reliability gap found.

---

## 1. Official guidance (fresh-fetched 2026-07-22)

Sources: [Define tools](https://platform.claude.com/docs/en/agents-and-tools/tool-use/define-tools.md) · [Tool use overview](https://platform.claude.com/docs/en/agents-and-tools/tool-use/overview.md) · [Tool search tool](https://platform.claude.com/docs/en/agents-and-tools/tool-use/tool-search-tool.md) · [Writing effective tools for AI agents](https://www.anthropic.com/engineering/writing-tools-for-agents) · [Advanced tool use](https://www.anthropic.com/engineering/advanced-tool-use)

### 1.1 Description quality is the dominant factor, by Anthropic's own words

> "**Provide extremely detailed descriptions. This is by far the most
> important factor in tool performance.** Your descriptions should explain
> every detail about the tool... Aim for **at least 3–4 sentences** for each
> tool description, more if the tool is complex."
> — [Define tools § Best practices for tool definitions](https://platform.claude.com/docs/en/agents-and-tools/tool-use/define-tools.md)

The same page contrasts a **good** description (`get_stock_price`: 4
sentences — what it does, valid input format, what it returns, what it does
NOT do) against a **poor** one (`"Gets the stock price for a ticker."`) and
states plainly that the poor one "leaves Claude with many open questions
about the tool's behavior and usage." This exact poor-description shape
recurs across our own tool population (§2, §3.3).

### 1.2 Schema quality and strict mode

- `input_schema` is a full JSON Schema object; every parameter should carry
  its own `description`; `enum` should be used for fixed value sets.
- `strict: true` on a tool definition (top-level, alongside
  `name`/`description`/`input_schema`) guarantees the model's `tool_use.input`
  validates exactly against the schema — requires `additionalProperties:
  false` + `required`. Combine with `tool_choice: {"type": "any"}` to
  guarantee both that a tool is called **and** that its input is
  schema-valid.
- `input_examples` (optional array of example input objects, schema-validated
  at request time, ~20–50 tokens each) is explicitly recommended for "tools
  with complex inputs, nested objects, or format-sensitive parameters."

### 1.3 Tool-count thresholds — concrete numbers, not vibes

> "Claude's ability to pick the right tool **degrades once you exceed 30–50
> available tools**." — [Tool search tool](https://platform.claude.com/docs/en/agents-and-tools/tool-use/tool-search-tool.md)

> "Use tool search when... your tool definitions consume more than **10k
> tokens**... Standard tool calling is a better fit when you have fewer than
> 10 tools." — same page

Measured impact from [Advanced tool use](https://www.anthropic.com/engineering/advanced-tool-use):
a 5-server, 58-tool setup (GitHub 35 tools, Slack 11, Sentry 5, Grafana 5,
Splunk 2) consumes **~55K tokens before the conversation starts**; enabling
Tool Search Tool moved measured tool-selection accuracy from **49%→74%
(Opus 4)** and **79.5%→88.1% (Opus 4.5)**.

### 1.4 Consolidate and namespace

> "**Consolidate related operations into fewer tools.** Rather than creating
> a separate tool for every action (`create_pr`, `review_pr`, `merge_pr`),
> group them into a single tool with an `action` parameter. Fewer, more
> capable tools reduce selection ambiguity."
> "**Use meaningful namespacing** in tool names... especially important when
> using tool search." — Define tools

The [Writing effective tools for AI agents](https://www.anthropic.com/engineering/writing-tools-for-agents)
engineering post adds: return "high-signal" semantic identifiers, not raw
IDs; implement pagination/truncation with sensible defaults; iterate via a
**Prototype → Evaluate → Collaborate** loop with dozens of realistic
prompt/response pairs, measuring accuracy, tool-call count, and tool errors.

### 1.5 `tool_choice` and parallel tool use

Four values: `auto` (default), `any` (must call some tool), `tool` (must
call this specific one), `none`. Parallel tool use is on by default — one
assistant turn may contain multiple `tool_use` blocks; all corresponding
`tool_result` blocks must return in a single user message or the model is
silently trained to stop calling tools in parallel.

### 1.6 Tool search tool — the current deferred-loading API

Two server-side variants, both GA on current models:
`tool_search_tool_regex_20251119` (Claude writes Python `re.search()`
patterns) and `tool_search_tool_bm25_20251119` (Claude writes natural-language
queries, real BM25 ranking — term frequency / inverse document frequency,
not naive token overlap). Mechanics:

- Every tool's full definition is still sent in `tools` on every request
  (server needs it to search and to expand results); `defer_loading: true`
  controls only what enters the **context window** up front.
- Both variants search tool **names, descriptions, argument names, and
  argument descriptions** — so keyword-poor descriptions are invisible to
  search regardless of which variant is used.
- At least one tool (normally the search tool itself) must stay
  non-deferred; keep the 3–5 most-used tools non-deferred too.
- Prompt cache is preserved — discovered tools are *appended* as
  `tool_reference` blocks, not spliced into the cached prefix.
- A **custom** implementation (embeddings, or anything else) is fully
  supported: a tool can return `tool_reference` blocks from its own
  `tool_result`, and the API expands them exactly the same way. This is the
  documented escape hatch for semantic/embedding-based search.

### 1.7 Chain-of-tools / multi-step failure modes

From Advanced tool use: multi-step tool chains suffer **context pollution**
(large intermediate results, e.g. "2,000+ expense line items" landing in
context) and **inference overhead** (each round trip is a full model pass).
Programmatic Tool Calling (code-execution-mediated tool calls,
`allowed_callers: ["code_execution"]`) is Anthropic's answer — out of scope
for a two-tier keyword registry but relevant context for why "just add more
tool calls" doesn't scale cleanly.

---

## 2. Our reality — graded sample (18 tools, file:line-verified)

Files audited: `server_modules/skills_service.py` (`ToolDescriptor` @
skills_service.py:37-51 — fields `tool_name, label, connector_id, action_id,
description, capability_id, risk_level, requires_approval, parameters,
requires_runtime, metadata, audience_safe, audience_note`; **no `strict`,
no `input_examples`, no `additionalProperties` field exists on the
dataclass at all** — confirmed via `grep -n '"strict"\|additionalProperties'`
across all three server_modules files audited: zero hits), 49 total
`ToolDescriptor(` literals in that file (`grep -c`), plus a dynamic
connector-app tier from `build_direct_chat_tools()` (skills_service.py:1414)
and an MCP tier from `mcp_registry_service.py`.

| Tool | Description (verbatim or excerpt) | Sentences | Schema quality | file:line |
|---|---|---|---|---|
| `task_complete` | "Call this tool when you have finished the user's task. Provide a short summary... Calling this tool signals that the work is complete... The platform will end the run cleanly." | 4 | 1 param, described, required | skills_service.py:617-640 |
| `update_plan` | "For a multi-step task, call this first to lay out the steps... Each call REPLACES the current plan... For a simple single-step request, don't use this tool." | 5 | nested array-of-objects, `enum` on status, per-field descriptions | skills_service.py:641-680 |
| `memory_search` | "Mandatory recall step before answering about prior work, decisions, dates, people, preferences, or todos. Search MEMORY.md and memory/*.md..." | 2 | both params described | skills_service.py:719-739 |
| `memory_write` | "Write or append content... MUST call this tool to persist facts — text replies alone do not save anything." | 4 | `enum` on mode, described | skills_service.py:740-762 |
| `memory_read` | "Read a file from the agent's memory directory. Use to recall previously saved facts." | 2 | 1 param, described | skills_service.py:763-778 |
| `memory_get` | "Read a small excerpt from MEMORY.md or memory/*.md after memory_search identifies the file and lines." | 1 | 3 params, described | skills_service.py:779-796 |
| `memory_update` | "Update one workspace memory context file. Use only when the user explicitly asks... Read the current file first with memory_get, then write the complete revised file content." | 3 | 2 params, described | skills_service.py:797-816 |
| `hardware__action` | "Run a browser, file, shell, screenshot, or app/window action through an Empyralis runtime target. Use runtime_target user_device_gateway for..." | 3 | `enum` on runtime_target; `action`/`arguments` are **free-text strings describing a whole sub-API** ("file.read, shell.execute, screenshot.capture, browser.open...") — schema does not actually constrain `action` to valid values | skills_service.py:681-718 |
| `web__search` | "Search the web and return the top 5 results with titles, URLs, and snippets." | 1 | 1 param, described | skills_service.py:975-983 |
| `web__fetch` | "Fetch a webpage and extract readable text from it." | 1 | 1 param, described | skills_service.py:985-993 |
| `file__read` | "Read a file from the local machine" | 1 | 1 param, described | skills_service.py:452-461 |
| `file__write` | "Write content to a file on the local machine" | 1 | 2 params, described | skills_service.py:462-478 |
| `shell__exec` | "Execute a shell command on the local machine" | 1 | 1 param, described, **no warning about destructive commands / no examples of safe usage** | skills_service.py:479-488 |
| `computer__click` | "Click on the screen by coordinates or visible text" | 1 | 3 optional params, **no `required`, no field descriptions on x/y/text** | skills_service.py:522-531 |
| `computer__applescript` | "Execute a system script on your computer" | 1 | 1 param (`script`), **no field description at all** | skills_service.py:542-551 |
| `computer__notify` | "Send a system notification" | 1 | both params described | skills_service.py:572-581 |
| `browser__navigate` | "Render a URL in a headless browser (for JS-rendered pages curl can't read). Follow with browser__extract_text or browser__extract_dom to read its content." | 2 | 1 param, described, cross-references sibling tools | skills_service.py:1148-1156 |
| `fleet__configure_agent` | "Update an agent's configuration: enabled tools, connectors, channel bindings, hardware access, subagents toggle, model config, ..." | multi | large free-form object | skills_service.py:1322-1354 |
| `query_tool_registry` (the discovery tool itself) | "Search for available tools and capabilities that are not in your default tool set... Returns the 3-5 most relevant tools with their full schemas..." | 3 | 1 required + 1 optional param, both described, **the `task_description` field description ships 5 worked example queries as steering** | tool_registry_service.py:394-431 |

**Reading the table:** the always-on tools that a human clearly iterated on
(`task_complete`, `update_plan`, `memory_write`, `query_tool_registry`) hit
or nearly hit Anthropic's 3–4-sentence bar. The much larger population of
local/computer/connector tools (`file__*`, `computer__*`, `web__*`,
`shell__exec`) are almost all **exactly the "poor description" shape**
Anthropic's docs warn against — one clause, no "when to use / when not to,"
no failure-mode notes, several with undocumented parameters
(`computer__click`, `computer__applescript`). Quality is not a
platform-wide policy; it tracks which tool a person happened to spend time
on.

---

## 3. Systemic findings (ordered by how much they affect "will the model use tools correctly")

### 3.1 The discovery search is naive token-overlap, not semantic — and it demonstrably fails on realistic phrasing

`server_modules/tool_registry_service.py:237-289` (`search_tool_registry`).
The entire algorithm:

```python
query_tokens = set(query.lower().split()) - stopwords
for entry in registry:
    for token in query_tokens:
        if token in entry_kw_lower: score += 1        # exact token match
        elif any(token in kw or kw in token for kw in entry_kw_lower): score += 0.5  # substring
```

No stemming, no synonyms, no embeddings, no real BM25 (despite the file's
own header comment describing it as keyword search over "48+ tools" —
`tool_registry_service.py:4-11`). Keywords are extracted once per tool from
`name + description + connector_id` via `_extract_keywords()`
(`tool_registry_service.py:55-71`), so a tool's discoverability is capped by
whatever words its own author happened to write.

**I ran this against the real code, not a hypothetical**, seeding 6
realistic tool descriptions (matching the actual style already in the
codebase) and 7 realistic user-intent paraphrases through the actual
`search_tool_registry()` function:

| Query (realistic paraphrase) | Result |
|---|---|
| "email the customer" | ✅ `smtp__send_email` |
| "notify them by mail" | ⚠️ `computer__notify` **and** `smtp__send_email`, tied — "mail" partial-matches "email" as a substring, an OS notification tool ranks alongside the actual email tool |
| "let the team know on chat" | ❌ **zero results** — none of {let, team, know, chat} overlap {channel, message, post, send, slack, user} |
| "put this on my calendar" | ✅ `google_workspace__create_calendar_event` **plus a spurious** `computer__notify` — because the substring `"put"` is literally inside `"computer"` (com-**put**-er), a pure coincidence of the substring-match rule |
| "schedule a meeting with the client" | ❌ **zero results** |
| "ping the customer about the invoice" | ❌ **zero results** |
| "look something up online" | ❌ **zero results** — should hit `web__search` |

**4 of 7 (57%) realistic paraphrases return nothing.** `query_tool_registry`
itself has no fallback behavior for an empty result beyond the plain string
`"No matching tools found for: ..."` (`format_registry_result`,
`tool_registry_service.py:436-461`) — the agent is left to either give up,
ask the user, or (the exact failure mode `tool_honesty_guard.py` was built
to catch, §3.10) claim it doesn't have the capability at all.

The one existing test of this path
(`server_modules/tests/test_mcp_tool_calling_wiring.py:167`) only checks
`"search notion pages"` and `"notion"` — both of which trivially contain the
tool's own name/keywords. **There is no adversarial or paraphrase test
anywhere in the repo for `search_tool_registry`.** The team has validated
the happy path and nothing else.

Anthropic's own answer to this exact problem is already GA:
`tool_search_tool_bm25_20251119` does real BM25 ranking server-side, or the
documented "Custom tool search implementation" pattern
(`tool_result` → `tool_reference` blocks) supports swapping in embeddings
while keeping our own registry/permission logic. Either is a strict upgrade
over the current substring scorer.

### 3.2 No `strict: true`, no `additionalProperties: false`, anywhere

Confirmed by grep across all three files audited — zero hits for `strict`
or `additionalProperties`. Nothing in our tool population gets Anthropic's
schema-conformance guarantee. For tools with structured/nested inputs
(`update_plan`'s task array, `hardware__action`'s free-text `action` field
describing an entire sub-API, any MCP tool's arbitrary `input_schema`), nothing
stops the model from emitting a plausible-but-invalid payload.

### 3.3 Description quality is inconsistent by tier, not by design

See §2. The always-on tier (12 tools, hand-tuned) is reasonable; the
long-tail local/connector tier (dozens of tools, one-sentence descriptions,
several missing per-field descriptions entirely —
`computer__applescript`'s `script` param, `computer__click`'s `x`/`y`/`text`)
is exactly Anthropic's documented "poor description" anti-pattern, at scale.

### 3.4 Twelve memory tools with overlapping purposes

`connector_id="memory"` appears 12 times in `skills_service.py` (grep
count): `memory_search, memory_write, memory_read, memory_get,
memory_update, memory_stage_edit, memory_apply_edit,
memory_append_daily_note, memory_stage_consolidation,
memory_consolidate_daily_notes, memory_list_versions,
memory_rollback_version` (line numbers 719, 740, 763, 779, 797, 817, 842,
863, and three more later in the same block). `memory_read` and `memory_get`
in particular are nearly indistinguishable from their descriptions alone
("Read a file from the agent's memory directory" vs "Read a small excerpt
from MEMORY.md... after memory_search identifies the file and lines") —
this is precisely the "separate tool for every action" pattern Anthropic's
guide says to consolidate ("group them into a single tool with an `action`
parameter... reduce selection ambiguity").

### 3.5 The file's own comments don't agree with its own code

`tool_registry_service.py:5-6` (module docstring): "the agent receives **8**
always-on tools plus `query_tool_registry`" (→ 9). `tool_registry_service.py:366`
(`build_always_on_tool_definitions` docstring): "These **~11** tools are
injected into every inference request." The actual `ALWAYS_ON_TOOL_NAMES`
frozenset (`tool_registry_service.py:26-39`) has **12** entries. None of the
three numbers agree. Harmless on its own, but it means the file's other
self-descriptions (e.g. the "~87% / ~75% token savings" claim at
`tool_registry_service.py:11`) haven't been re-verified either, and
shouldn't be taken as measured without checking.

### 3.6 `input_examples` — a zero-cost, officially-recommended lever, unused everywhere

Zero uses across all three files. Highest-value targets: `update_plan`
(nested array-of-objects with an enum), `hardware__action` (an `action`
string that's actually selecting between dozens of different sub-APIs —
exactly the "format-sensitive parameter" case Anthropic calls out), and any
MCP tool with a non-trivial `input_schema`.

### 3.7 MCP tool descriptions pass through with only truncation, no curation

`mcp_registry_service.py:264-311` (`_normalize_tool_payload`) truncates
`description` to 500 chars and does nothing else to it.
`list_workspace_mcp_direct_tool_payloads()` (`mcp_registry_service.py:995-1053`)
folds the server label and tool label into the description
(`f"[{server_label}] {tool_label}: {tool_description}"`,
line 1035-1039) purely so the keyword extractor picks them up — a
compensating hack for §3.1's weak search, not a quality control. Whatever
description quality a third-party MCP server ships is exactly what reaches
the model; there is no minimum-length check, no "when to use" enforcement,
no rewriting pass. Good: unapproved/unenabled MCP tools are excluded before
this point (`mcp_registry_service.py:1028`), so this is a *quality* gap, not
a *security* gap.

### 3.8 A real, if smaller, bug: keyword extraction doesn't strip punctuation

`_extract_keywords()`'s normalization regex is `re.sub(r"[_.\-]+", " ",
text)` (`tool_registry_service.py:59`) — it strips underscores, dots, and
hyphens but not commas. `web__search`'s description ("...titles, URLs, and
snippets") produces the keywords `titles,` and `urls,` with the comma
attached (reproduced live, §3.1 test harness). These never exact-match a
clean user token; they only ever fire via the substring rule, silently
weakening that tool's own discoverability.

### 3.9 `tool_honesty_guard.py` — a real safety net, but for a different failure mode

`server_modules/tool_honesty_guard.py` is well-built and worth keeping: it's
a structural, regex-based post-hoc check that a turn's final reply doesn't
contradict what tools actually did (`check_tool_reply_consistency`,
lines 113-128), with narrow, trace-anchored patterns for both directions —
denying a tool that succeeded (`_DENIAL_PATTERNS`, lines 52-66) and claiming
a lookup happened when nothing ran (`_CLAIM_PATTERNS`, lines 84-94). Its own
docstring (lines 1-25) documents *why* it exists: a prompt-only fix wasn't
reliable, live-testing on deepseek-chat still saw the model deny a search
that had just succeeded, "on some attempts, not others, same prompt." That's
strong independent evidence the founder's underlying worry is warranted —
just at the *self-reporting* layer, not the *tool-selection* layer this
audit otherwise focuses on. It does not, and is not designed to, help a
model find or correctly call the right tool in the first place (§3.1–§3.6).

### 3.10 Tool-count math against the 30–50 threshold

Always-on tier: 12 tools (well under any degradation threshold). Total
population: 49 `ToolDescriptor` literals in `skills_service.py` alone
(`grep -c`), plus a variable number of connector-app tools from
`build_direct_chat_tools()` (skills_service.py:1414, driven by which
connectors a workspace has enabled) plus a variable number of approved MCP
tools. The file's own docstring estimates "48+ tools" pre-registry
(`tool_registry_service.py:4`). **The base architecture — small hot set +
on-demand discovery — is the correct shape and matches Anthropic's own
recommended pattern for exactly this reason.** The problem is not tool
count; a well-instrumented workspace could cross 50-100 total tools and the
architecture would still be fine *if* the discovery step actually worked.
It's §3.1 that turns a sound architecture into an unreliable one.

---

## 4. Ordered fix list (worst offenders first)

Precise enough to hand to a build agent as-is; none of these require
touching the connector/capability config system, only the 4 audited files.

1. **Replace or augment `search_tool_registry`'s matcher with real
   semantic/BM25 ranking.** Two viable paths, not mutually exclusive:
   (a) migrate the two-tier system onto Anthropic's server-side
   `tool_search_tool_bm25_20251119` + `defer_loading: true`, letting
   Anthropic do the ranking instead of our own substring scorer — this also
   picks up prompt-cache-safe `tool_reference` expansion for free; or
   (b) keep our own `query_tool_registry` tool (needed regardless for the
   credential-note / availability-payload logic at
   `tool_registry_service.py:283-346`, which is bespoke to us) but replace
   its scoring internals with an embedding-similarity search over
   `tool_name + description + keywords`, using the documented
   `tool_reference`-block escape hatch if migrating to native tool search
   later. Either path directly fixes the 4-of-7 empirical failure rate in
   §3.1. This is the "semantic search upgrade" the task asked to weigh in
   on — it is warranted, not optional, given the reproduced failure rate.
2. **Add an adversarial/paraphrase test suite for `search_tool_registry`**
   before or alongside (1), so the fix is measurable. Extend
   `server_modules/tests/test_mcp_tool_calling_wiring.py`'s pattern (or a
   new file) with ~20-30 paraphrase queries per representative tool,
   asserting the intended tool appears in results — not just the two
   trivial exact-echo queries that exist today (line 167).
3. **Rewrite the long-tail tool descriptions to Anthropic's 3-4-sentence
   bar**, starting with the highest blast-radius tools: `shell__exec`
   (destructive, one clause, no guardrail language), `hardware__action`'s
   `action`/`arguments` free-text fields (currently describing an entire
   sub-API in prose with no schema enforcement), `computer__click` /
   `computer__applescript` (undocumented parameters). Use the `get_weather`
   /`get_stock_price` good-example shape from Define tools §1.1 as the
   template: what it does, when to use it, when not to, what each param
   means, what it returns.
4. **Add `strict: true` + `additionalProperties: false` to every
   `ToolDescriptor`'s emitted schema** (the dataclass has no such field —
   this is a new field on `ToolDescriptor` at `skills_service.py:37-51` plus
   a corresponding `required`/`additionalProperties` pass wherever
   `parameters` dicts are hand-written). Pair with `tool_choice: {"type":
   "any"}` wherever a caller currently needs to guarantee a tool call.
5. **Consolidate the 12 memory tools** toward Anthropic's `action`-parameter
   pattern — at minimum, merge `memory_read`/`memory_get` (near-duplicate
   purposes) and give the remaining set crisp, mutually-exclusive
   descriptions of when each applies.
6. **Fix `_extract_keywords`'s punctuation stripping**
   (`tool_registry_service.py:59`) to also strip commas/semicolons/colons —
   a one-line regex change, immediately improves every tool whose
   description uses a comma-separated list (this alone would have fixed
   `web__search`'s "titles," / "urls," junk keywords from §3.8).
7. **Reconcile the three disagreeing tool-count comments**
   (`tool_registry_service.py:5-6`, `:11`, `:366`) with the real
   `ALWAYS_ON_TOOL_NAMES` count (12) and re-measure the "~87%/~75% token
   savings" claim at line 11 rather than carrying it forward unverified —
   cheap to do, and it's the kind of small drift that erodes trust in the
   rest of the file's comments.
8. **Add `input_examples` to `update_plan` and `hardware__action` at
   minimum** — both have the "nested object / format-sensitive parameter"
   shape Anthropic calls out as the highest-value use case for the field.
9. **Add a minimum-description-length / "when to use" lint for MCP tool
   ingestion** in `mcp_registry_service.py:264-311` — even a soft warning
   surfaced to whoever approves a new MCP server's tools would stop
   low-quality third-party descriptions from silently degrading the
   registry's discoverability further as more MCP servers get connected.

---

## 5. Verdict

Two-tier design (12 always-on + keyword-discovered rest) is architecturally
sound and matches Anthropic's own pattern for large tool surfaces — that
part is not the risk.

The risk is that the *only* path to everything outside those 12 tools is
`search_tool_registry`'s plain token/substring overlap
(`tool_registry_service.py:237-289`), which I ran directly against 7
realistic user-intent paraphrases and got **zero tool results on 4 of them**
("let the team know on chat," "schedule a meeting with the client," "ping
the customer about the invoice," "look something up online") — plus a
coincidental false positive (`computer__notify` for a calendar request,
because "put" is a substring of "computer"). The one existing test only
checks queries that already contain the target tool's own name. So: no, we
are not sure the model will use our tools correctly — for anything outside
the always-on 12, correctness currently depends on the user's or model's
wording accidentally sharing word-stems with whatever the tool's original
author happened to type.

**Three worst reliability risks, in order:**
1. `search_tool_registry` is naive keyword overlap, not semantic search —
   empirically misses 4/7 realistic paraphrases, silently returning nothing
   (`tool_registry_service.py:237-289`).
2. Tool description quality is a coin-flip outside the hand-tuned always-on
   set — most local/connector tools are exactly Anthropic's documented
   "poor description" anti-pattern (one clause, no when/when-not, several
   undocumented parameters) (`skills_service.py`, §2/§3.3).
3. Zero schema-conformance guarantees anywhere (`strict`,
   `additionalProperties`, `input_examples` all unused) — nothing stops a
   plausible-but-invalid tool call from being emitted, especially for
   `update_plan`'s nested schema and `hardware__action`'s free-text
   sub-API-selector field (§3.2, §3.6).
