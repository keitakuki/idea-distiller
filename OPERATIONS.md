# Idea Distillery 運用マニュアル

## 前提

- 作業ディレクトリ: `/Users/d21605/dev/lab/idea-distillery`
- Obsidian Vault: `/Users/d21605/dentsuDropbox Dropbox/九鬼慧太/11_Obsidian/ideaDistillery`
- LLMモデル: Claude Haiku 4.5（約$0.016/件）
- スクレイパー: Love the Work（カンヌライオンズ専用）

---

## フルパイプライン（クリーンビルド）

既存データを削除して最初から全件構築する手順。

### Step 0: クリーンアップ

```bash
rm -f "/Users/d21605/dentsuDropbox Dropbox/九鬼慧太/11_Obsidian/ideaDistillery"/inbox/*.md
rm -f "/Users/d21605/dentsuDropbox Dropbox/九鬼慧太/11_Obsidian/ideaDistillery"/campaigns/*.md
rm -f "/Users/d21605/dentsuDropbox Dropbox/九鬼慧太/11_Obsidian/ideaDistillery"/techniques/*.md
rm -f "/Users/d21605/dentsuDropbox Dropbox/九鬼慧太/11_Obsidian/ideaDistillery"/technologies/*.md
rm -f "/Users/d21605/dentsuDropbox Dropbox/九鬼慧太/11_Obsidian/ideaDistillery"/themes/*.md
rm -f "/Users/d21605/dentsuDropbox Dropbox/九鬼慧太/11_Obsidian/ideaDistillery"/festivals/*.md
rm -f "/Users/d21605/dentsuDropbox Dropbox/九鬼慧太/11_Obsidian/ideaDistillery"/_Index.md
```

- `attachments/`（画像）は消してもOK。スクレイプ時に再ダウンロードされる
- `_tags.yaml`（タグマスター）は残す推奨。LLMのタグ一貫性に使われる。完全リセットなら削除可

### Step 1: ログインセッション確認

```bash
cd /Users/d21605/dev/lab/idea-distillery && python -m src.scraper.setup check
```

期限切れの場合：

```bash
cd /Users/d21605/dev/lab/idea-distillery && python -m src.scraper.setup login
```

### Step 2: フルスクレイプ

```bash
cd /Users/d21605/dev/lab/idea-distillery && python -m src.scraper.cannes 2025
```

- 全ページ自動。1ページ≒24件、2-3秒/件
- `inbox/` にrawノート + `data/raw/cannes2025/` にJSONバックアップ + `attachments/` に画像
- 中断しても再実行で途中から再開（既存slugスキップ）

### Step 3: LLM処理

```bash
cd /Users/d21605/dev/lab/idea-distillery && python -m src.llm.processor --vault "/Users/d21605/dentsuDropbox Dropbox/九鬼慧太/11_Obsidian/ideaDistillery"
```

- `inbox/` の status:raw を順次処理 → `campaigns/` に構造化ノートを生成
- 処理済みはinboxの status が `processed` に更新される
- `_tags.yaml` に新規タグを自動追記

### Step 4: インデックス生成

```bash
cd /Users/d21605/dev/lab/idea-distillery && python -m src.obsidian.index "/Users/d21605/dentsuDropbox Dropbox/九鬼慧太/11_Obsidian/ideaDistillery"
```

生成物:
- `_Index.md` — マスターインデックス（トップテクニック/テーマ一覧）
- `festivals/Cannes Lions 2025.md` — 賞レベル別キャンペーン一覧
- `techniques/*.md` — テクニックMOC
- `technologies/*.md` — テクノロジーMOC
- `themes/*.md` — テーマMOC

---

## 部分更新（追加スクレイプ）

新しいキャンペーンを追加する場合。既存データは削除不要。

```bash
# 1. スクレイプ（既存slugはスキップされる）
cd /Users/d21605/dev/lab/idea-distillery && python -m src.scraper.cannes 2025

# 2. LLM処理（status:raw のみ処理）
cd /Users/d21605/dev/lab/idea-distillery && python -m src.llm.processor --vault "/Users/d21605/dentsuDropbox Dropbox/九鬼慧太/11_Obsidian/ideaDistillery"

# 3. インデックス再生成
cd /Users/d21605/dev/lab/idea-distillery && python -m src.obsidian.index "/Users/d21605/dentsuDropbox Dropbox/九鬼慧太/11_Obsidian/ideaDistillery"
```

---

## 手動キャンペーン追加

スクレイパーを使わず手動で追加する場合。

1. `inbox/` に以下のMarkdownを作成:

```yaml
---
title: "キャンペーン名"
status: raw
source: manual
---

# キャンペーン名

[本文をコピペ。構造は自由。LLMが構造化してくれる]
```

2. Step 3（LLM処理）+ Step 4（インデックス生成）を実行

---

## トラブルシューティング

| 症状 | 対処 |
|---|---|
| スクレイプで0件 | `python -m src.scraper.setup check` でログイン確認 |
| LLM処理が0件 | inboxに `status: raw` のファイルがあるか確認 |
| 画像が表示されない | `attachments/` にファイルがあるか確認。Obsidianの添付ファイルフォルダ設定を確認 |
| タグが重複 | `_tags.yaml` を手動編集して統合後、campaigns/ のfrontmatterも修正 |
| LLM処理が途中で止まった | 再実行すればOK。処理済み（campaigns/に同slugあり）はスキップされる |

---

## 設定ファイル

| ファイル | 内容 |
|---|---|
| `config.yaml` | LLMモデル（haiku/sonnet）、スクレイパー設定 |
| `.env` | APIキー、認証情報、Vaultパス |
| `prompts/summarize.yaml` | LLMプロンプトテンプレート |
| `_tags.yaml`（Vault内） | タグマスターリスト（LLMが参照） |
