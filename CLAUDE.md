# Idea Distillery

受賞広告キャンペーンをスクレイピング→LLM分析→Obsidianノートとして構造化するツール。

## ディレクトリ構成

```
src/
├── config.py              # 設定管理（YAML + .env）
├── main.py                # FastAPI アプリ（Web UI）
├── scraper/               # Love the Work スクレイパー（カンヌ専用）
│   ├── cannes.py          # カンヌライオンズ スクレイピング
│   ├── parser.py          # HTML解析（リスト/詳細ページ/Entriesタブ）
│   ├── models.py          # Award, CampaignEntry, ScrapedCampaign
│   ├── auth.py            # Playwright認証
│   └── setup.py           # 手動ログイン/ページinspect
├── llm/                   # LLM処理
│   ├── processor.py       # process_from_vault() / process_campaigns()
│   ├── models.py          # ProcessedCampaign（3レベル日本語要約）
│   ├── provider.py        # LLMProvider抽象インターフェース
│   ├── anthropic_provider.py
│   └── openai_provider.py
├── obsidian/              # Obsidian連携
│   ├── writer.py          # write_inbox_note(), write_campaign_note()
│   ├── reader.py          # read_inbox_notes(), read_tags_yaml()
│   └── index.py           # generate_all_indices()
├── export/                # レガシーエクスポート（旧フロー）
│   ├── markdown.py        # 旧Markdown生成
│   └── index.py           # 旧インデックス生成（JSON → MOC）
├── storage/
│   ├── database.py        # SQLite DB
│   └── files.py           # JSON I/O, 画像ダウンロード
├── jobs/
│   └── manager.py         # パイプラインオーケストレーション
└── web/
    └── routes.py          # FastAPI Web UIルート
```

## Obsidian Vault構成

```
vault/
├── inbox/          # 未処理ノート（status: raw）。唯一の入力口
├── campaigns/      # 処理済みノート（status: processed）。3レベル日本語要約
├── techniques/     # テクニックMOC（Wikilink）
├── themes/         # テーマMOC（Wikilink）
├── festivals/      # フェスティバル別インデックス
├── attachments/    # 画像ファイル
├── _Index.md       # マスターインデックス
└── _tags.yaml      # タグマスターリスト
```

## データフロー

```
[ソース] → vault/inbox/{slug}.md (status: raw)
         → LLM処理 → vault/campaigns/{slug}.md (status: processed)
         → インデックス生成 → techniques/, themes/, festivals/, _Index.md

※ data/raw/ にもJSON保存（バックアップ）
```

## ノート形式

### inbox/ (raw)
- frontmatter: title, slug, brand, agency, awards, source_url, status: raw
- 本文: スクレイプした全テキストをそのまま保持

### campaigns/ (processed) - 3レベル構造
1. **概要**: 1-2文。パッと見てわかる
2. **全体像**: 背景/戦略/アイデア/結果 各1-2文
3. **詳細**: 背景/戦略/アイデア/結果 各200-400字

## タグの命名規則

| 種類 | 形式 | 用途 | Obsidian |
|---|---|---|---|
| techniques | Title Case英語 | クリエイティブ手法 | `[[Wikilink]]` |
| themes | Title Case英語 | 概念テーマ | `[[Wikilink]]` |
| tags | kebab-case英語 | フィルタリング | frontmatter |

## 運用マニュアル

### Vault パス

```
/Users/d21605/dentsuDropbox Dropbox/九鬼慧太/11_Obsidian/ideaDistillery
```

以降 `$VAULT` と表記。

### フルパイプライン（クリーンビルド）

既存データを削除して最初から全件構築する手順。

#### Step 0: Vault クリーンアップ

```bash
# 生成物を削除（attachments/ と _tags.yaml は残す）
rm -f "$VAULT"/inbox/*.md
rm -f "$VAULT"/campaigns/*.md
rm -f "$VAULT"/techniques/*.md
rm -f "$VAULT"/technologies/*.md
rm -f "$VAULT"/themes/*.md
rm -f "$VAULT"/festivals/*.md
rm -f "$VAULT"/_Index.md

# data/raw/ も必要なら削除
rm -rf data/raw/cannes2025
```

※ `attachments/` の画像と `_tags.yaml` は残してOK。画像は同名ファイルをスキップするので再ダウンロード不要。`_tags.yaml` はLLMのタグ一貫性に使われる。完全リセットしたい場合は `_tags.yaml` も削除可。

