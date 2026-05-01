from flask import (Flask, send_from_directory, session, redirect,
                   url_for, request, render_template)
from functools import wraps
from datetime import datetime
import os
from database import init_db, get_conn

app = Flask(__name__, static_folder=".", template_folder="templates")
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-key-change-in-production")

APP_PASSWORD = os.environ.get("APP_PASSWORD", "linkedin2026")

init_db()


# ── 文字数・プレビュー用テンプレートフィルター ────────────────────────────────
@app.template_filter("firstlines")
def first_lines_filter(s, n=3):
    lines = [l for l in (s or "").strip().split("\n") if l.strip()]
    return "\n".join(lines[:n])


# ── 認証デコレーター ──────────────────────────────────────────────────────────
def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("logged_in"):
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated


# ── 認証 ─────────────────────────────────────────────────────────────────────
@app.route("/")
@login_required
def index():
    return send_from_directory(".", "index.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        if request.form.get("password") == APP_PASSWORD:
            session["logged_in"] = True
            return redirect(url_for("index"))
        error = "パスワードが正しくありません"
    return render_template("login.html", error=error)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# ── 核心主張 (Beliefs) ────────────────────────────────────────────────────────
@app.route("/beliefs")
@login_required
def beliefs_list():
    conn = get_conn()
    beliefs = conn.execute(
        "SELECT * FROM beliefs ORDER BY created_at ASC"
    ).fetchall()
    conn.close()
    return render_template("beliefs.html", beliefs=beliefs)


@app.route("/beliefs/add", methods=["POST"])
@login_required
def beliefs_add():
    title = request.form.get("title", "").strip()
    content = request.form.get("content", "").strip()
    if title and content:
        conn = get_conn()
        conn.execute(
            "INSERT INTO beliefs (title, content) VALUES (?, ?)", (title, content)
        )
        conn.commit()
        conn.close()
    return redirect(url_for("beliefs_list"))


@app.route("/beliefs/<int:bid>/edit", methods=["GET", "POST"])
@login_required
def beliefs_edit(bid):
    conn = get_conn()
    if request.method == "POST":
        title = request.form.get("title", "").strip()
        content = request.form.get("content", "").strip()
        conn.execute(
            "UPDATE beliefs SET title=?, content=? WHERE id=?",
            (title, content, bid)
        )
        conn.commit()
        conn.close()
        return redirect(url_for("beliefs_list"))
    belief = conn.execute("SELECT * FROM beliefs WHERE id=?", (bid,)).fetchone()
    conn.close()
    if not belief:
        return redirect(url_for("beliefs_list"))
    return render_template("belief_edit.html", belief=belief)


@app.route("/beliefs/<int:bid>/delete", methods=["POST"])
@login_required
def beliefs_delete(bid):
    conn = get_conn()
    conn.execute("DELETE FROM beliefs WHERE id=?", (bid,))
    conn.commit()
    conn.close()
    return redirect(url_for("beliefs_list"))


# ── 記事管理 (Articles) ───────────────────────────────────────────────────────
@app.route("/articles")
@login_required
def articles_list():
    tab = request.args.get("tab", "schedule")
    conn = get_conn()
    if tab == "posted":
        articles = conn.execute(
            "SELECT * FROM articles WHERE status='posted' ORDER BY scheduled_date DESC"
        ).fetchall()
    elif tab == "all":
        articles = conn.execute(
            "SELECT * FROM articles ORDER BY created_at DESC"
        ).fetchall()
    else:  # schedule (default)
        articles = conn.execute(
            "SELECT * FROM articles WHERE status != 'posted' "
            "ORDER BY CASE WHEN scheduled_date='' THEN 1 ELSE 0 END, scheduled_date ASC"
        ).fetchall()
    conn.close()
    return render_template("articles.html", articles=articles, tab=tab)


@app.route("/articles/new", methods=["GET", "POST"])
@login_required
def articles_new():
    if request.method == "POST":
        title = request.form.get("title", "").strip()
        content = request.form.get("content", "").strip()
        status = request.form.get("status", "draft")
        scheduled_date = request.form.get("scheduled_date", "")
        conn = get_conn()
        conn.execute(
            "INSERT INTO articles (title, content, status, scheduled_date) VALUES (?, ?, ?, ?)",
            (title, content, status, scheduled_date)
        )
        conn.commit()
        conn.close()
        return redirect(url_for("articles_list"))
    conn = get_conn()
    beliefs = conn.execute("SELECT id, title FROM beliefs ORDER BY id ASC").fetchall()
    conn.close()
    return render_template("article_form.html", article=None, beliefs=beliefs)


@app.route("/articles/<int:aid>/edit", methods=["GET", "POST"])
@login_required
def articles_edit(aid):
    conn = get_conn()
    if request.method == "POST":
        title = request.form.get("title", "").strip()
        content = request.form.get("content", "").strip()
        status = request.form.get("status", "draft")
        scheduled_date = request.form.get("scheduled_date", "")
        conn.execute(
            "UPDATE articles SET title=?, content=?, status=?, scheduled_date=?, "
            "updated_at=datetime('now') WHERE id=?",
            (title, content, status, scheduled_date, aid)
        )
        conn.commit()
        conn.close()
        return redirect(url_for("articles_list"))
    article = conn.execute("SELECT * FROM articles WHERE id=?", (aid,)).fetchone()
    beliefs = conn.execute("SELECT id, title FROM beliefs ORDER BY id ASC").fetchall()
    conn.close()
    if not article:
        return redirect(url_for("articles_list"))
    return render_template("article_form.html", article=article, beliefs=beliefs)


@app.route("/articles/<int:aid>/delete", methods=["POST"])
@login_required
def articles_delete(aid):
    conn = get_conn()
    conn.execute("DELETE FROM articles WHERE id=?", (aid,))
    conn.commit()
    conn.close()
    return redirect(url_for("articles_list"))


@app.route("/articles/<int:aid>/mark-posted", methods=["POST"])
@login_required
def articles_mark_posted(aid):
    today = datetime.now().strftime("%Y-%m-%d")
    conn = get_conn()
    conn.execute(
        "UPDATE articles SET status='posted', scheduled_date=? WHERE id=?",
        (today, aid)
    )
    conn.commit()
    conn.close()
    return redirect(url_for("articles_list"))


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8765))
    app.run(host="0.0.0.0", port=port)
