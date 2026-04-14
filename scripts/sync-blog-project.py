#!/usr/bin/env python3
"""ブログプロジェクト整合性チェック＆修正ツール

使い方:
    python3 scripts/sync-blog-project.py                # レポートのみ（デフォルト）
    python3 scripts/sync-blog-project.py --apply         # 全修正を適用
    python3 scripts/sync-blog-project.py --safe-only     # 安全な修正のみ（URL設定等）
    python3 scripts/sync-blog-project.py --stale-weeks 2 # 停滞判定を2週間に変更
"""

import argparse
import difflib
import html
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Optional
from datetime import datetime, timedelta, timezone
from pathlib import Path

# --- 定数 ---
GH_REPO = "apc-m-koshikawa/blog"
GH_OWNER = "apc-m-koshikawa"
PROJECT_NUMBER = 2
PROJECT_ID = "PVT_kwHOD3OJhs4BUeFd"
STATUS_FIELD_ID = "PVTSSF_lAHOD3OJhs4BUeFdzhBl-HQ"
URL_FIELD_ID = "PVTF_lAHOD3OJhs4BUeFdzhBmCL0"
STATUS_OPTIONS = {
    "タネ": "792f05de",
    "下準備中": "67e24343",
    "執筆中": "5760d6d2",
    "レビュー中": "3466d620",
    "内部議論": "b8cb3021",
    "公開済み": "d8355eb6",
    "保留": "52f5b296",
}
LABEL_ALIASES = {"アイデア": "タネ", "ドラフト作成中": "執筆中"}
STATUS_LABELS = set(STATUS_OPTIONS.keys()) | set(LABEL_ALIASES.keys())
QIITA_USER = "m_koshikawa"
TECHBLOG_SEARCH = "https://techblog.ap-com.co.jp/search?q=m_koshikawa"
BLOG_DIR = Path(__file__).resolve().parent.parent


# --- データ構造 ---
@dataclass
class Article:
    title: str
    url: str
    platform: str  # "qiita" or "techblog"


@dataclass
class GithubIssue:
    number: int
    title: str
    state: str
    labels: list[str]
    updated_at: str
    url_from_comment: Optional[str] = None
    url_from_body: Optional[str] = None


@dataclass
class ProjectItem:
    item_id: str
    issue_number: int
    status: Optional[str] = None
    public_url: Optional[str] = None


@dataclass
class Finding:
    category: str
    severity: str  # "error", "warning", "info"
    issue_number: Optional[int]
    title: str
    detail: str
    fix_action: Optional[str] = None
    fix_data: dict = field(default_factory=dict)


# --- Phase 1: COLLECT ---

