# Tenderizer — frontend

React + Vite + TypeScript frontend for Tenderizer (Portal / Scout / Vault / Composer). Talks to the
FastAPI backend in `../src` via `VITE_API_BASE`; see `../TENDERIZER_HANDOFF.md` and
`../CLAUDE_CODE_BUILD.md` for the product/architecture handoff and `../DEPLOYMENT.md` for the
current production setup (Vercel + Railway).

## Local dev

```bash
npm install        # from the_scout/ (npm workspace root)
npm run dev:web     # or: npm --workspace frontend run dev
```

## Build

```bash
npm run build       # tsc -b && vite build
```
