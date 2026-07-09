// --- START OF FILE frontend/admin/discoveryState.js ---
// State management for discovery modal
// Exposed via window.DiscoveryState namespace

(function() {
  'use strict';

  // Discovery state management (Task 4.3)
  // Lifecycle: controllers, devicesByType, enclosureSlots, totalDevices, lastDiscovered persist across modal sessions
  // Lifecycle: groupingMode resets to 'none' on modal close
  let discoveryState = {
    controllers: [],
    devicesByType: {},
    enclosureSlots: [],
    scsiSlotProjections: [], // SCSI host slot projections for empty bay mapping
    totalDevices: 0,
    lastDiscovered: null,
    groupingMode: 'none', // 'none', 'type', 'pci'
    selectedControllers: new Set() // Set of selected PCI addresses for mapping
  };

  // Pattern mapping state (Task 4.4)
  let currentMappingPreview = null;

  // Undo state (Task 4.8)
  let previousBayMapState = null; // Stores bay map before applying mapping for undo functionality

  // Get discovery state
  function getDiscoveryState() {
    return discoveryState;
  }

  // Set discovery state
  function setDiscoveryState(newState) {
    // Rule #56: Preserve non-serializable types (Set) during state update
    // Shallow spread operator would break the selectedControllers Set reference
    discoveryState = {
      ...discoveryState,
      ...newState,
      selectedControllers: discoveryState.selectedControllers
    };
  }

  // Get current mapping preview
  function getCurrentMappingPreview() {
    return currentMappingPreview;
  }

  // Set current mapping preview
  function setCurrentMappingPreview(preview) {
    currentMappingPreview = preview;
  }

  // Get previous bay map state
  function getPreviousBayMapState() {
    return previousBayMapState;
  }

  // Set previous bay map state
  function setPreviousBayMapState(state) {
    previousBayMapState = state;
  }

  // Resets pattern mapping preview and undo state - called on modal open and close
  function resetDiscoveryPreview() {
    currentMappingPreview = null;
    previousBayMapState = null;
  }

  // Helper function to deep copy bay map configuration
  function deepCopyBayMap(bayMap) {
    if (!bayMap || typeof bayMap !== 'object') {
      return null;
    }
    return structuredClone(bayMap);
  }

  function savePreviousBayMapState(localBayMapCopy) {
    // Rule #4: Deep copy to prevent reference sharing
    previousBayMapState = deepCopyBayMap(localBayMapCopy);
  }

  /**
   * Restores the previous bay map state.
   * 
   * SIDE EFFECT: This function modifies localBayMapCopy in place by replacing all its entries
   * with the entries from the saved previous state. The caller should not rely on the return value.
   * 
   * @param {Object} localBayMapCopy - The current bay map configuration (modified in place)
   * @param {Function} renderBayMappingConfig - Optional callback to re-render the bay mapping UI
   * @param {Function} showUnsavedChangesIndicator - Optional callback to show unsaved changes indicator
   */
  function restorePreviousBayMapState(localBayMapCopy, renderBayMappingConfig, showUnsavedChangesIndicator) {
    if (!previousBayMapState || typeof previousBayMapState !== 'object') {
      alert('No previous state to restore');
      return;
    }

    // Restore the previous state (modifies localBayMapCopy in place)
    const restoredState = deepCopyBayMap(previousBayMapState);
    Object.keys(localBayMapCopy).forEach(bayId => {
      delete localBayMapCopy[bayId];
    });
    Object.keys(restoredState).forEach(bayId => {
      localBayMapCopy[bayId] = restoredState[bayId];
    });

    // Clear undo state after restore (Rule #26: complete security - don't leave stale state)
    previousBayMapState = null;

    // Re-render the bay mapping
    if (renderBayMappingConfig) {
      renderBayMappingConfig();
    }
    if (showUnsavedChangesIndicator) {
      showUnsavedChangesIndicator();
    }
  }

  // Expose public API
  window.DiscoveryState = {
    getDiscoveryState,
    setDiscoveryState,
    getCurrentMappingPreview,
    setCurrentMappingPreview,
    getPreviousBayMapState,
    setPreviousBayMapState,
    resetDiscoveryPreview,
    deepCopyBayMap,
    savePreviousBayMapState,
    restorePreviousBayMapState
  };

})();
// --- END OF FILE frontend/admin/discoveryState.js ---
