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
from PIL import Image
import io
from common import load_policy, get_cert_dir, get_data_dir, SIGNATURE_KDF_ITERATIONS
from app_config import logger
from smart_parsing import is_drive_ssd
from verification import get_software_versions

# Medium #38: Certificate chain validation is not required for standalone certificates.
# These certificates are self-contained attestations of data erasure, not part of a PKI hierarchy.
# Each certificate is independently signed with HMAC-SHA256 using a shared passphrase (if configured).
# There is no certificate authority, no intermediate certificates, and no chain of trust to validate.
# The signature integrity is verified by recomputing the HMAC with the known passphrase and comparing
# it to the stored signature value. This is a simple integrity check, not a PKI chain validation.

# Certificate CSS has been externalized to frontend/css/certificate.css
# Certificate HTML templates reference it via <link rel="stylesheet" href="/css/certificate.css">

def get_custom_logo_base64():
    """Load and convert custom logo to base64 data URI if it exists."""
    logo_path = os.path.join(get_data_dir(), "logo.png")
    hash_path = logo_path + ".sha256"

    if not os.path.exists(logo_path):
        return ""

    try:
        # Medium #60: Load configurable logo size limit from policy
        max_size_mb = 1  # Default fallback
        try:
            policy = load_policy()
            max_size_mb = policy.get("max_logo_size_mb", 1)
        except Exception:
            pass  # Use default if policy loading fails

        # Check file size against configurable limit
        file_size = os.path.getsize(logo_path)
        max_size_bytes = max_size_mb * 1024 * 1024
        if file_size > max_size_bytes:
            logger.warning(f"Logo file exceeds {max_size_mb}MB limit: {file_size} bytes")
            return ""
        
        # Validate file integrity by checking hash
        if os.path.exists(hash_path):
            with open(logo_path, "rb") as f:
                file_bytes = f.read()
                current_hash = hashlib.sha256(file_bytes).hexdigest()
            with open(hash_path, "r") as f:
                stored_hash = f.read().strip()
            if current_hash != stored_hash:
                logger.warning(f"Logo file integrity check failed: hash mismatch. File may have been tampered with.")
                return ""
        
        # Open and validate image
        with Image.open(logo_path) as img:
            # Validate format (only PNG, JPEG allowed)
            if img.format not in ("PNG", "JPEG"):
                logger.warning(f"Unsupported logo format: {img.format}")
                return ""
            
            # Convert to PNG bytes (HTML CSS will handle display sizing)
            buffer = io.BytesIO()
            img.save(buffer, format="PNG")
            img_bytes = buffer.getvalue()
            
            # Convert to base64 data URI
            base64_str = base64.b64encode(img_bytes).decode("utf-8")
            return f"data:image/png;base64,{base64_str}"
    
    except Exception as e:
        logger.warning(f"Failed to load custom logo: {str(e)}")
        return ""

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

def serialize_safe(item):
    """Safely serialize item to string, handling non-JSON-serializable types."""
    try:
        return json.dumps(item, sort_keys=True, default=str)
    except (TypeError, ValueError):
        return str(item)

def summarize_array(arr, max_items=5):
    """Collapse large arrays to summary format for HTML display."""
    if not isinstance(arr, list) or len(arr) == 0 or len(arr) <= max_items:
        return None
    
    # Check if all items are identical using JSON serialization for accurate comparison
    unique_items = list(set(serialize_safe(item) for item in arr))
    if len(unique_items) == 1:
        sample = str(arr[0])
        # Truncate long strings (hashes)
        if len(sample) > 20:
            sample = sample[:20] + "..."
        return f"{len(arr)} items, all identical: {sample}"
    
    # Mixed items - show first and last
    first = str(arr[0])
    last = str(arr[-1])
    if len(first) > 20:
        first = first[:20] + "..."
    if len(last) > 20:
        last = last[:20] + "..."
    return f"{len(arr)} items: [{first}, ..., {last}]"

