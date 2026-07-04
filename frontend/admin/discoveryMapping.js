// --- START OF FILE frontend/admin/discoveryMapping.js ---
// Pattern and manual mapping business logic for discovery modal
// Exposed via window.DiscoveryMapping namespace

(function() {
  'use strict';

  // Manual mapping state (Task 4.5)
  let mappingMode = 'pattern'; // 'pattern' or 'manual'
  let manualMappings = {}; // { bayId: { device_path, device_name, controller_pci, type } }
  let selectedDevice = null; // Currently selected device for manual mapping

  // Generic groupBy helper - groups array items by a key function
  function groupBy(items, keyFn) {
    const grouped = {};
    items.forEach(item => {
      const key = keyFn(item);
      if (!grouped[key]) grouped[key] = [];
      grouped[key].push(item);
    });
    return grouped;
  }

  function groupControllersByType(controllers) {
    // Type validation (CRITIQUE.md #3)
    if (!Array.isArray(controllers)) {
      console.error("groupControllersByType: expected array, got", typeof controllers);
      return {};
    }
    // Rule #4: Use proper object comparison with Set for deduplication
    return groupBy(controllers, c => c.controller_type || "unknown");
  }

  function groupControllersByPCI(controllers) {
    // Type validation (CRITIQUE.md #3)
    if (!Array.isArray(controllers)) {
      console.error("groupControllersByPCI: expected array, got", typeof controllers);
      return {};
    }
    // Group by PCI address prefix (bus:device.function)
    return groupBy(controllers, c => {
      const pci = c.pci_address || "unknown";
      return pci.substring(0, pci.lastIndexOf('.')) || pci;
    });
  }

  // Helper function to sort bay IDs by extracting numbers
  function sortBayIds(bayIds) {
    return bayIds.sort((a, b) => {
      const numA = parseInt(a.replace(/\D/g, ""), 10) || 0;
      const numB = parseInt(b.replace(/\D/g, ""), 10) || 0;
      return numA - numB;
    });
  }

  function flattenDevices(devicesByType, filter = 'all') {
    const flattened = [];

    for (const [type, devices] of Object.entries(devicesByType)) {
      if (!Array.isArray(devices)) continue;

      // Apply device filter
      if (filter === 'sas_sata' && type === 'nvme') continue;
      if (filter === 'nvme' && type !== 'nvme') continue;

      devices.forEach(device => {
        if (device && device.device_path) {
          // Rule #9: Validate device path against whitelist before using
          // Accept /dev/ device paths, udev by-path, and projected SCSI paths
          const pathType = window.DiscoveryValidation.getDevicePathType(device.device_path);
          
          if (pathType === 'unknown' && !window.DiscoveryValidation.validateDevicePath(device.device_path)) {
            console.warn(`Skipping invalid device path: ${device.device_path}`);
            return;
          }

          // Skip partitions (e.g., sda1, sda2, nvme0n1p1) - only map whole drives
          const deviceName = device.device_name || '';
          if (/^(sd[a-z]+[0-9]+|nvme[0-9]+n[0-9]+p[0-9]+)$/.test(deviceName)) {
            return;
          }

          flattened.push({
            device_path: device.device_path,
            by_path: device.by_path || device.device_path, // Use by_path if available, fallback to device_path
            device_name: device.device_name || 'Unknown',
            controller_pci: device.controller_pci || 'Unknown',
            type: type,
            smart: device.smart
          });
        }
      });
    }

    return flattened;
  }

  // Helper function for enclosure-based mapping to eliminate code duplication
  function applyEnclosureBasedMapping(devices, enclosureSlots, discoveryState, localBayMapCopy) {
    const mapping = {};
    let skippedCount = 0;
    let mismatchCount = 0;

    // Create a map of device_path -> controller_pci for filtering
    const deviceToController = {};
    devices.forEach(device => {
      if (device.device_path && device.controller_pci) {
        deviceToController[device.device_path] = device.controller_pci;
      }
    });

    // Create a map of slot_number -> device_path from enclosure data
    // Filter by selected controllers if any are selected
    const slotToDevice = {};
    enclosureSlots.forEach(slot => {
      if (slot.slot_number !== null && slot.device) {
        // If controllers are selected, check if this slot's device matches
        if (discoveryState.selectedControllers.size > 0) {
          const controller = deviceToController[slot.device];
          if (controller && discoveryState.selectedControllers.has(controller)) {
            slotToDevice[slot.slot_number] = slot.device;
          }
        } else {
          // No controller filter, include all slots
          slotToDevice[slot.slot_number] = slot.device;
        }
      }
    });

    // Create a map of device_path -> by_path for lookup
    const devicePathToByPath = {};
    devices.forEach(device => {
      if (device.device_path && device.by_path) {
        devicePathToByPath[device.device_path] = device.by_path;
      }
    });

    // If no slots have device data, return empty mapping to trigger fallback
    if (Object.keys(slotToDevice).length === 0) {
      return { mapping: {}, skippedCount: 0, mismatchCount: 0, hasDeviceData: false };
    }

    // Map bays based on physical slot numbers with explicit sorting for deterministic ordering
    Object.keys(slotToDevice).sort((a, b) => parseInt(a, 10) - parseInt(b, 10)).forEach(slotNum => {
      const bayId = `bay${slotNum}`;
      const devicePath = slotToDevice[slotNum];

      // Only map to bays that exist in the configuration
      if (localBayMapCopy && bayId in localBayMapCopy) {
        // Find device info for this path
        const deviceInfo = devices.find(d => d.device_path === devicePath);
        if (deviceInfo) {
          // Use by_path if available, otherwise use device_path
          const byPathToUse = deviceInfo.by_path || devicePath;
          mapping[bayId] = {
            device_path: byPathToUse, // Store by_path in device_path field for backend compatibility
            device_name: deviceInfo.device_name,
            controller_pci: deviceInfo.controller_pci
          };
        } else {
          mismatchCount++;
        }
      } else {
        skippedCount++;
      }
    });

    return { mapping, skippedCount, mismatchCount, hasDeviceData: true };
  }

  // Helper function for SCSI host slot projection mapping
  function applyScsiProjectionMapping(devices, scsiProjections, startBay, groupingStrategy, discoveryState, localBayMapCopy) {
    // Type validation (CRITIQUE.md #3)
    if (!Array.isArray(scsiProjections)) {
      console.error("applyScsiProjectionMapping: expected array for scsiProjections, got", typeof scsiProjections);
      return { mapping: {}, skippedCount: 0, emptySlotCount: 0 };
    }

    // Filter projections by selected controllers
    let filteredProjections = scsiProjections;
    if (discoveryState.selectedControllers.size > 0) {
      filteredProjections = scsiProjections.filter(proj =>
        discoveryState.selectedControllers.has(proj.pci_address)
      );
    }

    const mapping = {};
    let skippedCount = 0;
    let emptySlotCount = 0;

    // Group projections based on grouping strategy
    const projectionGroups = {};
    filteredProjections.forEach(proj => {
      let groupKey;
      if (groupingStrategy === 'controller') {
        // Group by full PCI address
        groupKey = proj.pci_address;
      } else if (groupingStrategy === 'pci') {
        // Group by PCI prefix (domain:bus)
        const pciParts = proj.pci_address.split(':');
        groupKey = pciParts.length >= 2 ? `${pciParts[0]}:${pciParts[1]}` : proj.pci_address;
      } else {
        // 'none' - group all together for sequential mapping
        groupKey = 'all';
      }
      
      if (!projectionGroups[groupKey]) {
        projectionGroups[groupKey] = [];
      }
      projectionGroups[groupKey].push(proj);
    });

    // Sort groups based on grouping strategy
    let sortedGroupKeys;
    if (groupingStrategy === 'none') {
      sortedGroupKeys = ['all'];
    } else {
      sortedGroupKeys = Object.keys(projectionGroups).sort();
    }

    let bayNum = startBay;
    sortedGroupKeys.forEach(groupKey => {
      const groupProjections = projectionGroups[groupKey];
      // Sort projections within group by slot number
      groupProjections.sort((a, b) => a.slot_number - b.slot_number);

      groupProjections.forEach(proj => {
        const bayId = `bay${bayNum}`;

        // Only map to bays that exist in the configuration
        if (localBayMapCopy && bayId in localBayMapCopy) {
          if (proj.device_path) {
            // Slot is occupied - find device info
            const deviceInfo = devices.find(d => d.device_path === proj.device_path);
            if (deviceInfo) {
              // Use by_path if available, otherwise use device_path
              const byPathToUse = deviceInfo.by_path || proj.device_path;
              mapping[bayId] = {
                device_path: byPathToUse, // Store by_path in device_path field for backend compatibility
                device_name: deviceInfo.device_name || proj.device_name,
                controller_pci: proj.pci_address,
                projected_by_path: proj.projected_by_path
              };
            } else {
              // Device path found in projection but not in device list
              mapping[bayId] = {
                device_path: proj.device_path,
                device_name: proj.device_name || 'Unknown',
                controller_pci: proj.pci_address,
                projected_by_path: proj.projected_by_path
              };
            }
          } else {
            // Slot is empty - use projected by-path for future mapping
            emptySlotCount++;
            mapping[bayId] = {
              device_path: proj.projected_by_path, // Use projected_by_path for empty slots
              device_name: 'Empty Slot',
              controller_pci: proj.pci_address,
              projected_by_path: proj.projected_by_path,
              is_empty: true
            };
          }
        } else {
          skippedCount++;
        }
        bayNum++;
      });
    });

    return { mapping, skippedCount, emptySlotCount };
  }

  // Helper function for sequential mapping with different grouping strategies
  function applySequentialMappingWithGrouping(devices, startBay, groupingStrategy, localBayMapCopy) {
    const mapping = {};
    let skippedCount = 0;

    // If no grouping, just map sequentially
    if (groupingStrategy === 'none') {
      let bayNum = startBay;
      devices.forEach(device => {
        const bayId = `bay${bayNum}`;
        if (localBayMapCopy && bayId in localBayMapCopy) {
          const byPathToUse = device.by_path || device.device_path;
          mapping[bayId] = {
            device_path: byPathToUse,
            device_name: device.device_name,
            controller_pci: device.controller_pci
          };
        } else {
          skippedCount++;
        }
        bayNum++;
      });
      return { mapping, skippedCount, mismatchCount: 0 };
    }

    // Group devices based on strategy
    const groups = groupBy(devices, device => {
      const pci = device.controller_pci || 'unknown';
      if (groupingStrategy === 'controller') return pci;
      if (groupingStrategy === 'pci') return pci.substring(0, pci.lastIndexOf('.')) || pci;
      return 'default';
    });

    // Sort groups by key for deterministic ordering (Rule #4: consistent ordering)
    const sortedGroupKeys = Object.keys(groups).sort();

    let bayNum = startBay;
    sortedGroupKeys.forEach(groupKey => {
      const groupDevices = groups[groupKey];
      // Sort devices within each group by device_path for deterministic ordering
      groupDevices.sort((a, b) => (a.device_path || '').localeCompare(b.device_path || ''));

      groupDevices.forEach(device => {
        const bayId = `bay${bayNum}`;
        if (localBayMapCopy && bayId in localBayMapCopy) {
          const byPathToUse = device.by_path || device.device_path;
          mapping[bayId] = {
            device_path: byPathToUse,
            device_name: device.device_name,
            controller_pci: device.controller_pci
          };
        } else {
          skippedCount++;
        }
        bayNum++;
      });
    });

    return { mapping, skippedCount, mismatchCount: 0 };
  }

  // Unified pattern mapping function with grouping strategy parameter
  // groupingStrategy: 'none' (sequential), 'controller' (by full PCI), 'pci' (by PCI prefix)
  function applyPatternMapping(devices, startBay, enclosureSlots, scsiProjections, groupingStrategy, discoveryState, localBayMapCopy) {
    // Validate startBay parameter
    if (typeof startBay !== 'number' || startBay < 0 || !Number.isInteger(startBay)) {
      console.error("Invalid startBay parameter in applyPatternMapping:", startBay);
      startBay = 0; // Default to 0
    }

    // Filter devices by selected controllers
    let filteredDevices = devices;
    if (discoveryState.selectedControllers.size > 0) {
      filteredDevices = devices.filter(device =>
        device.controller_pci && discoveryState.selectedControllers.has(device.controller_pci)
      );
    }

    // If enclosure slots are available with device data, use physical slot numbers for mapping
    // Note: startBay parameter is ignored when enclosure data is present, as physical
    // slot numbers from SCSI Enclosure Services determine the bay mapping
    if (enclosureSlots && enclosureSlots.length > 0) {
      console.log('Using enclosure-based mapping. enclosureSlots:', enclosureSlots);
      const result = applyEnclosureBasedMapping(filteredDevices, enclosureSlots, discoveryState, localBayMapCopy);
      console.log('Enclosure mapping result:', result);
      // If enclosure slots exist but have no device data, fall back to SCSI projection mapping
      if (!result.hasDeviceData) {
        console.log('Enclosure has no device data, falling back to SCSI projections');
        if (scsiProjections && scsiProjections.length > 0) {
          return applyScsiProjectionMapping(filteredDevices, scsiProjections, startBay, groupingStrategy, discoveryState, localBayMapCopy);
        }
        // Final fallback to sequential mapping with grouping strategy
        return applySequentialMappingWithGrouping(filteredDevices, startBay, groupingStrategy, localBayMapCopy);
      }
      return result;
    }

    // If SCSI projections are available, use them for physical bay mapping
    if (scsiProjections && scsiProjections.length > 0) {
      return applyScsiProjectionMapping(filteredDevices, scsiProjections, startBay, groupingStrategy, discoveryState, localBayMapCopy);
    }

    // Fallback to sequential mapping with grouping strategy if no enclosure or SCSI data
    return applySequentialMappingWithGrouping(filteredDevices, startBay, groupingStrategy, localBayMapCopy);
  }

  function generateMappingPreview(discoveryState, localBayMapCopy, mappingPattern, mappingStartBay, mappingDeviceFilter, mappingPreview, applyMappingBtn, showMappingValidationError, hideMappingValidationError, setPreviewMessage, escapeHtml, mappingValidationError) {
    // Clear previous validation errors
    hideMappingValidationError();
    
    if (!discoveryState.devicesByType || Object.keys(discoveryState.devicesByType).length === 0) {
      setPreviewMessage('No devices discovered. Click "Discover Slots" first.');
      return null;
    }
    
    const pattern = mappingPattern.value;
    const startBay = parseInt(mappingStartBay.value, 10);
    const filter = mappingDeviceFilter.value;
    
    // Validate inputs
    if (!window.DiscoveryValidation.validateMappingPattern(pattern)) { setPreviewMessage('Invalid mapping pattern selected.'); return null; }
    if (!window.DiscoveryValidation.validateStartBay(startBay)) { setPreviewMessage('Starting bay must be between 0 and 127.'); return null; }
    if (!window.DiscoveryValidation.validateDeviceFilter(filter)) { setPreviewMessage('Invalid device filter selected.'); return null; }
    
    // Flatten devices with filter
    const devices = flattenDevices(discoveryState.devicesByType, filter);
    
    if (devices.length === 0) {
      setPreviewMessage('No devices match the selected filter.', true);
      return null;
    }
    
    // Apply pattern
    let patternResult;
    let groupingStrategy = 'none';
    if (pattern === 'sequential') {
      groupingStrategy = 'none';
    } else if (pattern === 'controller_sequential') {
      groupingStrategy = 'controller';
    } else if (pattern === 'pci_sequential') {
      groupingStrategy = 'pci';
    } else {
      setPreviewMessage('Unknown pattern type.');
      return null;
    }

    patternResult = applyPatternMapping(devices, startBay, discoveryState.enclosureSlots, discoveryState.scsiSlotProjections, groupingStrategy, discoveryState, localBayMapCopy);

    const mapping = patternResult.mapping;
    const skippedCount = patternResult.skippedCount || 0;
    const mismatchCount = patternResult.mismatchCount || 0;

    // Show warning if devices were skipped
    if (skippedCount > 0) {
      showMappingValidationError(`Warning: ${skippedCount} device(s) skipped due to missing bays in configuration. Add more bays or adjust the starting bay number.`);
    }

    // Show warning if device paths mismatched between enclosure and device data
    if (mismatchCount > 0) {
      const existingWarning = mappingValidationError ? mappingValidationError.textContent || '' : '';
      const mismatchMsg = `Warning: ${mismatchCount} device(s) in enclosure slots not found in device list. Discovery data may be stale.`;
      showMappingValidationError(existingWarning ? `${existingWarning} ${mismatchMsg}` : mismatchMsg);
    }

    // Comprehensive validation (Task 4.8)
    const validation = window.DiscoveryValidation.validateMapping(mapping, localBayMapCopy);
    if (!validation.valid) {
      showMappingValidationError(validation.errors.join('; '));
      setPreviewMessage('Mapping validation failed. See error message above.');
      applyMappingBtn.disabled = true;
      return null;
    }

    // Render preview (Rule #5: DoS prevention - limit preview size)
    const mappingKeys = Object.keys(mapping);
    if (mappingKeys.length > 128) {
      setPreviewMessage('Mapping exceeds maximum of 128 bays.');
      return null;
    }

    let html = `<div class="discovery-preview-summary">${mappingKeys.length} device(s) will be mapped:</div>`;
    html += mappingKeys.slice(0, 100).map(bayId => {
      const device = mapping[bayId];
      return `
        <div class="discovery-preview-row">
          <strong class="discovery-mapping-bay">${escapeHtml ? escapeHtml(bayId) : bayId}</strong> → ${escapeHtml ? escapeHtml(device.device_name) : device.device_name} (${escapeHtml ? escapeHtml(device.device_path) : device.device_path})
        </div>
      `;
    }).join('');
    
    if (mappingKeys.length > 100) {
      html += `<div class="discovery-preview-more">... and ${mappingKeys.length - 100} more</div>`;
    }
    
    mappingPreview.innerHTML = html;
    mappingPreview.classList.remove('hidden');
    
    window.DiscoveryState.setCurrentMappingPreview(mapping);
    applyMappingBtn.disabled = false;
    
    return mapping;
  }

  async function applyMappingToBayConfig(localBayMapCopy, loadBayMappingConfig, closeDiscoveryModal, showMappingValidationError, hideMappingValidationError, safeFetch, renderBayMappingConfig, showUnsavedChangesIndicator) {
    // Handle manual mapping mode - set currentMappingPreview from manualMappings
    if (mappingMode === 'manual') {
      if (Object.keys(manualMappings).length === 0) {
        alert('No manual mappings to apply.');
        return;
      }
      window.DiscoveryState.setCurrentMappingPreview(manualMappings);
    }

    // Clear previous validation errors
    hideMappingValidationError();
    
    const currentMappingPreview = window.DiscoveryState.getCurrentMappingPreview();
    if (!currentMappingPreview || Object.keys(currentMappingPreview).length === 0) {
      alert('No valid mapping to apply. Generate a preview first.');
      return;
    }

    // Comprehensive validation before applying (Task 4.8)
    const validation = window.DiscoveryValidation.validateMapping(currentMappingPreview, localBayMapCopy);
    if (!validation.valid) {
      showMappingValidationError(validation.errors.join('; '));
      return;
    }

    // Save previous state for undo (Task 4.8)
    window.DiscoveryState.savePreviousBayMapState(localBayMapCopy);

    try {
      const response = await safeFetch('/api/admin/apply-slot-mapping', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json'
        },
        body: JSON.stringify(currentMappingPreview)
      });

      const result = await response.json();

      if (!response.ok) {
        const errorMsg = result.error || 'Failed to apply mapping';
        const details = Array.isArray(result.details) ? `\nDetails: ${result.details.join(', ')}` : '';
        showMappingValidationError(`${errorMsg}${details}`);
        // Restore previous state on error (Rule #26: complete security)
        window.DiscoveryState.restorePreviousBayMapState(localBayMapCopy, renderBayMappingConfig, showUnsavedChangesIndicator);
        return;
      }

      // Enable undo button after successful apply (Task 4.8)
      const undoMappingBtn = document.getElementById('undoMappingBtn');
      if (undoMappingBtn) {
        undoMappingBtn.disabled = false;
      }
      
      // Reload bay map from backend to get updated state
      await loadBayMappingConfig();
      closeDiscoveryModal();
      alert(`Mapping applied successfully to ${result.updated_bays} bay(s).`);
    } catch (error) {
      console.error('Error applying slot mapping:', error);
      showMappingValidationError('Failed to apply mapping. Please try again.');
      // Restore previous state on error (Rule #26: complete security)
      window.DiscoveryState.restorePreviousBayMapState(localBayMapCopy, renderBayMappingConfig, showUnsavedChangesIndicator);
    }
  }

  // Manual mapping functions (Task 4.5)

  function getMappingMode() {
    return mappingMode;
  }

  function setMappingMode(mode, patternModeBtn, manualModeBtn, patternMappingControls, manualMappingControls, applyMappingBtn) {
    mappingMode = mode;
    if (mode === 'pattern') {
      patternModeBtn.style.background = 'var(--color-primary)';
      manualModeBtn.style.background = '';
      patternMappingControls.classList.remove('hidden');
      manualMappingControls.classList.add('hidden');
      window.DiscoveryState.setCurrentMappingPreview(null);
      applyMappingBtn.disabled = true;
    } else {
      patternModeBtn.style.background = '';
      manualModeBtn.style.background = 'var(--color-primary)';
      patternMappingControls.classList.add('hidden');
      manualMappingControls.classList.remove('hidden');
      window.DiscoveryState.setCurrentMappingPreview(null);
      applyMappingBtn.disabled = Object.keys(manualMappings).length === 0;
    }
    // Rule #57: Disable undo button on mode switch to prevent cross-mode undo
    const undoMappingBtn = document.getElementById('undoMappingBtn');
    if (undoMappingBtn) {
      undoMappingBtn.disabled = true;
    }
  }

  function getManualMappings() {
    return manualMappings;
  }

  function setManualMappings(mappings) {
    manualMappings = mappings;
  }

  function getSelectedDevice() {
    return selectedDevice;
  }

  function setSelectedDevice(device) {
    selectedDevice = device;
  }

  function filterDevices(devices, searchTerm, filterType) {
    const term = searchTerm.toLowerCase().trim();
    
    return devices.filter(device => {
      // Apply type filter
      if (filterType === 'sas_sata' && device.type === 'nvme') return false;
      if (filterType === 'nvme' && device.type !== 'nvme') return false;
      
      // Apply search term filter (Rule #5: DoS prevention - limit search complexity)
      if (term === '') return true;
      return [device.device_path, device.device_name, device.controller_pci, device.smart?.model, device.smart?.serial]
        .map(v => (v || '').toLowerCase())
        .some(v => v.includes(term));
    });
  }

  function addManualMapping(manualBaySelect, showMappingValidationError, hideMappingValidationError) {
    // Clear previous validation errors
    hideMappingValidationError();
    
    if (!selectedDevice) {
      alert('Please select a device first.');
      return;
    }

    const bayId = manualBaySelect.value;
    if (!bayId) {
      alert('Please select a bay.');
      return;
    }

    // Validate device path (Rule #9, #15)
    // Accept /dev/ device paths, udev by-path, and projected SCSI paths
    const pathType = window.DiscoveryValidation.getDevicePathType(selectedDevice.device_path);
    
    if (pathType === 'unknown' && !window.DiscoveryValidation.validateDevicePath(selectedDevice.device_path)) {
      showMappingValidationError(`Invalid device path: ${selectedDevice.device_path}`);
      return;
    }

    // Check if device is already mapped
    const existingMapping = Object.entries(manualMappings).find(([_, m]) => m.device_path === selectedDevice.device_path);
    if (existingMapping) {
      showMappingValidationError(`Device is already mapped to ${existingMapping[0]}. Remove that mapping first.`);
      return;
    }

    // Validate bay ID format (Rule #15)
    if (typeof bayId !== 'string' || bayId.includes('\n') || bayId.includes('\r')) {
      showMappingValidationError('Invalid bay ID format');
      return;
    }
    const bayIdRegex = /^bay[0-9]+$/;
    if (!bayIdRegex.test(bayId)) {
      showMappingValidationError(`Invalid bay ID format: ${bayId}`);
      return;
    }

    // Add mapping
    manualMappings[bayId] = {
      device_path: selectedDevice.by_path || selectedDevice.device_path, // Use by_path if available
      device_name: selectedDevice.device_name,
      controller_pci: selectedDevice.controller_pci,
      type: selectedDevice.type
    };

    // Clear selection
    selectedDevice = null;
    return true;
  }

  function hasManualMappings() {
    return Object.keys(manualMappings).length > 0;
  }

  function clearManualMappings() {
    manualMappings = {};
    selectedDevice = null;
  }

  function removeManualMapping(bayId) {
    delete manualMappings[bayId];
  }

  // Expose public API
  window.DiscoveryMapping = {
    groupBy,
    groupControllersByType,
    groupControllersByPCI,
    sortBayIds,
    flattenDevices,
    applyEnclosureBasedMapping,
    applyScsiProjectionMapping,
    applySequentialMappingWithGrouping,
    applyPatternMapping,
    generateMappingPreview,
    applyMappingToBayConfig,
    getMappingMode,
    setMappingMode,
    getManualMappings,
    setManualMappings,
    getSelectedDevice,
    setSelectedDevice,
    filterDevices,
    addManualMapping,
    hasManualMappings,
    clearManualMappings,
    removeManualMapping
  };

})();
// --- END OF FILE frontend/admin/discoveryMapping.js ---
