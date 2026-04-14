#!/usr/bin/env python3
"""GitLab CI用: Claude APIで最新記事をレビューする"""

import glob
import os
import sys

try:
    import anthropic
except ImportError:
    print("anthropic パッケージがありません。pip install anthropic を実行してください。")
    sys.exit(1)

api_key = os.environ.get("ANTHROPIC_API_KEY")
if not api_key:
    print("ANTHROPIC_API_KEY が未設定です")
    sys.exit(1)

client = anthropic.Anthropic(api_key=api_key)

# 最新の記事を探す
articles = sorted(glob.glob("articles/*/*/article.md"), key=os.path.getmtime, reverse=True)
if not articles:
    print("記事が見つかりません")
    sys.exit(0)

article_path = articles[0]
print(f"レビュー対象: {article_path}")

with open(article_path) as f:
    content = f.read()

prompt = """以下のブログ記事を読者視点でレビューしてください。
チェック観点:
1. 論理の飛躍や矛盾がないか
2. 読者が「なぜ？」と感じる箇所がないか
3. 文末が「です・ます」で統一されているか
4. 一次情報（自分で検証した内容）と二次情報（引用）の区別が明確か

問題がなければ「問題なし」と回答してください。
問題がある場合は、該当箇所の引用と改善案を簡潔に示してください。

---
""" + content[:8000]

response = client.messages.create(
    model="claude-3-haiku-20240307",
    max_tokens=1024,
    messages=[{"role": "user", "content": prompt}],
)

review = response.content[0].text
print(review)

with open("ai-review-report.md", "w") as f:
    f.write(f"## AI レビュー結果\n\n対象: `{article_path}`\n\n{review}\n")

print("\nレポート: ai-review-report.md")
