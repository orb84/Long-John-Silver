"""
Tests for KeyStore security — permission hardening,
key masking, and security status diagnostics.
"""

import json
import stat
import tempfile
from pathlib import Path

import pytest

from src.llm_providers.key_store import KeyStore


class TestKeyStorePermissions:
    """Tests that saved key files have restricted permissions on POSIX."""

    def test_saved_file_has_owner_only_permissions(self):
        """After saving, the key file should be 0o600 (owner-only)."""
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            path = f.name

        store = KeyStore(store_path=path)
        store.add_key("openrouter", "sk-test-key-12345678")

        file_mode = Path(path).stat().st_mode
        perms = stat.S_IMODE(file_mode)
        assert perms == 0o600, f"Expected 0o600, got {oct(perms)}"
        Path(path).unlink()

    def test_multiple_saves_preserve_permissions(self):
        """Permissions should remain 0o600 after multiple saves."""
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            path = f.name

        store = KeyStore(store_path=path)
        store.add_key("openrouter", "sk-key-one-12345678")
        store.add_key("openrouter", "sk-key-two-12345678")

        file_mode = Path(path).stat().st_mode
        perms = stat.S_IMODE(file_mode)
        assert perms == 0o600, f"Expected 0o600 after multiple saves, got {oct(perms)}"
        Path(path).unlink()


class TestKeyMasking:
    """Tests for KeyStore.mask_key()."""

    def test_long_key_is_masked(self):
        """Long keys should show first 4 and last 4 chars."""
        result = KeyStore.mask_key("sk-abc123456789xyz")
        assert result.startswith("sk-a")
        assert result.endswith("9xyz")
        assert "*" in result

    def test_short_key_is_fully_masked(self):
        """Keys 8 chars or shorter should be fully masked."""
        result = KeyStore.mask_key("sk-test")
        assert result == "****"

    def test_eight_char_key_is_fully_masked(self):
        """Keys exactly 8 chars are fully masked."""
        result = KeyStore.mask_key("12345678")
        assert result == "****"

    def test_nine_char_key_is_partially_masked(self):
        """Keys with 9 chars should show first 4 and last 4."""
        result = KeyStore.mask_key("123456789")
        assert result.startswith("1234")
        assert result.endswith("6789")
        assert result == "1234*6789"

    def test_mask_preserves_length_pattern(self):
        """Masking should reveal only the boundary characters."""
        result = KeyStore.mask_key("sk-or-v1-0123456789abcdef0123456789abcdef")
        assert result.startswith("sk-o")
        assert result.endswith("cdef")
        # Middle portion should be all asterisks
        middle = result[4:-4]
        assert all(c == "*" for c in middle)


class TestListKeysMasked:
    """Tests for KeyStore.list_keys_masked()."""

    def test_masked_list_hides_full_keys(self):
        """list_keys_masked should return masked keys, not raw values."""
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            path = f.name

        store = KeyStore(store_path=path)
        store.add_key("openrouter", "sk-or-v1-abc123456789def", label="personal")

        masked = store.list_keys_masked("openrouter")
        assert len(masked) == 1
        entry = masked[0]
        assert "key_preview" in entry
        assert entry["key_preview"].startswith("sk-o")
        assert entry["key_preview"].endswith("9def")
        # The raw key should NOT appear anywhere in the masked list
        assert "sk-or-v1-abc123456789def" not in str(masked)
        Path(path).unlink()

    def test_masked_list_includes_metadata(self):
        """list_keys_masked should include id, label, is_active, created_at."""
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            path = f.name

        store = KeyStore(store_path=path)
        store.add_key("openrouter", "sk-test-123456789", label="work")

        masked = store.list_keys_masked("openrouter")
        entry = masked[0]
        assert "id" in entry
        assert entry["label"] == "work"
        assert entry["is_active"] is True
        assert "created_at" in entry
        assert "key_preview" in entry
        # Raw key should never appear
        assert "sk-test-123456789" not in entry.get("key_preview", "")
        Path(path).unlink()

    def test_list_keys_returns_raw_entries(self):
        """list_keys (unmasked) should still return raw APIKeyEntry objects."""
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            path = f.name

        store = KeyStore(store_path=path)
        store.add_key("openrouter", "sk-raw-key-value-here-12345")

        keys = store.list_keys("openrouter")
        assert len(keys) == 1
        assert keys[0].key == "sk-raw-key-value-here-12345"
        Path(path).unlink()


class TestSecurityStatus:
    """Tests for KeyStore.storage_security_status()."""

    def test_security_status_with_existing_store(self):
        """Security status should report file info and key counts."""
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            path = f.name

        store = KeyStore(store_path=path)
        store.add_key("openrouter", "sk-test-12345678")
        store.add_key("nvidia_nim", "nvapi-test-12345678")

        status = store.storage_security_status()
        assert status["store_exists"] is True
        assert status["permissions_ok"] is True
        assert status["key_count"] == 2
        assert status["provider_count"] == 2
        assert "store_path" in status
        Path(path).unlink()

    def test_security_status_nonexistent_file(self):
        """Security status should report when store file doesn't exist initially."""
        nonexistent_path = "/tmp/nonexistent_key_store_test_12345.json"
        # Clean up any leftover from previous runs
        Path(nonexistent_path).unlink(missing_ok=True)
        store = KeyStore(store_path=nonexistent_path)
        # Before adding any keys, the file doesn't exist
        status = store.storage_security_status()
        assert status["store_exists"] is False
        # After adding a key, the file is created
        store.add_key("openrouter", "sk-test-1234567890abc")
        status = store.storage_security_status()
        assert status["store_exists"] is True
        Path(nonexistent_path).unlink()

    def test_security_status_empty_store(self):
        """Security status for an empty store should have zero keys."""
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            path = f.name

        store = KeyStore(store_path=path)
        status = store.storage_security_status()
        assert status["key_count"] == 0
        assert status["provider_count"] == 0
        Path(path).unlink()


class TestDocstringHonesty:
    """Tests that the module docstring does not claim encryption."""

    def test_module_docstring_no_encryption_claim(self):
        """The module docstring should NOT claim at-rest encryption."""
        from src.llm_providers import key_store
        docstring = key_store.__doc__ or ""
        # The docstring must NOT say "with at-rest encryption" or similar
        assert "with at-rest encryption" not in docstring, (
            "Module docstring should not claim at-rest encryption"
        )
        # It SHOULD mention that keys are stored in plaintext
        assert "plaintext" in docstring.lower(), (
            "Module docstring should honestly state that keys are stored in plaintext"
        )