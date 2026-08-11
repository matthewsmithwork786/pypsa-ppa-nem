# Multi-year Zenodo plant data + tiered SLA constraints

Working branch: `feature/multiyear-plants-and-tiered-sla` off `feature/energy-first-results-deploy`.
Orchestrated by Claude Code (sub-sessions run via `opencode`) per
`IMPLEMENTATION_PLAN_multiyear_and_sla.md` (full text pasted below the answers).

## Answers to §2.2 open questions (as of 2026-08-11)

1. **Data span** — not asked; WP2 discovers it empirically per the original plan
   (do not hard-code 20 years).
2. **Zenodo record** — Hanan owns the Zenodo account and will create the record
   and upload token himself. WP4 is built and tested against a local HTTP
   fixture (per §4.5 of the plan) until Hanan hands over a real record ID /
   token; the real publish step is deferred, not blocking WP2/WP3/WP4 dev work.
3. **1+1 plants** — confirmed. Exactly one wind + one solar DUID, as today.
   Portfolio (N-plant) support is out of scope for this round.
4. **Acquisition-machine disk** — the acquisition machine (this GCP VM) has
   only ~36 GB free on `/`, not the ~952 GB assumed in `AGENTS.md` (which was
   written for a different machine at `/home/hanan/projects/pypsa-ppa-nem`).
   Resolution: `scripts/fetch_nem_availability.py` must process one
   (year, month) at a time — nemosis already pulls month-by-month — writing
   each month's slice straight into the compact per-DUID parquet accumulator
   and deleting the raw CSV/MMS archive for that month immediately after
   extraction, rather than retaining a full-year or multi-year raw archive.
   Amended into WP2's spec below.

## Environment facts specific to this deployment

- Repo checked out at `/home/matthewsmithwork786/pypsa-ppa-nem` on GCP VM
  `pypsa-ppa-dev`. `gh` and `git push` both work (origin over HTTPS,
  `matthewsmithwork786` account).
- `opencode` (v1.18.15) works out of the box on this VM with no separate auth
  step needed for the `opencode/*` models (`big-pickle`, `deepseek-v4-flash-free`,
  `mimo-v2.5-free`, others).
- Two Cloud Run services exist: `pypsa-ppa-nem-git` (auto-deploys from `main`
  only, via Cloud Build trigger `7b701e4e-25f3-4aed-9be6-3f6327e49b0f` in
  `europe-west1`) and `pypsa-ppa-explorer` (no auto-trigger; deployed manually
  with `gcloud builds submit --config cloudbuild.yaml`). Neither tracks this
  feature branch automatically — live testing of this branch means an
  explicit manual `gcloud builds submit` to `pypsa-ppa-explorer`, done and
  announced by the orchestrator (Claude), not silently automated.
- Disk: 36 GB free on `/`, 15 GB RAM, 4 vCPU. `/tmp` is a 6 GB tmpfs — see
  AGENTS.md §1, unchanged.

---

The full work-package definitions, dependency graph, and merge/review gates
are in `docs/IMPLEMENTATION_PLAN_multiyear_and_sla.md` (§3–§5 there are
authoritative and are not restated here).
