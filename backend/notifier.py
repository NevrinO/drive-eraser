# --- START OF FILE backend/notifier.py ---
import json
import urllib.request
from common import load_policy

def send_slack_notification(job, status_override=None):
    try:
        policy = load_policy()
        webhook_url = policy.get("slack_webhook_url")
        if not webhook_url:
            return

        request_data = job.get("request") or {}
        serial = request_data.get("serial") or "Unknown"
        model = request_data.get("model") or "Unknown"
        bay = request_data.get("bay") or "Unknown"
        method = request_data.get("method") or "Unknown"
        ticket = request_data.get("ticket_number") or "Unknown"
        tech = request_data.get("technician") or "Unknown"
        friendly_id = job.get("friendly_id") or "SANI-******"

        status_str = status_override or job.get("status") or "UNKNOWN"
        is_failed = status_str.upper() == "FAILED" or "FAIL" in status_str.upper()
        status_badge = "🔴 FAILED" if is_failed else "🟢 PASSED"
        if status_str.upper() == "RUNNING":
            status_badge = "🔵 RUNNING"

        text_block = (
            f"============================================================\n"
            f"💾 DRIVE SANITIZATION REPORT\n"
            f"============================================================\n"
            f"Job ID:       {friendly_id}\n"
            f"Ticket #:     {ticket}\n"
            f"Technician:   {tech}\n"
            f"Drive:        {model} (S/N: {serial})\n"
            f"Bay Location: {bay.upper()}\n"
            f"Method Code:  {method}\n"
            f"\n"
            f"STATUS:       {status_badge} - {status_str.upper()}\n"
            f"============================================================"
        )

        payload = {"text": text_block}
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            webhook_url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req) as response:
            response.read()
    except Exception:
        pass
# --- END OF FILE backend/notifier.py ---