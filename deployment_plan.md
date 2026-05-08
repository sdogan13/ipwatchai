# IP Watch AI - Azure Deployment Plan

Last updated: 2026-05-08
Status: Draft (pending decisions in Section 4)

## 1. Goal

Deploy IP Watch AI to Microsoft Azure under a Microsoft for Startups Founders
Hub subscription, in a configuration that:

- Splits heavy GPU batch work (pipeline ingest, MADLAD `name_tr` refresh,
  embedding generation) onto the existing local PC.
- Runs only the user-facing app on Azure: FastAPI backend, Postgres + pgvector,
  Redis, watchlist scanner, image-file serving, and live-query GPU work
  (MADLAD query translation + image search CLIP/DINOv2).
- Keeps `ipwatchai.com` reachable through the existing Cloudflare Tunnel so DNS
  and TLS do not change during cutover.
- Stays inside Founders Hub credit budgets, with auto-shutdown of the GPU VM
  outside Turkish business hours.

## 2. Architecture

```
LOCAL PC                              AZURE (westeurope)
─────────                             ──────────────────────────────
GPU pipeline ─┐                       ┌─ Backend FastAPI
              │ logical replication   │  + nginx + Redis
Postgres ─────┼──────────────────────►│  + watchlist scanner
   (publisher)│  (24/7 stream)        │  on NC4as_T4_v3 GPU VM
              │                       │  (auto-shutdown 8pm-8am Istanbul)
SMB sync ─────┼──────────────────────►├─ Azure Files
(bulletins/   │  (azcopy after        │  mounted at /app/bulletins
 clients/)    │   pipeline runs)      │  and /app/clients
              │                       │
              │                       └─ Azure Database for PostgreSQL
              │                          Flexible Server
              │                          (subscriber, always on,
              │                           pgvector enabled)
                                                    ▲
                          ipwatchai.com - Cloudflare Tunnel - nginx
```

### 2.1 Components on local PC

- Weekly pipeline ingest (`workers/pipeline_worker`): CLIP, DINOv2, EasyOCR
  embedding generation for new bulletins.
- `scripts/regenerate_name_tr.py`: MADLAD translation refresh.
- Source Postgres acting as logical-replication publisher.
- File source for `bulletins/Marka/.../images/` and `clients/`.

### 2.2 Components on Azure

- GPU VM (NC4as_T4_v3, Ubuntu 22.04, 1x Tesla T4 16 GB) running:
  - FastAPI backend container (Dockerfile.backend, CUDA 12.1.1).
  - Redis container (in-VM, ephemeral cache only).
  - nginx reverse proxy (deploy/nginx.prod.conf).
  - Cloudflare Tunnel (`cloudflared`) for ingress.
  - Watchlist scanner (`with-worker` profile, optional).
- Azure Database for PostgreSQL Flexible Server: always-on, holds
  `trademarks`, embeddings, `trademark_events`, watchlist alerts, billing,
  audit, and the rest of the canonical schema.
- Azure Files (Standard SMB) mounted into the VM at `/app/bulletins` and
  `/app/clients`, providing image files needed for image-search response
  thumbnails.
- Azure Key Vault for secrets.
- Static public IP and Log Analytics workspace.

### 2.3 Things deliberately not on Azure

- Pipeline worker / pipeline scheduler containers (`with-worker` profile).
  These stay local because the user runs all heavy GPU batch work on the local
  PC.
- Source-of-truth Postgres for pipeline writes. Local Postgres remains the
  publisher; Azure PG is the subscriber.

## 3. Cost (list price)

Region: West Europe.

| Item | Pay-as-you-go | 1-yr reserved |
|---|---|---|
| NC4as_T4_v3 (60 hrs/wk, Mon-Fri 8am-8pm Istanbul) | ~$140 | ~$95 |
| OS disk Premium SSD P10 (128 GB) | $20 | $20 |
| Azure Database for PostgreSQL Flexible Server, GP_Standard_D2s_v3, 256 GB storage, no zone-redundant HA | $170 | $170 |
| Azure Files Standard SMB, 1 TB share | $60 | $60 |
| Public IP (Standard, static) | $4 | $4 |
| Log Analytics + alerts (low-volume) | $20 | $20 |
| Egress (first 100 GB/mo free) | ~$0 | ~$0 |
| **Total** | **~$420 / mo** | **~$370 / mo** |

