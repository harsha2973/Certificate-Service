"""Bulk certificate generation script for multiple entries.

Demonstrates:
1. Bulk submission of many recipients in a single API request (POST /api/v1/jobs).
2. Validation & Failure Handling:
   - Valid entries are processed and generated as PDFs.
   - Invalid recipient entries (e.g., bad email format, empty name, duplicates) are
     recorded as individual failures without failing the rest of the batch.
3. Live progress polling and detailed failure reporting (position, name, email, error reason).
4. Downloading the consolidated ZIP archive of all successfully generated certificates.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
import uuid
import zipfile
from pathlib import Path
from typing import Any

# Ensure stdout and stderr support UTF-8 (e.g. on Windows console)
if sys.platform == "win32":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# Use httpx (installed in virtual environment) or fallback to urllib
try:
    import httpx
    USE_HTTPX = True
except ImportError:
    import urllib.error
    import urllib.request
    USE_HTTPX = False


API_BASE = os.getenv("API_BASE", "http://localhost:8000/api/v1")
OUTPUT_DIR = Path("generated_certificates")


# Sample dataset demonstrating multiple entries, including intentional invalid
# entries to showcase resilient failure isolation.
SAMPLE_RECIPIENTS: list[dict[str, Any]] = [
    # --- Valid entries ---
    {"name": "Harsha Gowda", "email": "harsha.gowda@example.com"},
    {"name": "Hemanth Kumar", "email": "hemanth.kumar@example.com"},
    {"name": "Chethan C J", "email": "chethan.cj@example.com"},
    {"name": "Mahan J Shetty", "email": "mahan.shetty@example.com"},
    {"name": "Eshwar S", "email": "eshwar.s@example.com"},
    {"name": "Ada Lovelace", "email": "ada.lovelace@example.com"},
    {"name": "Alan Turing", "email": "alan.turing@example.com"},
    {"name": "Grace Hopper", "email": "grace.hopper@example.com"},
    {"name": "Claude Shannon", "email": "claude.shannon@example.com"},
    {"name": "Margaret Hamilton", "email": "margaret.hamilton@example.com"},
    {"name": "Nikola Tesla", "email": "nikola.tesla@example.com"},
    {"name": "Marie Curie", "email": "marie.curie@example.com"},

    # --- Intentional invalid entries (to demonstrate validation & failure handling) ---
    {"name": "", "email": "empty.name@example.com"},  # Invalid: Empty name
    {"name": "Invalid Email User", "email": "not-a-valid-email"},  # Invalid: Bad email format
    {"name": "Missing Email User"},  # Invalid: Missing email field
    {"name": "Duplicate Harsha", "email": "harsha.gowda@example.com"},  # Invalid: Duplicate email in batch
]


def _http_request(method: str, url: str, headers: dict[str, str] | None = None, json_data: Any = None) -> tuple[int, Any, bytes]:
    """Helper to perform HTTP request using httpx or urllib."""
    headers = headers or {}
    if USE_HTTPX:
        with httpx.Client(timeout=30.0) as client:
            resp = client.request(method, url, headers=headers, json=json_data)
            try:
                body_json = resp.json()
            except Exception:
                body_json = None
            return resp.status_code, body_json, resp.content
    else:
        data_bytes = json.dumps(json_data).encode("utf-8") if json_data is not None else None
        req = urllib.request.Request(url, data=data_bytes, headers=headers, method=method)
        if json_data is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req) as resp:
                raw = resp.read()
                try:
                    body_json = json.loads(raw.decode("utf-8"))
                except Exception:
                    body_json = None
                return resp.status, body_json, raw
        except urllib.error.HTTPError as err:
            raw = err.read()
            try:
                body_json = json.loads(raw.decode("utf-8"))
            except Exception:
                body_json = None
            return err.code, body_json, raw


def load_recipients_from_csv(csv_path: Path) -> list[dict[str, str]]:
    """Load recipients list from a CSV file (columns: name, email)."""
    recipients = []
    with open(csv_path, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            recipients.append({
                "name": row.get("name", "").strip(),
                "email": row.get("email", "").strip(),
            })
    return recipients


def submit_bulk_job(
    course_name: str,
    organization: str,
    recipients: list[dict[str, Any]],
    title: str = "Certificate of Excellence",
    issue_date: str | None = None,
    api_base: str = API_BASE,
    api_key: str | None = None,
) -> dict[str, Any]:
    """Submit a bulk batch of recipients as a single job."""
    url = f"{api_base}/jobs"
    idempotency_key = str(uuid.uuid4())
    headers = {
        "Content-Type": "application/json",
        "Idempotency-Key": idempotency_key,
    }
    if api_key:
        headers["X-API-Key"] = api_key

    payload = {
        "course_name": course_name,
        "organization": organization,
        "title": title,
        "recipients": recipients,
    }
    if issue_date:
        payload["issue_date"] = issue_date

    print(f"\n📤 Submitting bulk job with {len(recipients)} recipients...")
    print(f"   Course:       {course_name}")
    print(f"   Organization: {organization}")
    print(f"   Idempotency:  {idempotency_key}")

    status_code, body, _ = _http_request("POST", url, headers=headers, json_data=payload)
    if status_code != 202:
        print(f"❌ Failed to submit job (HTTP {status_code}): {body}")
        sys.exit(1)

    print(f"✅ Job accepted! Job ID: {body['id']} (Status: {body['status']})")
    return body


def poll_job_until_finished(
    job_id: str,
    api_base: str = API_BASE,
    api_key: str | None = None,
    poll_interval: float = 1.0,
) -> dict[str, Any]:
    """Poll job status until it reaches a terminal state."""
    url = f"{api_base}/jobs/{job_id}"
    headers = {}
    if api_key:
        headers["X-API-Key"] = api_key

    print("\n⏳ Processing certificates in background...")
    last_percent = -1.0

    while True:
        status_code, body, _ = _http_request("GET", url, headers=headers)
        if status_code != 200:
            print(f"❌ Error polling job (HTTP {status_code}): {body}")
            sys.exit(1)

        progress = body["progress"]
        percent = progress["percent_complete"]
        succeeded = progress["succeeded"]
        failed = progress["failed"]
        total = progress["total"]
        status = body["status"]

        if percent != last_percent:
            bar_len = 25
            filled = int(bar_len * (percent / 100.0))
            bar = "█" * filled + "░" * (bar_len - filled)
            print(f"\r   [{bar}] {percent:5.1f}% | Succeeded: {succeeded}/{total} | Failed: {failed} | Status: {status}", end="", flush=True)
            last_percent = percent

        if status in ("completed", "completed_with_errors", "failed"):
            print("\n")
            return body

        time.sleep(poll_interval)


def print_job_summary(job: dict[str, Any]) -> None:
    """Print detailed outcome including failure breakdown."""
    status = job["status"]
    progress = job["progress"]
    failures = job.get("failures", [])

    print("=" * 65)
    print("                    JOB EXECUTION SUMMARY")
    print("=" * 65)
    print(f"Job ID:               {job['id']}")
    print(f"Final Status:         {status.upper()}")
    print(f"Total Recipients:     {progress['total']}")
    print(f"Successfully Created: {progress['succeeded']} certificates")
    print(f"Failed Entries:       {progress['failed']} entries")
    print("-" * 65)

    if failures:
        print("⚠️  FAILURE DETAILS (Invalid entries or render failures):")
        for f in failures:
            pos = f["position"]
            name = f["recipient_name"] or "<empty name>"
            email = f["recipient_email"] or "<empty email>"
            err = f["error"] or "Unknown error"
            print(f"  • [Index #{pos}] Name: {name:<22} | Email: {email:<26}")
            print(f"    ↳ Reason: {err}")
        if job.get("failures_truncated"):
            print("  (... additional failures truncated in summary view ...)")
        print("-" * 65)

    if progress["succeeded"] > 0:
        print(f"🎉 {progress['succeeded']} valid certificates generated successfully!")
    print("=" * 65)


def download_job_archive(
    job_id: str,
    output_dir: Path = OUTPUT_DIR,
    api_base: str = API_BASE,
    api_key: str | None = None,
    extract: bool = True,
) -> Path | None:
    """Download the batch ZIP archive of all successfully generated certificates."""
    url = f"{api_base}/jobs/{job_id}/download"
    headers = {}
    if api_key:
        headers["X-API-Key"] = api_key

    status_code, _, content = _http_request("GET", url, headers=headers)
    if status_code != 200:
        print(f"⚠️ Could not download archive (HTTP {status_code}).")
        return None

    output_dir.mkdir(parents=True, exist_ok=True)
    zip_path = output_dir / f"job-{job_id}-certificates.zip"
    with open(zip_path, "wb") as f:
        f.write(content)

    print(f"\n📦 Saved bulk certificate archive to:\n   {zip_path.resolve()}")

    if extract:
        extract_dir = output_dir / f"job-{job_id}-pdfs"
        extract_dir.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(extract_dir)
        print(f"📂 Extracted individual PDF certificates to:\n   {extract_dir.resolve()}")

    return zip_path


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Insert multiple entries and generate certificates with failure handling."
    )
    parser.add_argument(
        "--csv",
        type=Path,
        help="Optional path to a CSV file with 'name' and 'email' columns.",
    )
    parser.add_argument(
        "--course",
        default="Advanced Cloud Computing & System Design",
        help="Course or program name.",
    )
    parser.add_argument(
        "--org",
        default="Institute of Technology",
        help="Issuing organization.",
    )
    parser.add_argument(
        "--title",
        default="Certificate of Excellence",
        help="Certificate title.",
    )
    parser.add_argument(
        "--api-base",
        default=API_BASE,
        help=f"API Base URL (default: {API_BASE})",
    )
    parser.add_argument(
        "--api-key",
        default=os.getenv("API_KEY"),
        help="API key if authentication is enabled.",
    )

    args = parser.parse_args()

    # 1. Load or prepare multiple recipient entries
    if args.csv:
        if not args.csv.is_file():
            print(f"❌ CSV file not found: {args.csv}")
            sys.exit(1)
        recipients = load_recipients_from_csv(args.csv)
        print(f"📄 Loaded {len(recipients)} recipients from {args.csv}")
    else:
        recipients = SAMPLE_RECIPIENTS
        print(f"📋 Using sample dataset with {len(recipients)} entries (including intentional test invalid entries).")

    # 2. Submit multiple entries in a single request (Bulk Processing)
    job = submit_bulk_job(
        course_name=args.course,
        organization=args.org,
        recipients=recipients,
        title=args.title,
        api_base=args.api_base,
        api_key=args.api_key,
    )

    job_id = job["id"]

    # 3. Poll progress until completed (Background Processing)
    finished_job = poll_job_until_finished(job_id, api_base=args.api_base, api_key=args.api_key)

    # 4. Print detailed status & failure report (Validation & Failure Handling)
    print_job_summary(finished_job)

    # 5. Download the bulk zip archive if any certificates succeeded
    if finished_job["progress"]["succeeded"] > 0:
        download_job_archive(job_id, api_base=args.api_base, api_key=args.api_key)


if __name__ == "__main__":
    main()