def fetch_qiita_articles() -> list[Article]:
    url = f"https://qiita.com/api/v2/users/{QIITA_USER}/items?per_page=100"
    req = urllib.request.Request(url, headers={"User-Agent": "sync-blog-project/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            items = json.loads(resp.read())
        return [Article(title=i["title"], url=i["url"], platform="qiita") for i in items]
    except Exception as e:
        print(f"  ! Qiita API エラー: {e}", file=sys.stderr)
        return []


def fetch_techblog_articles() -> list[Article]:
    articles = []
    page = 1
    while True:
        url = f"{TECHBLOG_SEARCH}&page={page}" if page > 1 else TECHBLOG_SEARCH
        req = urllib.request.Request(url, headers={"User-Agent": "sync-blog-project/1.0"})
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                body = resp.read().decode("utf-8")
        except Exception as e:
            print(f"  ! APC技術ブログ取得エラー (page {page}): {e}", file=sys.stderr)
            break

        # 記事リンクを抽出
        matches = re.findall(
            r'href="(https://techblog\.ap-com\.co\.jp/entry/[^"]+)"[^>]*class="entry-title-link"[^>]*>([^<]+)',
            body,
        )
        if not matches:
            # 別パターン
            matches = re.findall(
                r'<a[^>]*href="(https://techblog\.ap-com\.co\.jp/entry/\d{4}/\d{2}/\d{2}/\d+[^"]*)"[^>]*>([^<]+)</a>',
                body,
            )
        if not matches:
            break

        for url_match, title_match in matches:
            title_clean = html.unescape(title_match.strip())
            if title_clean and url_match not in [a.url for a in articles]:
                articles.append(Article(title=title_clean, url=url_match, platform="techblog"))

        # 次ページチェック
        if f"page={page + 1}" in body:
            page += 1
        else:
            break

    return articles


def fetch_github_issues() -> list[GithubIssue]:
    issues = []
    for state in ["open", "closed"]:
        result = subprocess.run(
            ["gh", "issue", "list", "--repo", GH_REPO, "--state", state,
             "--limit", "200", "--json", "number,title,state,labels,comments,updatedAt,body"],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            print(f"  ! gh issue list ({state}) エラー: {result.stderr.strip()}", file=sys.stderr)
            continue
        for item in json.loads(result.stdout):
            url_from_comment = None
            for comment in item.get("comments", []):
                m = re.search(r"公開済み:\s*(https?://\S+)", comment.get("body", ""))
                if m:
                    url_from_comment = m.group(1)
                    break
            # bodyからもURL抽出（コメントにない場合のフォールバック）
            url_from_body = None
            body = item.get("body", "") or ""
            m_body = re.search(r"公開済み:\s*(https?://\S+)", body)
            if m_body:
                url_from_body = m_body.group(1)

            issues.append(GithubIssue(
                number=item["number"],
                title=item["title"],
                state=item["state"],
                labels=[l["name"] for l in item.get("labels", [])],
                updated_at=item.get("updatedAt", ""),
                url_from_comment=url_from_comment,
                url_from_body=url_from_body,
            ))
    return sorted(issues, key=lambda x: x.number)


def _get_project_token() -> Optional[str]:
    """GH_PROJECT_TOKEN を .env またはEnvironment variablesから取得"""
    token = os.environ.get("GH_PROJECT_TOKEN", "")
    if token:
        return token
    env_path = BLOG_DIR / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            if line.strip().startswith("GH_PROJECT_TOKEN="):
                return line.strip().split("=", 1)[1].strip().strip("'\"")
    return None


def fetch_project_items() -> list[ProjectItem]:
    """Project Items を GraphQL で取得。トークンがない場合は空リストを返す。"""
    token = _get_project_token()
    if not token:
        print("  ! GH_PROJECT_TOKEN が未設定のためProject取得をスキップ", file=sys.stderr)
        return []

    items = []
    cursor = None
    while True:
        query = """
        query($cursor: String) {
          node(id: "%s") {
            ... on ProjectV2 {
              items(first: 100, after: $cursor) {
                pageInfo { hasNextPage endCursor }
                nodes {
                  id
                  content { ... on Issue { number } }
                  fieldValues(first: 10) {
                    nodes {
                      ... on ProjectV2ItemFieldSingleSelectValue {
                        name
                        field { ... on ProjectV2SingleSelectField { name } }
                      }
                      ... on ProjectV2ItemFieldTextValue {
                        text
                        field { ... on ProjectV2Field { name } }
                      }
                    }
                  }
                }
              }
            }
          }
        }""" % PROJECT_ID

        url = "https://api.github.com/graphql"
        body = json.dumps({"query": query, "variables": {"cursor": cursor}}).encode()
        req = urllib.request.Request(url, data=body, headers={
            "Authorization": f"bearer {token}",
            "Content-Type": "application/json",
        })
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                result_data = json.loads(resp.read())
        except Exception as e:
            print(f"  ! GraphQL エラー: {e}", file=sys.stderr)
            break

        if "errors" in result_data:
            print(f"  ! GraphQL エラー: {result_data['errors'][0].get('message', '')[:100]}", file=sys.stderr)
            break

        page = result_data["data"]["node"]["items"]

        for node in page["nodes"]:
            content = node.get("content")
            if not content:
                continue
            issue_num = content.get("number")
            if not issue_num:
                continue

            status = None
            public_url = None
            for fv in node.get("fieldValues", {}).get("nodes", []):
                field_info = fv.get("field", {})
                field_name = field_info.get("name", "")
                if field_name == "Status" and "name" in fv:
                    status = fv["name"]
                elif field_name == "公開URL" and "text" in fv:
                    public_url = fv["text"]

            items.append(ProjectItem(
                item_id=node["id"],
                issue_number=issue_num,
                status=status,
                public_url=public_url,
            ))

        if not page["pageInfo"]["hasNextPage"]:
            break
        cursor = page["pageInfo"]["endCursor"]

    return items


def parse_published_md() -> list[Article]:
    path = BLOG_DIR / "published.md"
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8")
    articles = []
    for m in re.finditer(r"\|\s*[^|]+\|[^|]+\|[^|]+\|\s*\[URL\]\(([^)]+)\)", text):
        url = m.group(1).strip()
        # タイトルを同じ行から抽出
        line = text[text.rfind("\n", 0, m.start()) + 1 : m.end()]
        cols = [c.strip() for c in line.split("|")]
        title = cols[3] if len(cols) > 3 else ""
        platform = "qiita" if "qiita.com" in url else "techblog"
        articles.append(Article(title=title, url=url, platform=platform))
    return articles


# --- Phase 2: RECONCILE ---

def normalize_title(title: str) -> str:
    title = re.sub(r"^\[.*?\]\s*", "", title)
    title = title.replace("〜", "—").replace("～", "—").replace("　", " ")
    return title.strip()


def resolve_label_status(labels: list[str]) -> Optional[str]:
    for label in labels:
        if label in STATUS_OPTIONS:
            return label
        if label in LABEL_ALIASES:
            return LABEL_ALIASES[label]
    return None


def check_status_consistency(issues: list[GithubIssue], project_items: list[ProjectItem]) -> list[Finding]:
    findings = []
    item_map = {pi.issue_number: pi for pi in project_items}

    for issue in issues:
        label_status = resolve_label_status(issue.labels)
        pi = item_map.get(issue.number)

        if not label_status:
            continue

        # Issueラベル ↔ Projectステータス
        if pi and pi.status and pi.status != label_status:
            findings.append(Finding(
                category="ステータス不整合",
                severity="error",
                issue_number=issue.number,
                title=issue.title,
                detail=f"ラベル={label_status}, Project={pi.status}",
                fix_action="update_project_status",
                fix_data={"item_id": pi.item_id, "status": label_status},
            ))

        # 公開済みなのにopenのまま
        if label_status == "公開済み" and issue.state == "OPEN":
            findings.append(Finding(
                category="ステータス不整合",
                severity="error",
                issue_number=issue.number,
                title=issue.title,
                detail="公開済みラベルだがIssueがopen",
                fix_action="close_issue",
                fix_data={},
            ))

        # エイリアスラベルの正規化
        for label in issue.labels:
            if label in LABEL_ALIASES:
                findings.append(Finding(
                    category="ステータス不整合",
                    severity="warning",
                    issue_number=issue.number,
                    title=issue.title,
                    detail=f"非標準ラベル「{label}」→「{LABEL_ALIASES[label]}」",
                    fix_action="normalize_label",
                    fix_data={"old": label, "new": LABEL_ALIASES[label]},
                ))

    return findings


def check_url_completeness(issues: list[GithubIssue], project_items: list[ProjectItem]) -> list[Finding]:
    findings = []
    item_map = {pi.issue_number: pi for pi in project_items}

    for issue in issues:
        if "公開済み" not in issue.labels:
            continue

        pi = item_map.get(issue.number)

        if not issue.url_from_comment:
            # bodyまたはProjectフィールドからURL候補を探す
            candidate_url = issue.url_from_body
            if not candidate_url and pi and pi.public_url:
                candidate_url = pi.public_url
            findings.append(Finding(
                category="URLコメント不足",
                severity="warning",
                issue_number=issue.number,
                title=issue.title,
                detail="公開済みだが「公開済み: URL」コメントなし",
                fix_action="add_url_comment" if candidate_url else None,
                fix_data={"url": candidate_url} if candidate_url else {},
            ))

        if pi and not pi.public_url and issue.url_from_comment:
            findings.append(Finding(
                category="公開URL未設定",
                severity="warning",
                issue_number=issue.number,
                title=issue.title,
                detail=f"ProjectのURLフィールドが空",
                fix_action="set_project_url",
                fix_data={"item_id": pi.item_id, "url": issue.url_from_comment},
            ))

    return findings


def detect_missing_issues(
    articles: list[Article], issues: list[GithubIssue]
) -> list[Finding]:
    findings = []
    issue_urls = set()
    for iss in issues:
        if iss.url_from_comment:
            issue_urls.add(iss.url_from_comment)

    issue_titles_normalized = [normalize_title(iss.title) for iss in issues]

    for article in articles:
        if article.url in issue_urls:
            continue

        # タイトル類似度チェック
        best_ratio = 0.0
        for norm_title in issue_titles_normalized:
            ratio = difflib.SequenceMatcher(None, normalize_title(article.title), norm_title).ratio()
            if ratio > best_ratio:
                best_ratio = ratio

        if best_ratio < 0.6:
            findings.append(Finding(
                category="Issue未作成",
                severity="warning",
                issue_number=None,
                title=article.title,
                detail=f"URL: {article.url} ({article.platform})",
                fix_action=None,
                fix_data={},
            ))

    return findings


def detect_duplicates(issues: list[GithubIssue]) -> list[Finding]:
    findings = []
    seen_urls = {}

    for issue in issues:
        if issue.url_from_comment:
            if issue.url_from_comment in seen_urls:
                findings.append(Finding(
                    category="重複",
                    severity="warning",
                    issue_number=issue.number,
                    title=issue.title,
                    detail=f"#{seen_urls[issue.url_from_comment]} と同一URL",
                    fix_action=None,
                    fix_data={},
                ))
            else:
                seen_urls[issue.url_from_comment] = issue.number

    # タイトル類似度
    for i, a in enumerate(issues):
        for b in issues[i + 1 :]:
            na = normalize_title(a.title)
            nb = normalize_title(b.title)
            if na and nb and difflib.SequenceMatcher(None, na, nb).ratio() > 0.8:
                if a.number != b.number:
                    key = (min(a.number, b.number), max(a.number, b.number))
                    detail = f"#{a.number} と #{b.number} のタイトルが類似 ({na[:30]}...)"
                    if not any(f.detail == detail for f in findings):
                        findings.append(Finding(
                            category="重複の可能性",
                            severity="info",
                            issue_number=a.number,
                            title=a.title,
                            detail=detail,
                            fix_action=None,
                            fix_data={},
                        ))
    return findings


def detect_stale_issues(issues: list[GithubIssue], stale_weeks: int) -> list[Finding]:
    findings = []
    now = datetime.now(timezone.utc)
    threshold = now - timedelta(weeks=stale_weeks)

    for issue in issues:
        label_status = resolve_label_status(issue.labels)
        if label_status not in ("執筆中", "下準備中"):
            continue
        if issue.state != "OPEN":
            continue
        if not issue.updated_at:
            continue

        updated = datetime.fromisoformat(issue.updated_at.replace("Z", "+00:00"))
        if updated < threshold:
            days = (now - updated).days
            findings.append(Finding(
                category="停滞",
                severity="info",
                issue_number=issue.number,
                title=issue.title,
                detail=f"{label_status}のまま {days}日間 更新なし",
                fix_action=None,
                fix_data={},
            ))
    return findings


# --- Phase 3: REPORT ---

def generate_report(findings: list[Finding], stats: dict, markdown: bool = False) -> str:
    lines = []

    if markdown:
        lines.append("## ブログプロジェクト同期レポート")
    else:
        lines.append("=== ブログプロジェクト同期レポート ===")
    lines.append("")

    # 概要
    lines.append("### 概要" if markdown else "--- 概要 ---")
    for key, val in stats.items():
        lines.append(f"  {key}: {val}")
    lines.append("")

    # カテゴリ別
    categories = {}
    for f in findings:
        categories.setdefault(f.category, []).append(f)

    if not findings:
        lines.append("問題なし — 全て正常です")
        return "\n".join(lines)

    severity_icon = {"error": "x", "warning": "!", "info": "i"} if not markdown else {
        "error": ":x:", "warning": ":warning:", "info": ":information_source:",
    }

    for cat, items in categories.items():
        lines.append(f"### {cat} ({len(items)}件)" if markdown else f"--- {cat} ({len(items)}件) ---")

        if markdown:
            lines.append("| # | タイトル | 詳細 |")
            lines.append("|---|---|---|")

        for f in items:
            num_str = f"#{f.issue_number}" if f.issue_number else "—"
            icon = severity_icon[f.severity]
            title_short = f.title[:50]
            if markdown:
                lines.append(f"| {icon} {num_str} | {title_short} | {f.detail} |")
            else:
                lines.append(f"  [{icon}] {num_str} {title_short}")
                lines.append(f"      {f.detail}")

        lines.append("")

    # サマリー
    errors = sum(1 for f in findings if f.severity == "error")
    warnings = sum(1 for f in findings if f.severity == "warning")
    infos = sum(1 for f in findings if f.severity == "info")
    lines.append(f"**合計**: エラー {errors} / 警告 {warnings} / 情報 {infos}")

    return "\n".join(lines)


# --- Phase 4: APPLY ---

def _graphql_set_field(item_id: str, field_id: str, text_value: str) -> bool:
    token = _get_project_token()
    if not token:
        return False
    mutation = """
    mutation($pid: ID!, $iid: ID!, $fid: ID!, $val: String!) {
      updateProjectV2ItemFieldValue(input: {
        projectId: $pid, itemId: $iid, fieldId: $fid,
        value: { text: $val }
      }) { projectV2Item { id } }
    }"""
    body = json.dumps({"query": mutation, "variables": {
        "pid": PROJECT_ID, "iid": item_id, "fid": field_id, "val": text_value,
    }}).encode()
    req = urllib.request.Request("https://api.github.com/graphql", data=body, headers={
        "Authorization": f"bearer {token}", "Content-Type": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status == 200
    except Exception:
        return False


def _graphql_set_status(item_id: str, option_id: str) -> bool:
    token = _get_project_token()
    if not token:
        return False
    mutation = """
    mutation($pid: ID!, $iid: ID!, $fid: ID!, $oid: String!) {
      updateProjectV2ItemFieldValue(input: {
        projectId: $pid, itemId: $iid, fieldId: $fid,
        value: { singleSelectOptionId: $oid }
      }) { projectV2Item { id } }
    }"""
    body = json.dumps({"query": mutation, "variables": {
        "pid": PROJECT_ID, "iid": item_id, "fid": STATUS_FIELD_ID, "oid": option_id,
    }}).encode()
    req = urllib.request.Request("https://api.github.com/graphql", data=body, headers={
        "Authorization": f"bearer {token}", "Content-Type": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status == 200
    except Exception:
        return False


def apply_fixes(findings: list[Finding], safe_only: bool = False):
    applied = 0

    for f in findings:
        if not f.fix_action:
            continue

        if safe_only and f.fix_action not in ("set_project_url", "add_url_comment"):
            continue

        if f.fix_action == "set_project_url":
            item_id = f.fix_data["item_id"]
            url = f.fix_data["url"]
            if _graphql_set_field(item_id, URL_FIELD_ID, url):
                print(f"  ✓ #{f.issue_number} 公開URL設定: {url[:50]}")
                applied += 1
            else:
                print(f"  ✗ #{f.issue_number} 公開URL設定失敗")

        elif f.fix_action == "add_url_comment":
            url = f.fix_data.get("url")
            if not url:
                continue
            r = subprocess.run(
                ["gh", "issue", "comment", str(f.issue_number), "--repo", GH_REPO,
                 "--body", f"公開済み: {url}"],
                capture_output=True, text=True,
            )
            if r.returncode == 0:
                print(f"  ✓ #{f.issue_number} URLコメント追加: {url[:50]}")
                applied += 1
            else:
                print(f"  ✗ #{f.issue_number} コメント追加失敗: {r.stderr.strip()[:80]}")

        elif f.fix_action == "update_project_status":
            item_id = f.fix_data["item_id"]
            status = f.fix_data["status"]
            option_id = STATUS_OPTIONS.get(status)
            if not option_id:
                continue
            if _graphql_set_status(item_id, option_id):
                print(f"  ✓ #{f.issue_number} ステータス→{status}")
                applied += 1

        elif f.fix_action == "close_issue":
            r = subprocess.run(
                ["gh", "issue", "close", str(f.issue_number), "--repo", GH_REPO],
                capture_output=True, text=True,
            )
            if r.returncode == 0:
                print(f"  ✓ #{f.issue_number} クローズ")
                applied += 1

        elif f.fix_action == "normalize_label":
            old = f.fix_data["old"]
            new = f.fix_data["new"]
            subprocess.run(
                ["gh", "issue", "edit", str(f.issue_number), "--repo", GH_REPO,
                 "--remove-label", old],
                capture_output=True, text=True,
            )
            subprocess.run(
                ["gh", "issue", "edit", str(f.issue_number), "--repo", GH_REPO,
                 "--add-label", new],
                capture_output=True, text=True,
            )
            print(f"  ✓ #{f.issue_number} ラベル {old}→{new}")
            applied += 1

    print(f"\n修正適用: {applied} 件")


# --- メイン ---

def main():
    parser = argparse.ArgumentParser(description="ブログプロジェクト整合性チェック")
    parser.add_argument("--apply", action="store_true", help="全修正を適用")
    parser.add_argument("--safe-only", action="store_true", help="安全な修正のみ適用")
    parser.add_argument("--stale-weeks", type=int, default=4, help="停滞判定の週数 (デフォルト: 4)")
    parser.add_argument("--report", choices=["markdown", "terminal"], default="terminal")
    args = parser.parse_args()

    markdown = args.report == "markdown" or os.environ.get("GITHUB_STEP_SUMMARY")

    print("Phase 1: データ収集...")
    qiita = fetch_qiita_articles()
    print(f"  Qiita: {len(qiita)} 件")
    techblog = fetch_techblog_articles()
    print(f"  APC技術ブログ: {len(techblog)} 件")
    issues = fetch_github_issues()
    print(f"  GitHub Issues: {len(issues)} 件")
    project_items = fetch_project_items()
    print(f"  Project Items: {len(project_items)} 件")
    all_articles = qiita + techblog

    print(f"\nPhase 2: 整合性チェック...")
    findings = []
    findings.extend(check_status_consistency(issues, project_items))
    findings.extend(check_url_completeness(issues, project_items))
    findings.extend(detect_missing_issues(all_articles, issues))
    findings.extend(detect_duplicates(issues))
    findings.extend(detect_stale_issues(issues, args.stale_weeks))

    stats = {
        "GitHub Issues": len(issues),
        "Project Items": len(project_items),
        "Qiita記事": len(qiita),
        "APC技術ブログ記事": len(techblog),
        "検出された問題": len(findings),
    }

    print(f"\nPhase 3: レポート生成...")
    report = generate_report(findings, stats, markdown=markdown)
    print()
    print(report)

    # Actions Summary
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a") as f:
            f.write(report + "\n")

    if args.apply or args.safe_only:
        print(f"\nPhase 4: 修正適用 ({'safe-only' if args.safe_only else 'full'})...")
        apply_fixes(findings, safe_only=args.safe_only)

    # 終了コード
    errors = sum(1 for f in findings if f.severity == "error")
    if errors > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
