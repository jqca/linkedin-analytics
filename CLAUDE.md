# LinkedIn Analytics — CLAUDE.md

## プロジェクト概要
高野秀隆さん自身のLinkedIn投稿を管理するWebアプリ。
核心主張の登録・記事の作成と保存・投稿スケジュール管理・マルチプラットフォーム転用・YouTube動画要約・書籍化進捗トラッカーが主な機能。

## 基本情報
- **本番URL**: https://linkedin-management.up.railway.app
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
├── index.html                # 分析ダッシュボード: KPIサマリー・フォロワー推移・インプレッション推移・書籍化進捗
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

-- インプレッション数記録
impressions (id, count, recorded_at DATE UNIQUE, source)
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
| ダッシュボード | / | KPIサマリー・フォロワー推移・インプレッション推移・書籍化進捗 |
| 核心主張 | /beliefs | CRUD |
| 記事管理 | /articles | スケジュール・投稿済み・プラットフォーム別タブ |
| 記事作成 | /articles/new | video_url フィールド含む |
| LinkedIn投稿 | /articles/{id}/post-to-linkedin | Make Webhook経由 |
| Zenn投稿 | /articles/{id}/post-to-zenn | GitHub API経由 |
| X投稿 | /articles/{id}/post-to-x | Tweepy（━━━区切りでスレッド分割） |
| YouTube要約 | /youtube-summary | Claude APIで要約 → 記事ドラフト |
| 運用フロー | /workflow | 投稿手順ガイド |
| フォロワーAPI | /api/followers | GET: 履歴JSON / POST: Bearer認証でupsert |
| インプレッションAPI | /api/impressions | GET: 履歴JSON / POST: Bearer認証でupsert |
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
| 第5弾（生産計画） | https://manufacturing-scheduler-production.up.railway.app/static/qscheduler_promo_90s.mp4 |
| 第6弾（品質検査） | https://quality-inspector-production.up.railway.app/static/promo.mp4 |
| 第7弾（MaintAI） | https://maint-ai-production.up.railway.app/static/demo.mp4 |

## フォロワー数・インプレッション数 自動記録
- スクレイパー: `C:/Users/User/company/development/linkedin-auto-connect/scrape_followers.py`
- Playwright+CDP（ポート9223）で `/in/me/` プロフィールページ → フォロワー数、`/feed/` → インプレッション数を取得
- `/api/followers` と `/api/impressions` に別々にPOST
- **Windowsタスクスケジューラ**: タスク名 `LinkedIn_ScrapeFollowers`、毎朝 **09:00** に自動実行
  - `WakeToRun: False`（PCを無理にスリープから起こさない・2026-05-11 変更）
  - `StartWhenAvailable: True`（PCを起こした時点で未実行分を自動でキャッチアップ）
  - `ExecutionTimeLimit: 15分`
  - 登録スクリプト: `schedule_followers.ps1`（UTF-8 BOM付き）
  - 設定変更後、`Set-ScheduledTask` または `schedule_followers.ps1` 再実行で反映
- 認証: `Authorization: Bearer linkedin2026`
- ログ: `linkedin-auto-connect/logs/followers_YYYYMMDD_HHMMSS.log`

### 過去のバッチ失敗事例（2026-05-06〜05-10）
- 原因: `WakeToRun=True` でPCをスリープから強制起床していた
- 起床直後の `AppData\Local` 可視性問題で `linkedin-auto-chrome` プロファイルフォルダが見えず、スクリプトのリトライ計5分でも失敗
- 修正: `WakeToRun=False` + `StartWhenAvailable=True` で解決（PCが起きてれば即実行、寝てたらスキップしてユーザーがPC起こした時に自動実行）
- 教訓: ノートPC・デスクトップ問わず、`WakeToRun` はAppDataアクセスを伴うバッチでは避ける

## LinkedIn ネイティブ動画アップロード（2026-05-11 追加）
- 動画URLをテキストに書き込むとリンク表示にしかならない → ネイティブ動画として投稿するには、Make経由でLinkedIn API の動画アップロード機能を呼び出す
- アプリ側（`_make_post_to_linkedin`）でWebhook payloadに `video_url` と `media_type` を追加送信
- payload:
  ```json
  {
    "content": "本文...",
    "title": "タイトル",
    "video_url": "https://maint-ai-production.up.railway.app/static/demo.mp4",
    "media_type": "video"  // 動画なしの記事は "text"
  }
  ```
- Makeシナリオ側: Router で `media_type` 値により分岐
  - **video ルート**: LinkedIn → Upload Video → Create a Share with Video（Commentary: `{{1.content}}`, Video URL: `{{1.video_url}}`）
  - **text ルート**: 既存のLinkedIn → Create a Post（Commentary: `{{1.content}}`）
- UI改善: `linkedin_post.html` に動画あり記事用の緑バナー＋プレビュー＋「本文中のURL行は削除推奨」リマインダー追加
- 動画URL要件:
  - 外部から直接ダウンロード可能（Cloudflare認証・Basic認証NG）
  - MP4 / MOV / 最大10分 / 5GB / 推奨1080p
  - Railway `/static/xxx.mp4` のような直接URLが理想

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

## Design System

### Theme
- Light mode, LinkedIn brand aesthetic

### Colors
| Token | Value | Usage |
|-------|-------|-------|
| primary | #0077B5 | LinkedIn blue |
| primary-dark | #005885 | Hover state |
| primary-light | #00a0dc | Light accent |
| primary-bg | #e8f4fb | Light blue background |
| bg | #f1f5f9 | Page background |
| surface | #ffffff | Card background |
| text | #1e293b | Primary text |
| text-muted | #64748b | Secondary text |
| border | #e2e8f0 | Borders |
| header | #0f172a | Dark header bg |
| green | #16a34a | Success |
| amber | #d97706 | Warning |
| red | #dc2626 | Error |
| purple | #8b5cf6 | Analytics accent |
| teal | #14b8a6 | Chart accent |

### Typography
- System fonts: `Hiragino Sans`, `Noto Sans JP`, `Yu Gothic`

### Spacing / Radius
- Card radius: `16px`

### Component Patterns
- Header: dark gradient with LinkedIn branding
- Cards: white bg, rounded, subtle shadow
- Charts: bar charts with gradient fills
- Insight cards: dark bg with gradient overlay
