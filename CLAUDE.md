# LinkedIn Analytics — CLAUDE.md

## プロジェクト概要
高野秀隆さん自身のLinkedIn投稿を管理するWebアプリ。
核心主張の登録・記事の作成と保存・投稿スケジュール管理・マルチプラットフォーム転用・YouTube動画要約・書籍化進捗トラッカーが主な機能。

## 基本情報
- **本番URL**: https://linkedin-analytics-production-d434.up.railway.app
- **パスワード**: linkedin2026（環境変数 APP_PASSWORD）
- **GitHub**: jqca/linkedin-analytics（ブランチ: `master`）
- **インフラ**: Railway（Webサービス + PostgreSQL）
- **Railway プロジェクトID**: `1be56691-e0bf-4219-aa1a-62cdc98184d8`
- **Railway 環境ID**: `4a197200-2f06-4de0-81af-6d8b3990e4ae`
- **Railway サービスID**: `764aa517-4949-4464-83ba-2485f974bbab`
- **Railway PostgreSQL サービスID**: `27410a64-67ec-42d5-b2c1-d51ef9def426`

## デプロイ方法
```bash
git add .
git commit -m "変更内容"
git push origin master
```

**重要**: Railway の `githubRepoDeploy` API は常に新しいサービスを作成する（既存サービスを更新しない）。
コードを変更したら必ず `git push origin master` でプッシュし、Railway ダッシュボードから手動で再デプロイするか、既存サービスに GitHub webhook を設定する。

## 起動コマンド
```
web: gunicorn app:app --workers 1 --bind 0.0.0.0:$PORT --timeout 120
```

## 技術スタック
- Flask 3.x + Jinja2
- SQLite（ローカル開発）/ PostgreSQL（Railway本番）
- psycopg2-binary（PostgreSQL接続）
- gunicorn（本番サーバー、`--timeout 120`）
- anthropic SDK（AI記事拡張・YouTube要約）
- httpx（Make Webhook送信・GitHub API）
- youtube-transcript-api（YouTube字幕取得）
- tweepy（X/Twitter API v2 スレッド投稿・OAuth 1.0a）

## ファイル構成
```
linkedin-analytics/
├── app.py                    # Flask本体・全ルート
├── database.py               # SQLite/PG両対応・init_db・_migrate
├── requirements.txt
├── Procfile                  # gunicorn --workers 1 --timeout 120
├── index.html                # 分析ダッシュボード（静的）+ 書籍化進捗カード
└── templates/
    ├── base_app.html              # ナビ共通レイアウト
    ├── login.html
    ├── beliefs.html               # 核心主張一覧
    ├── belief_edit.html           # 核心主張編集
    ├── articles.html              # 記事一覧（スケジュール/すべて/投稿済み/プラットフォーム別タブ）
    ├── article_form.html          # 記事作成・編集（video_url フィールド含む）
    ├── article_variant_form.html  # note/Zenn/X バリアント編集（AI拡張ボタン含む）
    ├── linkedin_post.html         # LinkedIn投稿確認（Make Webhook）
    ├── zenn_post.html             # Zenn投稿確認（GitHub API）
    ├── x_post.html                # X スレッド投稿確認（Tweepy）
    ├── workflow.html              # 運用フロー手順ガイド
    └── youtube_summary.html       # YouTube動画要約
```

## データベース構造
```sql
-- 核心主張
beliefs (id, title, content, created_at)

-- 記事
articles (id, title, content, status, scheduled_date, video_url, created_at, updated_at)
-- status: 'draft' | 'scheduled' | 'posted'
-- video_url: Railway静的ファイルURL（例: https://maint-ai-production.up.railway.app/static/demo.mp4）

-- プラットフォームバリアント
article_variants (id, article_id, platform, title, content, url, status, created_at, updated_at)
-- platform: 'note' | 'zenn' | 'x' | 'newsletter' | 'slide'
-- status: 'draft' | 'published'

-- フォロワー数記録
followers (id, count, recorded_at DATE UNIQUE, source)
```

## database.py の注意点
- `_PGConn` / `_PGCursor` で psycopg2 を sqlite3 互換にラップ
- `_migrate()` でカラム追加・テーブル作成（冪等）
- **PostgreSQL で `ALTER TABLE` が失敗（列が既存）した場合、`conn.rollback()` が必須**
  → しないと「current transaction is aborted」で後続SQLがすべて失敗する
- `row["column_name"]` でアクセス（sqlite3.Row / RealDictRow 共通）
- PostgreSQL は `created_at` を `datetime.datetime` で返す（SQLiteは文字列）
  → Jinja2 の `datefmt` フィルターで吸収