#### Step 1: スクレイピング

Love the Workからデータ取得 → `inbox/` にraw MD + `data/raw/` にJSON + `attachments/` に画像。

```bash
cd /Users/d21605/dev/lab/idea-distillery

# ログインセッション確認（期限切れなら再ログイン）
python -m src.scraper.setup check
python -m src.scraper.setup login   # 必要な場合

# フルスクレイプ（全ページ）
python -m src.scraper.cannes 2025
#                             ^^^^
#                             年（必須）。job_idは自動で "cannes2025" になる
#                             JSONバックアップは data/raw/cannes2025/ に保存

# ページ制限付き（テスト用。3ページ＝約72件）
python -m src.scraper.cannes 2025 cannes2025 'cannes lions' 3
```

- 1ページ≒24件、2-3秒/件
- 全ページ数は実行時にログ出力される
- 中断しても `data/raw/cannes2025/` にJSON、`inbox/` にMDが残るので再開可能（既存slugはスキップ）

#### Step 2: LLM 処理

`inbox/` の status:raw ノートをLLM分析 → `campaigns/` に構造化ノートを生成。

```bash
python -m src.llm.processor --vault "$VAULT"
```

- モデル: Claude Haiku 4.5（`config.yaml` で設定）
- コスト: 約 $0.016/件
- 処理済みノートはinboxのstatusが `processed` に更新される
- 既に `campaigns/` に同じslugがあればスキップ
- `_tags.yaml` に新規タグを自動追記

#### Step 3: インデックス生成

`campaigns/` のfrontmatterから各種MOC/インデックスを再生成。

```bash
python -m src.obsidian.index "$VAULT"
```

生成物:
- `_Index.md` — マスターインデックス（トップテクニック/テーマ一覧）
- `festivals/Cannes Lions 2025.md` — 賞レベル別キャンペーン一覧
- `techniques/*.md` — テクニックMOC（使用キャンペーン一覧）
- `technologies/*.md` — テクノロジーMOC
- `themes/*.md` — テーマMOC

### 部分更新（追加スクレイプ）

新しいキャンペーンを追加する場合。既存データは削除不要。

```bash
# 1. スクレイプ（既存slugはスキップされる）
python -m src.scraper.cannes 2025 cannes2025

# 2. LLM処理（status:raw のみ処理）
python -m src.llm.processor --vault "$VAULT"

# 3. インデックス再生成（全件から再ビルド）
python -m src.obsidian.index "$VAULT"
```

### 手動キャンペーン追加

スクレイパーを使わず、手動でキャンペーンを追加する場合。

1. `$VAULT/inbox/` に以下のMarkdownを作成:

```yaml
---
title: "キャンペーン名"
status: raw
source: manual
---

# キャンペーン名

[本文をコピペ。LLMが構造化してくれる]
```

2. LLM処理 + インデックス生成を実行（Step 2, 3と同じ）

### トラブルシューティング

| 症状 | 対処 |
|---|---|
| スクレイプで0件 | `python -m src.scraper.setup check` でログイン確認 |
| LLM処理が0件 | inboxに `status: raw` のファイルがあるか確認 |
| 画像が表示されない | `attachments/` にファイルがあるか確認。Obsidianの添付ファイルフォルダ設定を確認 |
| タグが重複 | `_tags.yaml` を手動編集して統合後、campaigns/ のfrontmatterも修正 |

## コマンドリファレンス

```bash
# スクレイピング
python -m src.scraper.cannes <year> [job_id] [festival] [max_pages]
python -m src.scraper.cannes --url '<library_url>' [job_id]

# LLM処理
python -m src.llm.processor --vault <vault_path>
python -m src.llm.processor <raw_dir>              # レガシーJSON経由

# インデックス
python -m src.obsidian.index <vault_path>

# セッション管理
python -m src.scraper.setup login
python -m src.scraper.setup check
python -m src.scraper.setup inspect <url> [--tab entries|credits]

# Web UI
python -m src.main
```

## 設定

- `config.yaml` — LLMモデル、スクレイパー設定
- `.env` — APIキー、認証情報、Vault パス
- `prompts/summarize.yaml` — LLMプロンプトテンプレート

## 技術スタック

- Python 3.12+, FastAPI, Playwright, Anthropic/OpenAI, SQLite
- `python-frontmatter` でYAML frontmatter読み書き
- `pydantic-settings` で設定管理

## 開発

```bash
# テスト
pytest

# lint
ruff check src/
```
