# ip3-hackathon

Docker Composeで構築したWeb / API / DBの3層構成。

## 構成

| サービス | 内容 |
|---|---|
| `web` | nginx。静的ファイル(`web/html`)を配信し、`/api/` を `api` サービスへリバースプロキシ |
| `api` | Python + [uv](https://docs.astral.sh/uv/) + FastAPI |
| `db` | PostgreSQL 16 |

## システム構成図

```mermaid
graph LR
    U["ブラウザ"]

    subgraph docker["Docker Compose ネットワーク"]
        W["web (nginx)\n:8080 → :80"]
        A["api (FastAPI)\n:8000"]
        D[("db (PostgreSQL 16)\n:5432")]
    end

    U -->|"http://localhost:8080"| W
    W -->|"静的ファイル\nweb/html/*.html"| U
    W -->|"location /api/ → proxy_pass"| A
    A -->|"asyncpg (SQLAlchemy async)"| D
```

認証まわりの処理の流れ（ログイン → 保護ページ表示）:

```mermaid
sequenceDiagram
    participant U as ブラウザ
    participant W as web (nginx)
    participant A as api (FastAPI)
    participant D as db (PostgreSQL)

    U->>W: GET /
    W-->>U: index.html (ログイン画面)

    U->>W: POST /api/auth/login {username, password}
    W->>A: proxy_pass /auth/login
    A->>D: SELECT * FROM users WHERE username = ?
    D-->>A: password_hash
    A->>A: bcrypt.checkpw() で照合
    A-->>U: 200 {access_token (JWT)}
    U->>U: access_token を localStorage に保存し welcome.html へ遷移

    U->>W: GET /api/auth/me (Authorization: Bearer <token>)
    W->>A: proxy_pass /auth/me
    A->>A: JWT検証 (署名 / 有効期限)
    A->>D: SELECT * FROM users WHERE username = sub
    D-->>A: user行
    A-->>U: 200 {username}
```

## セットアップ

```bash
cp .env.example .env   # 必要に応じて値を編集
docker compose up -d --build
```

## エンドポイント

- Web: http://localhost:8080
  - `index.html` — ログイン画面
  - `welcome.html` — ログイン後の画面（`/auth/me` で認証チェック）
- API: http://localhost:8000
  - `GET /health`
  - `POST /auth/register`
  - `POST /auth/login`
  - `GET /auth/me`（要 `Authorization: Bearer <token>`）
  - `GET /items`
  - `POST /items`
- DB: localhost:5432

## ディレクトリ構成

```
.
├── docker-compose.yml
├── .env.example
├── api/            # FastAPI (uv管理)
│   ├── Dockerfile
│   ├── pyproject.toml
│   └── app/
│       ├── main.py
│       ├── auth.py     # パスワードハッシュ化 / JWT発行・検証
│       ├── config.py
│       ├── db.py
│       ├── models.py   # Item, User
│       └── schemas.py
└── web/            # nginx
    ├── Dockerfile
    ├── nginx.conf
    └── html/
        ├── index.html
        └── welcome.html
```

## 停止

```bash
docker compose down        # コンテナ停止(DBデータは保持)
docker compose down -v     # コンテナ停止 + DBデータ削除
```
