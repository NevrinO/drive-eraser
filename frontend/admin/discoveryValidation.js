// --- START OF FILE frontend/admin/discoveryValidation.js ---
// Validation functions for discovery modal
// Exposed via window.DiscoveryValidation namespace

(function() {
  'use strict';

  // Generic regex validation helper with strict newline rejection
  function validateRegex(input, pattern, options = {}) {
    const { allowNewlines = false, type = 'string' } = options;
    
    if (typeof input !== type) {
      return false;
    }
    
    if (type === 'string') {
      // Explicitly reject newlines for strict end-of-string matching in JavaScript
      if (!allowNewlines && (input.includes('\n') || input.includes('\r'))) {
        return false;
      }
    }
    
    return pattern.test(input);
  }

  // PCI address validation (matches backend validate_pci_address format)
  // Format: domain:bus:device.function (e.g., 0000:00:1f.2)
  function validatePciAddress(pciAddress) {
    const pciRegex = /^[0-9a-fA-F]{4}:[0-9a-fA-F]{2}:[0-9a-fA-F]{2}\.[0-9a-fA-F]$/;
    return validateRegex(pciAddress, pciRegex);
  }

  // Use strict full-string anchors for validation regexes
  function validateMappingPattern(pattern) {
    const patternRegex = /^(sequential|controller_sequential|pci_sequential)$/;
    return validateRegex(pattern, patternRegex);
  }

  // Use strict full-string anchors for validation regexes
  function validateStartBay(startBay) {
    const startBayNum = parseInt(startBay, 10);
    if (isNaN(startBayNum) || startBayNum < 0 || startBayNum > 127) {
      return false;
    }
    return true;
  }

  // Use strict full-string anchors for validation regexes
  function validateDeviceFilter(filter) {
    const filterRegex = /^(all|sas_sata|nvme)$/;
    return validateRegex(filter, filterRegex);
  }

  // Rule #9: Device Path Validation - strict regex whitelist
  function validateDevicePath(devicePath) {
    // Whitelist for Linux device paths with limited depth for DoS prevention:
    // /dev/sd[a-z]+[0-9]*, /dev/nvme[0-9]+n[0-9]+, /dev/bus/usb/* (max 6 segments), /dev/sg[0-9]+, /dev/hd[a-z]+[0-9]*
    const devicePathRegex = /^\/dev\/(sd[a-z]+[0-9]*|nvme[0-9]+n[0-9]+|bus\/usb[0-9]+(?:\/[0-9]+){0,5}|sg[0-9]+|hd[a-z]+[0-9]*)$/;
    return validateRegex(devicePath, devicePathRegex);
  }

  // Validate projected by-path format (SCSI host slot projection)
  // Format: pci-{pci_addr}-scsi-{host}:0:{slot}:0
  // Example: pci-0000:01:00.0-scsi-0:0:0:0
  function validateProjectedByPath(projectedByPath) {
    // Strict regex for udev by-path format from SCSI host projection
    // Supports both SCSI and SAS expander phy formats
    // SCSI format: pci-{pci_addr}-scsi-{host}:0:{slot}:0
    // SAS expander format: pci-{pci_addr}-sas-exp{expander_id}-phy{phy_num}-lun-0
    const projectedPathRegex = /^(?:pci-[0-9a-fA-F]{4}:[0-9a-fA-F]{2}:[0-9a-fA-F]{2}\.[0-9a-fA-F]-scsi-\d+:0:\d+:0|pci-[0-9a-fA-F]{4}:[0-9a-fA-F]{2}:[0-9a-fA-F]{2}\.[0-9a-fA-F]-sas-exp0x[0-9a-fA-F]+-phy\d+-lun-0)$/;
    return validateRegex(projectedByPath, projectedPathRegex);
  }

  // Validate udev by-path format (from /dev/disk/by-path/)
  // Matches backend _UDEV_BY_PATH_RE pattern
  // Format: {type}-{bus-specific-info}
  // Examples: pci-0000:01:00.0-scsi-0:0:0:0, pci-0000:01:00.0-ata-1, pci-0000:aF:00.0-sas-exp0x500056b3059bdcff-phy0-lun-0
  //          usb-1:1.2, ieee1394-0, virtio-0, platform-...
  // Note: JavaScript regex uses $ (not \Z) for end anchor, but validateRegex explicitly checks for newlines,
  // providing equivalent strict validation to the backend's \Z anchor.
  function validateUdevByPath(udevByPath) {
    // Strict whitelist of known udev by-path prefix types to prevent malicious input
    const udevByPathRegex = /^(pci|usb|ieee1394|virtio|platform)-[0-9a-fA-F:\-.a-zA-Z]+$/;
    return validateRegex(udevByPath, udevByPathRegex);
  }

  // Comprehensive mapping validation (Task 4.8)
  function validateMapping(mapping, localBayMapCopy) {
    const errors = [];

    if (!mapping || typeof mapping !== 'object') {
      errors.push('Mapping is not a valid object');
      return { valid: false, errors };
    }

    const mappingKeys = Object.keys(mapping);

    // Rule #5: DoS prevention - limit mapping size
    if (mappingKeys.length === 0) {
      errors.push('Mapping is empty');
    }
    if (mappingKeys.length > 128) {
      errors.push('Mapping exceeds maximum of 128 bays');
    }

    // Rule #4: Check for duplicate device paths using proper object comparison
    const devicePathSet = new Set();
    const duplicatePaths = [];
    mappingKeys.forEach(bayId => {
      const device = mapping[bayId];
      if (device && device.device_path) {
        // Validate device path format (Rule #9, #15)
        // Check in order: projected SCSI path, udev by-path, regular /dev/ device path
        const isProjectedPath = device.device_path.startsWith('pci-') && device.device_path.includes('-scsi-');
        const isUdevByPath = /^(pci|usb|ieee1394|virtio|platform)-/.test(device.device_path) && !isProjectedPath;
        
        if (isProjectedPath) {
          if (!validateProjectedByPath(device.device_path)) {
            errors.push(`Invalid projected device path for ${bayId}: ${device.device_path}`);
          }
        } else if (isUdevByPath) {
          if (!validateUdevByPath(device.device_path)) {
            errors.push(`Invalid udev by-path for ${bayId}: ${device.device_path}`);
          }
        } else {
          if (!validateDevicePath(device.device_path)) {
            errors.push(`Invalid device path for ${bayId}: ${device.device_path}`);
          }
        }
        // Check for duplicates
        if (devicePathSet.has(device.device_path)) {
          duplicatePaths.push(device.device_path);
        }
        devicePathSet.add(device.device_path);
      }
      // Validate projected_by_path for empty slots
      if (device && device.is_empty && device.projected_by_path) {
        if (!validateProjectedByPath(device.projected_by_path)) {
          errors.push(`Invalid projected_by_path for ${bayId}: ${device.projected_by_path}`);
        }
      }
    });

    if (duplicatePaths.length > 0) {
      errors.push(`Duplicate device paths detected: ${duplicatePaths.join(', ')}`);
    }

    // Validate bay IDs exist in localBayMapCopy
    if (localBayMapCopy && typeof localBayMapCopy === 'object') {
      const missingBays = mappingKeys.filter(bayId => !(bayId in localBayMapCopy));
      if (missingBays.length > 0) {
        errors.push(`Bays do not exist in configuration: ${missingBays.join(', ')}`);
      }
    }

    // Rule #15: Validate bay ID format (strict regex with explicit newline rejection)
    const invalidBayIds = mappingKeys.filter(bayId => {
      if (typeof bayId !== 'string') return true;
      if (bayId.includes('\n') || bayId.includes('\r')) return true;
      // Expected format: bay followed by number (e.g., bay0, bay1, bay127)
      const bayIdRegex = /^bay[0-9]+$/;
      return !bayIdRegex.test(bayId);
    });

    if (invalidBayIds.length > 0) {
      errors.push(`Invalid bay ID format: ${invalidBayIds.join(', ')}`);
    }

    return {
      valid: errors.length === 0,
      errors
    };
  }

  // Helper function to determine device path type
  function getDevicePathType(devicePath) {
    if (!devicePath || typeof devicePath !== 'string') {
      return 'unknown';
    }
    
    const isUdevByPath = /^(pci|usb|ieee1394|virtio|platform)-/.test(devicePath);
    const isProjectedPath = devicePath.startsWith('pci-') && devicePath.includes('-scsi-');
    
    if (isProjectedPath) return 'projected';
    if (isUdevByPath) return 'udev';
    return 'standard';
  }

  // Expose public API
  window.DiscoveryValidation = {
    validateRegex,
    validatePciAddress,
    validateMappingPattern,
    validateStartBay,
    validateDeviceFilter,
    validateDevicePath,
    validateProjectedByPath,
    validateUdevByPath,
    validateMapping,
    getDevicePathType
  };

})();
// --- END OF FILE frontend/admin/discoveryValidation.js ---
