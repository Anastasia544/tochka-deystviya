import csv
import io
import json
import os
import secrets
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Depends, HTTPException, status
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = Path(os.environ.get("TEST_DB_PATH", str(BASE_DIR / "submissions.sqlite3")))
ADMIN_USER = os.environ.get("ADMIN_USER", "owner")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")

app = FastAPI(title="Точка действия — результаты теста", docs_url=None, redoc_url=None)
security = HTTPBasic()

def get_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with get_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS submissions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                name TEXT NOT NULL,
                email TEXT,
                instagram TEXT NOT NULL,
                scores_json TEXT NOT NULL,
                leading_json TEXT NOT NULL,
                low_scores INTEGER NOT NULL DEFAULT 0,
                answers_json TEXT NOT NULL
            )
        """)
        conn.commit()

init_db()

class Score(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    score: int = Field(ge=0, le=20)
    level: str = Field(max_length=100)

class Leader(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    score: int = Field(ge=0, le=20)

class Answer(BaseModel):
    question: str = Field(min_length=1, max_length=1000)
    score: int = Field(ge=0, le=4)

class Submission(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    email: str = Field(default="", max_length=254)
    instagram: str = Field(min_length=1, max_length=100)
    scores: list[Score]
    leading_results: list[Leader]
    low_scores: bool = False
    answers: list[Answer]

def require_admin(credentials: HTTPBasicCredentials = Depends(security)):
    # Set ADMIN_PASSWORD to a long, unique password before running this app.
    if not ADMIN_PASSWORD:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Set ADMIN_PASSWORD in the environment before opening the admin page."
        )
    user_ok = secrets.compare_digest(credentials.username, ADMIN_USER)
    password_ok = secrets.compare_digest(credentials.password, ADMIN_PASSWORD)
    if not (user_ok and password_ok):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username

def esc(value: Any) -> str:
    s = str(value if value is not None else "")
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
             .replace('"', "&quot;").replace("'", "&#x27;"))

@app.post("/api/submissions")
def create_submission(submission: Submission):
    email = submission.email.strip()
    if email and ("@" not in email or "." not in email.split("@")[-1]):
        raise HTTPException(status_code=422, detail="Invalid email")
    if len(submission.scores) != 5:
        raise HTTPException(status_code=422, detail="Exactly five scale results are required")
    if len(submission.answers) != 25:
        raise HTTPException(status_code=422, detail="Exactly 25 answers are required")
    with get_db() as conn:
        cur = conn.execute("""
            INSERT INTO submissions
            (created_at, name, email, instagram, scores_json, leading_json, low_scores, answers_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            datetime.now(timezone.utc).isoformat(timespec="seconds"),
            submission.name.strip(),
            email or None,
            submission.instagram.strip().lstrip("@"),
            json.dumps([x.model_dump() for x in submission.scores], ensure_ascii=False),
            json.dumps([x.model_dump() for x in submission.leading_results], ensure_ascii=False),
            int(submission.low_scores),
            json.dumps([x.model_dump() for x in submission.answers], ensure_ascii=False),
        ))
        conn.commit()
        submission_id = cur.lastrowid
    return {"ok": True, "id": submission_id}

