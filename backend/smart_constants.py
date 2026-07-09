# SMART-related constants

# SMART self-test log rollover constants
# SMART self-test log hours use 16-bit counters (max 65,535)
SMART_SELF_TEST_LOG_MAX_HOURS = 65535
SMART_SELF_TEST_LOG_ROLLOVER_BOUNDARY = 65536
SMART_SELF_TEST_AMBIGUOUS_THRESHOLD_HOURS = 1000

# Grace period for SMART test status updates to avoid false detection from stale drive log entries
# The drive's self-test log may not update immediately after starting a test
SMART_TEST_GRACE_PERIOD_SECONDS = 10

# Estimated test durations in seconds. Used to determine how long to wait before
# trusting "completed"/"failed" from the drive's log table when the DB status is
# still "started" (meaning we never confirmed the test was actually running).
# The drive's log table shows the PREVIOUS test's result until the current test
# completes and writes a new entry. For HDDs, the real-time status register can
# take 15-30+ seconds to show "in progress", so the old log entry's "completed"
# status gets falsely trusted after the 10-second grace period.
ESTIMATED_TEST_DURATION_SECONDS = {
    "short": 120,        # ~2 minutes
    "offline": 300,      # ~5 minutes
    "conveyance": 300,   # ~5 minutes
    "extended": 7200,    # ~120 minutes
}


def correct_self_test_log_hours(log_hours, current_poh, historical_poh):
    """Correct SMART self-test log hours for 16-bit counter rollover.

    SMART self-test log hours use 16-bit counters (max 65,535). For drives with
    >65,535 power-on hours, log hours will have rolled over. This function uses
    database history and current POH to determine the correct hours.

    Args:
        log_hours: Raw hours value from the self-test log entry.
        current_poh: Current power-on hours from smartctl.
        historical_poh: Historical POH from database (first recorded), or None.

    Returns:
        Tuple of (corrected_hours, rollover_corrected, ambiguous).
    """
    corrected_hours = log_hours
    rollover_corrected = False
    ambiguous = False

    if current_poh and log_hours is not None:
        if current_poh < SMART_SELF_TEST_LOG_MAX_HOURS:
            # No rollover possible
            corrected_hours = log_hours
        else:
            # POH > 65,535 - rollover has occurred
            # Only correct if we have historical evidence that drive was already over 65,535
            # when we started tracking it (proves this is our system's data)
            if historical_poh and historical_poh > SMART_SELF_TEST_LOG_MAX_HOURS:
                # We know from database that drive was already over 65,535 when we first saw it
                # Calculate rollovers based on current POH (use 65536 for accurate boundary)
                rollover_count = int(current_poh // SMART_SELF_TEST_LOG_ROLLOVER_BOUNDARY)
                corrected_hours = log_hours + (rollover_count * SMART_SELF_TEST_LOG_ROLLOVER_BOUNDARY)
                rollover_corrected = True
                # Flag ambiguous if near rollover boundary (within 1000 hours)
                # or if log hours differ significantly from expected corrected hours
                if current_poh > SMART_SELF_TEST_LOG_MAX_HOURS and (
                    abs(current_poh % SMART_SELF_TEST_LOG_MAX_HOURS) < SMART_SELF_TEST_AMBIGUOUS_THRESHOLD_HOURS
                    or abs(current_poh - corrected_hours) > SMART_SELF_TEST_AMBIGUOUS_THRESHOLD_HOURS
                ):
                    ambiguous = True
            else:
                # No database history or drive was under 65,535 when we first saw it
                # Don't correct - these may be from another system or before rollover
                corrected_hours = log_hours

    return corrected_hours, rollover_corrected, ambiguous
