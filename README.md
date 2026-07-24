# ip3-hackathon

Docker Composeで構築したWeb / API / DBの3層構成。

## 技術スタック

| 分類 | 技術・ライブラリ | 用途 |
| --- | --- | --- |
| フロントエンド | HTML / CSS / JavaScript | ログイン、OTP入力、認証後の画面を提供 |
| Webサーバー | nginx 1.27 | 静的ファイルの配信と、`/api/`からAPIへのリバースプロキシ |
| API | Python 3.12 / FastAPI / Uvicorn | REST APIと認証処理を非同期で実行 |
| データアクセス | SQLAlchemy 2 / asyncpg | PostgreSQLへの非同期アクセスとORM |
| 設定・バリデーション | Pydantic Settings / Pydantic | 環境変数の読み込みとリクエスト・レスポンスの検証 |
| パスワード認証 | bcrypt | パスワードのハッシュ化と照合 |
| OTP | Python標準ライブラリの`secrets` / `hmac` / `hashlib` | OTP生成とHMAC-SHA256によるハッシュ化 |
| トークン認証 | PyJWT | HS256署名のJSON Web Token（JWT）を発行・検証 |
| 外部API通信 | HTTPX | Nexway CPaaS NOWのSMS送信APIを非同期で呼び出し |
| データベース | PostgreSQL 16 | ユーザーとOTPチャレンジを永続化 |
| 実行環境 | Docker / Docker Compose / uv | 3サービスの構築・起動とPython依存関係の管理 |

`pyproject.toml`にはAlembicも含まれていますが、現時点ではマイグレーションを使用していません。API起動時にSQLAlchemyの`Base.metadata.create_all()`でテーブルを作成します。

## アーキテクチャ

Docker Compose上で、Web・API・DBを分離した3層構成です。

| サービス | 責務 |
| --- | --- |
| `web` | nginxで`web/html`の静的ファイルを配信し、`/api/`へのリクエストを`api`へ転送 |
| `api` | FastAPIでユーザー認証、OTPの発行・検証、JWTの発行、Nexway APIとの通信を担当 |
| `db` | PostgreSQLでユーザー情報とOTPチャレンジを永続化。Dockerボリューム`db_data`を使用 |

ブラウザは`http://localhost:8080`のnginxだけにアクセスします。nginxは画面を返し、`/api/`から始まるリクエストを内部ネットワークのFastAPIへ転送します。FastAPIはPostgreSQLへ非同期でアクセスし、OTPの送信時だけ外部のNexway CPaaS NOWを呼び出します。

## システム構成図

```mermaid
graph LR
    U["ブラウザ"]
    S["スマートフォン\n(SMSアプリ)"]
    N["Nexway CPaaS NOW\n(外部SMS送信API)"]

    subgraph docker["Docker Compose ネットワーク"]
        W["web (nginx)\n:8080 → :80"]
        A["api (FastAPI)\n:8000"]
        D[("db (PostgreSQL 16)\n:5432")]
    end

    U -->|"http://localhost:8080"| W
    W -->|"静的ファイル\nweb/html/*.html"| U
    W -->|"location /api/ → proxy_pass"| A
    A -->|"asyncpg (SQLAlchemy async)"| D
    A -->|"POST /api/v1/short_messages"| N
    N -->|"SMS配信"| S
    S -.->|"OTPを確認して入力"| U
```

認証まわりの処理の流れ(ログイン → SMS OTP検証 → 保護ページ表示):

