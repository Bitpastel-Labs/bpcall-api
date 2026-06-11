from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
import os
import json

from app.database import engine, Base, SessionLocal
from app import models  # noqa: F401 — registers models with Base
from app.routers import auth_router, users_router, connections_router, rooms_router, calls_router, admin_router
from app.websocket import manager
from app.auth import decode_token

load_dotenv()

app = FastAPI(title="BPCall API")

Base.metadata.create_all(bind=engine)

frontend_url = os.getenv("FRONTEND_URL", "http://localhost:3000")
origins = [frontend_url, "http://localhost:3000"]
# Also allow without trailing slash and with it
origins += [u.rstrip("/") for u in origins] + [u + "/" for u in origins if not u.endswith("/")]
origins = list(set(origins))
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


app.include_router(auth_router.router)
app.include_router(users_router.router)
app.include_router(connections_router.router)
app.include_router(rooms_router.router)
app.include_router(calls_router.router)
app.include_router(admin_router.router)


@app.get("/api/health")
def health_check():
    return {"status": "ok"}


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket, token: str = ""):
    if not token:
        await websocket.close(code=4001)
        return

    try:
        payload = decode_token(token)
        if payload.get("type") != "access":
            await websocket.close(code=4001)
            return
        user_id = int(payload["sub"])
    except Exception:
        await websocket.close(code=4001)
        return

    db = SessionLocal()
    try:
        await manager.connect(websocket, user_id, db)
        while True:
            text = await websocket.receive_text()
            try:
                data = json.loads(text)
                # Expire cached objects so we see fresh data (new rooms, members, etc.)
                db.expire_all()
                await manager.handle_message(user_id, data, db)
            except WebSocketDisconnect:
                raise  # Re-raise to break the loop
            except Exception as e:
                # Log but don't kill the connection for message processing errors
                print(f"[WS] Error handling message from user {user_id}: {e}")
                try:
                    db.rollback()
                except Exception:
                    pass
    except WebSocketDisconnect:
        await manager.disconnect(user_id, db)
    except Exception:
        await manager.disconnect(user_id, db)
    finally:
        db.close()
