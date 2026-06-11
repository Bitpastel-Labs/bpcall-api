def test_get_me(client, auth_header):
    resp = client.get("/api/users/me", headers=auth_header)
    assert resp.status_code == 200
    assert resp.json()["username"] == "testuser"


def test_update_me(client, auth_header):
    resp = client.put("/api/users/me", json={
        "display_name": "New Name",
        "bio": "Hello world",
    }, headers=auth_header)
    assert resp.status_code == 200
    assert resp.json()["display_name"] == "New Name"
    assert resp.json()["bio"] == "Hello world"


def test_search_users(client, auth_header, create_user):
    create_user(email="search@bp.com", username="searchable")
    resp = client.get("/api/users/search?q=search", headers=auth_header)
    assert resp.status_code == 200
    assert len(resp.json()) == 1
    assert resp.json()[0]["username"] == "searchable"


def test_search_excludes_self(client, auth_header):
    resp = client.get("/api/users/search?q=testuser", headers=auth_header)
    assert resp.status_code == 200
    assert len(resp.json()) == 0