## 機能一覧
| 機能 | URL | 説明 |
|------|-----|------|
| ログイン | /login | セッションベース認証 |
| ダッシュボード | / | 書籍化進捗・フォロワー推移 |
| 核心主張 | /beliefs | CRUD |
| 記事管理 | /articles | スケジュール・投稿済み・プラットフォーム別タブ |
| 記事作成 | /articles/new | video_url フィールド含む |
| LinkedIn投稿 | /articles/{id}/post-to-linkedin | Make Webhook経由 |
| Zenn投稿 | /articles/{id}/post-to-zenn | GitHub API経由 |
| X投稿 | /articles/{id}/post-to-x | Tweepy（━━━区切りでスレッド分割） |
| YouTube要約 | /youtube-summary | Claude APIで要約 → 記事ドラフト |
| 運用フロー | /workflow | 投稿手順ガイド |
| フォロワーAPI | /api/followers | GET: 履歴JSON / POST: Bearer認証でupsert |
| 統計API | /api/stats | posted/scheduled/draft件数・達成率・完成予想 |

## 自動投稿の仕組み
| プラットフォーム | 方式 |
|---|---|
| LinkedIn | Make（Integromat）Custom Webhook 経由 |
| Zenn | GitHub API → `jqca/zenn-content` の `articles/` に .md をコミット → Zenn自動公開 |
| X（Twitter） | Tweepy v4（OAuth 1.0a）→ スレッド投稿。━━━ で分割して返信チェーン |
| note | 手動（APIなし・ToS上自動化不可） |

## AI拡張（バックグラウンドジョブ方式）
Railway のプロキシが30秒でHTTP接続を切断するため：
1. POST `/start` → `job_id` を即時返却（<1秒）
2. バックグラウンドスレッドで Anthropic API 呼び出し（60〜90秒）
3. クライアントが `/status/{job_id}` を3秒ごとにポーリング
4. `done` になったら結果を表示

## 動画URL（デモアプリ）
記事の `video_url` に設定することで、記事一覧・編集画面に動画プレーヤーが表示される。

| 記事 | video_url |
|------|-----------|
| 第5弾（生産計画） | https://manufacturing-scheduler-production.up.railway.app/static/demo.mp4 |
| 第6弾（品質検査） | https://quality-inspector-production.up.railway.app/static/demo.mp4 |
| 第7弾（MaintAI） | https://maint-ai-production.up.railway.app/static/demo.mp4 |

## フォロワー数自動記録
- スクレイパー: `C:/Users/User/company/development/linkedin-auto-connect/scrape_followers.py`
- Playwright+CDP（ポート9223）でLinkedInからフォロワー数取得
- タスクスケジューラ: `schedule_followers.ps1`（毎朝8:00）
- 認証: `Authorization: Bearer linkedin2026`

## 投稿スケジュール（2026-05-04時点）
| 弾 | タイトル | ステータス | 日付 |
|---|---|---|---|
| 第1弾 | 量子コンピュータ業界の人は「今」を語れない | ✅ 投稿済み | 2026-05-01（木） |
| 第2弾 | AIを高度化するのが量子コンピュータ、という考え方 | 📅 投稿予定 | 2026-05-07（水） |
| 第3弾 | 研究者目線 vs 事業家目線 | 📅 投稿予定 | 2026-05-09（金） |
| 第4弾 | 課題の大規模化は「今の課題」への没頭から始まる | 📅 投稿予定 | 2026-05-11（月） |
| 第5弾 | 生産計画・スケジューリングAIデモ紹介 | 📅 投稿予定 | 2026-05-13（水） |
| 第6弾 | 品質不良・トレーサビリティAIデモ紹介 | 📅 投稿予定 | 2026-05-15（金） |
| 第7弾 | 設備稼働率・予防保全AIデモ紹介（MaintAI） | 📅 投稿予定 | 2026-05-17（日） |

## ハマりどころ
- **PostgreSQL で ALTER TABLE が失敗した後は必ず rollback()** → しないと後続SQL全失敗
- Railway の `githubRepoDeploy` は既存サービスを更新しない（常に新規作成）
- ローカル git ブランチは `master`（`main` ではない）
- `login_required` デコレーターはルート定義より前に定義すること
- Railway のプロキシが30秒でHTTP切断 → Anthropic API呼び出しはバックグラウンドジョブ方式
- YouTube字幕の自動取得はRailwayのIPがブロックされる場合あり → 手動貼り付けタブで代替
- X投稿はMakeのTwitterモジュールが廃止 → Tweepy直接接続で解決
- X API: OAuth1.0a の4キー（Consumer Key/Secret + Access Token/Secret）がすべて必要
