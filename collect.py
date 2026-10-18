#!/usr/bin/env python3
"""GitHub の PR とそのコメントを収集して JSONL に保存する。

出力:
    <out>/prs.jsonl      PR 1 件 = 1 行
    <out>/comments.jsonl コメント 1 件 = 1 行 (reviews / インライン / 会話の 3 種類)

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
            "in_reply_to_id": None,
        }


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

        for c in collect_pr_comments(owner, repo, number):
            c["pr_number"] = number
            c["pr_author"] = pr["author"]
            com_f.write(json.dumps(c, ensure_ascii=False) + "\n")
            n_com += 1

        print(f"#{number} {pr['title'][:50]}", file=sys.stderr)

    prs_f.close()
    com_f.close()
    print(f"\nPR {n_pr} 件 / コメント {n_com} 件を {out}/ に保存しました", file=sys.stderr)


if __name__ == "__main__":
    main()
