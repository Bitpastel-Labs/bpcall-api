def test_signup_success(client):
    resp = client.post("/api/auth/signup", json={
        "email": "alice@bp.com",
        "username": "alice",
        "password": "securepass",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert "access_token" in data
    assert "refresh_token" in data


def test_signup_duplicate_email(client, create_user):
    create_user(email="dup@bp.com", username="user1")
    resp = client.post("/api/auth/signup", json={
        "email": "dup@bp.com",
        "username": "user2",
        "password": "pass",
    })
    assert resp.status_code == 400
    assert "Email already registered" in resp.json()["detail"]


def test_signup_duplicate_username(client, create_user):
    create_user(email="a@bp.com", username="dupuser")
    resp = client.post("/api/auth/signup", json={
        "email": "b@bp.com",
        "username": "dupuser",
        "password": "pass",
    })
    assert resp.status_code == 400
    assert "Username already taken" in resp.json()["detail"]


def test_login_with_email(client, create_user):
    create_user(email="login@bp.com", username="loginuser", password="mypass")
    resp = client.post("/api/auth/login", json={
        "identifier": "login@bp.com",
        "password": "mypass",
    })
    assert resp.status_code == 200
    assert "access_token" in resp.json()


def test_login_with_username(client, create_user):
    create_user(email="login2@bp.com", username="loginuser2", password="mypass")
    resp = client.post("/api/auth/login", json={
        "identifier": "loginuser2",
        "password": "mypass",
    })
    assert resp.status_code == 200


def test_login_wrong_password(client, create_user):
    create_user(email="wrong@bp.com", username="wronguser", password="correct")
    resp = client.post("/api/auth/login", json={
        "identifier": "wrong@bp.com",
        "password": "incorrect",
    })
    assert resp.status_code == 401


def test_refresh_token(client, create_user):
    tokens = create_user(email="refresh@bp.com", username="refreshuser")
    resp = client.post("/api/auth/refresh", json={
        "refresh_token": tokens["refresh_token"],
    })
    assert resp.status_code == 200
    assert "access_token" in resp.json()
