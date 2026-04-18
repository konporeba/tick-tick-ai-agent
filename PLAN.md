# TickTick AI Agent — Development Plan

## 1. Project Overview

Build a Python-based AI agent that runs on a GitHub Actions cron schedule, fetches unprocessed tasks from an allowlist of TickTick projects, and uses the Claude API to enrich them — improving titles, descriptions, priorities, and tags. The agent is idempotent: it only touches tasks that haven't been processed yet (identified by the absence of an emoji prefix in the title) and skips completed tasks.

---

## 2. Repository Structure

```
ticktick-agent/
├── .github/
│   └── workflows/
│       └── agent.yml                # GitHub Actions cron workflow
├── src/
│   ├── __init__.py
│   ├── main.py                      # Entry point — orchestrates the full run
│   ├── ticktick_client.py           # TickTick API client (OAuth2, CRUD)
│   ├── agent.py                     # Claude API call logic + prompt assembly
│   ├── filters.py                   # Task filtering (emoji, completed, allowlist)
│   └── config.py                    # Loads env vars, allowed_tags, allowed_projects
├── prompts/
│   └── system_prompt.md             # System prompt for Claude (standalone file, easy to iterate)
├── examples/
│   ├── few_shot.yaml                # 3–5 before/after pairs injected into every prompt call
│   └── eval_set.yaml                # 10–15 broader examples for testing prompt changes only
├── tests/
│   ├── test_filters.py              # Unit tests for emoji detection, completed/project filtering
│   ├── test_agent.py                # Tests prompt assembly, response parsing
│   └── run_eval.py                  # Script: runs eval_set through agent, scores with rubric
├── config/
│   ├── allowed_tags.yaml            # Single source of truth for allowed tags
│   └── allowed_projects.yaml        # Allowlist of project IDs the agent is permitted to touch
├── requirements.txt
├── .env.example                     # Template for local development secrets
└── README.md
```

---

## 3. Task Filtering

Tasks are passed through three filters before reaching Claude. All three must be satisfied.

### 3.1 Project allowlist

Only tasks belonging to a project ID listed in `config/allowed_projects.yaml` are considered. Tasks in any other project (shared projects, archived lists, work collaborator projects, etc.) are ignored entirely. This prevents the agent from silently rewriting tasks in lists where that's not wanted.

### 3.2 Incomplete tasks only

TickTick's `/project/{id}/data` endpoint returns both active and completed tasks. Completed tasks (`status == 2` per TickTick's schema) are filtered out before any further processing. There is no value in enriching tasks that are already done, and re-processing them wastes tokens.

### 3.3 Emoji prefix rule — the idempotency marker

The core mechanism that determines whether a task has been processed: **a task is considered processed if and only if its title starts with an emoji**. No external tracking, no hashes, no state file — just a visible convention.

```
Unprocessed:  "Prepare quarterly report"         → agent processes this
Processed:    "📊 Prepare quarterly report"       → agent skips this
Edge case:    "  📊 Some title"                   → strip whitespace first, then check
Edge case:    "(📊) Some title"                   → NOT emoji-prefixed, agent processes
```

Rules:
- Strip leading whitespace before checking.
- Only check the very first code point (after strip). Emojis mid-title don't count.
- Use the `emoji` Python package. Simple Unicode range checks miss compound emojis (flags, skin tone modifiers, ZWJ sequences). A robust check: `any(emoji.emoji_count(ch) > 0 for ch in title.lstrip()[:4])` — checks the first few code points so variation selectors and ZWJ sequences are handled.
- The agent's output always prepends exactly ONE emoji, which marks the task as "done" for future runs. This is what makes the system naturally idempotent.

### 3.4 Filtering flow

```
Fetch projects from TickTick
        │
        ▼
For each project in allowed_projects.yaml:
  Fetch tasks for that project
        │
        ▼
Drop completed tasks (status == 2)
        │
        ▼
Drop emoji-prefixed tasks (already processed)
        │
        ▼
Remaining tasks = agent workload
        │
        ▼
Send to Claude for enrichment
        │
        ▼
Update via TickTick API (title now has emoji → won't be picked up next run)
```

