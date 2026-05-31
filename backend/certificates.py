# --- START OF FILE backend/certificates.py ---
import os
import json
import html
import hashlib
import hmac
import copy
import base64
import secrets
from datetime import datetime, timezone
from common import load_policy, get_cert_dir

SIGNATURE_KDF_ITERATIONS = 200000

def calculate_certificate_hash(certificate, passphrase, salt=None, iterations=SIGNATURE_KDF_ITERATIONS):
    if not passphrase:
        return "unsigned_local"
    
    cert_copy = copy.deepcopy(certificate)
    cert_copy.pop("signature", None)
    cert_copy.pop("path", None)
    cert_copy.pop("filename", None)
    cert_copy.pop("formats", None)

    serialized = json.dumps(cert_copy, sort_keys=True, separators=(",", ":")).encode("utf-8")
    try:
        salt_bytes = base64.b64decode(salt.encode("ascii")) if salt else b"DWS_SALT_v1"
    except Exception:
        raise ValueError("Invalid base64 salt provided for certificate signature")
    derived_key = hashlib.pbkdf2_hmac("sha256", passphrase.encode("utf-8"), salt_bytes, iterations)
    return hmac.new(derived_key, serialized, hashlib.sha256).hexdigest()

def json_cell(value):
    if isinstance(value, (dict, list)):
        return html.escape(json.dumps(value, indent=2, sort_keys=True))
    return html.escape(str(value if value is not None else ""))

def build_standard_claims(method, interface_type, verification):
    selected_method = str(method or "").lower()
    iface = str(interface_type or "").lower()
    details = verification.get("details") or {}
    if selected_method == "crypto":
        nist_category = "Purge"
        basis = "Controller cryptographic erase/sanitize attestation"
    elif selected_method in {"block", "secure_erase", "enhanced_secure_erase"}:
        nist_category = "Purge" if iface in {"nvme", "sata", "sas"} else "Clear"
        basis = "Controller sanitize/secure erase attestation with supplemental verification"
    elif selected_method == "overwrite":
        nist_category = "Clear"
        basis = "Overwrite with sampled post-erase read verification"
    else:
        nist_category = "Unclassified"
        basis = "Unsupported or unknown sanitization method"

    zero_verified = details.get("secondary_status") == "PASSED" or details.get("verification_level") == "full_overwrite_sampled"
    dod_text = "DoD-style overwrite verification compatible evidence recorded" if selected_method == "overwrite" and zero_verified else "DoD-style overwrite method compliance not claimed"
    return {
        "nist_sp_800_88_category": nist_category,
        "nist_basis": basis,
        "dod_5220_22_m": dod_text,
        "claim_limitations": "Certificate describes observed tool/controller evidence and does not assert third-party certification."
    }

