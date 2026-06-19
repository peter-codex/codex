#!/usr/bin/env python3
"""Local browser app for Billbee CSV exports."""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from urllib.parse import unquote, urlparse

import billbee_sales_export as exporter

APP_DIR = Path(__file__).resolve().parent
EXPORT_DIR = APP_DIR / "billbee_exports"
ALLOWED_ROWS = {"all", "positions", "orders"}
ALLOWED_PLATFORMS = {"Etsy", "Kasuwa", "Amazon", "eBay", "__blank__"}


@dataclass
class ExportJob:
    id: str
    status: str = "queued"
    message: str = "Export wartet."
    created_at: float = field(default_factory=time.time)
    output_file: str | None = None
    orders: int = 0
    rows_mode: str = "all"
    date_from: str = ""
    date_to: str = ""
    platforms: list[str] = field(default_factory=list)
    error: str | None = None


jobs: dict[str, ExportJob] = {}
jobs_lock = threading.Lock()


def html_page() -> str:
    today = date.today()
    month_start = today.replace(day=1).isoformat()
    today_iso = today.isoformat()
    return f"""<!doctype html>
<html lang="de">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Billbee Export</title>
  <style>
    body {{ margin:0; font-family:Segoe UI, Arial, sans-serif; background:#f5f7f8; color:#172026; }}
    main {{ width:min(980px, calc(100vw - 32px)); margin:32px auto; }}
    h1 {{ margin:0 0 6px; font-size:28px; }}
    .sub {{ color:#66727c; margin:0 0 18px; }}
    .panel {{ background:white; border:1px solid #dce3e8; border-radius:8px; padding:20px; box-shadow:0 14px 35px rgba(15,23,42,.08); }}
    form {{ display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:16px; }}
    label {{ display:grid; gap:7px; color:#66727c; font-size:13px; font-weight:700; }}
    input, select {{ height:42px; border:1px solid #dce3e8; border-radius:6px; padding:0 11px; font:inherit; }}
    fieldset {{ grid-column:1 / -1; border:1px solid #dce3e8; border-radius:6px; padding:12px; }}
    legend {{ color:#66727c; font-size:13px; font-weight:700; padding:0 6px; }}
    .checks {{ display:grid; grid-template-columns:repeat(5,minmax(0,1fr)); gap:8px; }}
    .check {{ min-height:38px; border:1px solid #dce3e8; border-radius:6px; padding:8px 10px; display:flex; align-items:center; gap:8px; color:#172026; }}
    .check input {{ width:16px; height:16px; }}
    .actions {{ grid-column:1 / -1; display:flex; gap:10px; align-items:center; }}
    button, .download {{ height:42px; border-radius:6px; padding:0 16px; font-weight:700; text-decoration:none; display:inline-flex; align-items:center; justify-content:center; }}
    button {{ border:0; background:#0f766e; color:white; cursor:pointer; }}
    button:disabled {{ opacity:.72; cursor:wait; }}
    .download {{ border:1px solid #b9ded8; background:#eef8f6; color:#115e59; }}
    .status {{ margin-top:18px; border-top:1px solid #dce3e8; padding-top:16px; display:grid; gap:8px; }}
    .error {{ color:#b42318; white-space:pre-wrap; }}
    @media (max-width:720px) {{ main {{ width:calc(100vw - 20px); margin:16px auto; }} form {{ grid-template-columns:1fr; }} .checks {{ grid-template-columns:repeat(2,minmax(0,1fr)); }} .actions {{ flex-direction:column; align-items:stretch; }} button,.download {{ width:100%; }} }}
  </style>
</head>
<body>
<main>
  <h1>Billbee Export</h1>
  <p class="sub">CSV-Export nach Datumsbereich und Plattform starten.</p>
  <section class="panel">
    <form id="exportForm">
      <label>Von <input name="date_from" type="date" value="{month_start}" required></label>
      <label>Bis <input name="date_to" type="date" value="{today_iso}" required></label>
      <label>Exportart
        <select name="rows">
          <option value="all" selected>Alle Felder als einzelne Spalten</option>
          <option value="positions">Artikelpositionen kompakt</option>
          <option value="orders">Bestellungen kompakt</option>
        </select>
      </label>
      <label>Seitengröße
        <select name="page_size"><option value="50" selected>50</option><option value="100">100</option><option value="250">250</option></select>
      </label>
      <fieldset>
        <legend>Plattformen</legend>
        <div class="checks">
          <label class="check"><input type="checkbox" name="platforms" value="Etsy"> Etsy</label>
          <label class="check"><input type="checkbox" name="platforms" value="Kasuwa"> Kasuwa</label>
          <label class="check"><input type="checkbox" name="platforms" value="Amazon"> Amazon</label>
          <label class="check"><input type="checkbox" name="platforms" value="eBay"> eBay</label>
          <label class="check"><input type="checkbox" name="platforms" value="__blank__"> Ohne Plattform</label>
        </div>
      </fieldset>
      <div class="actions"><button id="startBtn" type="submit">Import starten</button><a id="downloadLink" class="download" href="#" hidden>CSV herunterladen</a></div>
    </form>
    <div class="status"><div id="statusMessage">Bereit.</div><div id="statusMeta"></div><div id="statusError" class="error"></div></div>
  </section>
</main>
<script>
const form = document.getElementById('exportForm');
const startBtn = document.getElementById('startBtn');
const downloadLink = document.getElementById('downloadLink');
const statusMessage = document.getElementById('statusMessage');
const statusMeta = document.getElementById('statusMeta');
const statusError = document.getElementById('statusError');
let pollTimer = null;
function setStatus(job) {
  statusMessage.textContent = job.message || 'Bereit.';
  statusMeta.textContent = job.orders ? `${job.orders} Bestellungen verarbeitet` : '';
  statusError.textContent = job.error || '';
  if (job.status === 'done' && job.download_url) { downloadLink.hidden = false; downloadLink.href = job.download_url; }
  if (job.status === 'done' || job.status === 'failed') { startBtn.disabled = false; startBtn.textContent = 'Import starten'; clearInterval(pollTimer); }
}
async function poll(jobId) { const response = await fetch('/api/status?id=' + encodeURIComponent(jobId)); setStatus(await response.json()); }
form.addEventListener('submit', async (event) => {
  event.preventDefault(); clearInterval(pollTimer); downloadLink.hidden = true; statusError.textContent = '';
  startBtn.disabled = true; startBtn.textContent = 'Export läuft'; statusMessage.textContent = 'Export wird gestartet.'; statusMeta.textContent = '';
  const formData = new FormData(form); const payload = Object.fromEntries(formData.entries()); payload.platforms = formData.getAll('platforms');
  const response = await fetch('/api/export', { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(payload) });
  const job = await response.json();
  if (!response.ok) { setStatus({status:'failed', message:'Export konnte nicht gestartet werden.', error:job.error || 'Unbekannter Fehler'}); return; }
  setStatus(job); pollTimer = setInterval(() => poll(job.id).catch(error => { statusError.textContent = String(error); }), 1000);
});
</script>
</body>
</html>"""


