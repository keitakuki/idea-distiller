# Idea Distillery

受賞広告キャンペーンをスクレイピング→LLM分析→Obsidianノートとして構造化するツール。

## 開発原則

**コンテキスト消失前提の設計**: このツールは実装者・運用者・保守者のコンテキストが完全に失われることを前提とする。すべての変更において以下を確認すること:

1. **CLAUDE.md（本ファイル）が最新か** — コマンドの引数変更、ディレクトリ構造の変更、新規スクリプト追加は即座にここに反映する
2. **ワークフローが自己完結しているか** — 年次パイプライン手順だけ読めば、前提知識なしで全工程を実行できること
3. **復元可能性** — アーカイブ、バックアップには復元手順を同梱する（例: `RESTORE.md`）
4. **暗黙知を作らない** — 「なぜそうなっているか」の判断理由もコード内コメントまたは本ファイルに残す

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

scripts/
├── vault_status.py            # Vault状態のジョブ別表示
├── migrate_to_subfolders.py   # フラット→サブフォルダ移行（一回限り）
├── archive_job.py             # ジョブ単位のtar.gzアーカイブ作成
├── migrate_methods.py         # メソッド統合スクリプト
└── test_prompt.py             # LLMプロンプトのテスト

data/
└── raw/
    └── {job_id}/              # スクレイプ時のJSONバックアップ
        ├── {slug}.json        # キャンペーンごとのJSON
        └── images/            # ダウンロード画像（大容量）

archives/
└── {job_id}.tar.gz            # ジョブ単位のアーカイブ（実行時に生成）
    # 内部構造:
    #   RESTORE.md               — 復元手順（展開先パス付き）
    #   vault/inbox/{job_id}/    — → $VAULT/inbox/{job_id}/ に復元
    #   vault/campaigns/{job_id}/ — → $VAULT/campaigns/{job_id}/ に復元
    #   project/data/raw/{job_id}/ — → $PROJECT/data/raw/{job_id}/ に復元
    # ※ images/ は容量のため除外（保管方針は要検討）
```

## Obsidian Vault構成

```
vault/
├── inbox/
│   ├── cannes2025/        ← job_id別サブフォルダ
│   │   ├── slug-a.md
│   │   └── slug-b.md
│   └── cannes2024/
│       └── slug-c.md
├── campaigns/
│   ├── cannes2025/
│   │   └── Title A.md
│   └── cannes2024/
│       └── Title C.md
├── methods/               ← フラット維持（全年横断）
├── festivals/             ← フラット維持
├── attachments/           ← フラット維持（共有）
├── _Index.md              # マスターインデックス
└── _tags.yaml             # タグマスターリスト（methods dict + tags list の2軸）
```

Obsidian の wikilink は `[[ファイル名]]` でフォルダ構造に依存しないため、リンクはサブフォルダ内でもそのまま機能する。

## データフロー

```
[ソース] → vault/inbox/{job_id}/{slug}.md (status: raw)
         → LLM処理 → vault/campaigns/{job_id}/{Title}.md (status: processed)
         → インデックス生成 → methods/, festivals/, _Index.md

※ data/raw/ にもJSON保存（バックアップ）
※ 全コマンドは --job <job_id> でジョブ単位にフィルタ可能
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

特定ジョブを最初から再構築する手順。

#### Step 0: ジョブ単位のクリーンアップ

```bash
# 特定ジョブのみ削除（他年のデータは安全）
rm -rf "$VAULT"/inbox/cannes2025
rm -rf "$VAULT"/campaigns/cannes2025

# インデックス（全年横断なので再生成で対応）
rm -f "$VAULT"/methods/*.md
rm -f "$VAULT"/festivals/*.md
rm -f "$VAULT"/_Index.md

# data/raw/ も必要なら削除
rm -rf data/raw/cannes2025
```

※ `attachments/` の画像と `_tags.yaml` は残してOK。

#### Step 1: スクレイピング

Love the Workからデータ取得 → `inbox/{job_id}/` にraw MD + `data/raw/` にJSON + `attachments/` に画像。

```bash
cd /Users/d21605/dev/lab/idea-distillery

# ログインセッション確認（期限切れなら再ログイン）
python -m src.scraper.setup check
python -m src.scraper.setup login   # 必要な場合

# フルスクレイプ（全ページ）
python -m src.scraper.cannes 2025
#                             ^^^^
#                             年（必須）。job_idは自動で "cannes2025" になる
#                             inbox/cannes2025/ にMD、data/raw/cannes2025/ にJSON

# ページ制限付き（テスト用。3ページ＝約72件）
python -m src.scraper.cannes 2025 cannes2025 'cannes lions' 3
```

- 1ページ≒24件、2-3秒/件
- 全ページ数は実行時にログ出力される
- 中断しても再開可能（既存slugはスキップ）

#### Step 2: LLM 処理

`inbox/{job_id}/` の status:raw ノートをLLM分析 → `campaigns/{job_id}/` に構造化ノートを生成。

```bash
python -m src.llm.processor --vault "$VAULT" --job cannes2025
```

- `--job` を指定すると対象ジョブのみ処理（推奨）
- `--job` なしで全ジョブの未処理ノートを一括処理も可
- モデル: Claude Haiku 4.5（`config.yaml` で設定）
- コスト: 約 $0.016/件

#### Step 2.5: アイデアの作り方 抽出（オプション）

```bash
# テスト（5件）
python -m src.llm.idea_formula --vault "$VAULT" --job cannes2025 --limit 5

# 全件
python -m src.llm.idea_formula --vault "$VAULT" --job cannes2025
```