```mermaid
sequenceDiagram
    participant U as ブラウザ
    participant S as スマートフォン (SMS)
    participant W as web (nginx)
    participant A as api (FastAPI)
    participant D as db (PostgreSQL)
    participant N as Nexway CPaaS NOW

    U->>W: GET /
    W-->>U: index.html (ログイン画面)

    U->>W: POST /api/auth/login {username, password}
    W->>A: proxy_pass /auth/login
    A->>D: SELECT * FROM users WHERE username = ?
    D-->>A: password_hash, phone_number
    A->>A: bcrypt.checkpw() で照合
    A->>A: OTPコード生成 + HMAC-SHA256でハッシュ化
    A->>D: INSERT INTO otp_challenges (code_hash, expires_at, attempts_remaining)
    A->>N: POST /api/v1/short_messages {to, text, user_reference}
    N-->>A: 202 Accepted {delivery_order_id}
    A-->>U: 200 {challenge_id, expires_in}
    U->>U: challenge_id を sessionStorage に保存し otp.html へ遷移

    N-->>S: SMSでワンタイムパスワードを配信
    S-->>U: ユーザーがOTPを確認して入力

    U->>W: POST /api/auth/otp/verify {challenge_id, code}
    W->>A: proxy_pass /auth/otp/verify
    A->>D: SELECT * FROM otp_challenges WHERE id = challenge_id
    D-->>A: code_hash, expires_at, attempts_remaining
    A->>A: 有効期限 / 試行回数 / ハッシュ一致を検証
    alt 検証成功
        A->>D: DELETE FROM otp_challenges WHERE id = challenge_id
        A-->>U: 200 {access_token (JWT)}
        U->>U: access_token を localStorage に保存し welcome.html へ遷移
    else 検証失敗
        A->>D: UPDATE otp_challenges SET attempts_remaining -= 1
        A-->>U: 401 Unauthorized
        U->>U: otp.html にエラー表示(再入力 or ログイン画面に戻る)
    end

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

## ログイン(SMS OTP)フロー

ID/パスワード認証に加えて、[Nexway CPaaS NOW](https://smslink.nexway.co.jp/service/api) を利用したSMSワンタイムパスワード(OTP)認証を実装しています。

1. `index.html` でログイン名・パスワードを送信(`POST /auth/login`)
2. サーバがID/PWを検証し、OTPコードを生成してCPaaS NOW経由でSMS送信。`challenge_id` を返す
3. `otp.html` でSMSに届いたコードを入力(`POST /auth/otp/verify`)
4. コードが正しければアクセストークンを発行し、`welcome.html` へ遷移

OTPコードはハッシュ化して保存し、有効期限(既定5分)・試行回数制限(既定5回)を設けています。検証に成功したOTPチャレンジはデータベースから削除するため、再利用できません。

CPaaS NOWのAPIホスト・APIトークンはハッシュソン当日に配布されるため、`.env` の以下の値を差し替えてください。

```
NEXWAY_API_BASE_URL=<配布されたホストURL>
NEXWAY_API_TOKEN=<配布されたAPIトークン>
```

検証環境では、正常送信用に`9001111101`〜`9001111104`、エラー確認用に`9001111201`〜`9001111204`と`9002222001`が用意されています。デモユーザーの電話番号は、画面上で扱いやすい国内形式の`09001111101`を初期値にしています。SMS送信時に先頭の`0`を除去し、検証環境の`9001111101`へ変換します。

検証環境のエンドポイントとアクセストークンは`.env`へ設定してください。アクセストークンは秘密情報のため、READMEやGitの管理対象には含めません。また、検証環境へ大量のリクエストを送らないでください。送信結果を確認する場合は、アイピーキューブ社員へ問い合わせてください。

### 接続情報が届く前のローカルテスト方法

CPaaS NOWの接続情報が届くまでは、`POST /api/v1/short_messages` を模したモックサーバーを立ててブラウザから通しでテストできます。

```bash
# 1. ローカルでモックSMSサーバーを起動(202 Acceptedを返し、送信内容を標準出力に表示)
python3 - <<'EOF' &
import json
from http.server import BaseHTTPRequestHandler, HTTPServer

class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        print("RECEIVED:", json.loads(self.rfile.read(length)), flush=True)
        self.send_response(202)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"delivery_order_id": 1, "accepted_at": "2026-01-01T00:00:00Z"}).encode())
    def log_message(self, *args): pass

HTTPServer(("0.0.0.0", 9999), Handler).serve_forever()
EOF

# 2. .env のホストをモックに向ける
#    NEXWAY_API_BASE_URL=http://host.docker.internal:9999
docker compose up -d api

# 3. ブラウザで http://localhost:8080 からログイン
#    OTPコードはモックサーバーの標準出力(RECEIVED: ... "text": "ワンタイムパスワードは ****** です。")に表示される
```

ログインボタンを押すたびに新しいOTPチャレンジ・新しいコードが発行される点に注意してください(古いコードは無効)。接続情報が届いたら `NEXWAY_API_BASE_URL` / `NEXWAY_API_TOKEN` を本物の値に戻して `docker compose up -d api` してください。

## エンドポイント

- Web: http://localhost:8080
  - `index.html` — ログイン画面(ID/パスワード)
  - `otp.html` — ワンタイムパスワード入力画面
  - `welcome.html` — ログイン後の画面(ユーザー名表示・ログアウト)
- API: http://localhost:8000
  - `GET /health`
  - `POST /auth/register`(username, password, phone_number)
  - `POST /auth/login`(ID/PW検証 → OTP送信、`challenge_id` を返す)
  - `POST /auth/otp/verify`(OTP検証 → アクセストークン発行)
  - `GET /auth/me`(要 `Authorization: Bearer <token>`)
  - `GET /items`
  - `POST /items`
- DB: localhost:5432

デモ用アカウント: `demo` / `password`(電話番号 `09001111101`。API起動時に自動作成)

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
│       ├── auth.py       # パスワード/JWT/OTPハッシュ
│       ├── nexway.py     # CPaaS NOW SMS送信クライアント
│       ├── config.py
│       ├── db.py
│       ├── models.py   # Item, User
│       └── schemas.py
└── web/            # nginx
    ├── Dockerfile
    ├── nginx.conf
    └── html/
        ├── index.html    # ログイン画面(ID/パスワード)
        ├── otp.html      # OTP入力画面
        └── welcome.html  # ログイン後画面
```

## 停止

```bash
docker compose down        # コンテナ停止(DBデータは保持)
docker compose down -v     # コンテナ停止 + DBデータ削除
```
