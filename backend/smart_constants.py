# SMART-related constants

# SMART self-test log rollover constants
# SMART self-test log hours use 16-bit counters (max 65,535)
SMART_SELF_TEST_LOG_MAX_HOURS = 65535
SMART_SELF_TEST_LOG_ROLLOVER_BOUNDARY = 65536
SMART_SELF_TEST_AMBIGUOUS_THRESHOLD_HOURS = 1000

# Grace period for SMART test status updates to avoid false detection from stale drive log entries
# The drive's self-test log may not update immediately after starting a test
SMART_TEST_GRACE_PERIOD_SECONDS = 10
