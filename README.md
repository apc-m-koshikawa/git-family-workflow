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
| 日常のpush | Gitea | （設定不要、起動するだけ） |

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

- GitHub アカウント + gh CLI
- Python 3.9+
- Docker Engine（Docker Desktopは不要）

## セットアップ

### 1. GitHub（クラウド）

```bash
# Issueテンプレートとworkflowsをリポジトリにコピー
cp -r .github/ your-repo/.github/

# Secretsを設定
gh secret set PROJECT_TOKEN --repo your-org/your-repo  # Classic PAT (project + repo)
```

### 2. GitLab CE セルフホスト

#### macOS の場合

colima（OSSのDocker互換ランタイム）を使います。Docker Desktopは不要です。

```bash
# Docker環境のセットアップ
brew install colima docker docker-compose
colima start --cpu 4 --memory 8

# GitLabを起動
mkdir -p ~/gitlab && cd ~/gitlab
cat > docker-compose.yml << 'YAML'
services:
  gitlab:
    image: gitlab/gitlab-ce:latest
    container_name: gitlab
    restart: unless-stopped
    ports:
      - "8080:80"
      - "2222:22"
    volumes:
      - gitlab_config:/etc/gitlab
      - gitlab_logs:/var/log/gitlab
      - gitlab_data:/var/opt/gitlab
    shm_size: '256m'
    environment:
      GITLAB_OMNIBUS_CONFIG: |
        external_url 'http://localhost:8080'
        gitlab_rails['gitlab_shell_ssh_port'] = 2222
        puma['worker_processes'] = 2
        prometheus_monitoring['enable'] = false

  gitlab-runner:
    image: gitlab/gitlab-runner:latest
    container_name: gitlab-runner
    restart: unless-stopped
    volumes:
      - runner_config:/etc/gitlab-runner
      - /var/run/docker.sock:/var/run/docker.sock
    depends_on:
      - gitlab

volumes:
  gitlab_config:
  gitlab_logs:
  gitlab_data:
  runner_config:
YAML

docker compose up -d
# http://localhost:8080 でアクセス（初回起動に数分かかります）
```

#### Windows（WSL2）の場合

WSL2上のDocker Engine（無料・OSS）を使います。Docker Desktopは不要です。

```bash
# WSL2にDocker Engineをインストール（未導入の場合）
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin

# GitLabを起動
mkdir -p ~/gitlab && cd ~/gitlab
cat > docker-compose.yml << 'YAML'
services:
  gitlab:
    image: gitlab/gitlab-ce:latest
    container_name: gitlab
    restart: unless-stopped
    ports:
      - "80:80"
      - "443:443"
      - "2222:22"
    volumes:
      - gitlab_config:/etc/gitlab
      - gitlab_logs:/var/log/gitlab
      - gitlab_data:/var/opt/gitlab
    shm_size: '256m'
    environment:
      GITLAB_OMNIBUS_CONFIG: |
        external_url 'http://localhost'
        gitlab_rails['gitlab_shell_ssh_port'] = 2222
        puma['worker_processes'] = 2
        prometheus_monitoring['enable'] = false

  gitlab-runner:
    image: gitlab/gitlab-runner:latest
    container_name: gitlab-runner
    restart: unless-stopped
    volumes:
      - runner_config:/etc/gitlab-runner
      - /var/run/docker.sock:/var/run/docker.sock
    depends_on:
      - gitlab

volumes:
  gitlab_config:
  gitlab_logs:
  gitlab_data:
  runner_config:
YAML

docker compose up -d
# http://localhost でアクセス（初回起動に数分かかります）
```

#### GitLab Runner の登録（共通）

```bash
# 初期rootパスワードを確認
docker exec gitlab grep 'Password:' /etc/gitlab/initial_root_password

# 登録トークンを取得
REG_TOKEN=$(docker exec gitlab gitlab-rails runner \
  "puts Gitlab::CurrentSettings.current_application_settings.runners_registration_token" 2>/dev/null)

# Runnerを登録
docker exec gitlab-runner gitlab-runner register \
  --non-interactive \
  --url "http://gitlab:80" \
  --registration-token "$REG_TOKEN" \
  --executor "docker" \
  --docker-image "python:3.12-slim" \
  --description "local-docker-runner" \
  --docker-network-mode "gitlab_default" \
  --docker-volumes "/var/run/docker.sock:/var/run/docker.sock"
```

#### CI変数の設定

GitLab WebUI → Settings → CI/CD → Variables で以下を追加:
- `ANTHROPIC_API_KEY`: Claude APIキー（ai-review用、masked推奨）

### 3. Gitea

#### macOS の場合

```bash
brew install gitea
brew services start gitea
# http://localhost:3000 でアクセス
```

#### Windows（WSL2）の場合

```bash
# Dockerで起動
docker run -d --name gitea \
  -p 3000:3000 -p 2223:22 \
  -v gitea_data:/data \
  gitea/gitea:latest
# http://localhost:3000 でアクセス
```

### 4. 3つのリモートを設定

```bash
# GitHub（クラウド）
git remote add github-personal https://github.com/your-org/your-repo.git

# GitLab（ローカル、SSH鍵認証推奨）
git remote add gitlab ssh://git@localhost:2222/your-user/your-repo.git

# Gitea（ローカル）
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
