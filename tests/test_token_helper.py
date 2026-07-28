from accessToken.newtoken import (
    extract_auth_code,
    generate_and_store_token,
    looks_like_access_token,
)


def test_extract_auth_code_from_redirect_url():
    url = "https://example.com/callback?state=abc&auth_code=FYERS_CODE_123"
    assert extract_auth_code(url) == "FYERS_CODE_123"


def test_detects_jwt_style_access_token():
    token = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.payload.signature"
    assert looks_like_access_token(token)


def test_generate_and_store_token_accepts_raw_access_token(tmp_path, monkeypatch):
    token = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.payload.signature"
    output = tmp_path / "token.txt"

    def fail_exchange(*args, **kwargs):
        raise AssertionError("exchange_auth_code should not be called for a raw token")

    monkeypatch.setattr("accessToken.newtoken.exchange_auth_code", fail_exchange)

    path = generate_and_store_token(
        token,
        client_id="client",
        secret_key="secret",
        redirect_uri="https://example.com/callback",
        token_path=output,
    )

    assert path == output.resolve()
    assert output.read_text(encoding="utf-8") == token
