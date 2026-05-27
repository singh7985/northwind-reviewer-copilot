# Deploy

The case study runs locally end-to-end with a single command. There is no cloud
hosting in this setup — everything is reproducible from the repo.

## One-command stack

Prereqs: Docker Desktop running, ports 3000/8000/5432 free.

```bash
# from repo root
docker compose -f docker-compose.prod.yml up --build

# optional: enable LLM extraction / adjudication / Q&A
OPENAI_API_KEY=sk-... docker compose -f docker-compose.prod.yml up --build
```

What you get:

| URL | Service |
|---|---|
| http://localhost:3000 | Next.js reviewer UI |
| http://localhost:8000 | FastAPI backend |
| http://localhost:8000/docs | OpenAPI explorer |
| http://localhost:8000/health | liveness, reports `llm_enabled` |
| postgres://northwind:northwind@localhost:5432/northwind | (not exposed in prod compose; internal only) |

First-time setup of the corpus (run once after the stack is up):

```bash
curl -X POST http://localhost:8000/admin/seed
```

This creates the schema, loads the 5 sample employees, parses the policy PDFs
into clauses, and embeds them into `pgvector`. Idempotent — safe to re-run.

Then open <http://localhost:3000> and click any of the **demo submission** buttons
to ingest a sample expense report.

### Deterministic-only mode

If `OPENAI_API_KEY` is not set, the system runs in deterministic-only mode:

- Receipt extraction uses regex heuristics (`MONEY_RE`, `ALCOHOL_TERMS`, premium
  rideshare patterns, category-from-filename hints).
- The adjudicator returns a verdict built from the rule findings and the top
  retrieved clauses, without any LLM call.
- Policy Q&A returns the top retrieved clause verbatim with `refused=true` for
  questions outside the policy library.

This mode is what CI runs against, so every PR proves the safety floor works
without any third-party dependency.

## CI

`.github/workflows/ci.yml` runs on every push and PR. Three jobs:

1. **backend** — installs deps, byte-compiles every Python source, loads the
   FastAPI app to assert routes register, and runs deterministic-engine asserts
   on the two canonical cases (`alcohol_solo_travel` → reject, `meal_cap_exceeded`
   → flag).
2. **frontend** — `npm ci` + `next build` with TypeScript strict mode.
3. **docker** — builds both production images, brings up the full
   `docker-compose.prod.yml` stack, waits for `/health` and `/`, seeds the
   corpus, and tears down.

A failed CI run blocks merge. The smoke test in particular guarantees that the
images themselves boot and talk to each other — not just that the source compiles.

## Notes for a real production deploy

This repo is intentionally local-first. If you wanted to host it:

- **Backend**: any container host (Fly, Render, Cloud Run, ECS). The image
  exposes `:8000`, takes `DATABASE_URL` and `OPENAI_API_KEY` from the
  environment, and is stateless — scale horizontally behind a load balancer.
- **Database**: any managed Postgres ≥ 14 with the `vector` extension
  available (Supabase, Neon, RDS with `pgvector`). Run `/admin/seed` once
  per environment. Embeddings are cheap; re-embed on policy changes.
- **Frontend**: `next build` with `output: "standalone"` (already set) and
  deploy the resulting Node server, or use Vercel and point
  `NEXT_PUBLIC_API_BASE` at your backend URL.
- **Secrets**: `OPENAI_API_KEY` is the only sensitive value. Inject via the
  platform's secret manager; never bake into the image.
- **Cost / scale**: see the [README](README.md#cost-and-scaling) section.

---

## One-click deploy to Render

The repo ships a [`render.yaml`](render.yaml) Blueprint that provisions the
whole stack — Postgres (with `pgvector`), the FastAPI backend, and the Next.js
frontend — from your existing Dockerfiles, on Render's free tier.

### Steps

1. **Push the repo to GitHub** (or GitLab/Bitbucket — anything Render can read).

2. **Create the Blueprint.** In Render → *New +* → *Blueprint* → connect the
   repo. Render reads `render.yaml` and shows you the three services it's
   about to create (`northwind-db`, `northwind-api`, `northwind-web`). Click
   *Apply*.

3. **Set your OpenAI key (optional).** Open the `northwind-api` service →
   *Environment* → set `OPENAI_API_KEY` (or leave blank to run the offline /
   deterministic path — the eval harness and demo still work without it).

4. **Wait for the first build.** Free-tier first builds take ~6–10 minutes
   because of the Docker layer cold start. Subsequent deploys are cached.

5. **Seed the corpus.** Once `northwind-api` is live:

   ```bash
   curl -X POST https://northwind-api.onrender.com/admin/seed
   ```

   This creates the schema, runs `CREATE EXTENSION IF NOT EXISTS vector`,
   loads employees, parses the 8 policy PDFs into clauses, and embeds them.
   Idempotent — safe to re-run.

6. **Open the app.** <https://northwind-web.onrender.com>

### Notes

- **Cold start.** Free-tier services sleep after 15 min idle and take ~30 s
  to wake. Fine for a recruiter demo; upgrade to Starter ($7/mo per service)
  to eliminate it.
- **Service names.** If `northwind-api` / `northwind-web` are already taken
  globally, Render will suffix them. Update the matching URLs in
  `render.yaml` (`CORS_ORIGINS` on the API, `NEXT_PUBLIC_API_BASE` on the
  web service) and redeploy — the frontend bakes its API URL at build time,
  so it must be correct *before* `npm run build`.
- **Free Postgres expires after 90 days.** Upgrade the database to a paid
  plan or recreate it before then.
- **Render delivers `DATABASE_URL` as `postgresql://...`.** The backend's
  `config.py` auto-rewrites this to the `postgresql+psycopg://` driver scheme
  we actually depend on, so no manual munging is required.
