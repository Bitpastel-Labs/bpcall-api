def test_send_connection_request(client, auth_header, create_user):
    user2 = create_user(email="user2@bp.com", username="user2")
    resp = client.get("/api/users/search?q=user2", headers=auth_header)
    user2_id = resp.json()[0]["id"]

    resp = client.post("/api/connections/request", json={"user_id": user2_id}, headers=auth_header)
    assert resp.status_code == 200
    assert resp.json()["status"] == "pending"


def test_cannot_connect_self(client, auth_header):
    me = client.get("/api/users/me", headers=auth_header).json()
    resp = client.post("/api/connections/request", json={"user_id": me["id"]}, headers=auth_header)
    assert resp.status_code == 400


def test_accept_creates_chat_room(client, create_user):
    tokens1 = create_user(email="a@bp.com", username="usera", password="pass")
    tokens2 = create_user(email="b@bp.com", username="userb", password="pass")
    h1 = {"Authorization": f"Bearer {tokens1['access_token']}"}
    h2 = {"Authorization": f"Bearer {tokens2['access_token']}"}

    user_b = client.get("/api/users/search?q=userb", headers=h1).json()[0]
    client.post("/api/connections/request", json={"user_id": user_b["id"]}, headers=h1)

    pending = client.get("/api/connections/pending", headers=h2).json()
    assert len(pending) == 1

    resp = client.put(f"/api/connections/{pending[0]['id']}/accept", headers=h2)
    assert resp.status_code == 200
    assert resp.json()["status"] == "accepted"

    conns = client.get("/api/connections", headers=h1).json()
    assert len(conns) == 1


def test_reject_connection(client, create_user):
    tokens1 = create_user(email="c@bp.com", username="userc", password="pass")
    tokens2 = create_user(email="d@bp.com", username="userd", password="pass")
    h1 = {"Authorization": f"Bearer {tokens1['access_token']}"}
    h2 = {"Authorization": f"Bearer {tokens2['access_token']}"}

    user_d = client.get("/api/users/search?q=userd", headers=h1).json()[0]
    client.post("/api/connections/request", json={"user_id": user_d["id"]}, headers=h1)

    pending = client.get("/api/connections/pending", headers=h2).json()
    resp = client.put(f"/api/connections/{pending[0]['id']}/reject", headers=h2)
    assert resp.status_code == 200
    assert resp.json()["status"] == "rejected"


def test_duplicate_request_blocked(client, auth_header, create_user):
    tokens2 = create_user(email="e@bp.com", username="usere")
    user_e = client.get("/api/users/search?q=usere", headers=auth_header).json()[0]

    client.post("/api/connections/request", json={"user_id": user_e["id"]}, headers=auth_header)
    resp = client.post("/api/connections/request", json={"user_id": user_e["id"]}, headers=auth_header)
    assert resp.status_code == 400