Founders Hub credit context (program tiers as of 2026):

| Tier | Azure credit | Months covered at $420/mo |
|---|---|---|
| Foundation | $1,000 | ~2.4 |
| Build | $5,000 | ~12 |
| Scale | $25,000 | ~60 |
| Founders | $150,000 | effectively unlimited |

External API costs (DeepSeek, Qwen, OpenAI gpt-image-2, Google Gemini) are not
on the Azure bill. Logo Studio image-gen is the largest variable cost there;
defaults already cap quality at `medium` per the README.

### 3.1 Cost-reduction levers (in order of preference)

1. **1-year reservation on the VM** once SKU is validated (~30% off).
2. **Tighten the auto-shutdown window** if traffic concentrates further.
3. **Drop to nightly pg_dump sync** instead of logical replication and put
   Postgres back on the VM, eliminating the managed PG line item (saves
   ~$170/mo). See Section 4 decision 1.
4. **Spot VM for any future Azure-side worker** (not used in this plan).

## 4. Pending decisions

The plan can be finalized as soon as these are confirmed.

### Decision 1: managed Postgres vs nightly pg_dump sync

The user picked logical replication. Logical replication needs the subscriber
reachable continuously, which is incompatible with auto-shutting down the GPU
VM. Two viable resolutions:

- **Option A (current plan): managed Postgres on Azure (~$170/mo extra).**
  Replication keeps streaming 24/7. Real-time consistency between local
  pipeline and Azure search.
- **Option B: keep Postgres on the VM, switch sync method to nightly pg_dump
  / restore.** No managed PG cost. Loses real-time updates between pipeline
  runs (acceptable since the pipeline is weekly anyway). Auto-shutdown stays
  trivial. Backup story is weaker (manual snapshots vs PG flexible-server PITR).

### Decision 2: `bulletins/` folder size

Drives Azure Files quota, the initial `azcopy` migration time, and whether the
VM OS disk needs to be P10 (128 GB) or P20 (512 GB). Pending: run
`du -sh bulletins clients` on the local PC and record the result here.

### Decision 3: ingress

Plan assumes Cloudflare Tunnel stays. If the user wants to switch to direct
public ingress on Azure (Caddy + Let's Encrypt), the plan must add a public
443 rule on the NSG and a Caddyfile-based service in compose.

### Decision 4: watchlist scanner placement

Watchlist scanner is mostly CPU once embeddings exist, so it can run on Azure.
But it relies on having `trademarks` and embedding data current, which depends
on Decision 1. If logical replication is chosen, the scanner runs cleanly on
Azure. If nightly pg_dump is chosen, the scanner sees stale data between
syncs; user should confirm that is acceptable for alert SLAs.

## 5. Phase-by-phase deployment

### Phase 1 - Account and subscription (~1 day)

1. Apply to Microsoft for Startups Founders Hub at
   `foundershub.startups.microsoft.com`. Inputs: business email, company
   description, LinkedIn profile.
2. Once approved, redeem Azure credits. This creates a sponsored Azure
   subscription `Microsoft Azure Sponsorship` (or similar).
3. Create resource group `rg-ipwatchai-prod` in `westeurope`.

### Phase 2 - Provision Azure infra (~half day)

4. **VNet** `vnet-ipwatchai` (10.20.0.0/16) with subnets:
   - `snet-app` (10.20.1.0/24) for the VM.
   - `snet-db` (10.20.2.0/24) delegated to
     `Microsoft.DBforPostgreSQL/flexibleServers`.
5. **Azure Database for PostgreSQL Flexible Server**:
   - SKU `Standard_D2s_v3` (2 vCore, 8 GB), 256 GB Premium SSD storage.
   - Postgres 16, private access via `snet-db`, no public endpoint.
   - Enable extensions used by the schema:
     `vector`, `pg_trgm`, `unaccent`, `uuid-ossp`, plus anything else
     `deploy/schema.sql` requires.
   - Server parameters: `wal_level=logical`, `max_replication_slots=4`,
     `max_wal_senders=4`.
   - Backup retention: 14 days, geo-redundant.
6. **Azure Files** storage account `stipwatchaiprod`:
   - Standard SMB, share `bulletins-prod` (1 TB quota; resize once Decision 2
     resolves).
   - Enable secure transfer; access via storage account key stored in Key
     Vault.
7. **GPU VM** NC4as_T4_v3, Ubuntu 22.04 LTS Gen2, in `snet-app`:
   - OS disk Premium SSD P10 (128 GB) by default; bump to P20 if Decision 2
     shows `bulletins/` is large enough that local cache space matters.
   - System-assigned managed identity.
   - NSG: SSH (22) from the user's static IP only; 80/443 closed (Cloudflare
     Tunnel handles ingress).
   - Enable auto-shutdown: 8pm Istanbul (UTC+3 -> 17:00 UTC).
   - Auto-start: Logic App or Automation runbook on cron `0 5 * * 1-5 UTC`
     (= 8am Istanbul Mon-Fri).
