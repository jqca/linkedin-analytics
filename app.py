from flask import (Flask, send_from_directory, session, redirect,
                   url_for, request, render_template, jsonify, flash)
from functools import wraps
from datetime import datetime, timezone, timedelta
import os
import threading
import uuid
import httpx
from database import init_db, get_conn

JST = timezone(timedelta(hours=9))

# ── バックグラウンドジョブストア ──────────────────────────────────────────────
_expand_jobs: dict = {}          # {job_id: {"status": ..., "content": ..., "error": ...}}
_expand_jobs_lock = threading.Lock()

app = Flask(__name__, static_folder=".", template_folder="templates")
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-key-change-in-production")

APP_PASSWORD = os.environ.get("APP_PASSWORD", "linkedin2026")

init_db()

# ── プラットフォーム定義 ───────────────────────────────────────────────────────
PLATFORMS = {
    'note': {
        'icon': '📝', 'label': 'note',
        'tip': 'LinkedIn版（約500文字）をベースに2,000〜3,000文字へ拡張。'
               '見出し（#）・具体例・まとめを追加するとSEOに強くなります。',
    },
    'x': {
        'icon': '🐦', 'label': 'Xスレッド',
        'tip': '━━━ ごとに1ツイートに分割。各140文字以内。'
               '最初のツイートでフック、最後にLinkedIn記事へのリンクを入れましょう。',
    },
    'newsletter': {
        'icon': '📰', 'label': 'ニュースレター',
        'tip': '冒頭に今週テーマの挨拶、末尾に次回予告を追加。'
               '対話感のある文体で書くと開封率が上がります。',
    },
    'slide': {
        'icon': '📊', 'label': 'スライド',
        'tip': 'タイトル＋各━━━セクションを1スライドに変換。'
               'Canva/Google Slidesで作成後にURLを記録しましょう。',
    },
}
PLATFORM_KEYS = list(PLATFORMS.keys())


# ── AI 拡張 ───────────────────────────────────────────────────────────────────

_NOTE_PROMPT = """\
以下のLinkedIn記事（約500文字）をnote.com用の記事として2,000〜3,000文字に拡張してください。

【拡張ルール】
- 元記事の主張・メッセージを核として維持する
- note向けに読みやすい見出し構成（## サブタイトル）を入れる
- 各セクションに具体例・背景・エピソードを追加して読み応えを出す
- 冒頭に読者の課題感に共鳴する「フック」段落を追加する
- 末尾に「まとめ」と読者への「問いかけ」を追加する
- 著者（高野秀隆・量子コンピュータ×AI事業家）の文体を維持する
- 専門的だが読みやすい日本語で書く
- ━━━ などの区切り線は使わず、## 見出しで構造化する

【タイトル】
{title}

【LinkedIn記事（元文）】
{content}

note記事（## 見出しを使った2,000文字以上の完全版）:"""


def _run_expansion(job_id: str, art_content: str, art_title: str) -> None:
    """バックグラウンドスレッドで記事を生成し _expand_jobs に結果を書き込む"""
    try:
        import anthropic
        client = anthropic.Anthropic(
            api_key=os.environ.get("ANTHROPIC_API_KEY"),
            timeout=100.0,
        )
        prompt = (_NOTE_PROMPT
                  .replace("{title}", art_title)
                  .replace("{content}", art_content))
        message = client.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=4000,
            messages=[{"role": "user", "content": prompt}],
        )
        with _expand_jobs_lock:
            _expand_jobs[job_id] = {"status": "done", "content": message.content[0].text}
    except Exception as e:
        with _expand_jobs_lock:
            _expand_jobs[job_id] = {"status": "error", "error": f"{type(e).__name__}: {e}"}


# ── テンプレートフィルター ────────────────────────────────────────────────────
@app.template_filter("firstlines")
def first_lines_filter(s, n=3):
    lines = [l for l in (s or "").strip().split("\n") if l.strip()]
    return "\n".join(lines[:n])


@app.template_filter("datefmt")
def datefmt_filter(v):
    """datetime オブジェクトと文字列の両方に対応して YYYY-MM-DD を返す"""
    if v is None:
        return ""
    if hasattr(v, "strftime"):          # PostgreSQL: datetime オブジェクト
        return v.strftime("%Y-%m-%d")
    return str(v)[:10]                  # SQLite: 文字列


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


