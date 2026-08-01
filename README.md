# Trends Video Pipeline

Daily Google Trends → witty narrated video with related image slides, stored **locally**, with a library dashboard grouped by date and country.

For each country in `config/countries.yaml`, the pipeline runs at **9:00 PM local time**, fetches **top 20** trending searches, enriches them with news + images, generates narration (Groq or template), and renders an MP4 under 10 minutes. YouTube upload is **manual for now**.

## Features

- Config-driven country list (add/remove countries in YAML)
- Per-country timezone scheduling (9pm local)
- Top 20 Google Trends + Google News RSS
- Related images (Wikimedia / Openverse / news og:image)
- Witty narration via free **Groq** LLM (template fallback without a key)
- edge-tts voiceover + MoviePy slides (NVIDIA NVENC when available)
- Flexible length, hard-capped under 10 minutes
- Local video library at `http://127.0.0.1:8080` with play / download / delete

## Prerequisites (Windows)

1. **Python 3.11+** — `winget install Python.Python.3.12`
2. **FFmpeg** — `winget install Gyan.FFmpeg`
3. **Git** — for clone/push
4. **Groq API key** (optional, free) — [console.groq.com](https://console.groq.com/) for witty narration

## Quick Start

```powershell
git clone https://github.com/bastianjosekottekudy-cmyk/trends-video-pipeline.git
cd trends-video-pipeline
.\scripts\setup-windows.ps1
```

Add your free Groq key to `.env` (recommended):

```env
GROQ_API_KEY=gsk_...
```

Then:

```powershell
.\scripts\run.ps1
```

Open **http://127.0.0.1:8080** to browse the library and generate videos.

## Manual pipeline run

```powershell
.\.venv\Scripts\python.exe -m src.pipeline --country US
.\.venv\Scripts\python.exe -m src.pipeline --country US --mock
```

Videos land in:

```
output/
  YYYY-MM-DD/
    US/
      run_{id}/
        *.mp4
        images/
        slides/
        trends.json
        news.json
        script.txt
        manifest.json
```

## Adding countries

Edit `config/countries.yaml` and restart the app.

## Configuration

- `config/countries.yaml` — country list
- `config/pipeline.yaml` — `top_trends: 20`, `max_video_duration_sec: 570`, script/images settings
- `.env` — `GROQ_API_KEY` and optional secrets

### Witty narration (Groq)

1. Create a free account at [console.groq.com](https://console.groq.com/)
2. Create an API key
3. Set `GROQ_API_KEY` in `.env`
4. Keep `script.provider: groq` in `pipeline.yaml`

Without a key, the pipeline uses a solid template narration instead.

## Dashboard

| URL | Description |
|-----|-------------|
| `/` | Video library by date + country filters |
| `/runs/{id}` | Detail: player, trends, news, script |
| `/videos/{id}/file` | Stream local MP4 |
| `/videos/{id}/download` | Download local MP4 |
| `DELETE /api/runs/{id}` | Permanently delete run + files |
| `POST /api/trigger/{code}` | Start a generate run |

## Project structure

```
config/          YAML configuration
src/
  trends/        Google Trends RSS fetcher
  news/          News RSS enrichment
  images/        Related image fetcher
  script/        Groq / template narration
  audio/         edge-tts
  video/         MoviePy + NVENC renderer
  youtube/       OAuth + upload (future scope)
  web/           FastAPI library dashboard
  db/            SQLite run store
scripts/         Windows setup + run scripts
output/          Generated videos (gitignored)
```

## Environment variables

| Variable | Required | Description |
|----------|----------|-------------|
| `GROQ_API_KEY` | No | Free witty narration (template fallback if empty) |
| `YOUTUBE_CLIENT_SECRETS` | No (future) | Path to OAuth client JSON |
| `YOUTUBE_REFRESH_TOKEN` | No (future) | Refresh token from auth flow |
| `NEWSAPI_KEY` | No | Optional news provider |

## License

MIT
