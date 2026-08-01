# Trends Video Pipeline

Daily Google Trends → narrated video → YouTube upload, with a local tracking dashboard.

For each country in `config/countries.yaml`, the pipeline runs at **9:00 PM local time**, fetches trending searches, enriches them with news, generates a narrated video, and uploads to your YouTube channel.

## Features

- Config-driven country list (add/remove countries in YAML)
- Per-country timezone scheduling (9pm local)
- Google Trends + Google News RSS enrichment
- edge-tts narration + MoviePy video rendering
- YouTube resumable upload with OAuth
- Local web dashboard at `http://127.0.0.1:8080`

## Prerequisites (Windows)

1. **Python 3.11+** — `winget install Python.Python.3.12`
2. **FFmpeg** — `winget install Gyan.FFmpeg`
3. **Git** — for clone/push

## Quick Start

```powershell
git clone https://github.com/YOUR_USERNAME/trends-video-pipeline.git
cd trends-video-pipeline
.\scripts\setup-windows.ps1
```

### YouTube OAuth setup

1. Create a project in [Google Cloud Console](https://console.cloud.google.com/)
2. Enable **YouTube Data API v3**
3. Create OAuth 2.0 Desktop credentials → download as `secrets/client_secrets.json`
4. Run the auth flow:

```powershell
.\.venv\Scripts\python.exe -m src.youtube.auth
```

5. Copy the printed `YOUTUBE_REFRESH_TOKEN` into `.env`

### Run the app

```powershell
.\scripts\run.ps1
```

Open **http://127.0.0.1:8080** to view runs, trigger manual jobs, and see YouTube links.

## Manual pipeline run

```powershell
# Full run for US
.\.venv\Scripts\python.exe -m src.pipeline --country US

# Mock data (no network) — good for testing video render
.\.venv\Scripts\python.exe -m src.pipeline --country US --mock --skip-upload
```

## Adding countries

Edit `config/countries.yaml`:

```yaml
countries:
  - code: US
    name: United States
    timezone: America/New_York
    trends_geo: US
    trends_pn: united_states
    language: en
    youtube_tags: [trends, news, usa]
```

| Field | Description |
|-------|-------------|
| `code` | ISO country code |
| `name` | Display name for titles and narration |
| `timezone` | IANA timezone for 9pm scheduling |
| `trends_geo` | Google Trends geo parameter |
| `trends_pn` | pytrends pn fallback parameter |
| `language` | TTS language (maps to voice in `pipeline.yaml`) |
| `youtube_tags` | Tags appended on upload |

Restart the app after changes.

## Configuration

- `config/countries.yaml` — country list
- `config/pipeline.yaml` — trends count, video settings, TTS voices, web port
- `.env` — secrets (see `.env.example`)

## Dashboard

| URL | Description |
|-----|-------------|
| `/` | Run history, stats, manual trigger buttons |
| `/runs/{id}` | Run detail: trends, news, script, YouTube link |
| `POST /api/trigger/{code}` | Start a run via API |
| `GET /api/countries` | Countries + next scheduled times |

## Keep running after reboot (optional)

Create a Windows Task Scheduler task:
- **Trigger:** At log on
- **Action:** `powershell -File C:\path\to\trends-video-pipeline\scripts\run.ps1`

> **Note:** If your PC sleeps at 9pm, scheduled jobs will be missed. Disable sleep during scheduled hours or run on a VPS.

## Project structure

```
config/          YAML configuration
src/
  trends/        Google Trends fetcher
  news/          News RSS enrichment
  script/        Narration generator
  audio/         edge-tts
  video/         MoviePy renderer
  youtube/       OAuth + upload
  web/           FastAPI dashboard
  db/            SQLite run store
  scheduler.py   APScheduler 9pm jobs
  pipeline.py    Orchestrator
  main.py        Entry point
scripts/         Windows setup + run scripts
output/          Generated videos (gitignored)
secrets/         OAuth credentials (gitignored)
```

## Environment variables

| Variable | Required | Description |
|----------|----------|-------------|
| `YOUTUBE_CLIENT_SECRETS` | Yes | Path to OAuth client JSON |
| `YOUTUBE_REFRESH_TOKEN` | Yes | Refresh token from auth flow |
| `SKIP_YOUTUBE_UPLOAD` | No | Set `true` to skip upload during dev |
| `OPENAI_API_KEY` | No | Optional future script polish |
| `NEWSAPI_KEY` | No | Optional news provider |

## License

MIT
