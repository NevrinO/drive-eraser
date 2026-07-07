# --- START OF FILE backend/smart_parsing.py ---
# Backward-compatibility re-export shim - do not add new code here
# All functions have been split into topic-specific modules:
#   smart_utils.py, smart_data_parsing.py, smart_health.py,
#   smart_test_runner.py, smart_health_gate.py

from smart_utils import (
    classify_interface_from_smart,
    detect_interface_type,
    is_drive_ssd,
    validate_device_path,
    get_raw_smart_diagnostics,
)
from smart_data_parsing import (
    get_smart_data,
    get_smart_identity,
    get_triage_thresholds,
    stabilize_smart_writes,
    _load_drive_models,
    _DEFAULT_TRIAGE_THRESHOLDS,
)
from smart_health import (
    calculate_drive_health_score,
    get_drive_recommendation,
)
from smart_test_runner import (
    run_smart_test,
    get_smart_test_status,
)
from smart_health_gate import (
    pre_wipe_health_gate,
)

# --- END OF FILE backend/smart_parsing.py ---
