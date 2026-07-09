# Parity test: verifies frontend buildTraversalPositions matches backend build_traversal_positions.
# Per A83 fix: instead of adding an API endpoint (which would add latency to the interactive
# wizard preview), we add this test to catch any divergence between the two implementations.
import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'backend'))

from layout_templates import build_traversal_positions, SUPPORTED_TRAVERSALS


def js_build_traversal_positions(rows, cols, traversal, slot_count, skip_positions=None):
    """Port of the frontend buildTraversalPositions function from traversalUtils.js.

    This is a direct copy of the JS logic for parity testing.
    If the frontend implementation changes, this function must be updated to match,
    which will cause the parity test to fail if the backend hasn't been updated too.
    """
    positions = []
    r = max(1, rows or 1)
    c = max(1, cols or 1)
    count = (slot_count is not None and slot_count > 0) and slot_count or (r * c)
    skip_set = set()
    if skip_positions:
        for p in skip_positions:
            skip_set.add(f"{p['row']},{p['col']}")

    if traversal == "bottom_left_up_then_across":
        for col in range(c):
            for row in range(r - 1, -1, -1):
                pos_key = f"{row},{col}"
                if pos_key not in skip_set and len(positions) < count:
                    positions.append({"row": row, "col": col})
    elif traversal == "top_left_across_then_down":
        for row in range(r):
            for col in range(c):
                pos_key = f"{row},{col}"
                if pos_key not in skip_set and len(positions) < count:
                    positions.append({"row": row, "col": col})
    elif traversal == "bottom_left_across_then_up":
        for row in range(r - 1, -1, -1):
            for col in range(c):
                pos_key = f"{row},{col}"
                if pos_key not in skip_set and len(positions) < count:
                    positions.append({"row": row, "col": col})
    else:
        # top_left_down_then_across (default)
        for col in range(c):
            for row in range(r):
                pos_key = f"{row},{col}"
                if pos_key not in skip_set and len(positions) < count:
                    positions.append({"row": row, "col": col})

    return positions


# Test matrix: (rows, cols, traversal, slot_count, skip_positions)
TEST_MATRIX = [
    # Square grids — no skip positions
    (2, 2, "top_left_down_then_across", None, None),
    (2, 2, "bottom_left_up_then_across", None, None),
    (2, 2, "top_left_across_then_down", None, None),
    (2, 2, "bottom_left_across_then_up", None, None),
    # Rectangular grids
    (1, 4, "top_left_down_then_across", None, None),
    (4, 1, "top_left_down_then_across", None, None),
    (3, 5, "top_left_across_then_down", None, None),
    (5, 3, "bottom_left_up_then_across", None, None),
    (2, 8, "bottom_left_across_then_up", None, None),
    # With explicit slot_count less than rows*cols
    (4, 4, "top_left_down_then_across", 8, None),
    (3, 3, "bottom_left_up_then_across", 5, None),
    (2, 4, "top_left_across_then_down", 6, None),
    # Edge cases
    (1, 1, "top_left_down_then_across", None, None),
    (1, 1, "bottom_left_up_then_across", 1, None),
    (0, 0, "top_left_down_then_across", None, None),
    (0, 5, "top_left_down_then_across", None, None),
    (5, 0, "top_left_down_then_across", None, None),
    # With skip positions (bay_count must account for skipped slots)
    (3, 3, "top_left_down_then_across", 7, [{"row": 0, "col": 1}, {"row": 1, "col": 1}]),
    (4, 4, "top_left_across_then_down", 10, [{"row": 0, "col": 0}, {"row": 3, "col": 3}]),
    (2, 4, "bottom_left_up_then_across", 7, [{"row": 0, "col": 2}]),
    (3, 3, "bottom_left_across_then_up", 6, [{"row": 1, "col": 0}, {"row": 2, "col": 2}]),
]


class TestTraversalParity:
    """Verify frontend and backend traversal position algorithms produce identical results."""

    @pytest.mark.parametrize("rows,cols,traversal,slot_count,skip_positions", TEST_MATRIX)
    def test_positions_match(self, rows, cols, traversal, slot_count, skip_positions):
        """Backend and frontend must produce the same positions for the same inputs."""
        backend_positions = build_traversal_positions(rows, cols, traversal, slot_count, skip_positions)
        frontend_positions = js_build_traversal_positions(rows, cols, traversal, slot_count, skip_positions)

        # Convert backend tuples to dicts for comparison
        backend_as_dicts = [{"row": r, "col": c} for r, c in backend_positions]

        assert backend_as_dicts == frontend_positions, (
            f"Mismatch for rows={rows}, cols={cols}, traversal={traversal}, "
            f"slot_count={slot_count}, skip_positions={skip_positions}:\n"
            f"  Backend:  {backend_as_dicts}\n"
            f"  Frontend: {frontend_positions}"
        )

    def test_slot_count_exceeds_grid_divergence(self):
        """Document known divergence: backend raises ValueError when slot_count > rows*cols,
        frontend silently returns all available positions. This edge case should not occur
        in normal usage (wizard prevents requesting more slots than the grid supports).
        """
        # Backend raises ValueError
        with pytest.raises(ValueError, match="bay_count exceeds grid capacity"):
            build_traversal_positions(2, 2, "top_left_down_then_across", 10)

        # Frontend silently returns all 4 positions
        frontend_positions = js_build_traversal_positions(2, 2, "top_left_down_then_across", 10)
        assert len(frontend_positions) == 4

    def test_skip_positions_exceed_bay_count_divergence(self):
        """Document known divergence: backend raises ValueError when skip_positions reduces
        available positions below bay_count, frontend silently returns fewer positions.
        Normal usage (wizard/template management) ensures bay_count accounts for skips.
        """
        skip = [{"row": 0, "col": 1}, {"row": 1, "col": 1}]

        # Backend raises ValueError (3x3=9 grid, 2 skips = 7 available, but bay_count=9)
        with pytest.raises(ValueError, match="skip_positions eliminates too many positions"):
            build_traversal_positions(3, 3, "top_left_down_then_across", None, skip)

        # Frontend silently returns 7 positions
        frontend_positions = js_build_traversal_positions(3, 3, "top_left_down_then_across", None, skip)
        assert len(frontend_positions) == 7

    def test_supported_traversals_match(self):
        """The frontend hardcoded list must match the backend SUPPORTED_TRAVERSALS set."""
        frontend_traversals = {
            "top_left_down_then_across",
            "bottom_left_up_then_across",
            "top_left_across_then_down",
            "bottom_left_across_then_up"
        }
        assert frontend_traversals == SUPPORTED_TRAVERSALS, (
            f"Frontend traversal list does not match backend.\n"
            f"  Frontend: {frontend_traversals}\n"
            f"  Backend:  {SUPPORTED_TRAVERSALS}"
        )