def send_json(handler: BaseHTTPRequestHandler, payload: dict[str, Any], status: int = 200) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def job_to_json(job: ExportJob) -> dict[str, Any]:
    result = {"id": job.id, "status": job.status, "message": job.message, "orders": job.orders, "rows": job.rows_mode, "date_from": job.date_from, "date_to": job.date_to, "platforms": job.platforms, "error": job.error}
    if job.output_file:
        result["download_url"] = f"/download/{job.output_file}"
    return result


def safe_date(value: str, name: str) -> date:
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as exc:
        raise ValueError(f"{name} muss ein gültiges Datum sein.") from exc


def export_worker(job_id: str, payload: dict[str, Any]) -> None:
    with jobs_lock:
        job = jobs[job_id]
        job.status = "running"
        job.message = "Bestellungen werden aus Billbee geladen."
    try:
        rows_mode = str(payload["rows"])
        date_from = str(payload["date_from"])
        date_to = str(payload["date_to"])
        platforms = list(payload.get("platforms", []))
        page_size = int(payload.get("page_size", 50))
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        platform_part = "alle" if not platforms else "_".join(platforms)
        filename = re.sub(r"[^A-Za-z0-9_.-]+", "_", f"billbee_export_{date_from}_bis_{date_to}_{rows_mode}_{platform_part}_{timestamp}.csv")
        output_path = EXPORT_DIR / filename
        args = SimpleNamespace(
            api_key=os.getenv("BILLBEE_API_KEY") or exporter.BILLBEE_API_KEY,
            username=os.getenv("BILLBEE_USERNAME") or exporter.BILLBEE_USERNAME,
            api_password=os.getenv("BILLBEE_API_PASSWORD") or exporter.BILLBEE_API_PASSWORD,
            base_url=os.getenv("BILLBEE_BASE_URL") or exporter.DEFAULT_BASE_URL,
            output=str(output_path), format="csv", list_fields=False, rows=rows_mode,
            min_order_date=f"{date_from}T00:00:00", max_order_date=f"{date_to}T23:59:59",
            modified_at_min=None, modified_at_max=None, minimum_billbee_order_id=None,
            shop_id=[], order_state_id=[], tag=[], exclude_tags=False,
            platform=["" if platform == "__blank__" else platform for platform in platforms],
            page_size=page_size, max_pages=None, timeout=240,
        )
        exporter.require_credentials(args)
        client = exporter.BillbeeClient(args.base_url, args.api_key, args.username, args.api_password)
        orders = exporter.filter_orders(exporter.fetch_orders(client, args), args.platform)
        with jobs_lock:
            jobs[job_id].orders = len(orders)
            jobs[job_id].message = "CSV wird geschrieben."
        exporter.write_csv(str(output_path), orders, rows_mode)
        with jobs_lock:
            job = jobs[job_id]
            job.status = "done"
            job.output_file = filename
            job.message = "Export fertig."
            job.orders = len(orders)
    except Exception as exc:
        with jobs_lock:
            job = jobs[job_id]
            job.status = "failed"
            job.error = str(exc)
            job.message = "Export fehlgeschlagen."


