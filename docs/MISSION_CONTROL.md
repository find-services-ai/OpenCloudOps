# Mission Control: running Axiom Ventures on OpenCloudOps

Status: prototype on a branch. Nothing here is deployed, and nothing in it can
create cloud resources or spend money. Facts were checked on 2026-11-24; prices
move, so re-check a figure before you commit to it.

## 1. What it is

One screen that shows the whole firm the way a large consultancy is organised,
and every bot in it: what it is doing now, whether it is overdue, and what needs
you next.

```
            ┌────────────────────────── Mission Control console (/console) ─────────────────────────┐
            │  Practices x bots, live status        │  Next moves (ranked, each with its evidence) │
            └───────────────▲───────────────────────┴───────────────▲───────────────────────────────┘
                            │ GET /fleet (every 5 s)                 │ GET /fleet/next-moves
                  ┌─────────┴──────────────────────────────────────┴─────────┐
                  │  OpenCloudOps API  (FastAPI, same service as /plan, /iac)  │
                  │  fleet/firm.py   firm.yaml: HQ, offices, practices, bots   │
                  │  fleet/state.py  event store, status derived from the clock│
                  │  fleet/nextmove  rules over state + inventory.yaml         │
                  └─────────▲────────────────────────────────────────────────┘
                            │ POST /fleet/events  (bearer token; refuses all if unset)
     ┌──────────────────────┼───────────────────────────┬─────────────────────────────┐
  gos harness           OpenCloudOps agent graph     AgentOS core + packs        any bot or cron
  (ethos, PR #10        (orchestrator, iac-builder,  (mcp-idea-publisher,        (curl one line)
   gos emit: same        validator, sre-observer,     find.buyers.ai, surplus
   event schema)         release-executor)            engine ...)
```

Three rules hold the design together:

1. **Status is derived, never claimed.** A bot reports what it did. The console
   decides from the clock whether that is recent enough (idle, late, silent).
   A bot with code that has never reported shows "never reported", because that
   is the truth.
2. **Next moves are rules, not a model.** Each one names its evidence (an event,
   a repo fact with the date it was checked) so you can check it before acting.
   Ranking is by who is blocked: a bot waiting on you comes first.
3. **No autonomy level above "draft for approval".** The firm model cannot even
   express an autonomous bot. Enforcement stays where it already is: the
   OpenCloudOps guard and gates, and the gos harness's section-8 rules (no
   trades, no payments, no infrastructure mutation).

## 2. The firm

Modelled on Deloitte, PwC and Thoughtworks: an Office of the Managing Partner,
offices by geography, practices that own delivery. `firm/firm.yaml` is the
source of truth; edit it and the console changes.

| Office | Role |
|---|---|
| Headquarters (US LLC), Naperville | Contracts, billing, approvals, partner office |
| India Delivery Centre | Build, test and run engagements |
| Home edge node (Pi 5) | Always-on worker for scheduled bots and private data |

| Practice | Bots today | Ready |
|---|---|---|
| Office of the Managing Partner | GOS harness, PR tribunal, Antigravity (personal lane) | 1 of 3 works |
| AI & Data Advisory | Client advisor, SOW architect, proposal reviewer, MVP bootstrap | 0 of 4 works (all partial) |
| Cloud & Platform Engineering | The five OpenCloudOps agents | 5 of 5 work |
| Trust, Risk & Governance | OpenDelegate, PII redaction gateway | both stubs |
| Ventures & Industry Solutions | find.buyers.ai, Amazon seller ops, surplus engine, DAO digest, pet grooming | 3 of 5 work |
| Firm Operations | AgentOS core, home NAS ops | both partial |

The weakest practice is the one that wins clients. Advisory has four bots and
none of them is finished, so the firm can deliver infrastructure better than it
can sell it. That is the first gap to close.

## 3. Repo inventory (from running the code)

| Line | Repo | State | Evidence |
|---|---|---|---|
| Platform | OpenCloudOps | **main broken**, branch works | main's zip is corrupt (41,545 bytes replaced, saved as text). Branch `claude/opencloudops-agentic-framework-unseh8`: 366 pass, 1 fails |
| Platform | OpenDelegate | stub | one-line README; design on a branch |
| Platform | finetune-llm | stub | PII redaction research only |
| Internal agents | ethos (GOS harness) | works | 100 pass, 1 skipped; 4 open PRs |
| Internal agents | mcp-idea-publisher (AgentOS) | partial | 111 pass, 1 fails |
| Internal agents | pi5-home-nas | partial | compose + scripts, no tests |
| Client delivery | mvp-monorepo | partial | pinned `pgserver==0.1.4` is not on the package index |
| Ventures | find.buyers.ai | works | 10 of 10 tests pass |
| Ventures | food-wastage | works | 20 of 20 pass |
| Ventures | amazon-seller-agentic-workflow | partial | dashboard builds; backend tests did not start; **public repo holds `users.json` with emails and password hashes** |
| Ventures | pet-gromming-uber-model | stub | README only |
| Personal | ai-trading-assistant | partial | 23 pass, 5 fail, 13 error |
| Personal | virtuosity, find-quantum-ai | stub | README only |
| Archive | leetcode | dead | README only, last commit 2025-07-10 |

## 4. Where to host it

### Recommendation: Pi 5 now, Google Cloud Run when clients depend on it, Cloudflare in front of both

