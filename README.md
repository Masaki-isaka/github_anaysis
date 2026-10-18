# GitHub PR コメント収集

GitHub の PR とそのコメントを収集して JSONL と Markdown に保存する。

| スクリプト | 出力 |
|---|---|
| `collect.py` | `data/prs.jsonl`, `data/comments.jsonl`, `data/prs.md` |

## セットアップ

```bash
pip install -r requirements.txt
export GITHUB_TOKEN=ghp_...        # repo 読み取り権限
```

## 実行

```bash
python collect.py --repo owner/name --since 2025-04-01 --limit 20   # まず試走
```

問題なければ `--limit` を外す。

| オプション | 説明 |
|---|---|
| `--repo` | 対象リポジトリ (`owner/name`、必須) |
| `--since` | この日以降に更新された PR (`YYYY-MM-DD`、必須) |
| `--state` | `all` / `open` / `closed` (既定 `all`) |
| `--limit` | PR 数の上限 (試走用) |
| `--out` | 出力ディレクトリ (既定 `data`) |

## 出力形式

`prs.jsonl` (PR 1 件 = 1 行): `pr_number`, `title`, `author`, `author_is_bot`, `state`,
`draft`, `created_at`, `merged_at`, `closed_at`, `additions`, `deletions`, `changed_files`, `url`

`comments.jsonl` (コメント 1 件 = 1 行): `kind`, `comment_id`, `author`, `author_is_bot`,
`created_at`, `body`, `review_state`, `path`, `line`, `in_reply_to_id`, `pr_number`, `pr_author`

`kind` は `review_body` / `review_comment` / `issue_comment` のいずれか。

`prs.md` は人が読む用の表。コメント 1 件 = 1 行で、PR・PR 作成者・コメント送信者・種類・
対象ファイル (インラインなら `パス L行番号`、それ以外は「PR 全体」)・コメント内容・日時を並べる。

## GitHub API の注意点

PR のコメントは 3 つの別エンドポイントに分かれている。1 つだけ取ると実態を見誤る。

| 種類 | エンドポイント | 中身 |
|---|---|---|
| レビュー本体 | `GET /repos/{o}/{r}/pulls/{n}/reviews` | Approve / Request changes とサマリ本文 |
| インラインコメント | `GET /repos/{o}/{r}/pulls/{n}/comments` | diff の行に付くコメント |
| 会話コメント | `GET /repos/{o}/{r}/issues/{n}/comments` | 会話タブの通常コメント |

- PR 一覧 (`/pulls`) は `additions` / `deletions` を返さない。サイズが要るなら個別取得が必要。
- 認証ありで 5,000 req/hour。1 PR あたり 4 リクエストなので約 1,200 PR で上限。
  `collect.py` は `X-RateLimit-Remaining` を見てリセットまで待つ。
- リポジトリ全体のインラインコメントは `GET /repos/{o}/{r}/pulls/comments?since=...` で
  一括取得できる。PR 単位の文脈が不要ならこちらの方が速い。
- さらに減らすなら GraphQL API で PR・レビュー・コメントを 1 クエリにまとめる。
