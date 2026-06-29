# Discovery signal/interrupt state — extracted from disk_ops.py (A70)
# Leaf module: shared state for discovery.py and extended_smart.py to avoid circular imports

import threading

# Global generation counter for discovery interruption.
# Uses a monotonically increasing counter instead of a boolean flag to avoid
# the cross-operation reset race (Lesson #101). Each discovery captures the
# generation in a thread-local and compares it to detect signals since then.
_discovery_interrupt_generation = 0
_discovery_thread_state = threading.local()

# Shared shutdown event for background SMART pool and discovery
_shutdown_event = threading.Event()


def _handle_discovery_signal(signum, frame):
    """Signal handler for SIGTERM/SIGINT during discovery operations.

    Uses lock-free atomic increment (safe under CPython GIL) to avoid
    deadlock if signal arrives while _check_discovery_interrupted() is reading.
    """
    global _discovery_interrupt_generation
    _discovery_interrupt_generation += 1
    _shutdown_event.set()


def _check_discovery_interrupted():
    """Check if discovery was interrupted by signal since this thread's operation started."""
    gen = getattr(_discovery_thread_state, 'generation', None)
    if gen is None:
        return False
    return _discovery_interrupt_generation != gen
