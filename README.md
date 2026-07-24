# ip3-hackathon

Docker Composeで構築したWeb / API / DBの3層構成。

## 構成

| サービス | 内容 |
|---|---|
| `web` | nginx。静的ファイル(`web/html`)を配信し、`/api/` を `api` サービスへリバースプロキシ |
| `api` | Python + [uv](https://docs.astral.sh/uv/) + FastAPI |
| `db` | PostgreSQL 16 |

## セットアップ

```bash
cp .env.example .env   # 必要に応じて値を編集
docker compose up -d --build
```

## エンドポイント

- Web: http://localhost:8080
- API: http://localhost:8000
  - `GET /health`
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
│       ├── config.py
│       ├── db.py
│       ├── models.py
│       └── schemas.py
└── web/            # nginx
    ├── Dockerfile
    ├── nginx.conf
    └── html/
```

## 停止

```bash
docker compose down        # コンテナ停止(DBデータは保持)
docker compose down -v     # コンテナ停止 + DBデータ削除
```
