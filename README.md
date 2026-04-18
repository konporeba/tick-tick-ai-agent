# TickTick AI Agent

A Python agent that runs on a GitHub Actions cron schedule, fetches unprocessed tasks from an allowlist of TickTick projects, and uses Claude (Haiku 4.5) to enrich them — improving titles, descriptions, priorities, and tags.

## How it works

1. Fetches tasks from allowed TickTick projects every 15 minutes.
2. Filters out completed tasks and tasks already processed (identified by an emoji prefix in the title).
3. Sends unprocessed tasks to Claude in batches of up to 10.
4. Claude adds an emoji prefix, improves the title, generates/rewrites the description, infers priority, and assigns tags.
5. Updates each task in TickTick via the Open API.

The emoji prefix is the idempotency marker — once a task has been processed, it won't be touched again.

## Setup

### 1. Clone and install dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure allowed projects

Edit `config/allowed_projects.yaml` and replace the placeholder IDs with your real TickTick project IDs. You can find them in the TickTick web app URL when viewing a project, or by calling the `/project` API endpoint.

### 3. Set up TickTick OAuth2

TickTick uses OAuth2. You need to:
1. Register an app at [developer.ticktick.com](https://developer.ticktick.com).
2. Run the one-time OAuth flow locally to get a refresh token.
3. Store the credentials as GitHub Actions secrets (see below).

### 4. Configure secrets

In your GitHub repository, go to **Settings → Secrets and variables → Actions** and add:

| Secret | Description |
|---|---|
| `TICKTICK_CLIENT_ID` | OAuth app client ID |
| `TICKTICK_CLIENT_SECRET` | OAuth app client secret |
| `TICKTICK_REFRESH_TOKEN` | Long-lived refresh token from the OAuth flow |
| `ANTHROPIC_API_KEY` | Your Anthropic API key |

### 5. Local development

Copy `.env.example` to `.env` and fill in your credentials:

```bash
cp .env.example .env
```

Then run the agent locally:

```bash
python -m src.main
```

## Project structure

```
.github/workflows/agent.yml   GitHub Actions workflow (cron + keep-alive)
src/
  main.py                     Orchestration entry point
  ticktick_client.py          TickTick API client (OAuth2, CRUD, rate limiting)
  agent.py                    Claude API call, prompt assembly, response parsing
  filters.py                  Task filtering (emoji, completed, allowlist)
  config.py                   Loads env vars and YAML config files
prompts/system_prompt.md      Claude system prompt (edit to tune behavior)
examples/
  few_shot.yaml               5 before/after examples injected into every call
  eval_set.yaml               12 edge-case examples for eval scoring
tests/
  test_filters.py             Unit tests for filtering logic
  test_agent.py               Unit tests for prompt assembly and validation
  run_eval.py                 Eval runner: structural assertions + LLM-as-judge
config/
  allowed_tags.yaml           Allowed tag vocabulary
  allowed_projects.yaml       Project IDs the agent is permitted to touch
```

## Running tests

```bash
pytest tests/test_filters.py tests/test_agent.py -v
```

## Running the evaluation

```bash
python tests/run_eval.py
```

This runs the 12 eval examples through the full agent pipeline and outputs a score table with structural (Layer 1) and LLM-as-judge (Layer 2) results.

## Tuning the prompt

Edit `prompts/system_prompt.md` directly — no code changes needed. After editing, run the eval to check for regressions:

```bash
python tests/run_eval.py
```

## Cost estimate

Using Claude Haiku 4.5 with prompt caching:
- Runs with 0 new tasks: $0 (no Claude call made).
- Run with 3 new tasks: ~$0.006.
- Realistic monthly cost: $3–5 depending on task volume.

## GitHub Actions cron notes

- Scheduled at `*/15` (every 15 minutes). On public repos, GitHub Actions minutes are free.
- A keep-alive commit to `.last_run` is made on every scheduled run to prevent GitHub from disabling the workflow after 60 days of repo inactivity.
- Scheduled workflows can be delayed 10–30 minutes during high GitHub load — acceptable for this use case.