@app.route("/workflow")
@login_required
def workflow():
    return render_template("workflow.html")


# ── Buffer API ────────────────────────────────────────────────────────────────

def _buffer_get_profiles(token: str) -> list:
    """Buffer に接続されたプロファイル一覧を取得"""
    r = httpx.get(
        "https://api.bufferapp.com/1/profiles.json",
        params={"access_token": token},
        timeout=10,
    )
    r.raise_for_status()
    return r.json()


def _buffer_create_post(token: str, profile_id: str, text: str, scheduled_at=None) -> dict:
    """Buffer に投稿を作成（scheduled_at は UTC Unix タイムスタンプ）"""
    data = {"access_token": token, "profile_ids[]": profile_id, "text": text}
    if scheduled_at:
        data["scheduled_at"] = str(int(scheduled_at))
    else:
        data["now"] = "1"
    r = httpx.post(
        "https://api.bufferapp.com/1/updates/create.json",
        data=data,
        timeout=15,
    )
    r.raise_for_status()
    return r.json()


@app.route("/articles/<int:aid>/send-to-buffer", methods=["GET", "POST"])
@login_required
def articles_send_to_buffer(aid):
    """Buffer 経由で LinkedIn に投稿する確認・送信画面"""
    token = os.environ.get("BUFFER_ACCESS_TOKEN", "")

    conn = get_conn()
    article = conn.execute("SELECT * FROM articles WHERE id=?", (aid,)).fetchone()
    conn.close()
    if not article:
        return redirect(url_for("articles_list"))

    # ── POST: Buffer に送信 ──────────────────────────────────────────────────
    if request.method == "POST":
        content      = request.form.get("content", "").strip()
        dt_str       = request.form.get("scheduled_datetime", "")
        profile_id   = request.form.get("profile_id", "")

        scheduled_at = None
        if dt_str:
            try:
                dt_naive     = datetime.strptime(dt_str, "%Y-%m-%dT%H:%M")
                dt_jst       = dt_naive.replace(tzinfo=JST)
                scheduled_at = int(dt_jst.timestamp())
            except ValueError:
                pass

        try:
            result = _buffer_create_post(token, profile_id, content, scheduled_at)
            if not result.get("success"):
                raise ValueError(result.get("message", "Buffer API エラー"))
        except Exception as e:
            profiles = []
            try:
                profiles = [p for p in _buffer_get_profiles(token)
                            if p.get("service") == "linkedin"]
            except Exception:
                pass
            return render_template(
                "buffer_send.html",
                article=article, profiles=profiles,
                default_dt=dt_str, selected_content=content,
                error=f"送信エラー: {e}",
            )

        # 成功 → 投稿済みに更新
        conn = get_conn()
        conn.execute(
            "UPDATE articles SET status='posted', updated_at=datetime('now') WHERE id=?",
            (aid,)
        )
        conn.commit()
        conn.close()
        return redirect(url_for("articles_list", tab="posted"))

    # ── GET: 確認フォーム表示 ────────────────────────────────────────────────
    error = None
    profiles = []

    if not token:
        error = "BUFFER_ACCESS_TOKEN が設定されていません。Railway の環境変数に追加してください。"
    else:
        try:
            all_profiles = _buffer_get_profiles(token)
            profiles = [p for p in all_profiles if p.get("service") == "linkedin"]
            if not profiles:
                error = "Buffer に LinkedIn アカウントが接続されていません。Buffer の設定で LinkedIn を追加してください。"
        except httpx.HTTPStatusError as e:
            error = f"Buffer API 認証エラー ({e.response.status_code}): アクセストークンを確認してください。"
        except Exception as e:
            error = f"Buffer API 接続エラー: {e}"

    # デフォルト日時: scheduled_date の 9:00 JST
    default_dt = ""
    if article["scheduled_date"]:
        default_dt = f"{article['scheduled_date']}T09:00"
    elif not default_dt:
        default_dt = datetime.now(JST).strftime("%Y-%m-%dT09:00")

    return render_template(
        "buffer_send.html",
        article=article, profiles=profiles,
        default_dt=default_dt, selected_content=article["content"] or "",
        error=error,
    )


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

    if tab in PLATFORM_KEYS:
        # プラットフォームタブ: 全記事を投稿済み→予定→下書き順で表示
        articles = conn.execute(
            "SELECT * FROM articles ORDER BY "
            "CASE status WHEN 'posted' THEN 0 WHEN 'scheduled' THEN 1 ELSE 2 END, "
            "scheduled_date DESC, created_at DESC"
        ).fetchall()
    elif tab == "posted":
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

    # 各記事のバリアントマップ構築 {article_id: {platform: row}}
    variants_map = {}
    for a in articles:
        rows = conn.execute(
            "SELECT * FROM article_variants WHERE article_id=?",
            (a['id'],)
        ).fetchall()
        variants_map[a['id']] = {row['platform']: row for row in rows}

    conn.close()
    return render_template("articles.html", articles=articles, tab=tab,
                           variants_map=variants_map,
                           platforms=PLATFORMS, platform_keys=PLATFORM_KEYS)


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


