# --- START OF FILE backend/certificates.py ---
import os
import json
import html
import hashlib
import hmac
import copy
from datetime import datetime, timezone
from common import load_policy, get_cert_dir

def calculate_certificate_hash(certificate, passphrase):
    if not passphrase:
        return "unsigned_local"
    
    # Create a deep copy and strip out non-signed / post-signing metadata to secure all fields
    cert_copy = copy.deepcopy(certificate)
    cert_copy.pop("signature", None)
    cert_copy.pop("path", None)
    cert_copy.pop("filename", None)
    cert_copy.pop("formats", None)
    
    # Serialize deterministically with sorted keys
    serialized = json.dumps(cert_copy, sort_keys=True, separators=(",", ":")).encode("utf-8")
    derived_key = hashlib.pbkdf2_hmac("sha256", passphrase.encode("utf-8"), b"DWS_SALT_v1", 10000)
    return hmac.new(derived_key, serialized, hashlib.sha256).hexdigest()

def build_certificate_html(certificate):
    def esc(value):
        return html.escape(str(value if value is not None else ""))

    verification = certificate.get("verification") or {}
    ok = verification.get("ok")
    
    # Dynamic header title and color accents based on physical wipe status
    title = "Certificate of Data Destruction" if ok else "Certificate of Sanitization Failure"
    header_color = "#1e3a8a" if ok else "#dc2626"
    status_class = "status-ok" if ok else "status-fail"
    status_text = esc(verification.get("status"))

    details = verification.get("details") or {}
    detail_rows = "".join(
        f"<tr><th>{esc(k)}</th><td>{esc(v)}</td></tr>" for k, v in sorted(details.items(), key=lambda item: str(item[0]))
    )
    if not detail_rows:
        detail_rows = "<tr><th>details</th><td>none</td></tr>"

    template = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{{TITLE}}</title>
