// Shared traversal utilities — used by enclosureWizard.js and templateManagement.js
// Mirrors backend build_traversal_positions in layout_templates.py

const SUPPORTED_TRAVERSALS = [
  "top_left_down_then_across",
  "bottom_left_up_then_across",
  "top_left_across_then_down",
  "bottom_left_across_then_up"
];

function buildTraversalPositions(rows, cols, traversal, slotCount, skipPositions) {
  const positions = [];
  const r = Math.max(1, rows || 1);
  const c = Math.max(1, cols || 1);
  const count = (slotCount !== null && slotCount !== undefined && slotCount > 0) ? slotCount : (r * c);
  const skipSet = new Set((skipPositions || []).map(p => `${p.row},${p.col}`));

  if (traversal === "bottom_left_up_then_across") {
    for (let col = 0; col < c; col++) {
      for (let row = r - 1; row >= 0; row--) {
        const posKey = `${row},${col}`;
        if (!skipSet.has(posKey) && positions.length < count) {
          positions.push({ row, col });
        }
      }
    }
  } else if (traversal === "top_left_across_then_down") {
    for (let row = 0; row < r; row++) {
      for (let col = 0; col < c; col++) {
        const posKey = `${row},${col}`;
        if (!skipSet.has(posKey) && positions.length < count) {
          positions.push({ row, col });
        }
      }
    }
  } else if (traversal === "bottom_left_across_then_up") {
    for (let row = r - 1; row >= 0; row--) {
      for (let col = 0; col < c; col++) {
        const posKey = `${row},${col}`;
        if (!skipSet.has(posKey) && positions.length < count) {
          positions.push({ row, col });
        }
      }
    }
  } else {
    // top_left_down_then_across (default)
    for (let col = 0; col < c; col++) {
      for (let row = 0; row < r; row++) {
        const posKey = `${row},${col}`;
        if (!skipSet.has(posKey) && positions.length < count) {
          positions.push({ row, col });
        }
      }
    }
  }

  return positions;
}
