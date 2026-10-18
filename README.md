# GitHub PR コメント収集

GitHub の PR とそのコメント (レビュー・インライン・会話) を収集して JSONL に保存する。

| スクリプト | 出力 |
|---|---|
| `collect.py` | `data/prs.jsonl`, `data/comments.jsonl` |

## 必要なもの

| 名前 | 用途 | 必須 |
|---|---|---|
| Python 3.8 以上 | `collect.py` の実行 | ○ |
| [requests](https://pypi.org/project/requests/) | GitHub API の呼び出し (標準ライブラリ以外で唯一の依存) | ○ |
| GitHub アクセストークン | API の認証。`GITHUB_TOKEN` が未設定だと `collect.py` はエラーで終了する | ○ |
| python3-venv | プロジェクト専用の仮想環境 | 推奨 |
| Git | コミット・push | 開発時 |
| [GitHub CLI (`gh`)](https://cli.github.com/) | ログイン、PR 作成、`gh auth token` でのトークン取得 | 任意 |

アクセストークンは GitHub の Settings → Developer settings → Personal access tokens で作る。
権限は対象リポジトリの読み取りだけでよい。

- Fine-grained: 対象リポジトリを選び、Pull requests を Read-only (Metadata は自動で付く)
- Classic: 非公開リポジトリなら `repo`。公開リポジトリだけならスコープなしでよい

## セットアップ

Ubuntu / WSL の場合:

```bash
sudo apt install python3 python3-venv git
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export GITHUB_TOKEN=ghp_...        # 上で作ったトークン
```

次回からは `source .venv/bin/activate` してから実行する。venv を使わない場合は以降の `python` を `python3` に読み替える。

Debian / Ubuntu ではシステムの Python に直接 `pip install` すると
`externally-managed-environment` エラーになる (PEP 668)。上のように venv を使うか、
`sudo apt install python3-requests` でシステムに入れる。

`gh` でログイン済みなら、トークンを作らずに `export GITHUB_TOKEN=$(gh auth token)` でもよい。

## 実行

```bash
python collect.py --repo owner/name --since 2025-04-01 --limit 20   # まず試走
```

問題なければ `--limit` を外す。

| オプション | 説明 |
|---|---|
| `--repo` | 対象リポジトリ (`owner/name`、必須) |
| `--since` | この日 (UTC 0 時) 以降に更新された PR (`YYYY-MM-DD`、必須) |
| `--state` | `all` / `open` / `closed` (既定 `all`) |
| `--limit` | PR 数の上限 (試走用) |
| `--out` | 出力ディレクトリ (既定 `data`) |

## 出力形式

`prs.jsonl` (PR 1 件 = 1 行): `pr_number`, `title`, `author`, `author_is_bot`, `state`,
`draft`, `created_at`, `merged_at`, `closed_at`, `additions`, `deletions`, `changed_files`, `url`

`comments.jsonl` (コメント 1 件 = 1 行): `kind`, `comment_id`, `author`, `author_is_bot`,
`created_at`, `body`, `review_state`, `path`, `in_reply_to_id`, `pr_number`, `pr_author`

`kind` は次のいずれか。

- `review_body`: レビュー本体のサマリ本文。本文のないレビュー (コメントなしの Approve など) は保存しない
- `review_comment`: diff の行に付くインラインコメント
- `issue_comment`: 会話タブの通常コメント

実行するたびに出力ファイルは上書きされる。

## GitHub API の注意点

PR のコメントは 3 つの別エンドポイントに分かれている。1 つだけ取ると実態を見誤る。

| 種類 | エンドポイント | 中身 |
|---|---|---|
| レビュー本体 | `GET /repos/{o}/{r}/pulls/{n}/reviews` | Approve / Request changes とサマリ本文 |
| インラインコメント | `GET /repos/{o}/{r}/pulls/{n}/comments` | diff の行に付くコメント |
| 会話コメント | `GET /repos/{o}/{r}/issues/{n}/comments` | 会話タブの通常コメント |

- PR 一覧 (`/pulls`) は `additions` / `deletions` を返さない。サイズが要るなら個別取得が必要。
- 認証ありで 5,000 req/hour。1 PR あたり 4 リクエスト (詳細 + コメント 3 種) と、PR 一覧 100 件ごとに 1 リクエストかかるので約 1,200 PR で上限。
  `collect.py` は `X-RateLimit-Remaining` を見てリセットまで待つ。
- リポジトリ全体のインラインコメントは `GET /repos/{o}/{r}/pulls/comments?since=...` で
  一括取得できる。PR 単位の文脈が不要ならこちらの方が速い。
- さらに減らすなら GraphQL API で PR・レビュー・コメントを 1 クエリにまとめる。
