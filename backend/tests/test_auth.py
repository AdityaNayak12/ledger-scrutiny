import os
import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_auth_register_creates_org_and_user():
    # STEP 5 Test 1: register creates org+user and returns a working token
    reg_payload = {
        "organization_name": "Alpha CA Firm",
        "email": "alpha_auditor@alphacafirm.com",
        "password": "Password123!"
    }
    res = client.post("/auth/register", json=reg_payload)
    assert res.status_code == 201
    data = res.json()
    assert "access_token" in data
    assert data["token_type"] == "bearer"
    assert data["organization_name"] == "Alpha CA Firm"
    assert data["email"] == "alpha_auditor@alphacafirm.com"

    token = data["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # Test working token on protected endpoint
    ent_res = client.get("/entities", headers=headers)
    assert ent_res.status_code == 200
    assert ent_res.json() == []


def test_auth_register_duplicate_email_rejected():
    reg_payload = {
        "organization_name": "Firm One",
        "email": "dup@firm.com",
        "password": "password"
    }
    res1 = client.post("/auth/register", json=reg_payload)
    assert res1.status_code == 201

    res2 = client.post("/auth/register", json=reg_payload)
    assert res2.status_code == 400
    assert "already exists" in res2.json()["detail"]


def test_auth_login_credentials():
    # STEP 5 Test 2: login with correct/incorrect credentials
    client.post("/auth/register", json={
        "organization_name": "Beta Firm",
        "email": "beta@betafirm.com",
        "password": "CorrectPassword"
    })

    # Correct login
    login_success = client.post("/auth/login", json={
        "email": "beta@betafirm.com",
        "password": "CorrectPassword"
    })
    assert login_success.status_code == 200
    data = login_success.json()
    assert "access_token" in data
    assert data["email"] == "beta@betafirm.com"

    # Wrong password
    login_wrong_pass = client.post("/auth/login", json={
        "email": "beta@betafirm.com",
        "password": "WrongPassword"
    })
    assert login_wrong_pass.status_code == 401
    assert login_wrong_pass.json()["detail"] == "Invalid credentials"

    # Nonexistent email (must return generic invalid credentials, no leakage)
    login_nonexistent = client.post("/auth/login", json={
        "email": "nonexistent@betafirm.com",
        "password": "CorrectPassword"
    })
    assert login_nonexistent.status_code == 401
    assert login_nonexistent.json()["detail"] == "Invalid credentials"


def test_protected_routes_unauthenticated():
    # STEP 5 Test 3: accessing any protected route without a token returns 401
    assert client.get("/entities").status_code == 401
    assert client.post("/entities", json={"name": "X", "materiality_threshold": "100"}).status_code == 401
    assert client.delete("/entities/1").status_code == 401
    assert client.get("/entities/1/periods").status_code == 401
    assert client.post("/entities/1/scrutiny-run?period_start=2025-04-01&period_end=2026-03-31").status_code == 401
    assert client.get("/entities/1/exceptions").status_code == 401
    assert client.post("/gstin/lookup", json={"gstin": "27AAAAA1111A1Z1"}).status_code == 401


def test_cross_organization_entity_access_returns_404():
    # STEP 5 Test 4 & 5: accessing another organization's entity_id returns 404 not entity data,
    # and full lifecycle: register Org A, create entity, register Org B, confirm Org B token cannot see Org A entity.
    
    # 1. Register Org A
    reg_a = client.post("/auth/register", json={
        "organization_name": "Org A CA Firm",
        "email": "auditor_a@orga.com",
        "password": "PasswordA"
    })
    token_a = reg_a.json()["access_token"]
    headers_a = {"Authorization": f"Bearer {token_a}"}

    # 2. Create Entity under Org A
    create_ent = client.post("/entities", json={
        "name": "Org A Client Pvt Ltd",
        "materiality_threshold": "5000.00"
    }, headers=headers_a)
    assert create_ent.status_code == 201
    entity_a_id = create_ent.json()["id"]

    # Org A sees its entity
    list_a = client.get("/entities", headers=headers_a)
    assert list_a.status_code == 200
    assert len(list_a.json()) == 1
    assert list_a.json()[0]["id"] == entity_a_id

    # 3. Register Org B
    reg_b = client.post("/auth/register", json={
        "organization_name": "Org B CA Firm",
        "email": "auditor_b@orgb.com",
        "password": "PasswordB"
    })
    token_b = reg_b.json()["access_token"]
    headers_b = {"Authorization": f"Bearer {token_b}"}

    # 4. Org B lists entities -> empty list (cannot see Org A entity)
    list_b = client.get("/entities", headers=headers_b)
    assert list_b.status_code == 200
    assert len(list_b.json()) == 0

    # 5. Org B tries to access Org A's entity_id -> returns 404 (not 403, no leakage)
    assert client.get(f"/entities/{entity_a_id}/periods", headers=headers_b).status_code == 404
    assert client.delete(f"/entities/{entity_a_id}", headers=headers_b).status_code == 404
    assert client.post(f"/entities/{entity_a_id}/scrutiny-run?period_start=2025-04-01&period_end=2026-03-31", headers=headers_b).status_code == 404
    assert client.get(f"/entities/{entity_a_id}/exceptions", headers=headers_b).status_code == 404
    assert client.patch(f"/entities/{entity_a_id}/exceptions/1", json={"status": "CLEARED"}, headers=headers_b).status_code == 404


def test_secret_key_missing_raises_startup_error(monkeypatch):
    # Genuinely remove SECRET_KEY from OS environment
    monkeypatch.delenv("SECRET_KEY", raising=False)
    
    from app.auth.security import get_secret_key
    from app.main import on_startup
    
    # 1. Verify get_secret_key() raises RuntimeError when env var is missing
    with pytest.raises(RuntimeError) as exc_info:
        get_secret_key()
    assert "SECRET_KEY environment variable is not set" in str(exc_info.value)

    # 2. Verify app on_startup hook fails loud, preventing server from starting up
    with pytest.raises(RuntimeError) as startup_exc_info:
        on_startup()
    assert "SECRET_KEY environment variable is not set" in str(startup_exc_info.value)


def test_google_client_id_missing_raises_startup_error(monkeypatch):
    monkeypatch.delenv("GOOGLE_CLIENT_ID", raising=False)
    
    from app.auth.security import get_google_client_id
    from app.main import on_startup

    with pytest.raises(RuntimeError) as exc_info:
        get_google_client_id()
    assert "GOOGLE_CLIENT_ID environment variable is not set" in str(exc_info.value)

    with pytest.raises(RuntimeError) as startup_exc_info:
        on_startup()
    assert "GOOGLE_CLIENT_ID environment variable is not set" in str(startup_exc_info.value)


def test_auth_login_google_account_password_attempt_rejected():
    from unittest.mock import patch
    
    # 1. Register a Google account via /auth/google
    with patch("google.oauth2.id_token.verify_oauth2_token") as mock_verify:
        mock_verify.return_value = {"email": "google_user@gmail.com"}
        
        reg_res = client.post("/auth/google", json={
            "id_token": "valid-mock-id-token",
            "organization_name": "Google User Firm"
        })
        assert reg_res.status_code == 200
        assert reg_res.json()["organization_name"] == "Google User Firm"

    # 2. Attempt login via password endpoint -> should reject with explicit Google Sign-In message
    login_res = client.post("/auth/login", json={
        "email": "google_user@gmail.com",
        "password": "AnyPassword123"
    })
    assert login_res.status_code == 401
    assert "This account uses Google Sign-In" in login_res.json()["detail"]


def test_auth_google_account_linking_and_new_org_flow():
    from unittest.mock import patch

    with patch("google.oauth2.id_token.verify_oauth2_token") as mock_verify:
        mock_verify.return_value = {"email": "new_google_auditor@gmail.com"}

        # 1. First attempt: new account without organization_name -> 422 Unprocessable Entity
        res_no_org = client.post("/auth/google", json={"id_token": "mock-token-1"})
        assert res_no_org.status_code == 422
        assert "organization_name is required" in res_no_org.json()["detail"]

        # 2. Second attempt: provide organization_name -> 200 Created
        res_with_org = client.post("/auth/google", json={
            "id_token": "mock-token-1",
            "organization_name": "New Google Firm Ltd"
        })
        assert res_with_org.status_code == 200
        token = res_with_org.json()["access_token"]
        assert res_with_org.json()["email"] == "new_google_auditor@gmail.com"
        assert res_with_org.json()["organization_name"] == "New Google Firm Ltd"

        # Verify token works on protected endpoints
        ent_res = client.get("/entities", headers={"Authorization": f"Bearer {token}"})
        assert ent_res.status_code == 200

        # 3. Third attempt (Account Linking): sign in again with same Google account -> logs straight in without org_name
        res_relogin = client.post("/auth/google", json={"id_token": "mock-token-1"})
        assert res_relogin.status_code == 200
        assert res_relogin.json()["email"] == "new_google_auditor@gmail.com"
        assert res_relogin.json()["organization_name"] == "New Google Firm Ltd"