def json_cell(value):
    def process(val, visited=None):
        """Recursively process value without HTML escaping, with cycle detection."""
        if visited is None:
            visited = set()
        
        # Cycle detection: use object id to track visited containers
        val_id = id(val)
        if val_id in visited:
            return "[circular reference]"
        
        if isinstance(val, (list, dict)):
            visited.add(val_id)
            try:
                if isinstance(val, list):
                    summary = summarize_array(val)
                    if summary:
                        return summary
                    return [process(item, visited) for item in val]
                if isinstance(val, dict):
                    return {k: process(v, visited) for k, v in val.items()}
            finally:
                visited.remove(val_id)
        
        return val if val is not None else ""
    
    processed = process(value)
    if isinstance(processed, (list, dict)):
        return html.escape(json.dumps(processed, indent=2, sort_keys=True))
    return html.escape(str(processed))

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
        "claim_limitations": "Certificate describes tool/controller attestation with independent sampled verification. Does not assert third-party certification."
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

def _esc(value):
    return html.escape(str(value if value is not None else ""))

def _apply_common_template_replacements(content, certificate, custom_logo=None):
    """Apply common template placeholder replacements shared between full and bulk certificate templates.

    Returns content with common replacements applied. Caller is responsible for
    applying any template-specific replacements (e.g., SMART_DIFF_ROWS, VERIFICATION_DETAILS).
    """
    verification = certificate.get("verification") or {}
    ok = verification.get("ok")

    title = "Certificate of Data Erasure" if ok else "Certificate of Sanitization Failure"
    title_class = "cert-title-ok" if ok else "cert-title-fail"
    status_class = "status-ok" if ok else "status-fail"
    status_text = _esc(verification.get("status"))

    standard_rows = "".join(
        f"<tr><th>Standard Claim: {_esc(k)}</th><td>{json_cell(v)}</td></tr>"
        for k, v in sorted((certificate.get("standard_claims") or {}).items(), key=lambda item: str(item[0]))
    )

    software_versions = certificate.get("software_versions") or {}
    software_versions_text = "; ".join(f"{k}: {v}" for k, v in sorted(software_versions.items())) if software_versions else "Not available"

    if custom_logo is None:
        custom_logo = get_custom_logo_base64()
    logo_img = f'<img src="{custom_logo}" alt="Logo" class="cert-logo">' if custom_logo else ""

    content = content.replace("{{TITLE}}", _esc(title))
    content = content.replace("{{TITLE_CLASS}}", _esc(title_class))
    content = content.replace("{{LOGO_IMG}}", logo_img)
    content = content.replace("{{FRIENDLY_ID}}", _esc(certificate.get("friendly_id")))
    content = content.replace("{{STARTED_AT}}", _esc(certificate.get("started_at")))
    content = content.replace("{{FINISHED_AT}}", _esc(certificate.get("finished_at")))
    content = content.replace("{{TICKET_NUMBER}}", _esc(certificate.get("ticket_number")))
    content = content.replace("{{SERIAL}}", _esc(certificate.get("serial")))
    content = content.replace("{{MODEL}}", _esc(certificate.get("model")))
    content = content.replace("{{INTERFACE_TYPE}}", _esc(certificate.get("interface_type")))
    content = content.replace("{{DRIVE_TYPE}}", _esc(certificate.get("drive_type")))
    content = content.replace("{{METHOD}}", _esc(certificate.get("method")))
    content = content.replace("{{SOFTWARE_VERSIONS}}", _esc(software_versions_text))
    content = content.replace("{{STATUS_CLASS}}", _esc(status_class))
    content = content.replace("{{STATUS_TEXT}}", _esc(status_text))
    content = content.replace("{{STANDARD_ROWS}}", standard_rows)
    content = content.replace("{{SIGNATURE_STATUS}}", _esc((certificate.get("signature_meta") or {}).get("status")))
    content = content.replace("{{SIGNATURE}}", _esc(certificate.get("signature")))

    return content

def build_certificate_html(certificate):
    verification = certificate.get("verification") or {}

    # Build SMART diff rows if available
    smart_diff = certificate.get("smart_diff") or {}
    smart_diff_rows = ""
    if smart_diff.get("worsened_metrics"):
        worsened = smart_diff["worsened_metrics"]
        for metric in worsened:
            pre = metric.get("pre_value", "N/A")
            post = metric.get("post_value", "N/A")
            delta = metric.get("delta", "N/A")
            metric_name = metric.get("metric", "Unknown")
            smart_diff_rows += f"<tr><th>SMART Degradation: {_esc(metric_name)}</th><td>Pre: {_esc(pre)} → Post: {_esc(post)} (Delta: {_esc(delta)})</td></tr>"
    else:
        smart_diff_rows = '<tr><th>SMART Health Comparison</th><td>No significant SMART metric degradation detected during wipe</td></tr>'

    template = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{{TITLE}}</title>
