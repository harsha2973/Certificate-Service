# Certificate Service

A bulk certificate generation API built with FastAPI, SQLAlchemy, and ReportLab.  
It validates recipients and renders customized PDFs in background worker threads.  
Users can track live progress and download certificates individually or as a ZIP.

Bulk certificate generation service. A client submits one request containing many recipients; the
service validates each recipient, generates a PDF per valid recipient from a single predefined
template **in the background**, and exposes job status/progress and the generated files.

**Stack:** Python 3.11+, FastAPI, SQLAlchemy 2 (SQLite by default, PostgreSQL via `DATABASE_URL`),
Alembic (migrations), ReportLab (PDF), Docker, pytest.

## Setup

### Local Environment

```bash
python -m venv .venv
# On Linux/macOS:
source .venv/bin/activate
# On Windows (PowerShell):
.\.venv\Scripts\Activate.ps1

pip install -r requirements-dev.txt
```

Configuration is via environment variables (see `.env.example`); defaults work out of the box
(SQLite file + PDFs under `./data/`).

| Variable | Default | Meaning |
|---|---|---|
| `DATABASE_URL` | `sqlite:///./data/certificates.db` | Any SQLAlchemy URL (e.g. `postgresql+psycopg://...`) |
| `STORAGE_DIR` | `./data/certificates` | Where PDFs are written |
| `PROCESSING_MODE` | `background` | `background` (worker threads) or `inline` (in the request) |
| `WORKER_THREADS` | `2` | Concurrent jobs in background mode |
| `MAX_RECIPIENTS_PER_JOB` | `5000` | Per-request cap |
| `RESUME_UNFINISHED_JOBS` | `true` | Re-queue pending/processing jobs on startup |
| `API_KEY` | `None` (disabled) | Optional secret key; when set, requires `X-API-Key` header on `/api/v1` routes |

### Docker & Docker Compose (PostgreSQL)

To run the application alongside a PostgreSQL 16 database:

```bash
docker compose up --build -d
```

The API will be available at `http://localhost:8000` and docs at `http://localhost:8000/docs`.

### Database Migrations (Alembic)

Database schema migrations are managed with Alembic. The application automatically applies pending migrations to `head` on startup. To run migrations manually via CLI:

```bash
alembic upgrade head
```

To create a new migration after model changes:

```bash
alembic revision --autogenerate -m "describe_change"
```

## Run

```bash
uvicorn app.main:create_app --factory --reload
```

Interactive docs: http://localhost:8000/docs

### Generate Certificates (CLI Script)

You can run the included bulk generation script to submit batches, observe real-time progress, and automatically download the generated certificates archive:

```bash
# Run with sample test data (includes valid & test failure cases)
python generate_certificates.py

# Or run with custom parameters and a CSV list:
python generate_certificates.py --course "Full Stack Engineering" --org "Acme Academy" --csv recipients.csv
```

Generated certificates are downloaded and unzipped into `./generated_certificates/`.

## Test

```bash
pytest -v
```

The pytest suite runs in `PROCESSING_MODE=inline` mode using isolated temporary SQLite databases and storage directories.

## API

### Submit a job — `POST /api/v1/jobs` → `202 Accepted`

```bash
curl -X POST http://localhost:8000/api/v1/jobs \
  -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: optional-uuid-or-key' \
  -d '{
    "course_name": "Python Bootcamp 2026",
    "organization": "Acme Academy",
    "title": "Certificate of Completion",
    "issue_date": "2026-10-09",
    "recipients": [
      {"name": "Ada Lovelace", "email": "ada@example.com"},
      {"name": "Alan Turing",  "email": "alan@example.com"}
    ]
  }'
```

* `title` and `issue_date` are optional (defaults: "Certificate of Completion", today UTC).
* Header `Idempotency-Key` (optional): Guarantees that duplicate requests with the same key return the previously created job without re-generating duplicates.
* Header `X-API-Key` (optional): Required if `API_KEY` is configured in the environment.
* The response contains the job `id` and a `Location` header.

### List jobs — `GET /api/v1/jobs?status=&limit=&offset=`

