from app.auth import create_access_token


def test_websocket_connects_with_valid_token(client, create_user):
    tokens = create_user(email="ws@bp.com", username="wsuser")
    token = tokens["access_token"]
    with client.websocket_connect(f"/ws?token={token}") as ws:
        ws.send_json({"type": "typing", "payload": {"room_id": 1}})
        # No error means connection is established