<link rel="stylesheet" href="/css/certificate.css">
</head>
<body>
<div class="certificate-container">
<div class="header">
  <div class="header-left">
    <h1 class="{{TITLE_CLASS}}">{{TITLE}}</h1>
  </div>
  <div class="header-right">
    {{LOGO_IMG}}
  </div>
</div>
<div class="section">
<table>
<tr><th>Certificate ID</th><td>{{FRIENDLY_ID}}</td></tr>
<tr><th>Started At</th><td>{{STARTED_AT}}</td></tr>
<tr><th>Finished At</th><td>{{FINISHED_AT}}</td></tr>
<tr><th>Ticket Number</th><td>{{TICKET_NUMBER}}</td></tr>
<tr><th>Station ID</th><td>{{STATION_ID}}</td></tr>
<tr><th>Bay Slot</th><td>{{BAY}}</td></tr>
<tr><th>System Device</th><td>{{DEVICE}}</td></tr>
<tr><th>Serial Number</th><td>{{SERIAL}}</td></tr>
<tr><th>Model String</th><td>{{MODEL}}</td></tr>
<tr><th>Capacity Bytes</th><td>{{CAPACITY_BYTES}}</td></tr>
<tr><th>Interface Type / Drive Type</th><td>{{INTERFACE_TYPE}} / {{DRIVE_TYPE}}</td></tr>
<tr><th>Method Used</th><td>{{METHOD}}</td></tr>
<tr><th>Software Versions</th><td>{{SOFTWARE_VERSIONS}}</td></tr>
<tr><th>Verification Integrity</th><td class="{{STATUS_CLASS}}">{{STATUS_TEXT}}</td></tr>
<tr><th>Verification Details</th><td><pre>{{VERIFICATION_DETAILS}}</pre></td></tr>
{{SMART_DIFF_ROWS}}
{{STANDARD_ROWS}}
<tr><th>Certificate Integrity</th><td>{{SIGNATURE_STATUS}}</td></tr>
<tr><th>Audit Signature (HMAC)</th><td><small>{{SIGNATURE}}</small></td></tr>
</table>
</div>
</div>
</body>
</html>
"""

    content = _apply_common_template_replacements(template, certificate)

    # Apply full-template-specific replacements
    content = content.replace("{{STATION_ID}}", _esc(certificate.get("station_id")))
    content = content.replace("{{BAY}}", _esc(certificate.get("bay")))
    content = content.replace("{{DEVICE}}", _esc(certificate.get("device")))
    content = content.replace("{{CAPACITY_BYTES}}", _esc(certificate.get("capacity_bytes")))
    verification_details = verification.get("details") or {}
    if verification.get("error"):
        verification_details = dict(verification_details)
        verification_details["error"] = verification.get("error")
    content = content.replace("{{VERIFICATION_DETAILS}}", json_cell(verification_details))
    content = content.replace("{{SMART_DIFF_ROWS}}", smart_diff_rows)

    return content

def build_bulk_certificate_html(certificates):
    """Generate a single HTML file containing multiple certificates for bulk printing."""
    if not certificates:
        return "<!doctype html><html><body><p>No certificates found.</p></body></html>"
    
    # Load logo once to avoid repeated file I/O for bulk operations
    custom_logo = get_custom_logo_base64()
    
    # Generate HTML for each certificate using simplified bulk template
    cert_htmls = []
    failed_count = 0
    for cert in certificates:
        try:
            cert_html = build_bulk_single_certificate_html(cert, custom_logo)
            cert_htmls.append(cert_html)
        except Exception as e:
            # Skip certificates that fail to generate HTML
            logger.warning(f"Skipping certificate with id {cert.get('id', 'unknown')}: failed to generate HTML: {str(e)}")
            failed_count += 1
            continue
    
    # If all certificates failed, return error message
    if failed_count == len(certificates):
        return "<!doctype html><html><body><p>All certificates failed to generate. Check certificate data format.</p></body></html>"
    
    # Build bulk HTML with shared head and individual certificate bodies
    bulk_template = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Bulk Certificates</title>
<link rel="stylesheet" href="/css/certificate.css">
</head>
<body>
{{CERTIFICATE_BODIES}}
</body>
</html>
"""
    
    content = bulk_template.replace("{{CERTIFICATE_BODIES}}", "\n".join(cert_htmls))
    return content

