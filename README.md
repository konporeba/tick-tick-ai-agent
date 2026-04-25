# TickTick AI Agent

A Python agent that runs on a Raspberry Pi via cron, fetches unprocessed tasks from an allowlist of TickTick projects, and uses Claude (Haiku 4.5) to enrich them — improving titles, descriptions, priorities, and tags.

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
2. Run the one-time OAuth flow to get an access token (opens a browser window — Chrome on Raspberry Pi works fine):
   ```bash
   python get_refresh_token.py
   ```
3. The token is written to your `.env` file automatically.

### 4. Configure credentials

Copy `.env.example` to `.env` and fill in your credentials:

```bash
cp .env.example .env
```

| Variable | Description |
|---|---|
| `TICKTICK_ACCESS_TOKEN` | Access token from the OAuth flow |
| `ANTHROPIC_API_KEY` | Your Anthropic API key |

### 5. Schedule with cron

Add the following entry to your crontab (`crontab -e`) to run the agent every 15 minutes between 06:00 and 23:45:

```
*/15 6-23 * * * cd /path/to/tick-tick-ai-agent && python -m src.main >> logs/agent.log 2>&1
```

To run the agent manually:

```bash
python -m src.main
```

## Project structure

```
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

## Cron notes

- Scheduled at `*/15 6-23` (every 15 minutes, 06:00–23:45). Adjust the hours to match your timezone.
- Raspberry Pi cron is reliable for this workload — no cold-start delay or platform-imposed dormancy limits.
- Logs are written to `logs/agent.log` and a run summary to `logs/job_summary.md` after each execution.
