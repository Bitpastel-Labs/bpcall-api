def _setup_two_connected_users(client, create_user):
    """Helper: create 2 users, connect them, return headers and user data."""
    t1 = create_user(email="r1@bp.com", username="roomuser1", password="pass")
    t2 = create_user(email="r2@bp.com", username="roomuser2", password="pass")
    h1 = {"Authorization": f"Bearer {t1['access_token']}"}
    h2 = {"Authorization": f"Bearer {t2['access_token']}"}

    u2 = client.get("/api/users/search?q=roomuser2", headers=h1).json()[0]
    client.post("/api/connections/request", json={"user_id": u2["id"]}, headers=h1)

    pending = client.get("/api/connections/pending", headers=h2).json()
    client.put(f"/api/connections/{pending[0]['id']}/accept", headers=h2)

    u1 = client.get("/api/users/me", headers=h1).json()
    return h1, h2, u1, u2


def test_accepting_connection_creates_direct_room(client, create_user):
    h1, h2, u1, u2 = _setup_two_connected_users(client, create_user)
    rooms = client.get("/api/rooms", headers=h1).json()
    assert len(rooms) == 1
    assert rooms[0]["is_direct"] is True


def test_create_group_room(client, create_user):
    h1, h2, u1, u2 = _setup_two_connected_users(client, create_user)
    resp = client.post("/api/rooms", json={
        "name": "Team Chat",
        "member_ids": [u2["id"]],
    }, headers=h1)
    assert resp.status_code == 200
    assert resp.json()["name"] == "Team Chat"
    assert resp.json()["is_direct"] is False


def test_create_room_non_connection_fails(client, create_user):
    create_user(email="nc1@bp.com", username="nc1", password="pass")
    t2 = create_user(email="nc2@bp.com", username="nc2", password="pass")
    h1_tokens = create_user(email="nc3@bp.com", username="nc3", password="pass")
    h1 = {"Authorization": f"Bearer {h1_tokens['access_token']}"}

    u2 = client.get("/api/users/search?q=nc2", headers=h1).json()[0]
    resp = client.post("/api/rooms", json={
        "name": "Bad Room",
        "member_ids": [u2["id"]],
    }, headers=h1)
    assert resp.status_code == 400


def test_get_room_details(client, create_user):
    h1, h2, u1, u2 = _setup_two_connected_users(client, create_user)
    rooms = client.get("/api/rooms", headers=h1).json()
    room_id = rooms[0]["id"]

    resp = client.get(f"/api/rooms/{room_id}", headers=h1)
    assert resp.status_code == 200
    assert len(resp.json()["members"]) == 2


def test_get_messages_empty(client, create_user):
    h1, h2, u1, u2 = _setup_two_connected_users(client, create_user)
    rooms = client.get("/api/rooms", headers=h1).json()
    room_id = rooms[0]["id"]

    resp = client.get(f"/api/rooms/{room_id}/messages", headers=h1)
    assert resp.status_code == 200
    assert resp.json() == []