- **GCP is the primary cloud.** It is where the existing work already points:
  the gos harness has a Cloud Run deploy path and Terraform, the ADK agents
  deploy with `adk deploy cloud_run`, and your sandbox project already has the
  agent APIs on. Cloud Run's free tier covers a console and a registry at this
  size.
- **The Pi 5 stays as the edge worker** for scheduled bots, the NAS and anything
  that should not leave the house. It costs nothing extra to run.
- **Cloudflare** for DNS, Tunnel and Access, so both are reachable from your
  phone without opening a port at home.
- **Client workloads run in the client's cloud and on their bill.**
  OpenCloudOps proposes IaC for their account; the firm's own hosting stays
  small.

### Prices checked on 2026-11-24

| Option | Smallest always-on cost | Notes | Source |
|---|---|---|---|
| Google Cloud Run | $0 within the free tier: 180,000 vCPU-s, 360,000 GiB-s and 2M requests a month (request-based) | Idle warm instance: $0.0000025 per vCPU-s and per GiB-s | [Cloud Run pricing](https://cloud.google.com/run/pricing) |
| GCP Always Free | Firestore 1 GiB, 50k reads and 20k writes a day; one e2-micro VM; Logging 50 GiB | New accounts: $300 for 90 days, which cannot pay for Gemini platform services for sign-ups from 16 Nov 2026 | [Free program](https://docs.cloud.google.com/free/docs/free-cloud-features) |
| Hetzner CX23 | $6.49/mo (EU), was $4.99 | Prices rose from 15 June 2026; US CPX11 went from $6.99 to $20.49 | [Hetzner price adjustment](https://docs.hetzner.com/general/infrastructure-and-availability/price-adjustment/) |
| Fly.io | $2.19/mo (256 MB), $6.70/mo (1 GB) | No free allowance for new orgs | [Fly.io pricing](https://docs.fly.io/about/pricing) |
| Railway | $5/mo Hobby incl. $5 usage | Then $10 per GB RAM and $20 per vCPU a month | [Railway plans](https://docs.railway.com/pricing/plans) |
| AWS Lightsail | $5/mo (512 MB, IPv4), $3.50 IPv6-only | Plain VPS | [Lightsail pricing](https://aws.amazon.com/lightsail/pricing) |
| Cloudflare Workers | $5/mo minimum on Paid | 10M requests, D1 and Durable Objects included; would mean rewriting the Python service | [Workers pricing](https://developers.cloudflare.com/workers/platform/pricing/) |

Not priced here: Azure (no existing footprint, so it starts from zero) and
Cloudflare Zero Trust's free-plan user limit (the plan page would not load;
Tunnel itself is free).

### Why not the others

- **Hetzner** is the cheapest fixed box, but its 2026 increases show the price is
  not stable, and you run and patch everything yourself.
- **Fly.io, Railway, Lightsail** are fine for one container, but none of them uses
  the GCP agent tooling and Terraform you already have.
- **Cloudflare Workers** is the cheapest global edge, but the console is Python.
  Keep Cloudflare for the network edge, not the app.

### Rollout and cost per stage

The hosting line excludes model tokens (Anthropic, Gemini), which will be the
larger bill once bots run all day. Put a budget alert on those first.

| Stage | When | What runs where | Hosting estimate |
|---|---|---|---|
| 0. Prove it | Now | Console, harness and scheduled bots on the Pi 5 (docker compose); Cloudflare Tunnel + Access for remote access; events in a JSONL file | $0 cloud; electricity only |
| 1. First clients | When a client depends on it | Console + harness API on Cloud Run (us-central1, scale to zero); events in Firestore; scheduled bots as Cloud Run jobs; Pi stays edge worker | $0 to $10 a month, inside the free tiers above (21 bots at one event every 15 min is about 2,000 writes a day against a 20,000 free) |
| 2. 24/7 firm | Several clients, India team on call | One warm console instance (about $10 a month by the idle rate above: 1 vCPU + 0.5 GiB for 30 days); a project per client; budget alerts | $20 to $60 a month, an estimate from the rates above, not a quote |

Stage 1 needs one code change first: a Firestore event store behind the same
`FleetState` interface, because Cloud Run's disk does not survive a restart.

## 5. What has to happen first

1. Merge the OpenCloudOps framework branch into main; the main branch is a
   corrupt zip.
2. Remove `backend/users.json` from the public amazon-seller repo and rotate
   those passwords.
3. Review ethos PR #10, then point `GOS_MC_URL` at `/fleet/events` so the
   harness reports here.
4. Add GCP patterns (Cloud Run, Firestore, Cloud Scheduler) to OpenCloudOps'
   catalog. It only knows AWS today, so it cannot yet write the firm's own
   infrastructure.
5. Finish one Advisory bot end to end (the SOW architect is closest), so the
   practice that sells has something that works.

## 6. Run it locally

```bash
pip install -e ".[api]"
OCC_INGEST_TOKEN=dev uvicorn opencloudops.api:app --port 8081
OCC_INGEST_TOKEN=dev python scripts/fleet_demo.py   # optional: labelled demo events
open http://127.0.0.1:8081/console
```

A bot reports with one call:

```bash
curl -X POST "$OCC_URL/fleet/events" -H "Authorization: Bearer $OCC_INGEST_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"agent":"sow-architect","kind":"needs_human","summary":"SOW draft ready for approval"}'
```
