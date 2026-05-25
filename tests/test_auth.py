"""Tests for the auth service."""

from src.utils.auth import AuthService


class TestAuthService:
    def test_password_hashing(self):
        """Hashed passwords should verify correctly."""
        hashed = AuthService.hash_password("test123")
        assert AuthService.verify_password("test123", hashed) is True
        assert AuthService.verify_password("wrong", hashed) is False

    def test_token_creation_and_verification(self):
        """JWT tokens should be verifiable."""
        service = AuthService(secret_key="test-secret")
        token = service.create_token("admin")
        username = service.verify_token(token)
        assert username == "admin"

    def test_invalid_token(self):
        """Invalid tokens should return None."""
        service = AuthService(secret_key="test-secret")
        result = service.verify_token("invalid.token.here")
        assert result is None

    def test_different_secrets(self):
        """Tokens created with different secrets should not verify."""
        service1 = AuthService(secret_key="secret1")
        service2 = AuthService(secret_key="secret2")
        token = service1.create_token("admin")
        result = service2.verify_token(token)
        assert result is None

    def test_long_password_hashing(self):
        """Passwords over 72 bytes (bcrypt limit) should hash and verify correctly."""
        long_password = "a" * 200
        hashed = AuthService.hash_password(long_password)
        assert AuthService.verify_password(long_password, hashed) is True
        assert AuthService.verify_password("wrong", hashed) is False

    def test_short_password_still_works(self):
        """Short passwords should continue to hash normally without SHA-256."""
        short_password = "admin"
        hashed = AuthService.hash_password(short_password)
        assert AuthService.verify_password(short_password, hashed) is True