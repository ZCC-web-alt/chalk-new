# Chalk Web Frontend

This directory contains the Next.js Web client for Chalk. The original v0 project is used only as the visual foundation; all scientific content is loaded from the FastAPI backend and no template demo results are shipped.

## Requirements

- Node.js with Corepack
- The Chalk Web backend running on `http://127.0.0.1:8000`

## Setup

```powershell
cd <copied-project>\frontend
Copy-Item .env.example .env.local
corepack pnpm install
```

## Run

```powershell
corepack pnpm dev --hostname 127.0.0.1 --port 3000
```

Open `http://127.0.0.1:3000`.

## Quality Checks

```powershell
corepack pnpm lint
corepack pnpm build
corepack pnpm test:e2e
corepack pnpm audit --audit-level=high
```

The browser client communicates only through `/api/*`. It never imports legacy Python code, receives API keys, or renders server file paths. Long-running analysis uses persistent Jobs so active work can be restored after a browser refresh.