#### Step 2.7: 和訳（オプション）

```bash
# テスト（5件）
python -m src.llm.translator --vault "$VAULT" --job cannes2025 --limit 5

# 全件
python -m src.llm.translator --vault "$VAULT" --job cannes2025
```

#### Step 3: インデックス生成

`campaigns/` 全サブフォルダのfrontmatterから各種MOC/インデックスを再生成。

```bash
python -m src.obsidian.index "$VAULT"
```

生成物:
- `_Index.md` — マスターインデックス（メソッド一覧）
- `festivals/Cannes Lions 2025.md` — 賞レベル別キャンペーン一覧
- `methods/*.md` — メソッドMOC（使用キャンペーン一覧）

### 部分更新（追加スクレイプ）

```bash
# 1. スクレイプ（既存slugはスキップされる）
python -m src.scraper.cannes 2025

# 2. LLM処理（対象ジョブのみ）
python -m src.llm.processor --vault "$VAULT" --job cannes2025

# 3. インデックス再生成（全年横断）
python -m src.obsidian.index "$VAULT"
```

### 手動キャンペーン追加

スクレイパーを使わず、手動でキャンペーンを追加する場合。

1. `$VAULT/inbox/{job_id}/` に以下のMarkdownを作成:

```yaml
---
title: "キャンペーン名"
festival: "Cannes Lions"
year: 2025
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
| LLMがタイトルだけから捏造 | `healthcheck --fix` でゴースト検出→inbox を retry に戻し偽 campaign を削除→リトライ |
| スクレイプ成功だが中身が空 | 品質ゲートにより自動で `status: retry` に設定される（raw にならない） |

## コマンドリファレンス

```bash
# スクレイピング（→ inbox/{job_id}/ に書き込み）
python -m src.scraper.cannes <year> [job_id] [festival] [max_pages]
python -m src.scraper.cannes --url '<library_url>' [job_id]
python -m src.scraper.cannes --retry [job_id]                    # status:retry を再スクレイプ

# LLM処理（--job でジョブ単位にフィルタ）
python -m src.llm.processor --vault <vault_path> [--job <job_id>]
python -m src.llm.processor <raw_dir>              # レガシーJSON経由

# アイデアの作り方 抽出
python -m src.llm.idea_formula --vault <vault_path> [--job <job_id>] [--limit N]

# ヘルスチェック（スクレイプ失敗の分類・修復・ゴースト検出）
# 分類: ok / parser_failure / ghost / paywall / already_processed / already_retry
# ghost = status:processed だがコンテンツが空（LLM捏造の疑い）
python -m src.scraper.healthcheck <vault_path> [--job <job_id>] [--fix]

# 和訳（英文ソース → 日本語訳）
python -m src.llm.translator --vault <vault_path> [--job <job_id>] [--limit N]

# インデックス（全年横断、--job なし）
python -m src.obsidian.index <vault_path>

# Vault 状態確認（ジョブ別表示）
python scripts/vault_status.py

# マイグレーション（フラット → サブフォルダ）
python scripts/migrate_to_subfolders.py              # dry-run
python scripts/migrate_to_subfolders.py --execute    # 実行

# アーカイブ（読み取り専用バックアップ、images除外）
python scripts/archive_job.py <job_id>
# → archives/{job_id}.tar.gz
#   vault/inbox/ + vault/campaigns/ + project/data/raw/ JSON
#   RESTORE.md 同梱（復元コマンド付き）

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

毎年のフェスティバル処理はこの順番で実行する。`JOB=cannes<year>` として使用。

```bash
JOB=cannes2025

# Step 1: スクレイプ
python -m src.scraper.cannes <year>

# Step 2: LLM処理
python -m src.llm.processor --vault "$VAULT" --job $JOB

# Step 2.5: アイデアの作り方
python -m src.llm.idea_formula --vault "$VAULT" --job $JOB

# Step 2.7: 和訳
python -m src.llm.translator --vault "$VAULT" --job $JOB

# Step 3: インデックス（全年横断）
python -m src.obsidian.index "$VAULT"

# Step 4: ヘルスチェック
python -m src.scraper.healthcheck "$VAULT" --job $JOB --fix

# Step 5: リトライ（parser_failure があれば）
python -m src.scraper.cannes --retry $JOB

# Step 6: リトライ後の再処理
python -m src.llm.translator --vault "$VAULT" --job $JOB
python -m src.scraper.healthcheck "$VAULT" --job $JOB --fix

# Step 7: インデックス再生成
python -m src.obsidian.index "$VAULT"

# Step 8: アーカイブ（オプション、完了後のバックアップ）
python scripts/archive_job.py $JOB
```

### 処理状態の確認

```bash
# ジョブ別の全体ステータス（推奨）
python scripts/vault_status.py

# 個別確認
python -m src.scraper.healthcheck "$VAULT" --job cannes2025
```

### 失敗リカバリ

1. `python -m src.scraper.healthcheck "$VAULT" --job $JOB` で分類確認
2. `--fix` で parser_failure / ghost → `status: retry` に変更（ghost は偽 campaign も削除）
3. `python -m src.scraper.cannes --retry $JOB` で再スクレイプ
4. 再度ヘルスチェック。まだ空なら paywall として記録される

**ゴーストキャンペーン**: スクレイプ時に空コンテンツが `status: raw` で保存され、LLMがタイトルのみから内容を捏造したケース。`healthcheck --fix` で inbox を retry に戻し、campaigns/ の偽ノートを削除する。現在は品質ゲートにより新規発生を防止。

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