@app.get("/admin", response_class=HTMLResponse)
def admin_dashboard(_: str = Depends(require_admin)):
    with get_db() as conn:
        rows = conn.execute("SELECT * FROM submissions ORDER BY id DESC").fetchall()
    body_rows = []
    for row in rows:
        scores = json.loads(row["scores_json"])
        leaders = json.loads(row["leading_json"])
        score_cells = "".join(
            f"<div><strong>{esc(s['name'])}:</strong> {s['score']}/20</div>" for s in scores
        )
        leader_text = " · ".join(f"{x['name']} ({x['score']}/20)" for x in leaders) or "Не выделен"
        body_rows.append(f"""
          <tr>
            <td>{row['id']}</td><td>{esc(row['created_at'].replace('T',' ')[:19])}</td>
            <td>{esc(row['name'])}</td><td>{esc(row['email'] or '—')}</td>
            <td>{'@' + esc(row['instagram'])}</td>
            <td>{esc(leader_text)}{'<br><small>Низкие баллы по всем шкалам</small>' if row['low_scores'] else ''}</td>
            <td><details><summary>Показать баллы</summary>{score_cells}</details></td>
          </tr>
        """)
    html = f"""<!doctype html><html lang="ru"><head><meta charset="utf-8">
    <meta name="viewport" content="width=device-width,initial-scale=1">
    <title>Закрытая таблица участников — Точка действия</title>
    <style>
    body{{font-family:system-ui,-apple-system,Segoe UI,sans-serif;background:#f7f5f1;color:#242c35;margin:0;padding:24px}}
    main{{max-width:1400px;margin:auto}}h1{{font-size:clamp(25px,4vw,38px);margin-bottom:5px}}
    p{{color:#626a75}}.top{{display:flex;justify-content:space-between;align-items:center;gap:16px;flex-wrap:wrap}}
    a,.btn{{display:inline-block;background:#4568d8;color:white;padding:11px 15px;border-radius:10px;text-decoration:none;border:0}}
    .table-wrap{{overflow:auto;background:#fffefa;border:1px solid #e7e3dc;border-radius:16px;margin-top:20px}}
    table{{width:100%;border-collapse:collapse;min-width:1050px}}th,td{{padding:13px 14px;text-align:left;vertical-align:top;border-bottom:1px solid #eee9e1;font-size:13px}}
    th{{background:#f0effb;position:sticky;top:0}}tr:hover td{{background:#faf9ff}}small{{color:#707780}}
    .empty{{padding:30px;text-align:center}}.notice{{background:#fff2df;padding:12px 15px;border-radius:10px;font-size:13px}}
    </style></head><body><main>
    <div class="top"><div><h1>Участники теста</h1><p>Закрытая таблица владельца · записей: {len(rows)}</p></div><a href="/admin/export.csv">Скачать CSV</a></div>
    <div class="notice">Эта страница защищена паролем. Не публикуйте пароль и используйте HTTPS при размещении в интернете. Контактные данные и ответы доступны только авторизованному владельцу.</div>
    <div class="table-wrap"><table><thead><tr><th>ID</th><th>Дата (UTC)</th><th>Имя</th><th>Email</th><th>Instagram</th><th>Ведущие результаты</th><th>Все шкалы</th></tr></thead>
    <tbody>{''.join(body_rows) if body_rows else '<tr><td colspan="7" class="empty">Пока никто не прошёл тест.</td></tr>'}</tbody></table></div>
    </main></body></html>"""
    return HTMLResponse(html)

@app.get("/admin/export.csv")
def export_csv(_: str = Depends(require_admin)):
    with get_db() as conn:
        rows = conn.execute("SELECT * FROM submissions ORDER BY id DESC").fetchall()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["ID", "Дата UTC", "Имя", "Email", "Instagram", "Ведущие результаты", "Зависимость от внешней оценки", "Перфекционизм", "Сомнение", "Избегание", "Внутренний конфликт", "Низкие баллы по всем шкалам"])
    for row in rows:
        scores = {x["name"]: x["score"] for x in json.loads(row["scores_json"])}
        leaders = json.loads(row["leading_json"])
        writer.writerow([
            row["id"], row["created_at"], row["name"], row["email"] or "",
            "@" + row["instagram"],
            "; ".join(f"{x['name']} ({x['score']}/20)" for x in leaders),
            scores.get("Зависимость от внешней оценки", ""),
            scores.get("Перфекционизм", ""), scores.get("Сомнение", ""),
            scores.get("Избегание", ""), scores.get("Внутренний конфликт", ""),
            "Да" if row["low_scores"] else "Нет",
        ])
    data = ("\ufeff" + output.getvalue()).encode("utf-8")
    return StreamingResponse(
        io.BytesIO(data), media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="participants.csv"'}
    )

app.mount("/", StaticFiles(directory=BASE_DIR, html=True), name="site")