8. **Static public IP** for the VM (Standard SKU).
9. **Key Vault** `kv-ipwatchai-prod`:
   - Store: `AUTH_SECRET_KEY`, `DB_PASSWORD`, `REDIS_PASSWORD`,
     `CREATIVE_DEEPSEEK_API_KEY`, `CREATIVE_QWEN_API_KEY`,
     `CREATIVE_OPENAI_API_KEY`, `CREATIVE_GOOGLE_API_KEY`, Cloudflare Tunnel
     credentials, Azure Files storage key.
   - Grant the VM managed identity `get`/`list` on secrets.
10. **Log Analytics workspace** `law-ipwatchai-prod` and a small alert set:
    backend `/health` failure, VM CPU >90% for 10 min, PG storage >80%,
    Azure Files >80%, replication lag >1 hour.

### Phase 3 - Bootstrap the VM (~1 day)

11. SSH in. Install:
    - Docker Engine + Compose plugin.
    - NVIDIA Container Toolkit, validate with
      `docker run --rm --gpus all nvidia/cuda:12.1.1-base-ubuntu22.04 nvidia-smi`.
    - `azcopy`, `cifs-utils`, `git`.
12. Mount Azure Files via `/etc/fstab`:
    - `//stipwatchaiprod.file.core.windows.net/bulletins-prod/bulletins
       /mnt/bulletins cifs ...`
    - `//stipwatchaiprod.file.core.windows.net/bulletins-prod/clients
       /mnt/clients cifs ...`
13. Clone the repo into `/opt/ipwatchai`. Copy `.env.production.example` to
    `deploy/.env.prod` and fill from Key Vault values. Key overrides for
    Azure:
    - `DB_HOST` -> Azure PG private FQDN.
    - `DB_PORT=5432`.
    - `DB_NAME=trademark_db`.
    - `DATA_PATH=/mnt/bulletins`.
    - `CLIENTS_PATH=/mnt/clients`.
    - `HF_HOME=/var/lib/ipwatchai/hf`, `TORCH_HOME=/var/lib/ipwatchai/torch`,
      `EASYOCR_HOME=/var/lib/ipwatchai/easyocr` (on the OS disk, not Azure
      Files - SMB latency would kill model load times).
    - `WORKERS=1` (per repo guidance).
    - `AI_DEVICE=cuda`, `USE_FP16=true`, `USE_TF32=true`.
    - `CORS_ORIGINS=["https://ipwatchai.com","https://www.ipwatchai.com"]`.
14. Pre-warm model caches: pull CLIP, DINOv2, MADLAD, EasyOCR weights once
    (~15 GB to local disk). This avoids first-request 60-90s stalls after VM
    wake.
15. Disable Postgres in the prod overlay since the DB is now managed:
    - Either remove the `postgres` service from
      `deploy/docker-compose.prod.yml`, or override it with
      `profiles: ["never"]` and confirm `backend.depends_on` no longer waits
      on it.
16. Start the prod stack:

    ```bash
    docker compose --env-file deploy/.env.prod \
      -f docker-compose.yml \
      -f deploy/docker-compose.prod.yml \
      up -d backend redis nginx
    ```

    Add `cloudflared` once the tunnel config (Phase 5) is in place. Add the
    watchlist scanner via `--profile with-worker` if Decision 4 puts it on
    Azure.

### Phase 4 - Data migration

This is the slow phase; duration depends on Decision 2 (`bulletins/` size).