Lists submitted jobs with pagination and optional status filter (`pending`, `processing`, `completed`, `completed_with_errors`, `failed`), ordered newest first.

```bash
curl http://localhost:8000/api/v1/jobs?limit=20&offset=0
```

### Check progress — `GET /api/v1/jobs/{job_id}`

```json
{
  "id": "…",
  "status": "completed_with_errors",
  "progress": {
    "total": 3,
    "succeeded": 2,
    "failed": 1,
    "pending": 0,
    "percent_complete": 100.0
  },
  "failures": [
    {
      "position": 1,
      "recipient_name": "",
      "recipient_email": "x@y.io",
      "error": "Validation failed - name: String should have at least 1 character"
    }
  ],
  "download_url": "/api/v1/jobs/…/download"
}
```

Job status transitions: `pending` → `processing` → `completed` | `completed_with_errors` | `failed`.
`failures[].position` is the zero-based index in the submitted `recipients` array.
(`failures` is capped at 100 in this endpoint; use the certificates endpoint with `?status=failed` for full listing.)

### Retry failed certificates — `POST /api/v1/jobs/{job_id}/retry` → `202 Accepted`

Re-queues only certificates that failed during PDF generation (e.g. transient renderer errors). Per-recipient validation failures (e.g. invalid emails or empty names) cannot be retried without modified inputs and are left unchanged.

```bash
curl -X POST http://localhost:8000/api/v1/jobs/{job_id}/retry
```

Returns `409 Conflict` if the job is still running, or `400 Bad Request` if there are no retriable generation failures.

### Retrieve certificates

