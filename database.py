import os
import sqlite3

DATABASE_URL = os.environ.get("DATABASE_URL")
DB_PATH = os.environ.get("DB_PATH", os.path.join(os.path.dirname(__file__), "linkedin.db"))


# ── PostgreSQL 互換レイヤー ────────────────────────────────────────────────────

def _adapt_sql(sql: str) -> str:
    """SQLite 用 SQL を PostgreSQL 用に変換"""
    return (sql
            .replace("?", "%s")
            .replace("datetime('now')", "NOW()")
            .replace("CURRENT_TIMESTAMP", "NOW()"))


class _PGCursor:
    """psycopg2 カーソルを sqlite3 カーソル互換にラップ"""
    def __init__(self, cur):
        self._c = cur

    def fetchall(self):
        return self._c.fetchall()   # RealDictRow → dict-like

    def fetchone(self):
        return self._c.fetchone()   # RealDictRow or None

    @property
    def rowcount(self):
        return self._c.rowcount


class _PGConn:
    """psycopg2 connection を sqlite3 Connection 互換にラップ"""
    def __init__(self, conn):
        self._conn = conn

    def execute(self, sql: str, params=()):
        import psycopg2.extras
        cur = self._conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(_adapt_sql(sql), params)
        return _PGCursor(cur)

    def commit(self):
        self._conn.commit()

    def close(self):
        self._conn.close()


def _is_pg(conn) -> bool:
    return isinstance(conn, _PGConn)


def _migrate(conn, pg):
    """article_variants テーブルを追加（冪等）"""
    if pg:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS article_variants (
                id         SERIAL PRIMARY KEY,
                article_id INTEGER NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
                platform   TEXT NOT NULL,
                title      TEXT DEFAULT '',
                content    TEXT NOT NULL DEFAULT '',
                url        TEXT DEFAULT '',
                status     TEXT DEFAULT 'draft',
                created_at TIMESTAMP DEFAULT NOW(),
                updated_at TIMESTAMP DEFAULT NOW(),
                UNIQUE(article_id, platform)
            )
        """)
    else:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS article_variants (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                article_id INTEGER NOT NULL,
                platform   TEXT NOT NULL,
                title      TEXT DEFAULT '',
                content    TEXT NOT NULL DEFAULT '',
                url        TEXT DEFAULT '',
                status     TEXT DEFAULT 'draft',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(article_id, platform)
            )
        """)


def get_conn():
    """DB 接続を返す。DATABASE_URL があれば PostgreSQL、なければ SQLite。"""
    if DATABASE_URL:
        import psycopg2
        url = DATABASE_URL
        # Railway は postgres:// を使う場合がある
        if url.startswith("postgres://"):
            url = url.replace("postgres://", "postgresql://", 1)
        return _PGConn(psycopg2.connect(url))
    else:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        return conn


# ── DB 初期化 ─────────────────────────────────────────────────────────────────

