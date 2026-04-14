# git-family-workflow

GitHub・GitLab・Giteaを併用するワークフローの実装例です。

## これは何か

Gitサービスを1つに統一するのではなく、**得意な領域で使い分ける**ためのCI/CD設定・スクリプト集です。

| 関心事 | どこでやるか | 設定ファイル |
|---|---|---|
| ネタ管理（カンバン） | GitHub Issues + Projects | `.github/ISSUE_TEMPLATE/blog-idea.yml` |
| 公開時の自動処理 | GitHub Actions | `.github/workflows/on-publish.yml` |
| Issue→Project自動追加 | GitHub Actions | `.github/workflows/auto-add-to-project.yml` |
| 整合性チェック | GitHub Actions | `.github/workflows/sync-blog-project.yml` |
| セキュリティスキャン（SAST） | GitLab CI | `.gitlab-ci.yml` |
| AI品質レビュー | GitLab CI | `.gitlab-ci.yml` + `scripts/ai-review.py` |
| 日常のpush | Gitea | （設定不要、brewで起動するだけ） |

## ファイル構成

```
git-family-workflow/
├── .gitlab-ci.yml                          # GitLab CI/CD パイプライン
│                                           #   - SAST（セキュリティスキャン、無料）
│                                           #   - 記事lint
│                                           #   - Claude APIレビュー（手動トリガー）
├── .github/
│   ├── workflows/
│   │   ├── auto-add-to-project.yml         # Issue作成 → Project自動追加
│   │   ├── on-publish.yml                  # 「公開済み: URL」コメント → 自動処理
│   │   └── sync-blog-project.yml           # 毎週の整合性チェック
│   └── ISSUE_TEMPLATE/
│       └── blog-idea.yml                   # ネタ起票テンプレート
├── scripts/
│   ├── sync-blog-project.py                # Qiita/技術ブログとIssueの突合スクリプト
│   └── ai-review.py                        # Claude APIで記事をレビュー
└── README.md
```

## 前提

- GitHub アカウント
- GitLab CE セルフホスト（Docker Engine）
- Gitea（brew install gitea）
- Python 3.9+
- gh CLI

## セットアップ

### 1. GitHub

```bash
# Issueテンプレートとworkflowsをリポジトリにコピー
cp -r .github/ your-repo/.github/

# Secretsを設定
gh secret set PROJECT_TOKEN --repo your-org/your-repo  # Classic PAT (project + repo)
```

### 2. GitLab

```bash
# .gitlab-ci.yml をリポジトリにコピー
cp .gitlab-ci.yml your-repo/

# CI変数を設定（GitLab WebUI → Settings → CI/CD → Variables）
# ANTHROPIC_API_KEY: Claude APIキー（ai-review用）
```

### 3. Gitea

```bash
brew install gitea
brew services start gitea
# http://localhost:3000 でアクセス
```

### 4. 3つのリモートを設定

```bash
git remote add github-personal https://github.com/your-org/your-repo.git
git remote add gitlab git@your-gitlab:your-user/your-repo.git
git remote add gitea http://localhost:3000/your-user/your-repo.git
```

## カスタマイズ

### sync-blog-project.py

`GH_REPO`、`PROJECT_ID`、`STATUS_FIELD_ID`、`URL_FIELD_ID` を自分の環境に合わせて変更してください。

### ai-review.py

`model` を自分のAPIキーで使えるモデルに変更してください。

### on-publish.yml

Project IDとフィールドIDを自分の環境に合わせて変更してください。

## 関連記事

- [GitHubだけで詰まった3つのこと](https://qiita.com/m_koshikawa/items/3e89b06b4ffe1de8f162) — このリポジトリの背景
- [AIのルール遵守は確率的、hooksは決定論的 — ハーネスの2つのレイヤー](https://techblog.ap-com.co.jp/entry/2026/03/30/093000) — hooksとCIの関係

## ライセンス

MIT