17. **Schema seed**: from local PG, dump schema only:

    ```powershell
    pg_dump --schema-only --no-owner --no-acl `
      -h 127.0.0.1 -p 5433 -U turk_patent trademark_db > schema.sql
    ```

    Restore to Azure PG (use a jump host inside the VNet, since Azure PG is
    private):

    ```bash
    psql "host=<pg-fqdn> dbname=trademark_db user=... sslmode=require" \
      -f schema.sql
    ```

    Note the schema must include `CREATE EXTENSION vector` and the other
    extensions enabled in step 5.
18. **Initial data load** (option A - logical replication will catch up; this
    is just to seed):

    ```powershell
    pg_dump --data-only --no-owner -h 127.0.0.1 -p 5433 -U turk_patent `
      trademark_db | psql "host=<pg-fqdn> ..."
    ```

    For very large tables, prefer `pg_dump -Fd` directory format with
    `-j 4` parallel workers and `pg_restore -j 4`.
19. **Files**: from the local PC,

    ```powershell
    azcopy sync "C:/Users/701693/turk_patent/bulletins" `
      "https://stipwatchaiprod.file.core.windows.net/bulletins-prod/bulletins?<SAS>" `
      --recursive
    azcopy sync "C:/Users/701693/turk_patent/clients" `
      "https://stipwatchaiprod.file.core.windows.net/bulletins-prod/clients?<SAS>" `
      --recursive
    ```
20. **Logical replication** (assuming Decision 1 = managed PG):
    - On local PG: ensure `wal_level=logical`, `max_wal_senders>=4`,
      `max_replication_slots>=4`, then
      `CREATE PUBLICATION pub_ipwatch FOR ALL TABLES;`.
    - On Azure PG:
      `CREATE SUBSCRIPTION sub_ipwatch
         CONNECTION 'host=<local-public-host> port=5432 dbname=trademark_db user=replicator password=...'
         PUBLICATION pub_ipwatch
         WITH (copy_data = false);`
      (`copy_data=false` because step 18 already seeded the data.)
    - Verify lag with `pg_stat_replication` on local and
      `pg_stat_subscription` on Azure.
    - Local PG must be reachable from Azure: either expose port 5432 via
      Cloudflare Tunnel TCP / Tailscale / WireGuard, or via a static
      forwarded port on the home router. Plain public exposure is not
      acceptable.

### Phase 5 - Cutover (~half day)

21. Move the existing Cloudflare Tunnel config: copy `cloudflared/config.yml`
    and certs from local to `/opt/ipwatchai/cloudflared/`. Update the tunnel
    target to `http://nginx:80` inside the compose network.
22. Start `cloudflared` in the compose stack:

    ```bash
    docker compose --env-file deploy/.env.prod \
      -f docker-compose.yml \
      -f deploy/docker-compose.prod.yml \
      --profile with-tunnel up -d cloudflared
    ```
23. In the Cloudflare dashboard, update the `ipwatchai.com` and
    `www.ipwatchai.com` tunnel routes to the new tunnel ID. DNS records do
    not need to change.
24. Verify, in this order:
    - `curl https://ipwatchai.com/health` returns 200.
    - `curl https://ipwatchai.com/api/info` returns valid metadata.
    - `curl https://ipwatchai.com/api/v1/status` shows database stats.
    - `GET /api/v1/tools/status` reports Name Lab and Logo Studio available
      (confirms OpenAI, Qwen, Gemini, DeepSeek keys loaded).
    - Run `python tests/test_live_app_e2e.py` against
      `TEST_BASE_URL=https://ipwatchai.com`.
25. If any verification fails, revert the Cloudflare Tunnel route to the old
    tunnel and investigate. The old local stack should remain running until
    cutover is signed off.

### Phase 6 - Operations and doc sync

26. **VM auto-start**: Logic App with cron trigger `0 5 * * 1-5` (UTC) calling
    `Microsoft.Compute/virtualMachines/start` against the VM.
27. **Backups**:
    - Managed PG: 14-day PITR is on; add weekly long-term retention to GRS
      blob.
    - VM OS disk: weekly snapshot via Azure Backup, 4-week retention.
    - Azure Files: enable soft delete (7 days) and snapshots (weekly,
      4-week retention).