def build_verification_evidence(verification, marker):
    details = verification.get("details") or {}
    return {
        "result": {
            "ok": verification.get("ok"),
            "status": verification.get("status"),
            "error": verification.get("error"),
        },
        "verification_level": details.get("verification_level"),
        "primary": details.get("primary_details") or {k: v for k, v in details.items() if k not in {"secondary_validation", "secondary_status"}},
        "secondary": details.get("secondary_validation"),
        "supplemental_marker": {
            "ok": (marker or {}).get("ok"),
            "status": (marker or {}).get("status"),
            "error": (marker or {}).get("error"),
            "details": (marker or {}).get("details") or {},
            "standards_role": "Supplemental station marker; not required by NIST SP 800-88 or DoD 5220.22-M."
        }
    }

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

    standard_rows = "".join(
        f"<tr><th>Standard Claim: {esc(k)}</th><td>{json_cell(v)}</td></tr>"
        for k, v in sorted((certificate.get("standard_claims") or {}).items(), key=lambda item: str(item[0]))
    )
    evidence_rows = "".join(
        f"<tr><th>Evidence: {esc(k)}</th><td><pre>{json_cell(v)}</pre></td></tr>"
        for k, v in sorted((certificate.get("verification_evidence") or {}).items(), key=lambda item: str(item[0]))
    )

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
pre { white-space: pre-wrap; margin: 0; font-family: monospace; font-size: 12px; }
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
<tr><th>Started At</th><td>{{STARTED_AT}}</td></tr>
<tr><th>Finished At</th><td>{{FINISHED_AT}}</td></tr>
<tr><th>Ticket Number</th><td>{{TICKET_NUMBER}}</td></tr>
<tr><th>Station ID</th><td>{{STATION_ID}}</td></tr>
<tr><th>Bay Slot</th><td>{{BAY}}</td></tr>
<tr><th>System Device</th><td>{{DEVICE}}</td></tr>
<tr><th>Serial Number</th><td>{{SERIAL}}</td></tr>
<tr><th>Model String</th><td>{{MODEL}}</td></tr>
<tr><th>Capacity Bytes</th><td>{{CAPACITY_BYTES}}</td></tr>
<tr><th>Interface protocol</th><td>{{INTERFACE_TYPE}}</td></tr>
<tr><th>Method Used</th><td>{{METHOD}}</td></tr>
<tr><th>Recommended Method</th><td>{{RECOMMENDED_METHOD}}</td></tr>
<tr><th>Method Override Used</th><td>{{METHOD_OVERRIDE}}</td></tr>
<tr><th>Verification Integrity</th><td class="{{STATUS_CLASS}}">{{STATUS_TEXT}}</td></tr>
<tr><th>Certificate Integrity</th><td>{{SIGNATURE_STATUS}}</td></tr>
<tr><th>Audit Signature (HMAC)</th><td><small>{{SIGNATURE}}</small></td></tr>
{{STANDARD_ROWS}}
{{EVIDENCE_ROWS}}
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
    content = content.replace("{{STARTED_AT}}", esc(certificate.get("started_at")))
    content = content.replace("{{FINISHED_AT}}", esc(certificate.get("finished_at")))
    content = content.replace("{{TICKET_NUMBER}}", esc(certificate.get("ticket_number")))
    content = content.replace("{{STATION_ID}}", esc(certificate.get("station_id")))
    content = content.replace("{{BAY}}", esc(certificate.get("bay")))
    content = content.replace("{{DEVICE}}", esc(certificate.get("device")))
    content = content.replace("{{SERIAL}}", esc(certificate.get("serial")))
    content = content.replace("{{MODEL}}", esc(certificate.get("model")))
    content = content.replace("{{CAPACITY_BYTES}}", esc(certificate.get("capacity_bytes")))
    content = content.replace("{{INTERFACE_TYPE}}", esc(certificate.get("interface_type")))
    content = content.replace("{{METHOD}}", esc(certificate.get("method")))
    content = content.replace("{{RECOMMENDED_METHOD}}", esc(certificate.get("recommended_method")))
    content = content.replace("{{METHOD_OVERRIDE}}", esc(certificate.get("method_override_used")))
    content = content.replace("{{STATUS_CLASS}}", esc(status_class))
    content = content.replace("{{STATUS_TEXT}}", esc(status_text))
    content = content.replace("{{SIGNATURE_STATUS}}", esc((certificate.get("signature_meta") or {}).get("status")))
    content = content.replace("{{SIGNATURE}}", esc(certificate.get("signature")))
    content = content.replace("{{STANDARD_ROWS}}", standard_rows)
    content = content.replace("{{EVIDENCE_ROWS}}", evidence_rows)

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
    policy = {}
    try:
        policy = load_policy()
        passphrase = policy.get("wipe_passphrase")
        strict_audit = policy.get("strict_audit_mode", False)
    except Exception:
        passphrase = None
        strict_audit = False

    if strict_audit and not passphrase:
        raise ValueError("Passphrase is required in strict audit mode but is not configured or is empty.")

    marker = job.get("marker") or {}
    method = request_data.get("method")
    recommended_method = request_data.get("recommended_method")
    signature_salt = base64.b64encode(secrets.token_bytes(16)).decode("ascii") if passphrase else None
    certificate = {
        "id": certificate_id,
        "job_id": job.get("id"),
        "friendly_id": friendly_id,
        "issued_at": issued_at,
        "started_at": job.get("started_at"),
        "finished_at": finished_at,
        "station_id": policy.get("station_id"),
        "ticket_number": request_data.get("ticket_number"),
        "bay": request_data.get("bay"),
        "device": request_data.get("device"),
        "serial": request_data.get("serial"),
        "model": request_data.get("model"),
        "capacity_bytes": request_data.get("capacity_bytes"),
        "interface_type": request_data.get("interface_type"),
        "method": method,
        "recommended_method": recommended_method,
        "method_override_used": bool(recommended_method and method and method != recommended_method),
        "verification": {
            "ok": verification.get("ok"),
            "status": verification.get("status"),
            "error": verification.get("error"),
            "details": verification.get("details") or {},
        },
        "standard_claims": build_standard_claims(method, request_data.get("interface_type"), verification),
        "verification_evidence": build_verification_evidence(verification, marker),
    }

    certificate["signature_meta"] = {
        "status": "signed_hmac_sha256" if passphrase else "unsigned_local",
        "strict_audit_mode": bool(strict_audit),
        "kdf": "pbkdf2_hmac_sha256" if passphrase else None,
        "iterations": SIGNATURE_KDF_ITERATIONS if passphrase else None,
        "salt": signature_salt,
    }
    certificate["signature"] = calculate_certificate_hash(certificate, passphrase, signature_salt, SIGNATURE_KDF_ITERATIONS)

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