| Endpoint | Returns |
|---|---|
| `GET /api/v1/jobs/{id}/certificates?status=&limit=&offset=` | Paginated per-recipient status + `download_url` |
| `GET /api/v1/certificates/{cert_id}/download` | One PDF (`409` if that certificate failed/isn't ready) |
| `GET /api/v1/jobs/{id}/download` | Zip of all successful PDFs (`409` while the job is running) |
| `GET /health` | Health check (`{"status": "ok"}`) |

## Bulk Processing

The Certificate Service API is designed specifically around high-volume, bulk requests where a single request may contain hundreds or thousands of recipients.

### Generation Processing Strategy: Background vs. Synchronous

| Dimension | Background Processing (Default: `PROCESSING_MODE=background`) | Synchronous / Inline (`PROCESSING_MODE=inline`) |
|---|---|---|
| **Response Code** | `202 Accepted` with `Location` header | `202 Accepted` (completed before return in testing) |
| **Request Duration** | Fixed, rapid (~10–25ms to validate and persist rows) | Proportional to recipient count (e.g. 50s for 1,000 PDFs) |
| **Connection Risk** | Immune to HTTP client & reverse-proxy (Nginx/Cloudflare) timeouts | High risk of connection termination and gateway `504` errors |
| **Worker Utilization**| HTTP worker threads remain free to serve incoming traffic | HTTP threads blocked during CPU-bound PDF rendering |
| **Progress Visibility**| Real-time polling via `GET /api/v1/jobs/{job_id}` | No progress visibility until the entire HTTP response completes |
| **Crash Resilience** | Crash recovers safely; ungenerated certificates resume on restart | Interrupted request loses all progress or leaves undefined state |

### Architectural Choice & Reasoning

**Primary Choice: Background Asynchronous Processing with Polling**
1. **Prevent Gateway & Client Timeouts:** Generating a PDF document requires ReportLab drawing routines, font metrics calculations, and disk I/O. Even generating 1,000 certificates at 30ms per PDF takes 30 seconds—approaching or exceeding standard reverse-proxy timeouts (e.g. Nginx 60s, AWS ALB 60s). Background processing decouples job submission from generation time.
2. **Web Thread Availability:** Handling PDF generation off the HTTP request path prevents thread starvation, allowing the FastAPI service to accept concurrent submissions and serve status queries with low latency.
3. **Fault Tolerance & Partial Progress:** In background mode, each certificate is generated and committed in its own database transaction. If the server process crashes or restarts midway, unfinished certificates (`pending`) are automatically resumed on startup (`RESUME_UNFINISHED_JOBS=true`).
4. **Pluggable Execution Seam (`JobRunner`):** The application uses an in-process `ThreadPoolExecutor` runner by default. The `JobRunner` interface abstracts job execution, making it seamless to transition to distributed queues (Celery, RQ, AWS SQS) for horizontal scaling without altering the API contract.
5. **Deterministic Testing via Inline Mode:** When running automated tests (`PROCESSING_MODE=inline`), generation runs sequentially inside the request, enabling completely deterministic assertions without complex polling loops in test cases.

### Bulk Handling Features
- **Configurable Batch Limits:** Requests are capped by `MAX_RECIPIENTS_PER_JOB` (default `5000`) to protect against unbounded memory allocation.
- **Idempotent Submissions:** Callers can pass an `Idempotency-Key` header. Duplicate submissions with the same key safely return the existing job (`Location` header + job payload) without duplicate generation.
- **Bulk Archive Download:** Once completed, clients can retrieve all successfully generated certificates in a single compressed archive via `GET /api/v1/jobs/{job_id}/download`.
- **Paginated Listing:** Individual recipient status and downloads can be retrieved via paginated endpoints (`GET /api/v1/jobs/{job_id}/certificates?status=&limit=&offset=`).

---

## Validation & Failure Handling

The service implements a multi-tier validation and isolation strategy to guarantee that failures in one entry never compromise the rest of the batch.

### 1. Handling Invalid Recipient Data Appropriately
- **Separation of Envelope vs. Recipient Validation:**
  - **Envelope validation (HTTP 422):** Only structural issues that prevent processing any certificate (missing `course_name`, missing `organization`, empty `recipients` list, unparseable dates, or batch size exceeding `MAX_RECIPIENTS_PER_JOB`) reject the request with HTTP `422 Unprocessable Entity`.
  - **Per-recipient validation:** The `recipients` list bypasses top-level rejection (`SkipValidation[RecipientIn]`). Instead, each entry is parsed individually via `validate_recipient()`.
- **Non-Fatal Per-Recipient Errors:**
  - If an item has an empty name, invalid email format, missing fields, or invalid data types, the service marks that specific recipient record as `status="failed"` with a human-readable reason (e.g. `Validation failed - email: is not a valid email address`).
  - **Duplicate Prevention:** If an email appears multiple times within the same request, the first instance is accepted while subsequent instances are marked as `status="failed"` (`Validation failed - duplicate recipient email in this request`).
  - **Data Retention for Reconciliation:** Even for invalid recipients, the submitted raw strings are saved in the `certificates` table so the client can cross-reference errors by input index (`position`).

### 2. Isolation of Generation Failures
- **Per-Certificate Isolation:** During processing, each pending certificate is generated individually in its own `try...except` block (`_process_certificate`).
- **Atomic File Writing:** PDFs are initially rendered to a `.tmp` file and atomically renamed (`os.replace`). If rendering fails (e.g. unrenderable glyphs, unexpected font crash, or disk errors), the temp file is unlinked immediately, and only that certificate row is marked `status="failed"` with `error="Generation failed - <reason>"`.
- **Independent Commits:** Each certificate commits its status and file path independently upon completion. A failure while generating certificate #42 does not rollback or interrupt certificate #43.
- **Selective Retry:** Clients can invoke `POST /api/v1/jobs/{job_id}/retry` to re-queue generation-failed certificates (e.g., after fixing transient render issues or missing fonts). Validation errors are immutable and not retried.

### 3. Detailed Job Status & Progress Tracking
The job status endpoint (`GET /api/v1/jobs/{job_id}`) provides full visibility into the lifecycle and individual outcomes of the batch:
- **Comprehensive Lifecycle States:**
  - `pending`: Job accepted and queued in runner.
  - `processing`: Worker is actively generating certificates.
  - `completed`: All certificates in the job succeeded.
  - `completed_with_errors`: Partial success (some certificates succeeded, some failed due to validation or generation errors).
  - `failed`: Every certificate failed or a fatal job-level error occurred.
- **Real-Time Progress Counters:**
  - Computed dynamically from certificate rows: `total`, `succeeded`, `failed`, `pending`, and `percent_complete`.
- **Granular Failure Identification:**
  - Returns a `failures` array containing:
    - `position`: The original 0-based index in the submitted `recipients` array.
    - `recipient_name`: The submitted recipient name.
    - `recipient_email`: The submitted email.
    - `error`: Clear diagnostic reason for failure.
  - Capped at 100 in the primary status payload (flagged with `failures_truncated: true`), with full paginated drill-down available via `GET /api/v1/jobs/{job_id}/certificates?status=failed`.

---

## Design decisions

* **Asynchronous processing (202 + polling).** A request can hold thousands of recipients;
  generating them inside the request would hit client/proxy timeouts and tie up workers. POST only
  validates and persists, then hands the job to a `JobRunner` (in-process thread pool) and returns.
  `PROCESSING_MODE=inline` runs the same code synchronously, which the tests use for determinism.
  The runner is the only seam to replace with Celery/RQ/SQS for multi-process deployment.
* **Every input row is persisted.** One `certificates` row per submitted recipient. Invalid
  recipients become `failed` rows with a reason, so the client can reconcile every input
  by `position`. Request-level problems (missing course, empty list, too many recipients) are `422`
  because nothing can be processed.
* **Per-recipient validation is separate from envelope validation** (`recipients` is validated
  item by item), so one bad row never rejects the batch. Duplicate emails within a request are
  rejected (first occurrence wins).
* **Failure isolation.** Each certificate is generated and committed independently; an exception
  fails only that row. Job outcome: all ok → `completed`; none ok → `failed`; otherwise
  `completed_with_errors`.
* **Live progress without drift.** Counters are computed from certificate rows (`GROUP BY status`),
  never stored separately. The worker commits after each certificate.
* **Restart-safe.** `process_job` only touches `pending` rows, so unfinished jobs are re-queued
  at startup. PDFs are written to a temp file and atomically renamed.
* **Files on local disk, metadata in the DB.** Local filesystem abstraction is separated from DB metadata,
  making it easy to swap `STORAGE_DIR` for S3/GCS.
* **Unicode Typography & Template.** Template in `app/generator.py` (ReportLab, A4 landscape).
  Includes embedded Unicode TrueType fonts (Noto Sans Regular & Bold) in `app/fonts/`, supporting
  Latin, accented Latin, Cyrillic, and Greek scripts, with graceful fallback to standard PDF fonts.
* **Idempotency & Simple Auth.** `POST /jobs` supports `Idempotency-Key` headers to safely tolerate
  network retries without duplicating batches. Simple API-key authentication is supported via `API_KEY`
  and the `X-API-Key` header.
* **Alembic Migrations & Postgres.** Automatic startup migration ensures database schemas are always
  kept in sync across SQLite and PostgreSQL environments.

## Known limitations / next steps

* Thread-pool runner is in-process; running several API processes against one DB could process
  the same job twice. For horizontal scaling across multiple instances, swap the runner seam with Celery, RQ, or a DB-level `SELECT ... FOR UPDATE SKIP LOCKED` worker.
* Cleanup / retention policy for old jobs and generated PDF files (e.g. cron job or background lifecycle deletion after N days).
* Support for CJK (Chinese, Japanese, Korean) scripts via multi-megabyte CJK font packs or web font loading.

## Layout

```
app/
  main.py            app factory, lifespan (resume + shutdown)
  config.py          env-based settings
  database.py        engine / session factory / migration runner
  models.py          Job, Certificate, status enums
  schemas.py         request/response + recipient validation
  generator.py       the certificate template (PDF + Unicode fonts)
  worker.py          JobRunner (background / inline)
  fonts/             embedded Unicode TrueType fonts (Noto Sans)
  services/jobs.py   job creation, retry, progress, response building
  services/processing.py  per-certificate generation loop
  api/routes.py      HTTP endpoints
alembic/             Alembic migrations
tests/               pytest suite (inline mode, injected generators)
```
