import os
import sys
import tempfile
from pathlib import Path

os.environ["DB_PATH"] = str(Path(tempfile.mkdtemp()) / "test.db")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient  # noqa: E402
import app  # noqa: E402

c = TestClient(app.app)


def test_flow():
    r = c.post("/api/events", json={"title": "Pivo", "dates": ["2026-11-20"], "start_hour": 18, "end_hour": 22})
    assert r.status_code == 200
    eid = r.json()["id"]
    r = c.put(f"/api/events/{eid}/responses", json={"name": "Pepa", "slots": ["2026-11-20|19", "2030-01-01|19"]})
    assert r.json()["saved"] == 1  # neplatnej slot se zahodí
    ev = c.get(f"/api/events/{eid}").json()
    assert ev["responses"] == [{"name": "Pepa", "slots": ["2026-11-20|19"]}]


def test_frontend_and_404():
    assert c.get("/").status_code == 200
    assert c.get("/api/events/neexistuje").status_code == 404


def test_migrations_idempotent():
    app.migrate()
    with app.db() as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == len(app.MIGRATIONS)
