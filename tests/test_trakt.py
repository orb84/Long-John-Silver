import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from src.integrations.trakt import TraktClient

@pytest.mark.asyncio
async def test_pkce_generation():
    """Test that PKCE verifier and challenge are generated correctly."""
    verifier, challenge = TraktClient.generate_pkce_pair()
    
    assert len(verifier) >= 43
    assert len(challenge) > 0
    assert "=" not in challenge  # Should be base64url encoded without padding
    assert verifier != challenge

def test_auth_url_construction():
    """Test that the authorization URL is built correctly with PKCE params."""
    import urllib.parse
    client = TraktClient(client_id="test_id")
    redirect_uri = "http://localhost/callback"
    state = "random_state"
    verifier, challenge = client.generate_pkce_pair()
    
    auth_url = client.get_auth_url(redirect_uri, state, challenge)
    
    assert "https://trakt.tv/oauth/authorize" in auth_url
    assert "client_id=test_id" in auth_url
    assert f"redirect_uri={urllib.parse.quote(redirect_uri, safe='')}" in auth_url
    assert f"state={state}" in auth_url
    assert f"code_challenge={challenge}" in auth_url
    assert "code_challenge_method=S256" in auth_url

@pytest.mark.asyncio
async def test_token_exchange_success():
    """Test exchanging an auth code for tokens."""
    client = TraktClient(client_id="test_id")
    
    # Mock response data
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "access_token": "mock_access_token",
        "refresh_token": "mock_refresh_token",
        "expires_in": 7776000
    }
    mock_response.raise_for_status = MagicMock()

    # Mock httpx.AsyncClient.post
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_response
        
        tokens = await client.exchange_code_for_token(
            code="auth_code", 
            redirect_uri="http://localhost/callback", 
            verifier="verifier_string"
        )
        
        assert tokens["access_token"] == "mock_access_token"
        assert tokens["refresh_token"] == "mock_refresh_token"
        # Verify the client headers were updated
        assert client._headers["Authorization"] == "Bearer mock_access_token"

@pytest.mark.asyncio
async def test_authenticated_request():
    """Test that requests include the Bearer token."""
    client = TraktClient(client_id="test_id", access_token="mock_token")
    
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = []
    mock_response.raise_for_status = MagicMock()

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = mock_response
        
        await client.get_popular_shows()
        
        assert mock_get.called
        args, kwargs = mock_get.call_args
        headers = kwargs.get("headers")
        assert headers["Authorization"] == "Bearer mock_token"
        assert headers["trakt-api-key"] == "test_id"

@pytest.mark.asyncio
async def test_get_category_item_details():
    """Test fetching show details."""
    client = TraktClient(client_id="test_id")
    
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"title": "The Bear", "year": 2022}
    mock_response.raise_for_status = MagicMock()

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = mock_response
        
        details = await client.get_category_item_details("the-bear")
        
        assert details["title"] == "The Bear"
        assert "shows/the-bear" in mock_get.call_args[0][0]
        assert mock_get.call_args[1]["params"]["extended"] == "full"

@pytest.mark.asyncio
async def test_get_calendar_auth():
    """Test calendar endpoint selection based on auth."""
    # 1. No auth -> all/shows
    client_no_auth = TraktClient(client_id="test_id")
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = []
    mock_response.raise_for_status = MagicMock()

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = mock_response
        await client_no_auth.get_calendar()
        assert "calendars/all/shows" in mock_get.call_args[0][0]

    # 2. With auth -> my/shows
    client_auth = TraktClient(client_id="test_id", access_token="mock_token")
    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = mock_response
        await client_auth.get_calendar()
        assert "calendars/my/shows" in mock_get.call_args[0][0]
