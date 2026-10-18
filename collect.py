#!/usr/bin/env python3
"""GitHub の PR とそのコメントを収集して JSONL と Markdown に保存する。

出力:
    <out>/prs.jsonl      PR 1 件 = 1 行
    <out>/comments.jsonl コメント 1 件 = 1 行 (reviews / インライン / 会話の 3 種類)
    <out>/prs.md         PR ごとにコメントを時系列で並べた、人が読む用のまとめ

使い方:
    export GITHUB_TOKEN=ghp_...
    python collect.py --repo owner/name --since 2025-04-01
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

import requests

API = "https://api.github.com"
SESSION = requests.Session()


def headers() -> dict[str, str]:
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        sys.exit("GITHUB_TOKEN が未設定です。repo スコープの PAT を設定してください。")
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def paginate(url: str, params: dict | None = None):
    """Link ヘッダを辿って全ページを返す。レート制限が尽きたらリセットまで待つ。"""
    params = dict(params or {}, per_page=100)
    while url:
        r = SESSION.get(url, headers=headers(), params=params, timeout=30)

        remaining = int(r.headers.get("X-RateLimit-Remaining", "1"))
        if remaining == 0:
            reset = int(r.headers.get("X-RateLimit-Reset", time.time() + 60))
            wait = max(reset - time.time(), 0) + 1
            print(f"  レート制限到達。{wait:.0f} 秒待機", file=sys.stderr)
            time.sleep(wait)

        r.raise_for_status()
        yield from r.json()
        url = r.links.get("next", {}).get("url")
        params = None  # next の URL にクエリが含まれているので渡さない


def is_bot(user: dict | None) -> bool:
    if not user:
        return True
    return user.get("type") == "Bot" or user.get("login", "").endswith("[bot]")


def collect_pr_comments(owner: str, repo: str, number: int):
    """1 つの PR に紐づく 3 種類のコメントを正規化して返す。

    GitHub はこれらを別エンドポイントに分けている:
      - reviews        : Approve / Request changes とそのサマリ本文
      - pulls/comments : diff の行に付くインラインコメント
      - issues/comments: 会話タブの通常コメント
    3 つとも取らないとレビューの実態は見えない。
    """
    base = f"{API}/repos/{owner}/{repo}"

    for rv in paginate(f"{base}/pulls/{number}/reviews"):
        if not (rv.get("body") or "").strip():
            continue  # 本文なしの Approve などは保存しない
        yield {
            "kind": "review_body",
            "comment_id": rv["id"],
            "author": (rv.get("user") or {}).get("login"),
            "author_is_bot": is_bot(rv.get("user")),
            "created_at": rv.get("submitted_at"),
            "body": rv["body"],
            "review_state": rv.get("state"),
            "path": None,
            "line": None,
            "in_reply_to_id": None,
        }

    for c in paginate(f"{base}/pulls/{number}/comments"):
        yield {
            "kind": "review_comment",
            "comment_id": c["id"],
            "author": (c.get("user") or {}).get("login"),
            "author_is_bot": is_bot(c.get("user")),
            "created_at": c["created_at"],
            "body": c["body"],
            "review_state": None,
            "path": c.get("path"),
            "line": c.get("line") or c.get("original_line"),
            "in_reply_to_id": c.get("in_reply_to_id"),
        }

    for c in paginate(f"{base}/issues/{number}/comments"):
        yield {
            "kind": "issue_comment",
            "comment_id": c["id"],
            "author": (c.get("user") or {}).get("login"),
            "author_is_bot": is_bot(c.get("user")),
            "created_at": c["created_at"],
            "body": c["body"],
            "review_state": None,
            "path": None,
            "line": None,
            "in_reply_to_id": None,
        }


KIND_LABEL = {
    "review_body": "レビュー",
    "review_comment": "インライン",
    "issue_comment": "会話",
}


MD_HEADER = "| PR | PR 作成者 | コメント送信者 | 種類 | 対象ファイル | コメント内容 | 日時 |\n"
MD_HEADER += "|---|---|---|---|---|---|---|\n"


def md_cell(text: str) -> str:
    """表のセルに入れられるよう、改行と | をエスケープする。"""
    return text.strip().replace("|", "\\|").replace("\r\n", "\n").replace("\n", "<br>")


def write_markdown_pr(f, pr: dict, comments: list[dict]) -> None:
    """1 つの PR のコメントを表の行として書き出す。コメントがない PR も 1 行残す。"""
    pr_cell = f"[#{pr['pr_number']}]({pr['url']}) {md_cell(pr['title'])}"
    if not comments:
        f.write(f"| {pr_cell} | {pr['author']} | — | — | — | _コメントなし_ | — |\n")
        return
    # 種類ごとに別エンドポイントから取っているので、時系列に並べ直す
    for c in sorted(comments, key=lambda c: c["created_at"] or ""):
        kind = KIND_LABEL[c["kind"]]
        if c["review_state"]:
            kind += f" ({c['review_state']})"
        if c["path"]:
            target = f"`{c['path']}`" + (f" L{c['line']}" if c["line"] else "")
        else:
            target = "PR 全体"
        author = c["author"] + (" (bot)" if c["author_is_bot"] else "")
        f.write(
            f"| {pr_cell} | {pr['author']} | {author} | {kind} | {target} "
            f"| {md_cell(c['body'] or '')} | {c['created_at'] or ''} |\n"
        )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True, help="owner/name")
    ap.add_argument("--since", required=True, help="この日以降に更新された PR (YYYY-MM-DD)")
    ap.add_argument("--state", default="all", choices=["all", "open", "closed"])
    ap.add_argument("--limit", type=int, default=None, help="PR 数の上限 (試走用)")
    ap.add_argument("--out", default="data", help="出力ディレクトリ")
    args = ap.parse_args()

    owner, repo = args.repo.split("/")
    since = f"{args.since}T00:00:00Z"
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    prs_f = (out / "prs.jsonl").open("w", encoding="utf-8")
    com_f = (out / "comments.jsonl").open("w", encoding="utf-8")
    md_f = (out / "prs.md").open("w", encoding="utf-8")
    md_f.write(f"# {args.repo} の PR コメント ({args.since} 以降に更新)\n\n")
    md_f.write(MD_HEADER)
    n_pr = n_com = 0

    # updated 降順で辿り、since より古くなった時点で打ち切る
    listing = paginate(
        f"{API}/repos/{owner}/{repo}/pulls",
        {"state": args.state, "sort": "updated", "direction": "desc"},
    )
    for item in listing:
        if item["updated_at"] < since:
            break
        if args.limit and n_pr >= args.limit:
            break

        number = item["number"]
        # 一覧エンドポイントは additions/deletions を返さないので個別に取り直す
        detail = SESSION.get(
            f"{API}/repos/{owner}/{repo}/pulls/{number}", headers=headers(), timeout=30
        )
        detail.raise_for_status()
        d = detail.json()

        pr = {
            "pr_number": number,
            "title": d["title"],
            "author": (d.get("user") or {}).get("login"),
            "author_is_bot": is_bot(d.get("user")),
            "state": d["state"],
            "draft": d.get("draft", False),
            "created_at": d["created_at"],
            "merged_at": d.get("merged_at"),
            "closed_at": d.get("closed_at"),
            "additions": d.get("additions"),
            "deletions": d.get("deletions"),
            "changed_files": d.get("changed_files"),
            "url": d["html_url"],
        }
        prs_f.write(json.dumps(pr, ensure_ascii=False) + "\n")
        n_pr += 1

        comments = []
        for c in collect_pr_comments(owner, repo, number):
            c["pr_number"] = number
            c["pr_author"] = pr["author"]
            com_f.write(json.dumps(c, ensure_ascii=False) + "\n")
            comments.append(c)
            n_com += 1
        write_markdown_pr(md_f, pr, comments)

        print(f"#{number} {pr['title'][:50]}", file=sys.stderr)

    prs_f.close()
    com_f.close()
    md_f.close()
    print(f"\nPR {n_pr} 件 / コメント {n_com} 件を {out}/ に保存しました", file=sys.stderr)


if __name__ == "__main__":
    main()
