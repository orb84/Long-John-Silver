"""Tests for two-phase confirmation token binding."""

from src.core.security.confirmation import SecurityConfirmationService


def test_confirmation_token_is_bound_to_exact_payload() -> None:
    """A confirmation token cannot be reused for a modified destructive action."""
    service = SecurityConfirmationService()
    payload = {"category_id": "tv", "item_id": "show", "path": "/library/tv/show"}
    request = service.create_request("delete_item", payload, category_id="tv", affected_paths=[payload["path"]])

    assert not service.verify(request.token, "delete_item", {**payload, "item_id": "other"})
    assert service.verify(request.token, "delete_item", payload)
    assert not service.verify(request.token, "delete_item", payload)


def test_confirmation_receipt_exposes_token_and_paths() -> None:
    """Confirmation receipts should show the exact paths requiring approval."""
    service = SecurityConfirmationService()
    request = service.create_request("delete_item", {"id": "x"}, category_id="movie", affected_paths=["/library/movie"])
    receipt = service.receipt_for_request(request)

    assert receipt.status == "needs_confirmation"
    assert receipt.data["confirmation_token"] == request.token
    assert receipt.data["affected_paths"] == ["/library/movie"]
