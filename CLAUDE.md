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
│   ├── healthcheck.py     # スクレイプ失敗の分類・修復
│   ├── auth.py            # Playwright認証
│   └── setup.py           # 手動ログイン/ページinspect
├── llm/                   # LLM処理
│   ├── processor.py       # process_from_vault() / process_campaigns()
│   ├── idea_formula.py    # アイデアの作り方 抽出（定式）
│   ├── translator.py      # 英文ソース → 日本語訳
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
├── methods/        # メソッドMOC（Wikilink）
├── festivals/      # フェスティバル別インデックス
├── attachments/    # 画像ファイル
├── _Index.md       # マスターインデックス
└── _tags.yaml      # タグマスターリスト（methods dict + tags list の2軸）
```

## データフロー

```
[ソース] → vault/inbox/{slug}.md (status: raw)
         → LLM処理 → vault/campaigns/{slug}.md (status: processed)
         → インデックス生成 → methods/, festivals/, _Index.md

※ data/raw/ にもJSON保存（バックアップ）
```

## ノート形式

### inbox/ (raw)
- frontmatter: title, slug, brand, agency, awards, source_url, status: raw
- 本文: スクレイプした全テキストをそのまま保持

### campaigns/ (processed) - 3レベル構造
1. **概要**: 1-2文。パッと見てわかる
2. **全体像**: 背景/戦略/アイデア/結果 各1-2文
2.5. **アイデアの作り方**: 転用可能な定式（1行blockquote）
3. **詳細**: 背景/戦略/アイデア/結果 各200-400字

## タグの命名規則（2軸構成）

| 種類 | 形式 | 用途 | Obsidian |
|---|---|---|---|
| methods | Title Case英語 | クリエイティブ手法（1-2個/キャンペーン）。MOC生成 | `[[Wikilink]]` |
| tags | kebab-case英語（ネスト構造） | 検索・フィルタリング（5-10個/キャンペーン） | frontmatter |

### タグのプレフィックス
- `tech/` — テクノロジー（tech/ai, tech/ar 等）
- `industry/` — 業界・商材（industry/automotive 等）
- `theme/` — 社会テーマ（theme/accessibility 等）
- `channel/` — チャネル（channel/social-media 等）
- プレフィックスなし — その他（humor, gen-z 等）

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
rm -f "$VAULT"/methods/*.md
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

#### Step 2.5: アイデアの作り方 抽出（オプション）

`campaigns/` の処理済みノートから転用可能な定式を抽出。要約とは別のLLMパスで実行。

```bash
# テスト（5件）
python -m src.llm.idea_formula --vault "$VAULT" --limit 5

# 全件
python -m src.llm.idea_formula --vault "$VAULT"
```

- 入力: 各キャンペーンの概要+全体像セクションのみ
- 出力: `## アイデアの作り方` セクションを全体像と詳細の間に挿入
- 既にセクションがある場合はスキップ
- コスト: 約 $0.002/件（入力が短いため）

#### Step 2.7: 和訳（オプション）

`inbox/` の英文ソース（Description + Case Study）を GPT-4o-mini で日本語訳し、`campaigns/` ノートに `## 和訳` セクションとして挿入。

```bash
# テスト（5件）
python -m src.llm.translator --vault "$VAULT" --limit 5

# 全件
python -m src.llm.translator --vault "$VAULT"
```

- モデル: GPT-4o-mini（設定に依存せず固定）
- 入力: inbox ノートの Description + Case Study セクション
- 出力: `## 和訳` セクションを `## メソッド` の前に挿入
- 既に和訳がある場合はスキップ
- inbox にノートがないキャンペーンもスキップ
- コスト: 約 $0.0015/件

#### Step 3: インデックス生成

`campaigns/` のfrontmatterから各種MOC/インデックスを再生成。

```bash
python -m src.obsidian.index "$VAULT"
```

生成物:
- `_Index.md` — マスターインデックス（メソッド一覧）
- `festivals/Cannes Lions 2025.md` — 賞レベル別キャンペーン一覧
- `methods/*.md` — メソッドMOC（使用キャンペーン一覧）

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
python -m src.scraper.cannes --retry [job_id]                    # status:retry を再スクレイプ