# ── バリアント管理 (Article Variants) ────────────────────────────────────────

@app.route("/articles/<int:aid>/variants/<platform>", methods=["GET", "POST"])
@login_required
def variant_form(aid, platform):
    if platform not in PLATFORM_KEYS:
        return redirect(url_for("articles_list", tab="note"))
    conn = get_conn()
    article = conn.execute("SELECT * FROM articles WHERE id=?", (aid,)).fetchone()
    if not article:
        conn.close()
        return redirect(url_for("articles_list"))
    variant = conn.execute(
        "SELECT * FROM article_variants WHERE article_id=? AND platform=?",
        (aid, platform)
    ).fetchone()

    if request.method == "POST":
        title   = request.form.get("title", "").strip()
        content = request.form.get("content", "").strip()
        url     = request.form.get("url", "").strip()
        status  = request.form.get("status", "draft")
        if variant:
            conn.execute(
                "UPDATE article_variants SET title=?, content=?, url=?, status=?, "
                "updated_at=datetime('now') WHERE id=?",
                (title, content, url, status, variant['id'])
            )
        else:
            conn.execute(
                "INSERT INTO article_variants "
                "(article_id, platform, title, content, url, status) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (aid, platform, title, content, url, status)
            )
        conn.commit()
        conn.close()
        return redirect(url_for("articles_list", tab=platform))

    conn.close()
    return render_template("article_variant_form.html",
                           article=article, variant=variant,
                           platform=platform, pinfo=PLATFORMS[platform])


@app.route("/articles/<int:aid>/variants/<platform>/expand-content/start", methods=["POST"])
@login_required
def variant_expand_start(aid, platform):
    """AI拡張ジョブをバックグラウンドで開始し job_id を即時返す"""
    if platform != "note":
        return jsonify({"error": "このプラットフォームはAI拡張未対応です"}), 400
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return jsonify({"error": "ANTHROPIC_API_KEY が設定されていません"}), 500

    conn = get_conn()
    article = conn.execute("SELECT * FROM articles WHERE id=?", (aid,)).fetchone()
    conn.close()
    if not article:
        return jsonify({"error": "article not found"}), 404

    job_id = str(uuid.uuid4())
    with _expand_jobs_lock:
        _expand_jobs[job_id] = {"status": "running"}

    thread = threading.Thread(
        target=_run_expansion,
        args=(job_id, article["content"] or "", article["title"] or "（タイトルなし）"),
        daemon=True,
    )
    thread.start()
    return jsonify({"job_id": job_id})


@app.route("/articles/<int:aid>/variants/<platform>/expand-content/status/<job_id>")
@login_required
def variant_expand_status(aid, platform, job_id):
    """ジョブのステータス・結果を返す"""
    with _expand_jobs_lock:
        job = dict(_expand_jobs.get(job_id, {"status": "not_found"}))
    if job["status"] in ("done", "error"):
        with _expand_jobs_lock:
            _expand_jobs.pop(job_id, None)
    return jsonify(job)


@app.route("/articles/<int:aid>/variants/<platform>/delete", methods=["POST"])
@login_required
def variant_delete(aid, platform):
    conn = get_conn()
    conn.execute(
        "DELETE FROM article_variants WHERE article_id=? AND platform=?",
        (aid, platform)
    )
    conn.commit()
    conn.close()
    return redirect(url_for("articles_list", tab=platform))


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8765))
    app.run(host="0.0.0.0", port=port)
