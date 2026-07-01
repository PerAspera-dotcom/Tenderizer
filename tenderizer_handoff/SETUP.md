# Tenderizer — Local Stack Setup (the "package")

Goal: one monorepo that holds the existing engine, a FastAPI backend, and a React frontend, all
wired so `npm run dev` boots the whole thing. Do this **before** handing to Claude Code so it
starts from a working skeleton. Commands lead with **PowerShell (Windows)**; bash differences noted.

```
tenderizer/                 ← the package (one git repo)
├─ the_scout/               ← EXISTING engine, moved in unchanged
├─ api/                     ← NEW FastAPI backend (imports the_scout)
├─ web/                     ← NEW React + Vite + TS frontend
├─ docker-compose.yml       ← local Postgres
├─ package.json             ← root glue: runs api + web together
└─ .venv/                   ← one Python env for engine + api
```

---

## 0 · Prerequisites (install once, then verify)

- **Node 20+**, **Python 3.11+**, **Docker Desktop**, **Git**.

```powershell
node -v ; npm -v ; python --version ; docker --version ; git --version
```

If any is missing: Node → nodejs.org, Python → python.org (tick "Add to PATH"), Docker → docker.com.

---

## 1 · Create the repo and move the engine in

```powershell
mkdir C:\Users\Maximilian\Projects\tenderizer
cd C:\Users\Maximilian\Projects\tenderizer
git init
# copy your existing engine into the monorepo
Copy-Item -Recurse C:\Users\Maximilian\Projects\Tenderizer\the_scout .\the_scout
```

Add a `.gitignore` at the root:

```
.venv/
node_modules/
__pycache__/
*.pyc
.env
the_scout/data/*.db
dist/
```

---

## 2 · Make the engine an installable package

So `api` can `import the_scout`. Create `the_scout/pyproject.toml`:

```toml
[project]
name = "the_scout"
version = "0.1.0"
requires-python = ">=3.11"
# keep your existing runtime deps here (requests, pyyaml, openpyxl, etc.)

[tool.setuptools.packages.find]
where = ["src"]
```

> If your engine code lives directly in `the_scout/src/`, adjust `where` to match. The point is
> that `pip install -e ./the_scout` makes `import store, config, run` (or `from the_scout import …`)
> work from anywhere.

---

## 3 · Python env + FastAPI backend

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1        # bash/mac: source .venv/bin/activate
python -m pip install --upgrade pip
pip install -e .\the_scout          # engine, editable
pip install "fastapi[standard]" uvicorn sqlalchemy alembic psycopg2-binary apscheduler python-dotenv
pip freeze > api\requirements.txt
```

Create a hello-world `api/main.py` to prove the wiring:

```python
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import os

app = FastAPI(title="Tenderizer API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("ALLOWED_ORIGINS", "http://localhost:5173").split(","),
    allow_methods=["*"], allow_headers=["*"],
)

@app.get("/api/health-check")
def health_check():
    return {"status": "ok"}
```

Run it:

```powershell
uvicorn api.main:app --reload        # → http://localhost:8000/api/health-check
```

---

## 4 · Frontend package (React + Vite + TS)

```powershell
npm create vite@latest web -- --template react-ts
cd web
npm install
npm install @tanstack/react-query react-router-dom @clerk/clerk-react
cd ..
```

Frontend env — `web/.env`:

```
VITE_API_BASE_URL=http://localhost:8000
VITE_CLERK_PUBLISHABLE_KEY=pk_test_...      # from your Clerk dashboard
```

---

## 5 · Root glue — run everything with one command

This is what makes it feel like one package. Root `package.json`:

```json
{
  "name": "tenderizer",
  "private": true,
  "workspaces": ["web"],
  "scripts": {
    "dev": "concurrently -n api,web -c cyan,magenta \"npm run dev:api\" \"npm run dev:web\"",
    "dev:api": "uvicorn api.main:app --reload",
    "dev:web": "npm --workspace web run dev",
    "db:up": "docker compose up -d postgres",
    "db:down": "docker compose down"
  },
  "devDependencies": { "concurrently": "^9.0.0" }
}
```

```powershell
npm install                          # installs concurrently + web workspace
```

> Note: `npm run dev:api` uses the active venv's uvicorn — keep the venv activated in the terminal
> you run `npm run dev` from, or point the script at `.\.venv\Scripts\uvicorn.exe`.

---

## 6 · Local Postgres + migrations

Root `docker-compose.yml`:

```yaml
services:
  postgres:
    image: postgres:16
    environment:
      POSTGRES_USER: tenderizer
      POSTGRES_PASSWORD: dev
      POSTGRES_DB: tenderizer
    ports: ["5432:5432"]
    volumes: ["pgdata:/var/lib/postgresql/data"]
volumes:
  pgdata:
```

```powershell
npm run db:up                        # starts Postgres
cd api ; alembic init ..\alembic ; cd ..   # scaffold migrations (configure later)
```

Backend env — `api/.env` (loaded by python-dotenv; never commit it):

```
DATABASE_URL=postgresql://tenderizer:dev@localhost:5432/tenderizer
ALLOWED_ORIGINS=http://localhost:5173
CLERK_SECRET_KEY=sk_test_...
```

---

## 7 · Verify the skeleton (the "it's alive" check)

```powershell
# engine still healthy
cd the_scout ; pytest -q ; cd ..        # expect 52 passing

# whole stack up
npm run db:up
.\.venv\Scripts\Activate.ps1
npm run dev
```

You should have: Postgres on 5432, API on **:8000** (`/api/health-check` → `{"status":"ok"}`),
web on **:5173**. Open the web URL — Vite's starter page loads. That's the green light.

```powershell
git add -A ; git commit -m "Scaffold monorepo: engine + api + web + postgres"
```

---

## 8 · Hand over

Commit, then point Claude Code at the repo with **`CLAUDE_CODE_BUILD.md`** + **`TENDERIZER_HANDOFF.md`**.
It picks up from this skeleton at Phase 1 (engine §5 additions) — the plumbing is already proven.

**Deploy later (not now):** `web` → Vercel (set `VITE_API_BASE_URL` to the prod API), `api` +
Postgres → Railway/Render (Dockerfile around the same venv install). Own the domain + DB yourself.