# LLM処理
python -m src.llm.processor --vault <vault_path>
python -m src.llm.processor <raw_dir>              # レガシーJSON経由

# アイデアの作り方 抽出
python -m src.llm.idea_formula --vault <vault_path>
python -m src.llm.idea_formula --vault <vault_path> --limit 5   # テスト用

# ヘルスチェック（スクレイプ失敗の分類・修復）
python -m src.scraper.healthcheck <vault_path>
python -m src.scraper.healthcheck <vault_path> --fix             # status→retry に変更

# 和訳（英文ソース → 日本語訳）
python -m src.llm.translator --vault <vault_path>
python -m src.llm.translator --vault <vault_path> --limit 5     # テスト用

# インデックス
python -m src.obsidian.index <vault_path>

# セッション管理
python -m src.scraper.setup login
python -m src.scraper.setup check
python -m src.scraper.setup inspect <url> [--tab entries|credits]

# Web UI
python -m src.main
```

## 用語

| 用語 | 意味 | コード |
|---|---|---|
| アイデアの作り方 | 転用可能な定式（1行blockquote） | `src/llm/idea_formula.py` |
| 概要 | 1-2文の要約 | `src/llm/processor.py` |
| 全体像 | 背景/戦略/アイデア/結果 各1-2文 | 同上 |
| 詳細 | 背景/戦略/アイデア/結果 各200-400字 | 同上 |
| 和訳 | 英文ソースの日本語訳 | `src/llm/translator.py` |

## 年次パイプライン手順

毎年のフェスティバル処理はこの順番で実行する。

```bash
# Step 1: スクレイプ
python -m src.scraper.cannes <year>

# Step 2: LLM処理
python -m src.llm.processor --vault "$VAULT"

# Step 2.5: アイデアの作り方
python -m src.llm.idea_formula --vault "$VAULT"

# Step 2.7: 和訳
python -m src.llm.translator --vault "$VAULT"

# Step 3: インデックス
python -m src.obsidian.index "$VAULT"

# Step 4: ヘルスチェック
python -m src.scraper.healthcheck "$VAULT" --fix

# Step 5: リトライ（parser_failure があれば）
python -m src.scraper.cannes --retry cannes<year>

# Step 6: リトライ後の再処理
python -m src.llm.translator --vault "$VAULT"
python -m src.scraper.healthcheck "$VAULT" --fix  # 残留問題を paywall に分類

# Step 7: インデックス再生成
python -m src.obsidian.index "$VAULT"
```

### 処理状態の確認

| 確認項目 | コマンド |
|---|---|
| inbox の status 分布 | `grep -r "^status:" "$VAULT"/inbox/ \| sort \| uniq -c` |
| campaigns/ の件数 | `ls "$VAULT"/campaigns/*.md \| wc -l` |
| 和訳なしの campaigns | `grep -rL "## 和訳" "$VAULT"/campaigns/` |
| アイデアの作り方なし | `grep -rL "## アイデアの作り方" "$VAULT"/campaigns/` |
| ヘルスチェック | `python -m src.scraper.healthcheck "$VAULT"` |

### 失敗リカバリ

1. `python -m src.scraper.healthcheck "$VAULT"` で分類確認
2. `--fix` で parser_failure → `status: retry` に変更
3. `python -m src.scraper.cannes --retry cannes<year>` で再スクレイプ
4. 再度ヘルスチェック。まだ空なら paywall として記録される

## メソッド統合（手動ステップ）

年次処理後にメソッド一覧を確認し、類似メソッドを統合する。

1. `methods/` のMOCファイル一覧を確認
2. 類似メソッドを特定（例: "Brand Utility" と "Utility-Driven Marketing"）
3. `_tags.yaml` の methods dict で統合先を残し、統合元を削除
4. `campaigns/` の frontmatter で該当メソッド名を置換
5. `python -m src.obsidian.index "$VAULT"` でインデックス再生成
6. `methods/` の不要MOCファイルを削除

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
