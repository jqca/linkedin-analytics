"""
LinkedIn Analytics - デモアプリ紹介記事（第5〜7弾）を登録するスクリプト
"""
import requests

BASE_URL = "https://linkedin-analytics-production-d434.up.railway.app"
PASSWORD  = "linkedin2026"

# ── 記事コンテンツ ──────────────────────────────────────────────────────────────

ARTICLES = [
    {
        "title": "第5弾：「設備が壊れてから対応する」をやめる。AI予知保全デモを公開しました",
        "scheduled_date": "2026-05-11",
        "status": "scheduled",
        "content": """\
製造業の現場コストで見落とされがちなのが「突発故障による生産停止」。

設備が壊れてから対応する「事後保全」では、
機会損失・修理コスト・品質問題が連鎖する。

OEE（設備総合効率）が80%を切る工場の多くは、
ここに根本原因がある。

━━━━━━━━━━━━━━━━
AIがこれを変える。

MaintAI は設備の稼働データをリアルタイムで解析し、
故障が起きる「前」に異常を検知するシステムです。

【主な機能】
✅ OEEダッシュボード（設備総合効率・稼働率をリアルタイム可視化）
✅ AI故障予測（30日・60日・90日の故障確率をスコア提示）
✅ 設備監視（5台の機械状態を一覧で常時モニタリング）
✅ 保全カレンダー（計画的メンテナンスをチーム全体で共有）

━━━━━━━━━━━━━━━━
「うちの工場でも使えますか？」
「導入費用はどれくらいですか？」

実際に動くデモが公開されています。
まずは触ってみてください👇

🔗 https://maint-ai-production.up.railway.app

気になる方はコメント or DM でお気軽にどうぞ。

#製造業DX #予知保全 #スマートファクトリー #AI活用 #IoT #中小企業DX #生成AI""",
    },
    {
        "title": "第6弾：目視検査の限界をAIが超える。AI品質検査デモを公開しました",
        "scheduled_date": "2026-05-13",
        "status": "scheduled",
        "content": """\
「検査員が疲れると見落としが増える」
「判定基準が人によってバラバラ」
「トレーサビリティが紙台帳で追えない」

製造業の品質管理には、構造的な課題がある。
人の注意力に頼る限り、この問題は解決しない。

━━━━━━━━━━━━━━━━
QInspect AI は、これらのペインを一気に解決します。

【主な機能】
✅ AIリアルタイム検査（不良品を0.3秒で高精度検出）
✅ 品質ダッシュボード（良品率・不良件数・トレンドを即時集計）
✅ トレーサビリティ追跡（どのロットで何が起きたか即座に特定）
✅ 改善提案（不良パターンをAIが分析し、次のアクションを提示）

━━━━━━━━━━━━━━━━
AIに任せられることをAIに任せる。
検査員はより付加価値の高い判断に集中できる。

それが製造業DXの現実的な第一歩です。

実際に動くデモはここから👇
🔗 https://quality-inspector-production.up.railway.app

「自社の検査工程に当てはめたらどうなるか」
一緒に考えたい方はDMください。

#製造業DX #品質管理 #AI検査 #トレーサビリティ #スマートファクトリー #中小企業 #生成AI""",
    },
    {
        "title": "第7弾：AIが市場を分析し、戦略的に自動売買する。kabu-trader デモを公開しました",
        "scheduled_date": "2026-05-15",
        "status": "scheduled",
        "content": """\
「毎日チャートを見る時間がない」
「感情で売買してしまう」
「バックテストをしたいが複雑すぎる」

個人投資家・中小企業の資産運用担当者が抱えるこの課題、
AIが解決できる時代になりました。

━━━━━━━━━━━━━━━━
kabu-trader は、auカブコム証券API連携で動く
AI株式自動売買システムです。

【主な機能】
✅ バックテスト（東証全銘柄で過去データ検証）
✅ パラメータ最適化（AIが最良の売買条件を自動探索）
✅ ライブトレード（ドライランモードで安全に稼働確認）
✅ ダッシュボード（損益・取引履歴をリアルタイム表示）

━━━━━━━━━━━━━━━━
「AIで投資判断を補助したい」
「感情を排除したルールベースの売買を試したい」

バックテストUIはこちらで公開中👇
🔗 https://web-production-2bd5f.up.railway.app

※ ライブトレードはセキュリティのためローカル環境のみで動作します。

投資×AI活用に関心のある方、ぜひコメントで教えてください。

#AI投資 #自動売買 #株式投資 #資産運用 #フィンテック #AI活用 #生成AI""",
    },
]

# ── HTTP セッションで記事登録 ───────────────────────────────────────────────────

def main():
    session = requests.Session()

    # 1. ログイン
    print("ログイン中...")
    r = session.post(f"{BASE_URL}/login", data={"password": PASSWORD}, allow_redirects=True)
    if r.status_code != 200:
        print(f"ログイン失敗: {r.status_code}")
        return

    # ログイン成功確認（/articles にリダイレクトされているはず）
    if "/login" in r.url:
        print("ログイン失敗: パスワードが違う可能性があります")
        return
    print(f"ログイン成功 (URL: {r.url})")

    # 2. 各記事を登録
    for i, article in enumerate(ARTICLES, start=5):
        print(f"\n記事 第{i}弾 登録中: {article['title'][:40]}...")
        r = session.post(
            f"{BASE_URL}/articles/new",
            data={
                "title": article["title"],
                "content": article["content"],
                "status": article["status"],
                "scheduled_date": article["scheduled_date"],
            },
            allow_redirects=True,
        )
        if r.status_code == 200:
            print(f"  [OK] 登録完了 (第{i}弾)")
        else:
            print(f"  [NG] 登録失敗: {r.status_code}")

    print("\n=== 全記事登録完了 ===")
    print(f"確認: {BASE_URL}/articles")

if __name__ == "__main__":
    main()