---

## 4. System Prompt — Structure & Guidelines

The system prompt lives in `prompts/system_prompt.md` as a standalone file. The agent code reads it at runtime and injects dynamic context (allowed tags, few-shot examples, task batch).

### Prompt Assembly Order (in `src/agent.py`)

1. **System prompt** (from `prompts/system_prompt.md`) — cached
2. **Allowed tags** (from `config/allowed_tags.yaml`) — cached
3. **Few-shot examples** (from `examples/few_shot.yaml`, as user/assistant message pairs) — cached
4. **User message** containing the batch of tasks to process (JSON array) — not cached, changes every run

See section 8 for details on prompt caching. Because blocks 1–3 are identical across every run, they're a perfect fit for the cache.

### System Prompt Content — What to Include

#### Section A: Role & Objective

You are a task management assistant. You receive a batch of tasks and return improved versions. Your job is to make tasks clear, actionable, and consistently formatted.

#### Section B: Language Rule

**Preserve the original language of each task.** If a title is in Polish, output in Polish. If in English, output in English. Do not translate. Detect language per-task, not globally.

#### Section C: Title Rules

- Must be informative, actionable, and ≤60 characters **excluding the emoji prefix and separating space**. (So a title of up to 60 visible content chars plus "🔧 " at the front is fine.)
- Prefix with exactly ONE emoji that reflects the task's content (not its priority).
- Do not change the meaning or context of the title — only improve clarity and formatting.
- If the title is already clear and under 60 chars, keep the wording and only add the emoji.

#### Section D: Description Rules

