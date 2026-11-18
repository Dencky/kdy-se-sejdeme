import json
import os
import secrets
import sqlite3
from contextlib import closing
from datetime import date
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, field_validator

BASE = Path(__file__).parent
INDEX = BASE / "static" / "index.html"

if os.getenv("DB_PATH"):
    DB = Path(os.environ["DB_PATH"])
elif os.getenv("RAILWAY_VOLUME_MOUNT_PATH"):
    DB = Path(os.environ["RAILWAY_VOLUME_MOUNT_PATH"]) / "data.db"
else:
    DB = BASE / "data.db"
DB.parent.mkdir(parents=True, exist_ok=True)
print(f"[kdy-se-sejdem] database: {DB}", flush=True)

app = FastAPI(title="Kdy se sejdem?")


def db():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    return conn


MIGRATIONS = [
    """
    CREATE TABLE IF NOT EXISTS events (
        id TEXT PRIMARY KEY,
        title TEXT NOT NULL,
        dates TEXT NOT NULL,          -- JSON array "YYYY-MM-DD"
        start_hour INTEGER NOT NULL,
        end_hour INTEGER NOT NULL
    );
    CREATE TABLE IF NOT EXISTS responses (
        event_id TEXT NOT NULL REFERENCES events(id),
        name TEXT NOT NULL,
        slots TEXT NOT NULL,          -- JSON array "YYYY-MM-DD|HH"
        PRIMARY KEY (event_id, name)
    );
    """,
]


def migrate():
    with closing(db()) as c:
        current = c.execute("PRAGMA user_version").fetchone()[0]
        for i, sql in enumerate(MIGRATIONS[current:], start=current + 1):
            c.executescript(f"BEGIN; {sql}; PRAGMA user_version = {i}; COMMIT;")
            print(f"[kdy-se-sejdem] migration {i} finished", flush=True)


migrate()


class EventIn(BaseModel):
    title: str = Field(min_length=1, max_length=100)
    dates: list[str] = Field(min_length=1, max_length=31)
    start_hour: int = Field(ge=0, le=23)
    end_hour: int = Field(ge=1, le=24)

    @field_validator("dates")
    @classmethod
    def check_dates(cls, v):
        for d in v:
            date.fromisoformat(d)
        return sorted(set(v))


class ResponseIn(BaseModel):
    name: str = Field(min_length=1, max_length=40)
    slots: list[str] = Field(max_length=31 * 24)


def load_event(conn, event_id):
    row = conn.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Event does not exist")
    return row


@app.post("/api/events")
def create_event(ev: EventIn):
    if ev.end_hour <= ev.start_hour:
        raise HTTPException(400, "End hour must be greater than start hour")
    event_id = secrets.token_urlsafe(6)
    with closing(db()) as c:
        c.execute(
            "INSERT INTO events VALUES (?, ?, ?, ?, ?)",
            (event_id, ev.title.strip(), json.dumps(ev.dates), ev.start_hour, ev.end_hour),
        )
        c.commit()
    return {"id": event_id}


@app.get("/api/events/{event_id}")
def get_event(event_id: str):
    with closing(db()) as c:
        row = load_event(c, event_id)
        resp = c.execute(
            "SELECT name, slots FROM responses WHERE event_id = ? ORDER BY name COLLATE NOCASE",
            (event_id,),
        ).fetchall()
    return {
        "id": row["id"],
        "title": row["title"],
        "dates": json.loads(row["dates"]),
        "start_hour": row["start_hour"],
        "end_hour": row["end_hour"],
        "responses": [{"name": r["name"], "slots": json.loads(r["slots"])} for r in resp],
    }


@app.put("/api/events/{event_id}/responses")
def save_response(event_id: str, r: ResponseIn):
    with closing(db()) as c:
        row = load_event(c, event_id)
        dates = set(json.loads(row["dates"]))
        hours = range(row["start_hour"], row["end_hour"])
        valid = {f"{d}|{h:02d}" for d in dates for h in hours}
        slots = sorted(set(r.slots) & valid)
        c.execute(
            "INSERT INTO responses VALUES (?, ?, ?) "
            "ON CONFLICT(event_id, name) DO UPDATE SET slots = excluded.slots",
            (event_id, r.name.strip(), json.dumps(slots)),
        )
        c.commit()
    return {"ok": True, "saved": len(slots)}


@app.delete("/api/events/{event_id}/responses/{name}")
def delete_response(event_id: str, name: str):
    with closing(db()) as c:
        load_event(c, event_id)
        c.execute("DELETE FROM responses WHERE event_id = ? AND name = ?", (event_id, name))
        c.commit()
    return {"ok": True}


@app.get("/")
@app.get("/e/{event_id}")
def index(event_id: str | None = None):
    return FileResponse(INDEX)