def init_db():
    conn = get_conn()
    pg = _is_pg(conn)

    if pg:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS beliefs (
                id      SERIAL PRIMARY KEY,
                title   TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT NOW()
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS articles (
                id             SERIAL PRIMARY KEY,
                title          TEXT DEFAULT '',
                content        TEXT NOT NULL DEFAULT '',
                status         TEXT DEFAULT 'draft',
                scheduled_date TEXT DEFAULT '',
                created_at     TIMESTAMP DEFAULT NOW(),
                updated_at     TIMESTAMP DEFAULT NOW()
            )
        """)
    else:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS beliefs (
                id      INTEGER PRIMARY KEY AUTOINCREMENT,
                title   TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS articles (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                title          TEXT DEFAULT '',
                content        TEXT NOT NULL DEFAULT '',
                status         TEXT DEFAULT 'draft',
                scheduled_date TEXT DEFAULT '',
                created_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

    _migrate(conn, pg)

    row = conn.execute("SELECT COUNT(*) AS c FROM beliefs").fetchone()
    if row["c"] == 0:
        _seed(conn)

    conn.commit()
    conn.close()


# ── シードデータ ──────────────────────────────────────────────────────────────

_BELIEF_1 = """\
量子業界の人はほとんど数年先の未来しか語らない。今目の前の課題を量子技術でどうするかについて語れない。
経営者が必要としているのは「3年後の夢」ではなく「今期の意思決定に使える情報」だ。
この業界の語り口を変えることが、事業化への第一歩。"""

_BELIEF_2 = """\
AIを高度化するものが量子コンピュータであることを軸として考える。

【短期・今すぐ】量子的な指向性・アルゴリズムの考え方でAIに取り組む。量子コンピュータがなくてもできる。
【中期・3〜5年】量子コンピュータの実用性が高まる領域から既存AIと統合する。
【長期・5年以降】量子AIが産業インフラになる。そのプレイヤーになるための今の準備。

この3段階は別々の戦略ではなく、一本の連続した道だ。短中長期にわたりビジネスを仕掛けられる。"""

_BELIEF_3 = """\
「量子でAIの学習を高速化できる」——これは研究者の言葉であり、事業化の言葉ではない。

量子業界で「AIを高度化するのが量子コンピュータとわかっている」という人も、
よく話してみると研究者目線でしか語れていない。

事業家が問うべきは「目の前の課題を大規模化できる想像力があるか」だ。
量子×AIで今は解けない大規模課題が解けるようになる。
だから今から「大規模化した課題」を想像し、設計し、動き始めることが重要。
研究者は技術を育てる。事業家は、まだない市場を想像して作る。"""

_ARTICLE_2 = """\
「量子コンピュータはAIを高度化する装置である」
——この一文を軸にすると、ビジネス戦略が変わる。

先日の投稿で「量子業界は今を語れない」と書いたところ、
多くの方から反応をいただいた。

では、どう考えればいいのか。
私なりのフレームワークをお伝えしたい。

━━━━━━━━━━━━━━━━
量子コンピュータの本質的な役割は何か。

それはAIの限界を突破する技術だ。

今のAIは、膨大なデータと電力を使って
「それらしい答え」を出している。
だが複雑な最適化問題や、組み合わせ爆発が起きる問題には
古典コンピュータでは根本的な限界がある。

そこに量子コンピュータが入ってくる。

━━━━━━━━━━━━━━━━
だからこそ、私は3段階で考えている。

【短期・今すぐ】
量子インスパイアード——量子の考え方をAIに応用する。
量子コンピュータがなくてもできる。今日から着手できる。

【中期・3〜5年】
量子コンピュータの実用性が高まる領域から
既存AIと組み合わせた実装を始める。

【長期・5年以降】
量子AIが産業のインフラになる。
そのときのプレイヤーになるための、今の準備。

━━━━━━━━━━━━━━━━
この3段階は「別々の戦略」ではない。

一本の連続した道だ。

短期に動き始めた企業が中期で優位に立ち、
長期の果実を得る。

「量子は関係ない」と言っている間に、
競合はすでに短期フェーズを終えているかもしれない。

あなたの会社は今、どのフェーズを見ていますか？"""

_ARTICLE_3 = """\
「AIを高度化するのが量子コンピュータだ」
——この話をすると、必ずこう返ってくる。

「そんなことはわかっていますよ。」

だが、よく話してみると気づく。
その「わかっている」は、研究者としての理解だ。

「量子アルゴリズムでAIの学習時間が短縮できる」
「量子サンプリングで精度が上がる」

技術的には正しい。
でもそれは事業化の言葉ではない。

━━━━━━━━━━━━━━━━
私が言いたいのは、まったく別のことだ。

量子×AIが本当に変えるのは、
「解けなかった大規模課題が、解けるようになる」
という事実だ。

だから私が経営者に問うのはこれだ。

「あなたは今、目の前の課題を
大規模化して想像できますか？」

━━━━━━━━━━━━━━━━
例えば、今100社の顧客最適化をしているとする。
それが100万社になったとき、古典AIでは解けない。

今、1都市の物流を最適化しているとする。
それが国全体になったとき、組み合わせは爆発する。

量子×AIは、そのスケールの壁を壊す技術だ。

━━━━━━━━━━━━━━━━
だから今やるべきことは、
量子コンピュータを待つことではない。

「大規模化したらどんな課題が生まれるか」
を今から想像し、設計し、動き始めることだ。

研究者は技術を育てる。
事業家は、まだない市場を想像して作る。

あなたの目の前の課題、大規模化したら何になりますか？"""


def _seed(conn):
    for title, content in [
        ("量子業界は「今」を語れない", _BELIEF_1),
        ("AIを高度化するのが量子コンピュータ", _BELIEF_2),
        ("研究者目線 vs 事業家目線", _BELIEF_3),
    ]:
        conn.execute(
            "INSERT INTO beliefs (title, content) VALUES (?, ?)",
            (title, content)
        )

    for title, content, status, date in [
        ("第2弾：AIを高度化するのが量子コンピュータ、という考え方",
         _ARTICLE_2, "scheduled", "2026-05-07"),
        ("第3弾：研究者目線 vs 事業家目線",
         _ARTICLE_3, "scheduled", "2026-05-09"),
    ]:
        conn.execute(
            "INSERT INTO articles (title, content, status, scheduled_date) VALUES (?, ?, ?, ?)",
            (title, content, status, date)
        )