- **If empty:** Generate a concise description (2–4 sentences) covering key considerations, potential subtasks, or risks. Make it useful, not generic filler.
- **If present:** Rewrite for clarity and brevity. Preserve ALL key information. Remove redundancy, vague language, and noise. Do not drop specifics (names, dates, numbers, links).
- **Keywords block (always added):**
  - Append 2–5 keywords at the end of every description.
  - Keywords must relate to both the title and description content.
  - Format: wrap in `_` for italic (underscores render more reliably than asterisks across TickTick's web/desktop/mobile clients — verify during Phase 2 testing and switch to `*` only if underscores misbehave). Separate keywords with ` • `.
  - Separate from the main description with a horizontal rule.
  - Use exactly this pattern for the separator to ensure TickTick renders it correctly:

    ```
    {main description text}

    ---

    _keyword1 • keyword2 • keyword3_
    ```

  - The blank lines before and after `---` are critical — without them, TickTick does not render the separator.

#### Section E: Priority Rules

TickTick priority scale: 0 = none, 1 = low, 3 = medium, 5 = high. (2 and 4 are not valid TickTick priorities.)

Inference heuristics (include these in the prompt to guide the model):

| Signal in task | Suggested priority |
|---|---|
| Mentions a hard deadline within 48h | 5 |
| Uses urgency words (ASAP, urgent, critical, broken, blocked) | 5 |
| Routine/recurring work, no deadline | 1 |
| Learning, reading, exploration | 1 |
| Project deliverable, moderate timeline | 3 |
| Health or safety related | 3–5 |
| Fun, entertainment, wishlist | 0–1 |
| Ambiguous or insufficient context | default to 1 |

If the task already has a non-zero priority and the content supports it, do not change it.

#### Section F: Tag Rules

Allowed tags (inject from config at runtime):

```yaml
- analytics
- work
- data
- automation
- fabriq
- factory_screens
- digital_community
- ai
- reports
- sport
- learning
- home
- health
- car
- finance
- fun
```

Rules:
- **If tags are empty:** Assign 1–2 tags from the allowed list. At least one tag is mandatory.
- **If tags are present:** Keep all existing tags. Add at most 1 additional tag only if it strongly fits. Never remove existing tags.
- **Never invent new tags.** Only use tags from the allowed list.

#### Section G: Reasoning (Debug Log)

Include a `reasoning` string field (1–2 sentences) explaining what was changed and why. This field is used for logging only — never sent to TickTick. Zero-cost debugging when reviewing job logs.

#### Section H: Output Format

Return ONLY a valid JSON array. Each element has the following keys:

```json
{
  "project_id": "string (pass through unchanged)",
  "task_id": "string (pass through unchanged)",
  "title": "string (with emoji prefix)",
  "description": "string (with keywords block appended)",
  "priority": 0,
  "tags": ["string"],
  "reasoning": "string"
}
```

No explanations, no markdown fences, no text outside the JSON array.

> **Note:** The previous draft included a `changed` boolean. It's been removed. Because the emoji-prefix filter ensures only unprocessed tasks reach the agent, and the agent always adds an emoji, every output is by definition a change — the flag would always be `true` and provided no real signal.

---

## 5. Few-Shot Examples (`examples/few_shot.yaml`)

Create 5 example pairs covering these scenarios:

### Example 1 — Empty description, no tags (Polish task)

- **Raw:** title "Przygotować raport kwartalny", description empty, priority 0, tags empty
- **Expected:** emoji prefix, generated description in Polish, priority inferred (3), 1–2 tags assigned, keywords block appended
- **Why this matters:** Tests description generation, language preservation, tag assignment

### Example 2 — Verbose description, existing tags (English task)

- **Raw:** title "review the new automation dashboard we discussed last week with the team", long rambling description, priority 0, tags ["automation"]
- **Expected:** title shortened + emoji, description tightened, priority set, existing tag kept, maybe one tag added
- **Why this matters:** Tests title truncation, description rewriting without information loss, tag preservation

### Example 3 — Existing priority that should be preserved

- **Raw:** title "research competitors' pricing models", description empty, priority 5 (user manually set high), tags empty
- **Expected:** emoji added, description generated, **priority stays at 5** (user's signal respected), tags assigned
- **Why this matters:** Teaches the agent not to overwrite explicit user priority signals, even when heuristics would suggest something lower.

### Example 4 — High-priority task with urgency signals

- **Raw:** title "fix broken Fabriq integration ASAP", description mentions production is affected, priority 0, tags empty
- **Expected:** priority 5, tags ["fabriq", "automation"], description rewritten with context
- **Why this matters:** Tests priority inference from content signals

### Example 5 — Personal/lifestyle task

- **Raw:** title "find a good gym nearby", description empty, priority 0, tags empty
- **Expected:** emoji, description generated, priority 1, tags ["sport", "health"]
- **Why this matters:** Tests non-work task handling, correct low priority, appropriate tags from personal categories

### YAML Format for Each Example

```yaml
- scenario: "empty_description_polish"
  raw:
    project_id: "proj_001"
    task_id: "task_001"
    title: "Przygotować raport kwartalny"
    description: ""
    priority: 0
    tags: []
  expected:
    project_id: "proj_001"
    task_id: "task_001"
    title: "📊 Przygotować raport kwartalny"
    description: "Zebrać dane z ostatniego kwartału..."
    priority: 3
    tags: ["reports", "work"]
    reasoning: "Added emoji, generated description, set priority to 3 (project deliverable), assigned relevant tags."
```

---

## 6. Evaluation Set (`examples/eval_set.yaml`)

Use the same schema as `few_shot.yaml`. Include 10–15 examples covering additional edge cases:

- Task with description containing links and specific names (must be preserved)
- Task with only 1–2 words as title
- Task with all fields already filled correctly
- Mixed-language task (Polish title, English description)
- Task with existing priority that should NOT be changed
- Task with 3+ existing tags (should not add more)
- Task that could match multiple tags (tests tag relevance ranking)
- Task with very long description (>500 chars — tests brevity rewriting)
- Duplicate/near-duplicate of a few-shot example (tests consistency)
- Task with special characters, URLs, or code snippets in description

### Evaluation Script (`tests/run_eval.py`)

Exact field-by-field matching against the `expected` values won't work — even at temperature 0, two valid outputs can legitimately differ (emoji choice 📊 vs 📈, exact keyword wording, description phrasing). The eval needs two layers:

**Layer 1 — Structural assertions (deterministic, hard pass/fail):**
- Title starts with an emoji.
- Title length (excluding emoji + space) ≤ 60 chars.
- Priority is one of {0, 1, 3, 5}.
- Every tag is in the allowed list.
- Every tag from the input is preserved in the output.
- Description ends with a keywords block matching the expected format.
- `project_id` and `task_id` are unchanged from input.

**Layer 2 — LLM-as-judge (semantic, scored):**
- Send `raw`, `expected`, and `actual` to Claude with a rubric: "Did the output preserve the task's meaning? Are the tags relevant? Is the priority justified by the content?"
- Output a score per field plus a pass/fail verdict.
- Use a cheaper model (Haiku 4.5) for the judge to keep eval costs low.

The script should output a summary table to stdout with pass/fail counts per layer. This is a development tool for iterating on the prompt — not a CI gate initially.

---

## 7. TickTick API Client (`src/ticktick_client.py`)

### Authentication

TickTick uses OAuth2. For a headless agent running in GitHub Actions:

1. **One-time manual setup:** Run the OAuth flow locally to get a refresh token. Store it as a GitHub Actions secret.
2. **At runtime:** The client uses the refresh token to obtain a short-lived access token.
3. **Token refresh:** If the access token expires mid-run, the client refreshes automatically.

Secrets needed in GitHub Actions:
- `TICKTICK_CLIENT_ID`
- `TICKTICK_CLIENT_SECRET`
- `TICKTICK_REFRESH_TOKEN`
- `ANTHROPIC_API_KEY`

### API Surface — Important Constraint

**TickTick's Open API does not provide a "get all tasks" endpoint.** The available endpoints are:

| Endpoint | Method | Purpose |
|---|---|---|
| `/project` | GET | List all projects the user owns |
| `/project/{projectId}/data` | GET | Get a project with its tasks (active + completed) |
| `/task` | POST | Create a task |
| `/task/{taskId}` | POST | Update a task (title, content, priority, tags) |
| `/project/{pid}/task/{tid}/complete` | POST | Mark complete |

Getting the agent's workload therefore requires one `GET /project` call plus one `GET /project/{id}/data` call per allowed project. With an allowlist of ~5 projects, that's 6 API calls per run just to gather candidates.

### Inbox handling

The Inbox is a special project that may not appear in the `/project` response. Its ID for a given account is fixed and can be discovered once via the user info endpoint (or found in the TickTick web app URL when viewing the Inbox). If the Inbox is in the allowlist, hardcode its ID in `config/allowed_projects.yaml`.

### API Methods Needed

- `get_projects()` → returns list of project dicts
- `get_project_data(project_id)` → returns project + its tasks
- `update_task(task_id, payload)` → POST title, content, priority, tags
- `fetch_allowed_tasks()` → high-level method: iterates allowed projects, returns flat list of tasks filtered to `status != 2`

### Rate Limiting & Concurrency Safety

TickTick doesn't document rate limits. Implement:
- 200ms delay between update calls.
- Retry with exponential backoff on 429 responses.
- **Optimistic concurrency check:** before calling `update_task`, re-fetch the task and compare its `etag` or `modifiedTime` against the version the agent reasoned over. If it changed (the user edited it mid-run), skip the update and log a warning. This closes a low-probability race where the user edits a task while the agent has it in flight.

---

## 8. Agent Logic (`src/agent.py`)

### Model Choice: Haiku 4.5

Use **`claude-haiku-4-5-20251001`**. This task — enriching short task text with a well-specified rubric, few-shot examples, and a constrained output format — is comfortably within Haiku's capabilities. Pricing (as of April 2026):

| Model | Input | Output |
|---|---|---|
| Haiku 4.5 | $1 / MTok | $5 / MTok |
| Sonnet 4.6 | $3 / MTok | $15 / MTok |

That's 3x cheaper than Sonnet across the board. If the eval runs in Phase 3 show meaningful quality issues on specific task types, upgrade to Sonnet 4.6 then — but start cheap.

### Prompt Caching

System prompt, allowed tags, and few-shot examples are byte-identical across every run. Mark them with `cache_control: {type: "ephemeral"}` so subsequent runs read from cache at ~10% of standard input price.

Cache mechanics:
- 5-minute TTL. At a cron cadence of 10–15 minutes, the cache will usually be cold on each run, BUT cache writes cost only 1.25x base input — still a huge net win because the cached blocks are large (system prompt + ~5 few-shot pairs ≈ 2–3K tokens) relative to the per-task user message.
- If you move to a tighter cadence (e.g. `*/5`) or add a warmup call, cache hits become the common case and input cost drops further.

### Prompt Assembly

```python
def build_messages(tasks, system_prompt, few_shot, allowed_tags):
    # System block includes prompt + allowed tags, with cache_control on the full block
    system = [{
        "type": "text",
        "text": system_prompt + "\n\n" + render_allowed_tags(allowed_tags),
        "cache_control": {"type": "ephemeral"}
    }]
    # Few-shot as alternating user/assistant messages; cache_control on the LAST few-shot turn
    messages = []
    for i, pair in enumerate(few_shot):
        messages.append({"role": "user", "content": json.dumps([pair["raw"]])})
        last = (i == len(few_shot) - 1)
        assistant_content = [{
            "type": "text",
            "text": json.dumps([pair["expected"]]),
            **({"cache_control": {"type": "ephemeral"}} if last else {})
        }]
        messages.append({"role": "assistant", "content": assistant_content})
    # Real task batch
    messages.append({"role": "user", "content": json.dumps(tasks)})
    return system, messages
```

### Claude API Call

- Model: `claude-haiku-4-5-20251001`
- Temperature: 0 (deterministic output for consistency)
- Max tokens: scale with task count (~500 tokens per task, so ~5000 for a batch of 10). If batches grow or descriptions tend long, raise to 8000.
- Parse response: strip markdown fences if present, parse JSON array.
- Validate: each task in response must have all required fields, tags must be from allowed list, priority must be in {0, 1, 3, 5}, title must start with an emoji.

### Error Handling

- If JSON parsing fails: log the raw response, skip the batch, alert in job summary.
- If a task in the response has an unknown tag: strip it, log a warning.
- If the response contains fewer tasks than the input: log which tasks were skipped, do not update them. They'll be picked up next run.
- If a task's priority is not in {0, 1, 3, 5}: coerce to the nearest valid value, log a warning.

---

## 9. Main Orchestration (`src/main.py`)

```
1.  Load config (env vars, allowed_tags, allowed_projects, few_shot examples)
2.  Initialize TickTick client, refresh access token
3.  Fetch projects; intersect with allowed_projects allowlist
4.  For each allowed project: fetch tasks
5.  Filter: drop completed tasks (status == 2)
6.  Filter: drop tasks whose title starts with an emoji
7.  If no tasks to process → log "nothing to do", exit 0 (no Claude call made)
8.  Batch tasks (all at once if ≤10, otherwise chunk into groups of 10)
9.  For each batch: call Claude API, parse response, validate
10. For each valid task in response:
    a. Re-fetch the task from TickTick; check etag/modifiedTime for concurrent edits
    b. If unchanged since batch start → call TickTick update API
    c. If changed → skip, log warning (user edited mid-run)
    d. Log: task_id, old title → new title, reasoning
11. Output GitHub Actions job summary
```

Note step 7: on quiet days when nothing new has been added, the Claude API is not called at all. This is the main cost-control lever.

---

## 10. GitHub Actions Workflow (`.github/workflows/agent.yml`)

### Cron cadence

```yaml
on:
  schedule:
    - cron: '*/15 * * * *'    # Every 15 minutes
  workflow_dispatch:            # Manual trigger for testing
```

**Why 15 and not 10:** GitHub Actions bills job minutes rounded UP to the nearest whole minute. At `*/10`, that's 144 runs/day × 1 min = 4,320 min/month, well over the 2,000 min/month free allowance on private repos. `*/15` puts you at 96 runs/day = 2,880 min/month — still over if the repo is private, but if the repo is public, Actions is free regardless of minutes. **Recommendation: make the repo public** (there's nothing sensitive in the code — secrets stay in GitHub Actions secret storage). If it must be private, drop to `*/20` or `*/30`.

### Cron reliability caveats

GitHub Actions scheduled workflows have two well-known issues worth designing around:

1. **Delays and skips.** During high load, scheduled runs can be delayed 10–30 minutes or skipped entirely. For a task-enrichment agent this is acceptable — nothing depends on sub-hour latency.
2. **60-day dormancy auto-disable.** GitHub automatically disables scheduled workflows in repos with no commits for 60 days. Mitigation: the workflow's final step writes a timestamp to `.last_run` and commits it back to the repo. This keeps the repo "active" indefinitely with zero manual intervention.

### Full workflow

```yaml
name: TickTick AI Agent

on:
  schedule:
    - cron: '*/15 * * * *'
  workflow_dispatch:

jobs:
  run-agent:
    runs-on: ubuntu-latest
    timeout-minutes: 5
    permissions:
      contents: write   # needed for the keep-alive commit step

    steps:
      - uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.12'
          cache: 'pip'

      - name: Install dependencies
        run: pip install -r requirements.txt

      - name: Run agent
        env:
          TICKTICK_CLIENT_ID: ${{ secrets.TICKTICK_CLIENT_ID }}
          TICKTICK_CLIENT_SECRET: ${{ secrets.TICKTICK_CLIENT_SECRET }}
          TICKTICK_REFRESH_TOKEN: ${{ secrets.TICKTICK_REFRESH_TOKEN }}
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
        run: python -m src.main

      - name: Keep-alive commit (defeats 60-day dormancy)
        if: github.event_name == 'schedule'
        run: |
          date -u +"%Y-%m-%dT%H:%M:%SZ" > .last_run
          git config user.name "github-actions[bot]"
          git config user.email "github-actions[bot]@users.noreply.github.com"
          git add .last_run
          git diff --quiet --cached || git commit -m "keep-alive"
          git push

      - name: Upload run log
        if: always()
        uses: actions/upload-artifact@v4
        with:
          name: agent-log-${{ github.run_id }}
          path: logs/
          retention-days: 7
```

### Cost Estimate (realistic)

Using Haiku 4.5 with prompt caching:

- System + few-shot + tags ≈ 2,500 tokens (cached after warmup). Cache read: $0.10/MTok.
- Per-task input: ~200 tokens (uncached). Per-task output: ~400 tokens.
- **On runs with 0 new tasks** (the typical case): 0 Claude calls, $0.
- **On a run with 3 new tasks:** ~$0.006 (0.0025 MTok × $0.10 cached + 0.6K input × $1/MTok + 1.2K output × $5/MTok) ≈ **under 1 cent per active run**.
- **Realistic daily volume:** maybe 5–20 new tasks per day → **well under $0.20/day**, probably closer to $3–5/month.

The original $86/month estimate assumed every run processes tasks, which won't happen. Real cost will be dominated by actual task volume, not cron frequency.

---

## 11. Development Roadmap

### Phase 1 — Foundation (Week 1)

- [ ] Create repository, set up project structure
- [ ] Implement TickTick OAuth2 flow (manual token acquisition script)
- [ ] Build `ticktick_client.py` with `get_projects`, `get_project_data`, `update_task`, `fetch_allowed_tasks`
- [ ] Populate `config/allowed_projects.yaml` with real project IDs
- [ ] Test API connection locally; verify Inbox ID if Inbox is in allowlist

### Phase 2 — Agent Core (Week 1–2)

- [ ] Write system prompt in `prompts/system_prompt.md`
- [ ] Create 5 few-shot examples in `examples/few_shot.yaml`
- [ ] Build `agent.py` — prompt assembly with cache_control, Claude API call, response parsing, validation
- [ ] Build `filters.py` — emoji detection, completed-task filter, project allowlist filter
- [ ] Implement `main.py` orchestration with etag concurrency check
- [ ] Test locally with real tasks; verify underscore vs. asterisk italic rendering in TickTick

### Phase 3 — Examples & Evaluation (Week 2)

- [ ] Create 10–15 eval examples in `examples/eval_set.yaml`
- [ ] Build `tests/run_eval.py` with structural assertions + LLM-as-judge scoring
- [ ] Run eval, iterate on system prompt based on results
- [ ] Decide: stay on Haiku 4.5 or upgrade to Sonnet 4.6 based on eval scores
- [ ] Add unit tests for filters and response parsing

### Phase 4 — Deployment (Week 2–3)

- [ ] Set up GitHub Actions workflow with keep-alive step
- [ ] Configure secrets in repository settings
- [ ] Test with `workflow_dispatch` trigger
- [ ] Enable cron schedule
- [ ] Monitor first 24h of runs via artifacts and job summaries

### Phase 5 — Refinement (Ongoing)

- [ ] Tune system prompt based on real-world results
- [ ] Add more eval examples from edge cases found in production
- [ ] Consider: notification on failures (GitHub Actions → email/Slack)
- [ ] Consider: weekly summary of all changes made (stored in repo as markdown)
- [ ] Consider: migration to Fly.io if cron timing becomes an issue

---

## 12. Configuration Reference

### `config/allowed_tags.yaml`

```yaml
allowed_tags:
  - analytics
  - work
  - data
  - automation
  - fabriq
  - factory_screens
  - digital_community
  - ai
  - reports
  - sport
  - learning
  - home
  - health
  - car
  - finance
  - fun
```

### `config/allowed_projects.yaml`

```yaml
# Only tasks in projects listed here will be considered for processing.
# Project IDs come from the TickTick /project endpoint (or from the URL in the web app).
# The Inbox has a special ID that may not appear in /project — fetch once and hardcode it here if needed.
allowed_projects:
  - id: "PLACEHOLDER_INBOX_ID"
    name: "Inbox"
  - id: "PLACEHOLDER_PROJECT_ID_1"
    name: "Personal"
  # ... add the rest of the IDs from your list
```

### `.env.example`

```
TICKTICK_CLIENT_ID=your_client_id
TICKTICK_CLIENT_SECRET=your_client_secret
TICKTICK_REFRESH_TOKEN=your_refresh_token
ANTHROPIC_API_KEY=your_api_key
```

---

## 13. Key Technical Decisions — Rationale

| Decision | Rationale |
|---|---|
| Emoji prefix as "processed" marker | Simple, visible to the user, no external state needed. The agent is naturally idempotent. |
| Project allowlist | Prevents the agent from touching shared, archived, or collaborator projects. Explicit opt-in per project. |
| Drop completed tasks before the agent sees them | `/project/{id}/data` returns both active and completed; processing completed tasks is pure waste. |
| System prompt in a separate `.md` file | Easy to edit independently, version-controlled diffs, no code changes needed to tweak behavior. |
| Few-shot in YAML, not in the prompt file | Keeps the prompt clean. Examples can be swapped/extended without touching prompt logic. YAML supports comments for annotation. |
| Haiku 4.5 as default model | 3x cheaper than Sonnet for a task well within Haiku's capabilities. Upgrade only if eval shows it's needed. |
| Prompt caching on system + few-shot + tags | These blocks are identical on every run; caching cuts input cost by ~90% on cache hits. |
| Batch all tasks in one Claude call | ≤10 tasks fit easily in one call. Saves cost vs per-task calls. If batch grows, chunk into groups of 10. |
| No `changed` flag | Emoji-prefix filter guarantees the agent only sees unprocessed tasks, and every output adds an emoji — so "no change" is not a reachable state. The flag added complexity without carrying signal. |
| Optimistic concurrency check (etag) before update | Closes a low-probability race where the user edits a task while the agent has it in flight. Cheap insurance. |
| `reasoning` field in output | Zero-cost debugging. Stored in logs, never sent to TickTick. Invaluable when reviewing why the agent made a choice. |
| Temperature 0 for Claude | Deterministic output. Same task should produce same result across runs. |
| `*/15` cron + keep-alive commit | Balances freshness against the GitHub Actions free-tier minute budget on private repos, and defeats the 60-day dormancy auto-disable. |
| GitHub Actions over Fly.io | Zero cost (or near-zero), zero infrastructure, good enough timing for this use case. Easy migration path if needed later. |