class BillbeeAppHandler(BaseHTTPRequestHandler):
    server_version = "BillbeeExportApp/1.0"

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/":
            body = html_page().encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if parsed.path == "/api/status":
            query = dict(part.split("=", 1) for part in parsed.query.split("&") if "=" in part)
            with jobs_lock:
                job = jobs.get(query.get("id", ""))
            if not job:
                send_json(self, {"error": "Job nicht gefunden."}, HTTPStatus.NOT_FOUND)
                return
            send_json(self, job_to_json(job))
            return
        if parsed.path.startswith("/download/"):
            self.send_download(unquote(parsed.path.removeprefix("/download/")))
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        if urlparse(self.path).path != "/api/export":
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        try:
            length = int(self.headers.get("Content-Length", "0") or "0")
            payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
            date_from = safe_date(str(payload.get("date_from", "")), "Von")
            date_to = safe_date(str(payload.get("date_to", "")), "Bis")
            rows_mode = str(payload.get("rows", "all"))
            page_size = int(payload.get("page_size", 50))
            raw_platforms = payload.get("platforms", [])
            platforms = raw_platforms if isinstance(raw_platforms, list) else [raw_platforms]
            if date_to < date_from:
                raise ValueError("Bis-Datum darf nicht vor dem Von-Datum liegen.")
            if rows_mode not in ALLOWED_ROWS:
                raise ValueError("Exportart ist ungültig.")
            if page_size not in {50, 100, 250}:
                raise ValueError("Seitengröße ist ungültig.")
            invalid_platforms = [platform for platform in platforms if platform not in ALLOWED_PLATFORMS]
            if invalid_platforms:
                raise ValueError("Mindestens eine Plattform ist ungültig.")
            job_id = uuid.uuid4().hex
            job = ExportJob(id=job_id, rows_mode=rows_mode, date_from=date_from.isoformat(), date_to=date_to.isoformat(), platforms=platforms)
            with jobs_lock:
                jobs[job_id] = job
            threading.Thread(target=export_worker, args=(job_id, {"date_from": date_from.isoformat(), "date_to": date_to.isoformat(), "rows": rows_mode, "page_size": page_size, "platforms": platforms}), daemon=True).start()
            send_json(self, job_to_json(job), HTTPStatus.ACCEPTED)
        except Exception as exc:
            send_json(self, {"error": str(exc)}, HTTPStatus.BAD_REQUEST)

    def send_download(self, filename: str) -> None:
        if "/" in filename or "\\" in filename:
            self.send_error(HTTPStatus.BAD_REQUEST)
            return
        path = EXPORT_DIR / filename
        if not path.exists() or not path.is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        body = path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", mimetypes.guess_type(path.name)[0] or "application/octet-stream")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Content-Disposition", f'attachment; filename="{path.name}"')
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:
        print(f"{self.address_string()} - {format % args}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Start the local Billbee export app.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    EXPORT_DIR.mkdir(exist_ok=True)
    server = ThreadingHTTPServer((args.host, args.port), BillbeeAppHandler)
    print(f"Billbee Export App läuft auf http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nServer beendet.")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