<style>
body { font-family: Arial, sans-serif; margin: 32px; color: #111; line-height: 1.4; }
h1 { margin: 0 0 6px 0; color: {{HEADER_COLOR}}; }
.meta { color: #555; margin-bottom: 24px; font-family: monospace; font-size: 1.1rem; }
.section { margin-bottom: 20px; }
table { border-collapse: collapse; width: 100%; margin-top: 10px; }
th, td { border: 1px solid #ccc; padding: 10px; text-align: left; vertical-align: top; }
th { width: 240px; background: #f8fafc; color: #334155; }
.status-ok { color: #16a34a; font-weight: 700; text-transform: uppercase; }
.status-fail { color: #dc2626; font-weight: 700; text-transform: uppercase; }
</style>
</head>
<body>
<h1>{{TITLE}}</h1>
<div class="meta">Certificate Ref: {{CERTIFICATE_ID}}</div>
<div class="section">
<table>
<tr><th>Job Number</th><td>{{FRIENDLY_ID}}</td></tr>
<tr><th>Issued At</th><td>{{ISSUED_AT}}</td></tr>
<tr><th>Finished At</th><td>{{FINISHED_AT}}</td></tr>
<tr><th>Technician</th><td>{{TECHNICIAN}}</td></tr>
<tr><th>Ticket Number</th><td>{{TICKET_NUMBER}}</td></tr>
<tr><th>Bay Slot</th><td>{{BAY}}</td></tr>
<tr><th>System Device</th><td>{{DEVICE}}</td></tr>
<tr><th>Serial Number</th><td>{{SERIAL}}</td></tr>
<tr><th>Model String</th><td>{{MODEL}}</td></tr>
<tr><th>Interface protocol</th><td>{{INTERFACE_TYPE}}</td></tr>
<tr><th>Method Used</th><td>{{METHOD}}</td></tr>
<tr><th>Verification Integrity</th><td class="{{STATUS_CLASS}}">{{STATUS_TEXT}}</td></tr>
<tr><th>Audit Signature (HMAC)</th><td><small>{{SIGNATURE}}</small></td></tr>
{{DETAIL_ROWS}}
</table>
</div>
</body>
</html>
"""

    # Run clean, robust text replacements to completely bypass quote-nesting quirks
    content = template
    content = content.replace("{{TITLE}}", esc(title))
    content = content.replace("{{HEADER_COLOR}}", esc(header_color))
    content = content.replace("{{CERTIFICATE_ID}}", esc(certificate.get("id")))
    content = content.replace("{{FRIENDLY_ID}}", esc(certificate.get("friendly_id")))
    content = content.replace("{{ISSUED_AT}}", esc(certificate.get("issued_at")))
    content = content.replace("{{FINISHED_AT}}", esc(certificate.get("finished_at")))
    content = content.replace("{{TECHNICIAN}}", esc(certificate.get("technician")))
    content = content.replace("{{TICKET_NUMBER}}", esc(certificate.get("ticket_number")))
    content = content.replace("{{BAY}}", esc(certificate.get("bay")))
    content = content.replace("{{DEVICE}}", esc(certificate.get("device")))
    content = content.replace("{{SERIAL}}", esc(certificate.get("serial")))
    content = content.replace("{{MODEL}}", esc(certificate.get("model")))
    content = content.replace("{{INTERFACE_TYPE}}", esc(certificate.get("interface_type")))
    content = content.replace("{{METHOD}}", esc(certificate.get("method")))
    content = content.replace("{{STATUS_CLASS}}", esc(status_class))
    content = content.replace("{{STATUS_TEXT}}", esc(status_text))
    content = content.replace("{{SIGNATURE}}", esc(certificate.get("signature")))
    content = content.replace("{{DETAIL_ROWS}}", detail_rows)  # Pre-escaped in generator loop

    return content

def build_certificate(job):
    request_data = job.get("request") or {}
    verification = job.get("verification") or {}
    finished_at = job.get("finished_at") or datetime.now(timezone.utc).isoformat()
    issued_at = datetime.now(timezone.utc).isoformat()
    friendly_id = job.get("friendly_id") or "SANI-******"
    certificate_id = f"cert-{friendly_id}"

    passphrase = None
    strict_audit = False
    try:
        policy = load_policy()
        passphrase = policy.get("wipe_passphrase")
        strict_audit = policy.get("strict_audit_mode", False)
    except Exception:
        passphrase = None
        strict_audit = False

    # Force the key block to exist if strict audits are active
    if strict_audit and not passphrase:
        raise ValueError("Passphrase is required in strict audit mode but is not configured or is empty.")

    certificate = {
        "id": certificate_id,
        "job_id": job.get("id"),
        "friendly_id": friendly_id,
        "issued_at": issued_at,
        "finished_at": finished_at,
        "technician": request_data.get("technician"),
        "ticket_number": request_data.get("ticket_number"),
        "bay": request_data.get("bay"),
        "device": request_data.get("device"),
        "serial": request_data.get("serial"),
        "model": request_data.get("model"),
        "interface_type": request_data.get("interface_type"),
        "method": request_data.get("method"),
        "verification": {
            "ok": verification.get("ok"),
            "status": verification.get("status"),
            "error": verification.get("error"),
            "details": verification.get("details") or {},
        },
    }

    certificate["signature"] = calculate_certificate_hash(certificate, passphrase)

    cert_filename = f"{certificate_id}.json"
    cert_path = os.path.join(get_cert_dir(), cert_filename)
    with open(cert_path, "w", encoding="utf-8") as cert_file:
        json.dump(certificate, cert_file, indent=2)

    html_filename = f"{certificate_id}.html"
    html_path = os.path.join(get_cert_dir(), html_filename)
    html_content = build_certificate_html(certificate)
    with open(html_path, "w", encoding="utf-8") as html_file:
        html_file.write(html_content)

    certificate["path"] = cert_path
    certificate["filename"] = cert_filename
    certificate["formats"] = {
        "json": {"filename": cert_filename, "path": cert_path},
        "html": {"filename": html_filename, "path": html_path},
    }
    return certificate
# --- END OF FILE backend/certificates.py ---