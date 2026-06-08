# Unit tests for layout_templates.py
import pytest
import sys
import os
import json
import tempfile
import hashlib
from unittest.mock import patch, MagicMock

# Add backend to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'backend'))

from layout_templates import (
    is_bay_entry,
    normalize_bay_map_document,
    compose_bay_map_document,
    load_layout_templates,
    save_layout_templates,
    build_traversal_positions,
    apply_template,
    validate_layout_metadata,
    validate_template,
    DEFAULT_TEMPLATES,
    SUPPORTED_TRAVERSALS
)


class TestIsBayEntry:
    """Test bay entry detection."""

    def test_valid_bay_entry(self):
        """Test that valid bay entries are detected."""
        valid_entries = [
            {"role": "wipe", "by_path": "/dev/sda"},
            {"by_path_nvme": "/dev/nvme0n1"},
            {"type": "sas_sata"},
            {"label": "Bay 1"},
            {"locked": False}
        ]
        for entry in valid_entries:
            assert is_bay_entry(entry) is True

    def test_invalid_bay_entry(self):
        """Test that invalid entries are rejected."""
        invalid_entries = [
            {"random_key": "value"},
            {},
            {"random": "value"}  # No bay marker keys
        ]
        for entry in invalid_entries:
            assert is_bay_entry(entry) is False

    def test_non_dict_input(self):
        """Test that non-dict inputs are rejected."""
        assert is_bay_entry("string") is False
        assert is_bay_entry(123) is False
        assert is_bay_entry([]) is False


class TestNormalizeBayMapDocument:
    """Test bay map document normalization."""

    def test_normalize_with_bays_dict(self):
        """Test normalization with explicit bays dict."""
        document = {
            "bays": {
                "bay0": {"role": "wipe", "by_path": "/dev/sda"},
                "bay1": {"type": "sas_sata"}
            },
            "layout_metadata": {"template_id": "dell_r320_4bay"}
        }
        bays, metadata = normalize_bay_map_document(document)
        assert "bay0" in bays
        assert "bay1" in bays
        assert metadata == {"template_id": "dell_r320_4bay"}

    def test_normalize_flat_structure(self):
        """Test normalization with flat structure."""
        document = {
            "bay0": {"role": "wipe", "by_path": "/dev/sda"},
            "layout_metadata": {"template_id": "test"},
            "random_key": "value"
        }
        bays, metadata = normalize_bay_map_document(document)
        assert "bay0" in bays
        assert "random_key" not in bays
        assert metadata == {"template_id": "test"}

    def test_normalize_non_dict_input(self):
        """Test normalization with non-dict input."""
        bays, metadata = normalize_bay_map_document("string")
        assert bays == {}
        assert metadata == {}

    def test_normalize_filters_non_bay_entries(self):
        """Test that non-bay entries are filtered out."""
        document = {
            "bay0": {"role": "wipe", "by_path": "/dev/sda"},
            "random": {"key": "value"}
        }
        bays, metadata = normalize_bay_map_document(document)
        assert "bay0" in bays
        assert "random" not in bays


class TestComposeBayMapDocument:
    """Test bay map document composition."""

    def test_compose_with_metadata(self):
        """Test composition with metadata."""
        bays = {"bay0": {"role": "wipe"}}
        metadata = {"template_id": "test"}
        result = compose_bay_map_document(bays, metadata)
        assert "bays" in result
        assert "layout_metadata" in result
        assert result["layout_metadata"] == metadata

    def test_compose_without_metadata(self):
        """Test composition without metadata."""
        bays = {"bay0": {"role": "wipe"}}
        result = compose_bay_map_document(bays, None)
        assert result == bays

    def test_compose_filters_invalid_bays(self):
        """Test that invalid bay entries are filtered."""
        bays = {
            "bay0": {"role": "wipe", "by_path": "/dev/sda"},
            "invalid": {"random": "key"}
        }
        result = compose_bay_map_document(bays, None)
        assert "bay0" in result
        assert "invalid" not in result


