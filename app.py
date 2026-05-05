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
    'zenn': {
        'icon': '📘', 'label': 'Zenn',
        'tip': 'LinkedIn版をエンジニア・技術者向けに再構成。'
               '量子×AI×事業化の専門的な内容を技術者目線で深掘りします。'
               'GitHub連携で自動投稿できます。',
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

_ZENN_PROMPT = """\
以下のLinkedIn記事（約500文字）をZenn.dev用の技術・ビジネス記事として2,000〜3,000文字に拡張してください。

【拡張ルール】
- 元記事の主張・メッセージを核として維持する
- Zennの読者層（エンジニア・技術者・スタートアップ関係者）を意識した文体にする
- ## 見出しで構造化し、技術的な深さとビジネス的な視点を両立する
- 具体的な技術例・実装イメージ・事業化ステップを追加する
- 冒頭に「この記事で分かること」を箇条書きで示す
- 末尾に「まとめ」と「次のアクション」を追加する
- 著者（高野秀隆・量子コンピュータ×AI事業家）の専門性を活かした内容にする
- Markdownのコードブロックや引用（>）を適切に使い読みやすくする
- ━━━ などの区切り線は使わず、## 見出しで構造化する

【タイトル】
{title}

【LinkedIn記事（元文）】
{content}

Zenn記事（## 見出しを使った2,000文字以上の完全版、Markdown形式）:"""


def _run_expansion(job_id: str, art_content: str, art_title: str,
                   platform: str = "note") -> None:
    """バックグラウンドスレッドで記事を生成し _expand_jobs に結果を書き込む"""
    try:
        import anthropic
        client = anthropic.Anthropic(
            api_key=os.environ.get("ANTHROPIC_API_KEY"),
            timeout=100.0,
        )
        base_prompt = _ZENN_PROMPT if platform == "zenn" else _NOTE_PROMPT
        prompt = (base_prompt
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


# ── 認証デコレーター ──────────────────────────────────────────────────────────
def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("logged_in"):
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated


# ── GitHub API → Zenn 自動投稿 ────────────────────────────────────────────────
import base64
import re
import unicodedata

def _slugify(text: str, aid: int) -> str:
    """記事タイトルから Zenn 用スラッグを生成"""
    text = unicodedata.normalize("NFKC", text)
    text = re.sub(r"[^\w\s-]", "", text, flags=re.UNICODE).strip().lower()
    text = re.sub(r"[\s_-]+", "-", text)[:40].strip("-")
    return f"article-{aid}" if not text else f"{text}-{aid}"


def _build_zenn_markdown(title: str, body: str, emoji: str = "🔬",
                         topics: list = None, published: bool = True) -> str:
    """Zenn の frontmatter 付き Markdown を生成"""
    topics = topics or ["quantum", "ai", "business"]
    topics_yaml = "[" + ", ".join(f'"{t}"' for t in topics) + "]"
    pub = "true" if published else "false"
    return (
        f"---\n"
        f'title: "{title}"\n'
        f"emoji: \"{emoji}\"\n"
        f"type: \"idea\"\n"
        f"topics: {topics_yaml}\n"
        f"published: {pub}\n"
        f"---\n\n"
        f"{body}"
    )


def _github_commit_file(token: str, repo: str, path: str,
                        content: str, message: str) -> dict:
    """GitHub API でファイルを作成・更新する"""
    url = f"https://api.github.com/repos/{repo}/contents/{path}"
    headers = {"Authorization": f"token {token}", "Accept": "application/vnd.github+json"}

    # 既存ファイルの SHA を取得（更新時に必要）
    r = httpx.get(url, headers=headers, timeout=10)
    sha = r.json().get("sha") if r.status_code == 200 else None

    payload: dict = {
        "message": message,
        "content": base64.b64encode(content.encode("utf-8")).decode(),
    }
    if sha:
        payload["sha"] = sha

    r = httpx.put(url, json=payload, headers=headers, timeout=15)
    r.raise_for_status()
    return r.json()


@app.route("/articles/<int:aid>/post-to-zenn", methods=["GET", "POST"])
@login_required
def articles_post_to_zenn(aid):
    """GitHub 経由で Zenn に自動投稿する確認・送信画面"""
    github_token = os.environ.get("GITHUB_TOKEN", "")
    zenn_repo    = os.environ.get("ZENN_GITHUB_REPO", "")

    conn = get_conn()
    article = conn.execute("SELECT * FROM articles WHERE id=?", (aid,)).fetchone()
    # Zenn バリアントがあればその本文を使う
    variant = conn.execute(
        "SELECT * FROM article_variants WHERE article_id=? AND platform='zenn'",
        (aid,)
    ).fetchone()
    conn.close()
    if not article:
        return redirect(url_for("articles_list"))

    body_default = (variant["content"] if variant and variant["content"]
                    else article["content"] or "")
    title_default = (variant["title"] if variant and variant["title"]
                     else article["title"] or "（タイトルなし）")

    # ── POST: GitHub にコミット ──────────────────────────────────────────────
    if request.method == "POST":
        title    = request.form.get("title", title_default).strip()
        body     = request.form.get("body", "").strip()
        emoji    = request.form.get("emoji", "🔬").strip() or "🔬"
        topics   = [t.strip() for t in
                    request.form.get("topics", "quantum,ai,business").split(",") if t.strip()]
        slug     = _slugify(title, aid)
        md       = _build_zenn_markdown(title, body, emoji, topics, published=True)
        path     = f"articles/{slug}.md"
        msg      = f"Add Zenn article: {title}"

        try:
            result = _github_commit_file(github_token, zenn_repo, path, md, msg)
            zenn_url = f"https://zenn.dev/articles/{slug}"
        except Exception as e:
            return render_template(
                "zenn_post.html",
                article=article, variant=variant,
                title_default=title_default, body_default=body_default,
                error=f"GitHub コミットエラー: {e}",
            )

        # バリアントの URL を更新 & 記事ステータスを投稿済みに
        conn = get_conn()
        if variant:
            conn.execute(
                "UPDATE article_variants SET url=?, status='published', "
                "updated_at=datetime('now') WHERE id=?",
                (zenn_url, variant["id"])
            )
        conn.execute(
            "UPDATE articles SET status='posted', updated_at=datetime('now') WHERE id=?",
            (aid,)
        )
        conn.commit()
        conn.close()
        return redirect(url_for("articles_list", tab="zenn"))

    # ── GET: 確認フォーム表示 ────────────────────────────────────────────────
    error = None
    if not github_token:
        error = "GITHUB_TOKEN が設定されていません。"
    elif not zenn_repo:
        error = "ZENN_GITHUB_REPO が設定されていません。"

    return render_template(
        "zenn_post.html",
        article=article, variant=variant,
        title_default=title_default, body_default=body_default,
        error=error,
    )


# ── X（Twitter）投稿 ─────────────────────────────────────────────────────────

def _post_to_x(tweets: list[str]) -> str:
    """Tweepy で X にスレッド投稿する。最初のツイートIDを返す"""
    import tweepy
    client = tweepy.Client(
        consumer_key=os.environ.get("X_CONSUMER_KEY"),
        consumer_secret=os.environ.get("X_CONSUMER_SECRET"),
        access_token=os.environ.get("X_ACCESS_TOKEN"),
        access_token_secret=os.environ.get("X_ACCESS_TOKEN_SECRET"),
    )
    reply_to = None
    first_id = None
    for tweet in tweets:
        if not tweet.strip():
            continue
        if reply_to:
            resp = client.create_tweet(text=tweet.strip(),
                                       in_reply_to_tweet_id=reply_to)
        else:
            resp = client.create_tweet(text=tweet.strip())
        tweet_id = resp.data["id"]
        if first_id is None:
            first_id = tweet_id
        reply_to = tweet_id
    return first_id


@app.route("/articles/<int:aid>/post-to-x", methods=["GET", "POST"])
@login_required
def articles_post_to_x(aid):
    """X（Twitter）にスレッド投稿する確認・送信画面"""
    missing = [k for k in ("X_CONSUMER_KEY", "X_CONSUMER_SECRET",
                            "X_ACCESS_TOKEN", "X_ACCESS_TOKEN_SECRET")
               if not os.environ.get(k)]

    conn = get_conn()
    article = conn.execute("SELECT * FROM articles WHERE id=?", (aid,)).fetchone()
    variant = conn.execute(
        "SELECT * FROM article_variants WHERE article_id=? AND platform='x'",
        (aid,)
    ).fetchone()
    conn.close()
    if not article:
        return redirect(url_for("articles_list"))

    # X バリアントの本文（なければ元記事）を ━━━ で分割
    raw = (variant["content"] if variant and variant["content"]
           else article["content"] or "")
    tweets = [t.strip() for t in raw.split("━━━") if t.strip()]
    if not tweets:
        tweets = [raw.strip()]

    if request.method == "POST":
        # フォームから個別ツイートを受け取る
        tweets_post = request.form.getlist("tweet")
        tweets_post = [t.strip() for t in tweets_post if t.strip()]

        if missing:
            return render_template("x_post.html", article=article, variant=variant,
                                   tweets=tweets_post, error=f"環境変数が未設定: {', '.join(missing)}")
        try:
            first_id = _post_to_x(tweets_post)
            x_url = f"https://x.com/{os.environ.get('X_USERNAME', 'i')}/status/{first_id}"
        except Exception as e:
            return render_template("x_post.html", article=article, variant=variant,
                                   tweets=tweets_post, error=f"投稿エラー: {e}")

        # バリアント URL を更新 & 投稿済みに
        conn = get_conn()
        if variant:
            conn.execute(
                "UPDATE article_variants SET url=?, status='published', "
                "updated_at=datetime('now') WHERE id=?",
                (x_url, variant["id"])
            )
        conn.execute(
            "UPDATE articles SET status='posted', updated_at=datetime('now') WHERE id=?",
            (aid,)
        )
        conn.commit()
        conn.close()
        return redirect(url_for("articles_list", tab="x"))

    error = f"環境変数が未設定: {', '.join(missing)}" if missing else None
    return render_template("x_post.html", article=article, variant=variant,
                           tweets=tweets, error=error)


# ── YouTube 字幕取得 & 要約 ───────────────────────────────────────────────────

_YT_SUMMARY_PROMPT = """\
以下はYouTube動画の字幕テキストです。内容を日本語で詳しく要約してください。

【動画タイトル】
{title}

【字幕テキスト】
{transcript}

以下の形式で要約してください：

## 動画の概要
（この動画のテーマと主な結論を200文字程度で）

## 主要なポイント
（重要な主張・データ・事例を箇条書きで5〜8点）

## キーワード・概念
（登場した重要な用語・フレームワークを箇条書きで）

## LinkedIn投稿アイデア
（この動画の内容をもとに、量子コンピュータ×AI×事業化の観点から、LinkedIn投稿のネタを2〜3個提案）

日本語で回答してください。"""

_yt_jobs: dict = {}
_yt_jobs_lock = threading.Lock()


def _extract_video_id(url: str):
    """YouTube URL から動画 ID を抽出"""
    patterns = [
        r'youtube\.com/watch\?v=([^&\s]+)',
        r'youtu\.be/([^?\s]+)',
        r'youtube\.com/embed/([^?\s]+)',
        r'youtube\.com/shorts/([^?\s]+)',
    ]
    for p in patterns:
        m = re.search(p, url)
        if m:
            return m.group(1)
    return None


def _run_claude_summary(job_id: str, transcript_text: str, title: str = "（YouTube動画）") -> None:
    """字幕テキストを Claude で要約する（共通処理）"""
    try:
        import anthropic
        client = anthropic.Anthropic(
            api_key=os.environ.get("ANTHROPIC_API_KEY"),
            timeout=100.0,
        )
        excerpt = transcript_text[:12000]
        prompt = (_YT_SUMMARY_PROMPT
                  .replace("{title}", title)
                  .replace("{transcript}", excerpt))
        message = client.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=3000,
            messages=[{"role": "user", "content": prompt}],
        )
        with _yt_jobs_lock:
            _yt_jobs[job_id] = {
                "status": "done",
                "summary": message.content[0].text,
                "chars": len(transcript_text),
            }
    except Exception as e:
        with _yt_jobs_lock:
            _yt_jobs[job_id] = {"status": "error", "error": f"{type(e).__name__}: {e}"}


def _run_yt_summary(job_id: str, video_id: str) -> None:
    """バックグラウンドで YouTube 字幕を取得し Claude で要約する"""
    try:
        from youtube_transcript_api import YouTubeTranscriptApi

        transcript_list = None
        last_error = ""
        for langs in (['ja', 'ja-JP'], ['en'], None):
            try:
                if langs:
                    transcript_list = YouTubeTranscriptApi.get_transcript(
                        video_id, languages=langs)
                else:
                    all_t = YouTubeTranscriptApi.list_transcripts(video_id)
                    for t in all_t:
                        transcript_list = t.fetch()
                        break
                if transcript_list:
                    break
            except Exception as e:
                last_error = str(e)

        if not transcript_list:
            # YouTube が IP ブロックしている場合のメッセージ
            if "blocked" in last_error.lower() or "ipblock" in last_error.lower():
                raise Exception(
                    "IPBLOCK: YouTubeがサーバーIPをブロックしています。"
                    "下の「字幕を手動入力」から字幕テキストを貼り付けて要約してください。"
                )
            raise Exception(
                f"NOTFOUND: 字幕が取得できませんでした（{last_error or '字幕なし'}）。"
                "下の「字幕を手動入力」から字幕テキストを貼り付けてください。"
            )

        full_text = ' '.join([t['text'] for t in transcript_list])
        _run_claude_summary(job_id, full_text)

    except Exception as e:
        err = str(e)
        # IPBLOCK / NOTFOUND フラグをフロントに渡す
        if err.startswith("IPBLOCK:") or err.startswith("NOTFOUND:"):
            with _yt_jobs_lock:
                _yt_jobs[job_id] = {"status": "manual_needed", "error": err.split(":", 1)[1].strip()}
        else:
            with _yt_jobs_lock:
                _yt_jobs[job_id] = {"status": "manual_needed",
                                    "error": f"字幕の自動取得に失敗しました（{err}）。"
                                             "下の「字幕を手動入力」から字幕テキストを貼り付けてください。"}


@app.route("/youtube-summary", methods=["GET"])
@login_required
def youtube_summary():
    return render_template("youtube_summary.html")


@app.route("/youtube-summary/start", methods=["POST"])
@login_required
def youtube_summary_start():
    data = request.get_json(silent=True) or {}
    url = data.get("url", "").strip()
    video_id = _extract_video_id(url)
    if not video_id:
        return jsonify({"error": "YouTube の URL を正しく入力してください"}), 400
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return jsonify({"error": "ANTHROPIC_API_KEY が設定されていません"}), 500

    job_id = str(uuid.uuid4())
    with _yt_jobs_lock:
        _yt_jobs[job_id] = {"status": "running"}

    threading.Thread(
        target=_run_yt_summary,
        args=(job_id, video_id),
        daemon=True,
    ).start()
    return jsonify({"job_id": job_id})


@app.route("/youtube-summary/status/<job_id>")
@login_required
def youtube_summary_status(job_id):
    with _yt_jobs_lock:
        job = dict(_yt_jobs.get(job_id, {"status": "not_found"}))
    if job["status"] in ("done", "error"):
        with _yt_jobs_lock:
            _yt_jobs.pop(job_id, None)
    return jsonify(job)


@app.route("/youtube-summary/manual-start", methods=["POST"])
@login_required
def youtube_summary_manual_start():
    """手動で貼り付けた字幕テキストを Claude で要約する"""
    data = request.get_json(silent=True) or {}
    transcript = data.get("transcript", "").strip()
    if not transcript:
        return jsonify({"error": "字幕テキストを入力してください"}), 400
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return jsonify({"error": "ANTHROPIC_API_KEY が設定されていません"}), 500

    job_id = str(uuid.uuid4())
    with _yt_jobs_lock:
        _yt_jobs[job_id] = {"status": "running"}

    threading.Thread(
        target=_run_claude_summary,
        args=(job_id, transcript),
        daemon=True,
    ).start()
    return jsonify({"job_id": job_id})


@app.route("/youtube-summary/create-article", methods=["POST"])
@login_required
def youtube_summary_create_article():
    """YouTube 要約から LinkedIn 記事ドラフトを作成"""
    title = request.form.get("title", "").strip()
    content = request.form.get("content", "").strip()
    conn = get_conn()
    conn.execute(
        "INSERT INTO articles (title, content, status) VALUES (?, ?, 'draft')",
        (title, content)
    )
    conn.commit()
    conn.close()
    return redirect(url_for("articles_list", tab="all"))


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


@app.route("/api/stats")
@login_required
def api_stats():
    """ダッシュボード用の統計情報を返す"""
    TARGET = 100
    PACE_PER_WEEK = 2
    conn = get_conn()
    posted = conn.execute(
        "SELECT COUNT(*) AS c FROM articles WHERE status='posted'"
    ).fetchone()['c']
    scheduled = conn.execute(
        "SELECT COUNT(*) AS c FROM articles WHERE status='scheduled'"
    ).fetchone()['c']
    draft = conn.execute(
        "SELECT COUNT(*) AS c FROM articles WHERE status='draft'"
    ).fetchone()['c']
    conn.close()

    remaining = max(TARGET - posted, 0)
    weeks_needed = remaining / PACE_PER_WEEK if PACE_PER_WEEK > 0 else 0

    from datetime import date, timedelta
    est_date = date.today() + timedelta(weeks=weeks_needed)

    return jsonify({
        "posted": posted,
        "scheduled": scheduled,
        "draft": draft,
        "target": TARGET,
        "remaining": remaining,
        "pct": round(posted / TARGET * 100, 1),
        "est_year": est_date.year,
        "est_month": est_date.month,
    })


# ── フォロワー数 API ──────────────────────────────────────────────────────────

@app.route("/api/followers", methods=["GET"])
def api_followers_get():
    """フォロワー数の履歴を返す（認証不要・公開）"""
    days = request.args.get("days", 90, type=int)
    conn = get_conn()
    rows = conn.execute(
        "SELECT count, recorded_at FROM followers "
        "ORDER BY recorded_at ASC"
    ).fetchall()
    conn.close()
    data = [{"count": r["count"], "date": str(r["recorded_at"])} for r in rows]
    # 最新フォロワー数・前日比も付与
    result = {"history": data}
    if data:
        result["latest"] = data[-1]["count"]
        result["latest_date"] = data[-1]["date"]
        if len(data) >= 2:
            result["diff"] = data[-1]["count"] - data[-2]["count"]
        else:
            result["diff"] = 0
    else:
        result["latest"] = None
        result["latest_date"] = None
        result["diff"] = 0
    return jsonify(result)


@app.route("/api/followers", methods=["POST"])
def api_followers_post():
    """フォロワー数を記録する（Bearer トークン認証必須）"""
    auth = request.headers.get("Authorization", "")
    if auth != f"Bearer {APP_PASSWORD}":
        return jsonify({"error": "unauthorized"}), 401

    data = request.get_json(force=True) or {}
    count = data.get("count")
    if count is None or not isinstance(count, int) or count < 0:
        return jsonify({"error": "count (integer >= 0) required"}), 400

    date_str = data.get("date") or datetime.now(JST).date().isoformat()
    source   = data.get("source", "scrape")

    conn = get_conn()
    existing = conn.execute(
        "SELECT id FROM followers WHERE recorded_at = ?", (date_str,)
    ).fetchone()
    if existing:
        conn.execute(
            "UPDATE followers SET count = ?, source = ? WHERE recorded_at = ?",
            (count, source, date_str)
        )
        action = "updated"
    else:
        conn.execute(
            "INSERT INTO followers (count, recorded_at, source) VALUES (?, ?, ?)",
            (count, date_str, source)
        )
        action = "inserted"
    conn.commit()
    conn.close()

    return jsonify({"ok": True, "count": count, "date": date_str, "action": action})


# ── Make Webhook → LinkedIn ───────────────────────────────────────────────────

def _make_post_to_linkedin(webhook_url: str, content: str, title: str = "") -> None:
    """Make の Custom Webhook 経由で LinkedIn に投稿する"""
    r = httpx.post(
        webhook_url,
        json={"content": content, "title": title},
        timeout=15,
    )
    r.raise_for_status()


@app.route("/articles/<int:aid>/post-to-linkedin", methods=["GET", "POST"])
@login_required
def articles_post_to_linkedin(aid):
    """Make 経由で LinkedIn に投稿する確認・送信画面"""
    webhook_url = os.environ.get("MAKE_WEBHOOK_URL", "")

    conn = get_conn()
    article = conn.execute("SELECT * FROM articles WHERE id=?", (aid,)).fetchone()
    conn.close()
    if not article:
        return redirect(url_for("articles_list"))

    # ── POST: Make Webhook に送信 ────────────────────────────────────────────
    if request.method == "POST":
        content = request.form.get("content", "").strip()
        title   = article["title"] or ""

        try:
            _make_post_to_linkedin(webhook_url, content, title)
        except Exception as e:
            return render_template(
                "linkedin_post.html",
                article=article,
                selected_content=content,
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
    if not webhook_url:
        error = "MAKE_WEBHOOK_URL が設定されていません。Railway の環境変数に追加してください。"

    return render_template(
        "linkedin_post.html",
        article=article,
        selected_content=article["content"] or "",
        error=error,
    )


# 旧 Buffer URL との後方互換（リダイレクト）
@app.route("/articles/<int:aid>/send-to-buffer")
@login_required
def articles_send_to_buffer(aid):
    return redirect(url_for("articles_post_to_linkedin", aid=aid))


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
        video_url = request.form.get("video_url", "").strip()
        conn = get_conn()
        conn.execute(
            "INSERT INTO articles (title, content, status, scheduled_date, video_url) VALUES (?, ?, ?, ?, ?)",
            (title, content, status, scheduled_date, video_url)
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
        video_url = request.form.get("video_url", "").strip()
        conn.execute(
            "UPDATE articles SET title=?, content=?, status=?, scheduled_date=?, "
            "video_url=?, updated_at=datetime('now') WHERE id=?",
            (title, content, status, scheduled_date, video_url, aid)
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
    if platform not in ("note", "zenn"):
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
        args=(job_id, article["content"] or "", article["title"] or "（タイトルなし）", platform),
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