28. **Monitoring**: alerts already provisioned in step 10. Add a synthetic
    check from outside (e.g., Cloudflare Health Check or UptimeRobot) hitting
    `/health` every 5 min during the auto-on window.
29. **Documentation sync** (per `CLAUDE.md` Documentation Sync Matrix):
    - Add an "Azure (Founders Hub)" section to `docs/DEPLOYMENT.md`
      describing the architecture, env-file deltas, and runbooks.
    - Update `README.md` Quick Start to mention the production deploy lives
      on Azure.
    - If schema or compose-file edits land in step 15, update
      `docs/DATABASE_SCHEMA.md` and any compose references in
      `docs/FILE_INDEX.md`.
30. **Test integrity** (per `CLAUDE.md` Test Integrity Rule):
    - Add or extend a smoke test that exercises the live app against the
      Azure URL using the managed test personas.
    - Confirm the test does not leave junk state on the live DB.

## 6. Rollback plan

Cutover is fully reversible until DNS is changed (which this plan does not
do - Cloudflare Tunnel route swap is the only ingress change).

Rollback steps:

1. In Cloudflare, repoint `ipwatchai.com` and `www.ipwatchai.com` tunnel
   routes back to the old (local) tunnel.
2. Stop the Azure VM (`docker compose down` then VM stop) so it does not
   accumulate cost.
3. Drop the logical-replication subscription on Azure PG to stop WAL
   accumulation on local:
   `DROP SUBSCRIPTION sub_ipwatch;`.
4. Local stack continues to serve traffic unchanged.

Cleanup of Azure resources, if abandoning:

1. Delete `rg-ipwatchai-prod` (one click - removes VM, PG, Files, IP, Key
   Vault, Log Analytics).
2. Drop `pub_ipwatch` on local PG.
3. Remove the Cloudflare Tunnel created for the VM if not reusing.

## 7. Risks and mitigations

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| MADLAD / CLIP / DINOv2 cold-start delay after VM wake | High | Slow first requests | Pre-warm model cache on OS disk; healthcheck `start_period: 600s` already set; trigger a synthetic search request as part of the auto-start runbook |
| Local PC offline -> replication stalls | Medium | Stale Azure search results | Alert on replication lag; weekly pg_dump fallback as belt-and-braces; pipeline cadence is weekly so brief gaps are tolerable |
| Azure Files SMB latency hurts image-search response | Medium | Slow image fetches | If observed, mirror a hot subset to local Premium SSD with cron-based rsync from the SMB share |
| Cloudflare Tunnel certs not portable | Low | Cutover blocked | Pre-test the tunnel from the VM before scheduling the route swap |
| WAL accumulation on local PG if Azure PG goes down | Low | Local disk fill | Set `max_slot_wal_keep_size` on local PG to a finite value (e.g. 50 GB); alert on slot lag |
| Founders Hub credit category does not include the chosen GPU SKU | Low | Some line items not credit-covered | Verify `NCasT4_v3` family is eligible before provisioning; switch to `NVadsA10_v5` if not |

## 8. Open items to resolve before starting

- [ ] Decision 1: managed Postgres vs nightly pg_dump sync
- [ ] Decision 2: capture local `bulletins/` and `clients/` size; lock in
      Azure Files quota
- [ ] Decision 3: confirm Cloudflare Tunnel stays as the ingress
- [ ] Decision 4: watchlist scanner on Azure or local
- [ ] Confirm Founders Hub tier and credit ceiling for budget alarms
- [ ] Identify a stable inbound path from Azure to local PG (Cloudflare TCP
      tunnel, Tailscale, or WireGuard) for the replication subscriber

## 9. Done gate

Per `CLAUDE.md` Done Gate, this deployment is complete only when:

- The Azure stack passes `tests/test_live_app_e2e.py` against
  `https://ipwatchai.com`.
- `docs/DEPLOYMENT.md` has been updated with the Azure section.
- The cutover and rollback runbooks are recorded in this file or a sibling
  ops doc.
- Backups (PG, VM disk, Azure Files) are configured and have completed at
  least one successful run.
- Monitoring alerts have been validated by intentionally tripping at least
  one (e.g. stop the backend container and confirm the `/health` alert
  fires).
- Created test data and migration artifacts (`schema.sql`, dump files) are
  removed from the VM and from `artifacts/` per the Documentation And
  State Rule.