def build_bulk_single_certificate_html(certificate, custom_logo=None):
    """Generate HTML for a single certificate using simplified bulk template.
    
    Args:
        certificate: Certificate data dictionary
        custom_logo: Pre-loaded base64 logo data (optional, to avoid repeated file I/O)
    """
    # Get verification level from evidence (bulk template only)
    verification_evidence = certificate.get("verification_evidence") or {}
    verification_level = verification_evidence.get("verification_level") or "Not specified"

    template = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{{TITLE}}</title>
<link rel="stylesheet" href="/css/certificate.css">
</head>
<body>
<div class="certificate-container">
<div class="header">
  <div class="header-left">
    <h1 class="{{TITLE_CLASS}}">{{TITLE}}</h1>
  </div>
  <div class="header-right">
    {{LOGO_IMG}}
  </div>
</div>
<div class="section">
<table>
<tr><th>Certificate ID</th><td>{{FRIENDLY_ID}}</td></tr>
<tr><th>Started At</th><td>{{STARTED_AT}}</td></tr>
<tr><th>Finished At</th><td>{{FINISHED_AT}}</td></tr>
<tr><th>Ticket Number</th><td>{{TICKET_NUMBER}}</td></tr>
<tr><th>Serial Number</th><td>{{SERIAL}}</td></tr>
<tr><th>Model String</th><td>{{MODEL}}</td></tr>
<tr><th>Interface Type / Drive Type</th><td>{{INTERFACE_TYPE}} / {{DRIVE_TYPE}}</td></tr>
<tr><th>Method Used</th><td>{{METHOD}}</td></tr>
<tr><th>Software Versions</th><td>{{SOFTWARE_VERSIONS}}</td></tr>
<tr><th>Verification Integrity</th><td class="{{STATUS_CLASS}}">{{STATUS_TEXT}}</td></tr>
<tr><th>Verification Level</th><td>{{VERIFICATION_LEVEL}}</td></tr>
{{STANDARD_ROWS}}
<tr><th>Certificate Integrity</th><td>{{SIGNATURE_STATUS}}</td></tr>
<tr><th>Audit Signature (HMAC)</th><td><small>{{SIGNATURE}}</small></td></tr>
</table>
</div>
</div>
</body>
</html>
"""

    content = _apply_common_template_replacements(template, certificate, custom_logo)

    # Apply bulk-template-specific replacements
    content = content.replace("{{VERIFICATION_LEVEL}}", _esc(verification_level))

    return content

def build_certificate(job):
    request_data = job.get("request") or {}
    verification = job.get("verification") or {}
    finished_at = job.get("finished_at") or datetime.now(timezone.utc).isoformat()
    issued_at = datetime.now(timezone.utc).isoformat()
    friendly_id = job.get("friendly_id") or "CERT-**********"
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
    
    # Determine drive type (HDD/SSD)
    interface_type = request_data.get("interface_type")
    smart_data = request_data.get("smart_data") or {}
    drive_type = "SSD" if is_drive_ssd(interface_type, smart_data) else "HDD"
    
    # Capture software versions
    software_versions = get_software_versions()
    
    # Medium #39: Detect and log bad sectors from SMART data
    bad_sectors_info = {
        "detected": False,
        "reallocated_sectors": None,
        "pending_sectors": None,
        "reallocated_normalized": None,
        "reallocated_threshold": None,
    }
    if smart_data:
        realloc = smart_data.get("reallocated_sectors")
        pending = smart_data.get("pending_sectors")
        realloc_norm = smart_data.get("reallocated_normalized")
        realloc_thresh = smart_data.get("reallocated_threshold")
        
        # Bad sectors are detected if there are any reallocated or pending sectors
        has_bad_sectors = (realloc is not None and realloc > 0) or (pending is not None and pending > 0)
        
        bad_sectors_info = {
            "detected": has_bad_sectors,
            "reallocated_sectors": realloc,
            "pending_sectors": pending,
            "reallocated_normalized": realloc_norm,
            "reallocated_threshold": realloc_thresh,
        }
    
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
        "interface_type": interface_type,
        "drive_type": drive_type,
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
        "software_versions": software_versions,
        "bad_sectors": bad_sectors_info,
        "smart_diff": job.get("smart_diff"),
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