class TestLoadLayoutTemplates:
    """Test template loading with hash validation (Lesson #22)."""

    def test_file_not_found_uses_defaults(self):
        """Test that missing file uses DEFAULT_TEMPLATES."""
        with tempfile.TemporaryDirectory() as tmpdir:
            templates, is_fallback = load_layout_templates(tmpdir)
            assert templates == DEFAULT_TEMPLATES
            assert is_fallback is True

    def test_valid_templates_loaded(self):
        """Test that valid templates are loaded."""
        with tempfile.TemporaryDirectory() as tmpdir:
            templates_file = os.path.join(tmpdir, "layout_templates.json")
            test_data = {
                "templates": {
                    "test_template": {
                        "id": "test_template",
                        "name": "Test",
                        "vendor": "Test",
                        "rows": 2,
                        "cols": 2,
                        "bay_count": 4,
                        "traversal_preset": "top_left_down_then_across"
                    }
                }
            }
            with open(templates_file, 'w') as f:
                json.dump(test_data, f)

            templates, is_fallback = load_layout_templates(tmpdir)
            assert "test_template" in templates
            assert is_fallback is False

    def test_hash_mismatch_uses_defaults(self):
        """Test that hash mismatch triggers fallback (Lesson #22)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            templates_file = os.path.join(tmpdir, "layout_templates.json")
            hash_file = os.path.join(tmpdir, "layout_templates.json.sha256")

            test_data = {"templates": {"test": {"id": "test", "name": "Test", "vendor": "Test", "rows": 1, "cols": 1, "bay_count": 1, "traversal_preset": "top_left_down_then_across"}}}
            with open(templates_file, 'w') as f:
                json.dump(test_data, f)

            # Write invalid hash
            with open(hash_file, 'w') as f:
                f.write("invalid_hash")

            templates, is_fallback = load_layout_templates(tmpdir)
            assert templates == DEFAULT_TEMPLATES
            assert is_fallback is True

    def test_valid_hash_accepted(self):
        """Test that valid hash is accepted."""
        with tempfile.TemporaryDirectory() as tmpdir:
            templates_file = os.path.join(tmpdir, "layout_templates.json")
            hash_file = os.path.join(tmpdir, "layout_templates.json.sha256")

            test_data = {"templates": {"test": {"id": "test", "name": "Test", "vendor": "Test", "rows": 1, "cols": 1, "bay_count": 1, "traversal_preset": "top_left_down_then_across"}}}
            content = json.dumps(test_data)
            with open(templates_file, 'w') as f:
                f.write(content)

            # Write valid hash
            hash_value = hashlib.sha256(content.encode('utf-8')).hexdigest()
            with open(hash_file, 'w') as f:
                f.write(hash_value)

            templates, is_fallback = load_layout_templates(tmpdir)
            assert "test" in templates
            assert is_fallback is False

    def test_invalid_json_uses_defaults(self):
        """Test that invalid JSON triggers fallback."""
        with tempfile.TemporaryDirectory() as tmpdir:
            templates_file = os.path.join(tmpdir, "layout_templates.json")
            with open(templates_file, 'w') as f:
                f.write("invalid json")

            templates, is_fallback = load_layout_templates(tmpdir)
            assert templates == DEFAULT_TEMPLATES
            assert is_fallback is True

    def test_missing_templates_key_uses_defaults(self):
        """Test that missing templates key triggers fallback."""
        with tempfile.TemporaryDirectory() as tmpdir:
            templates_file = os.path.join(tmpdir, "layout_templates.json")
            with open(templates_file, 'w') as f:
                json.dump({"invalid": "structure"}, f)

            templates, is_fallback = load_layout_templates(tmpdir)
            assert templates == DEFAULT_TEMPLATES
            assert is_fallback is True


class TestSaveLayoutTemplates:
    """Test template saving with atomic operations (Lesson #20)."""

    def test_save_valid_templates(self):
        """Test saving valid templates."""
        with tempfile.TemporaryDirectory() as tmpdir:
            templates = {
                "test": {
                    "id": "test",
                    "name": "Test",
                    "vendor": "Test",
                    "rows": 1,
                    "cols": 1,
                    "bay_count": 1,
                    "traversal_preset": "top_left_down_then_across"
                }
            }
            save_layout_templates(templates, tmpdir)

            # Verify file was created
            templates_file = os.path.join(tmpdir, "layout_templates.json")
            assert os.path.exists(templates_file)

            # Verify hash file was created
            hash_file = os.path.join(tmpdir, "layout_templates.json.sha256")
            assert os.path.exists(hash_file)

            # Verify content
            with open(templates_file, 'r') as f:
                loaded = json.load(f)
            assert "test" in loaded["templates"]

    def test_save_invalid_template_raises_error(self):
        """Test that invalid template raises ValueError."""
        with tempfile.TemporaryDirectory() as tmpdir:
            invalid_templates = {
                "test": {
                    "id": "test",
                    # Missing required fields
                }
            }
            with pytest.raises(ValueError, match="Invalid template"):
                save_layout_templates(invalid_templates, tmpdir)

    def test_save_creates_hash_file(self):
        """Test that hash file is created for integrity (Lesson #22)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            templates = {
                "test": {
                    "id": "test",
                    "name": "Test",
                    "vendor": "Test",
                    "rows": 1,
                    "cols": 1,
                    "bay_count": 1,
                    "traversal_preset": "top_left_down_then_across"
                }
            }
            save_layout_templates(templates, tmpdir)

            hash_file = os.path.join(tmpdir, "layout_templates.json.sha256")
            with open(hash_file, 'r') as f:
                stored_hash = f.read().strip()

            # Verify hash matches content
            templates_file = os.path.join(tmpdir, "layout_templates.json")
            with open(templates_file, 'r') as f:
                content = f.read()
            calculated_hash = hashlib.sha256(content.encode('utf-8')).hexdigest()

            assert stored_hash == calculated_hash


class TestBuildTraversalPositions:
    """Test traversal position building."""

    def test_top_left_down_then_across(self):
        """Test top-left-down-then-across traversal."""
        positions = build_traversal_positions(2, 2, "top_left_down_then_across", 4)
        expected = [(0, 0), (1, 0), (0, 1), (1, 1)]
        assert positions == expected

    def test_bottom_left_up_then_across(self):
        """Test bottom-left-up-then-across traversal."""
        positions = build_traversal_positions(2, 2, "bottom_left_up_then_across", 4)
        expected = [(1, 0), (0, 0), (1, 1), (0, 1)]
        assert positions == expected

    def test_top_left_across_then_down(self):
        """Test top-left-across-then-down traversal."""
        positions = build_traversal_positions(2, 2, "top_left_across_then_down", 4)
        expected = [(0, 0), (0, 1), (1, 0), (1, 1)]
        assert positions == expected

    def test_bottom_left_across_then_up(self):
        """Test bottom-left-across-then-up traversal."""
        positions = build_traversal_positions(2, 2, "bottom_left_across_then_up", 4)
        expected = [(1, 0), (1, 1), (0, 0), (0, 1)]
        assert positions == expected

    def test_bay_count_limit(self):
        """Test that bay_count limits positions."""
        positions = build_traversal_positions(3, 3, "top_left_down_then_across", 5)
        assert len(positions) == 5

    def test_skip_positions(self):
        """Test skip_positions filtering."""
        skip = [{"row": 0, "col": 0}]
        positions = build_traversal_positions(2, 2, "top_left_down_then_across", 3, skip)
        assert (0, 0) not in positions
        # Should have 3 positions (we request 3, and 1 is skipped)
        assert len(positions) == 3

    def test_skip_positions_invalid_entries(self):
        """Test that invalid skip entries are ignored."""
        skip = [{"row": 0, "col": 0}, {"invalid": "entry"}]
        positions = build_traversal_positions(2, 2, "top_left_down_then_across", 3, skip)
        assert (0, 0) not in positions
        # Should have 3 positions (we request 3, and 1 is skipped, invalid entry ignored)
        assert len(positions) == 3

    def test_skip_positions_all_eliminated(self):
        """Test error when all positions are eliminated."""
        skip = [{"row": 0, "col": 0}, {"row": 0, "col": 1}, {"row": 1, "col": 0}, {"row": 1, "col": 1}]
        with pytest.raises(ValueError, match="skip_positions eliminates all available positions"):
            build_traversal_positions(2, 2, "top_left_down_then_across", 4, skip)

    def test_skip_positions_too_many_eliminated(self):
        """Test error when too many positions are eliminated."""
        skip = [{"row": 0, "col": 0}, {"row": 0, "col": 1}]
        with pytest.raises(ValueError, match="skip_positions eliminates too many positions"):
            build_traversal_positions(2, 2, "top_left_down_then_across", 4, skip)

    def test_default_traversal(self):
        """Test that invalid traversal defaults to top_left_down_then_across."""
        positions = build_traversal_positions(2, 2, "invalid", 4)
        expected = [(0, 0), (1, 0), (0, 1), (1, 1)]
        assert positions == expected


class TestApplyTemplate:
    """Test template application."""

    def test_apply_simple_template(self):
        """Test applying a simple template."""
        template = {
            "id": "test",
            "name": "Test",
            "vendor": "Test",
            "rows": 2,
            "cols": 2,
            "bay_count": 4,
            "traversal_preset": "top_left_down_then_across"
        }
        result, traversal = apply_template({}, template)
        assert len(result) == 4
        assert "bay0" in result
        assert "bay1" in result
        assert traversal == "top_left_down_then_across"

    def test_apply_preserves_existing_data(self):
        """Test that existing bay data is preserved."""
        template = {
            "id": "test",
            "name": "Test",
            "vendor": "Test",
            "rows": 1,
            "cols": 1,
            "bay_count": 1,
            "traversal_preset": "top_left_down_then_across"
        }
        existing = {"bay0": {"label": "Custom Label", "role": "os"}}
        result, _ = apply_template(existing, template)
        assert result["bay0"]["label"] == "Custom Label"
        assert result["bay0"]["role"] == "os"

    def test_apply_custom_overrides(self):
        """Test custom overrides for display numbers."""
        template = {
            "id": "test",
            "name": "Test",
            "vendor": "Test",
            "rows": 2,
            "cols": 2,
            "bay_count": 4,
            "traversal_preset": "top_left_down_then_across"
        }
        overrides = {"bay0": {"display_number": "A1"}, "bay1": {"numbering_override": "A2"}}
        result, _ = apply_template({}, template, custom_overrides=overrides)
        assert result["bay0"]["display_number"] == "A1"
        assert result["bay1"]["display_number"] == "A2"

    def test_apply_type_override(self):
        """Test type override in custom_overrides."""
        template = {
            "id": "test",
            "name": "Test",
            "vendor": "Test",
            "rows": 1,
            "cols": 1,
            "bay_count": 1,
            "traversal_preset": "top_left_down_then_across"
        }
        overrides = {"bay0": {"type": "nvme"}}
        result, _ = apply_template({}, template, custom_overrides=overrides)
        assert result["bay0"]["type"] == "nvme"

    def test_apply_sets_physical_position(self):
        """Test that physical_position is set correctly."""
        template = {
            "id": "test",
            "name": "Test",
            "vendor": "Test",
            "rows": 2,
            "cols": 2,
            "bay_count": 4,
            "traversal_preset": "top_left_down_then_across"
        }
        result, _ = apply_template({}, template)
        assert result["bay0"]["physical_position"] == {"row": 0, "col": 0}
        assert result["bay1"]["physical_position"] == {"row": 1, "col": 0}


class TestValidateLayoutMetadata:
    """Test layout metadata validation."""

    def test_valid_metadata(self):
        """Test valid metadata passes validation."""
        metadata = {
            "template_id": "dell_r320_4bay",
            "traversal_preset": "top_left_down_then_across"
        }
        error = validate_layout_metadata(metadata, {}, DEFAULT_TEMPLATES)
        assert error is None

    def test_invalid_template_id(self):
        """Test invalid template_id is rejected."""
        metadata = {"template_id": "nonexistent"}
        error = validate_layout_metadata(metadata, {}, DEFAULT_TEMPLATES)
        assert "Unknown template_id" in error

    def test_invalid_traversal_preset(self):
        """Test invalid traversal_preset is rejected."""
        metadata = {"traversal_preset": "invalid"}
        error = validate_layout_metadata(metadata, {}, DEFAULT_TEMPLATES)
        assert "Unsupported traversal_preset" in error

    def test_invalid_custom_overrides_type(self):
        """Test non-dict custom_overrides is rejected."""
        metadata = {"custom_overrides": "invalid"}
        error = validate_layout_metadata(metadata, {}, DEFAULT_TEMPLATES)
        assert "custom_overrides must be an object" in error

    def test_skip_positions_validation(self):
        """Test skip_positions validation."""
        metadata = {
            "template": {
                "rows": 2,
                "cols": 2,
                "skip_positions": [{"row": 0, "col": 0}]
            }
        }
        error = validate_layout_metadata(metadata, {}, DEFAULT_TEMPLATES)
        assert error is None

    def test_skip_positions_too_large(self):
        """Test that oversized skip_positions is rejected (Lesson #5)."""
        metadata = {
            "template": {
                "rows": 10,
                "cols": 10,
                "skip_positions": [{"row": i, "col": 0} for i in range(101)]
            }
        }
        error = validate_layout_metadata(metadata, {}, DEFAULT_TEMPLATES)
        assert error is not None
        assert "too large" in error.lower()

    def test_skip_positions_out_of_bounds(self):
        """Test that out-of-bounds skip positions are rejected."""
        metadata = {
            "template": {
                "rows": 2,
                "cols": 2,
                "skip_positions": [{"row": 5, "col": 0}]
            }
        }
        error = validate_layout_metadata(metadata, {}, DEFAULT_TEMPLATES)
        assert "out of bounds" in error

    def test_duplicate_display_number(self):
        """Test that duplicate display_numbers are rejected."""
        bays = {
            "bay0": {"display_number": "1"},
            "bay1": {"display_number": "1"}
        }
        error = validate_layout_metadata({}, bays, DEFAULT_TEMPLATES)
        assert "Duplicate display_number" in error


class TestValidateTemplate:
    """Test template validation."""

    def test_valid_template(self):
        """Test valid template passes validation."""
        template = {
            "id": "test",
            "name": "Test",
            "vendor": "Test",
            "rows": 2,
            "cols": 2,
            "bay_count": 4,
            "traversal_preset": "top_left_down_then_across"
        }
        error = validate_template(template)
        assert error is None

    def test_missing_required_field(self):
        """Test missing required field is rejected."""
        template = {
            "id": "test",
            "name": "Test"
            # Missing vendor, rows, cols, bay_count, traversal_preset
        }
        error = validate_template(template)
        assert "missing required field" in error

    def test_empty_string_field(self):
        """Test empty string fields are rejected."""
        template = {
            "id": "",
            "name": "Test",
            "vendor": "Test",
            "rows": 1,
            "cols": 1,
            "bay_count": 1,
            "traversal_preset": "top_left_down_then_across"
        }
        error = validate_template(template)
        assert "must be a non-empty string" in error

    def test_invalid_numeric_fields(self):
        """Test non-integer numeric fields are rejected."""
        template = {
            "id": "test",
            "name": "Test",
            "vendor": "Test",
            "rows": "invalid",
            "cols": 1,
            "bay_count": 1,
            "traversal_preset": "top_left_down_then_across"
        }
        error = validate_template(template)
        assert "must be integers" in error

    def test_negative_numeric_fields(self):
        """Test negative numeric fields are rejected."""
        template = {
            "id": "test",
            "name": "Test",
            "vendor": "Test",
            "rows": -1,
            "cols": 1,
            "bay_count": 1,
            "traversal_preset": "top_left_down_then_across"
        }
        error = validate_template(template)
        assert "must be positive integers" in error

    def test_cols_exceeds_limit(self):
        """Test that cols > 5 is rejected (UI constraint)."""
        template = {
            "id": "test",
            "name": "Test",
            "vendor": "Test",
            "rows": 1,
            "cols": 6,
            "bay_count": 6,
            "traversal_preset": "top_left_down_then_across"
        }
        error = validate_template(template)
        assert "cols cannot exceed 5" in error

    def test_bay_count_exceeds_capacity(self):
        r"""Test that bay_count > rows * cols is rejected."""
        template = {
            "id": "test",
            "name": "Test",
            "vendor": "Test",
            "rows": 2,
            "cols": 2,
            "bay_count": 10,
            "traversal_preset": "top_left_down_then_across"
        }
        error = validate_template(template)
        assert "bay_count" in error and "cannot exceed" in error

    def test_invalid_traversal_preset(self):
        """Test invalid traversal_preset is rejected."""
        template = {
            "id": "test",
            "name": "Test",
            "vendor": "Test",
            "rows": 1,
            "cols": 1,
            "bay_count": 1,
            "traversal_preset": "invalid"
        }
        error = validate_template(template)
        assert "traversal_preset must be one of" in error

    def test_skip_positions_too_large(self):
        """Test that oversized skip_positions is rejected (Lesson #5)."""
        template = {
            "id": "test",
            "name": "Test",
            "vendor": "Test",
            "rows": 5,
            "cols": 5,
            "bay_count": 25,
            "traversal_preset": "top_left_down_then_across",
            "skip_positions": [{"row": i, "col": 0} for i in range(101)]
        }
        error = validate_template(template)
        assert "skip_positions array too large" in error

    def test_duplicate_skip_positions(self):
        """Test that duplicate skip positions are rejected."""
        template = {
            "id": "test",
            "name": "Test",
            "vendor": "Test",
            "rows": 2,
            "cols": 2,
            "bay_count": 4,
            "traversal_preset": "top_left_down_then_across",
            "skip_positions": [{"row": 0, "col": 0}, {"row": 0, "col": 0}]
        }
        error = validate_template(template)
        assert "Duplicate skip_positions entry" in error


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
