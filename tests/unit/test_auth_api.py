from fastapi.testclient import TestClient

from medishield.api.deps import ROLE_ADMIN, ROLE_CLAIMER, ROLE_REVIEWER
from medishield.api.main import create_app


def test_login_success_claimer():
    app = create_app()
    client = TestClient(app)

    res = client.post(
        "/api/v1/auth/login",
        json={"username": "claimer", "password": "claimer123"},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["role"] == ROLE_CLAIMER
    assert data["username"] == "claimer"
    assert "access_token" in data
    assert data["token_type"] == "bearer"


def test_login_success_reviewer():
    app = create_app()
    client = TestClient(app)

    res = client.post(
        "/api/v1/auth/login",
        json={"username": "reviewer", "password": "reviewer123"},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["role"] == ROLE_REVIEWER
    assert data["username"] == "reviewer"


def test_login_success_admin():
    app = create_app()
    client = TestClient(app)

    res = client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "admin123"},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["role"] == ROLE_ADMIN
    assert data["username"] == "admin"


def test_login_invalid_password():
    app = create_app()
    client = TestClient(app)

    res = client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "wrongpassword"},
    )
    assert res.status_code == 401
    assert "Invalid username or password" in res.json()["detail"]


def test_login_unknown_user():
    app = create_app()
    client = TestClient(app)

    res = client.post(
        "/api/v1/auth/login",
        json={"username": "unknown_user", "password": "secret"},
    )
    assert res.status_code == 401


def test_auth_me_with_bearer_token():
    app = create_app()
    client = TestClient(app)

    # 1. Login as reviewer
    login_res = client.post(
        "/api/v1/auth/login",
        json={"username": "reviewer", "password": "reviewer123"},
    )
    token = login_res.json()["access_token"]

    # 2. Call /auth/me
    me_res = client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert me_res.status_code == 200
    me_data = me_res.json()
    assert me_data["role"] == ROLE_REVIEWER
    assert me_data["username"] == "reviewer"


def test_rbac_with_login_token():
    app = create_app()
    client = TestClient(app)

    # Claimer token cannot access admin dashboard
    claimer_res = client.post(
        "/api/v1/auth/login",
        json={"username": "claimer", "password": "claimer123"},
    )
    claimer_token = claimer_res.json()["access_token"]

    res = client.get(
        "/api/v1/admin/dashboard",
        headers={"Authorization": f"Bearer {claimer_token}"},
    )
    assert res.status_code == 403

    # Admin token can access admin dashboard
    admin_res = client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "admin123"},
    )
    admin_token = admin_res.json()["access_token"]

    res_admin = client.get(
        "/api/v1/admin/dashboard",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert res_admin.status_code == 200

