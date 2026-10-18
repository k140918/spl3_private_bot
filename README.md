# splatoon-recruit-bot

スプラトゥーン3のプライベートマッチ（プラベ）募集を管理する Discord Bot です。

- `/プラベ` で募集を作成し、Bot が募集メッセージを投稿・更新します
- 参加方法は **ボタン / メッセージ（AI判定）/ 両方** から選べます
- メッセージ方式では Gemini API が「ノ」「行く」「ごめん無理」などの発言から参加意思を判定します
- データは SQLite に保存するので、Bot を再起動しても募集は続きます
- 1GB RAM の VPS で動かせるよう、キャッシュを最小限にしています

```
Discord ─── ConoHa VPS (Ubuntu, 1GB) ─── Python Bot ─┬─ SQLite
                                                      └─ Gemini API
```

---

## 使い方

### `/プラベ` 募集を作成

| 引数 | 内容 | 省略時 |
|---|---|---|
| 方式 | ボタン / メッセージ（AI判定）/ 両方 | ボタン |
| 定員 | 2〜10 | 8 |
| 開始時間 | 自由入力（例: `21:00`） | 表示しない |
| タイトル | 自由入力（50文字まで） | プラベ募集 |
| 締切 | 日本時間の絶対時刻（`22:30` / `22時` / `10/12 22:30`） | なし（作成から6時間で自動終了） |

- 作成者は自動で参加者に入ります
- 締切を指定すると、その時刻に自動で【終了】になり、ボタンも消えます（1分ごとに確認。締切を過ぎた参加操作はその時点で受け付けません）
  - 時刻だけ指定して、その時刻が今日すでに過ぎていれば翌日扱いです（深夜の `0:30` など）
  - 7日より先は指定できません
- 締切を指定しない募集は、作成から `RECRUIT_TTL_HOURS`（既定6時間）たつと自動で【終了】になります
- 募集メッセージを削除すると、その募集も取り消されます

### `/追加` 参加者を手動で追加（募集作成者か「メッセージの管理」権限）

- `/追加 ユーザー:@誰か`：その人を参加者に追加する（ボタン方式・メッセージ方式どちらの募集でも使えます）
- `/追加 ゲスト:名前`：**サーバーにいない人**（X のフォロワーなど）を名前だけで追加する
  - 募集メッセージには「名前（新規）」と表示されます。Discord アカウントとは紐づきません
  - 同じ名前のゲストは重複して追加できません（全角半角・大文字小文字の違いは同じ名前として扱います）
  - 取り消すときは `/取消 ゲスト:名前`（候補が表示されます）
- 定員・締切のチェックはボタン参加と同じです。参加済みの人や Bot は追加されません
- `/引き継ぎ` ではゲストも一緒に引き継がれます

### `/募集文` 募集文を入力・修正（募集作成者か「メッセージの管理」権限）

- `/募集文` を実行すると入力フォームが開き、**改行を含む募集文**（最大1000文字）を書けます
- 募集メッセージのタイトルのすぐ下に表示されます
- もう一度 `/募集文` を実行すると、今の募集文が入った状態でフォームが開くので修正できます。空にして送信すると削除されます
- 募集文は AI 判定には使いません（API 料金を増やさないため）

### `/締切` 締め切る・締切時刻を変更（募集作成者か「メッセージの管理」権限）

- `/締切`：今すぐ締め切る
- `/締切 時刻:23:00`：締切時刻を設定・変更する

### `/引き継ぎ` 終了した募集の参加者を戻す（募集作成者か「メッセージの管理」権限）

誤って締め切った場合などに使います。

1. `/プラベ` で新しい募集を作る
2. `/引き継ぎ 元の募集:#12` を実行（候補に最近7日間の終了した募集が出ます）

- 元の募集の参加者を、参加順のまま新しい募集へ追加します
- すでに参加している人はスキップし、定員を超える分は追加しません（追加できなかった人は結果に表示されます）

### 参加・取消

| 方式 | 参加 | 取消 |
|---|---|---|
| ボタン | 「参加する」ボタン | 「参加取消」ボタン / `/取消` |
| メッセージ | 「ノ」「行く」などと発言（✅ が付く） | 「やっぱ無理」などと発言（👋 が付く） / `/取消` |
| 両方 | 上記のどちらでも | 上記のどちらでも |

- 満員のときに参加を表明した発言には 🈵 が付きます
- AI が判定しなかった発言には何も付きません

### `/取消` 参加取消・誤判定の修正

- `/取消` を引数なしで使うと、自分の参加を取り消します
- `/取消 ユーザー:@誰か` を使うと、その人の参加を取り消します（**募集作成者** か「メッセージの管理」権限を持つ人だけ）。AI の誤判定はこれで直せます
- 同じチャンネルに受付中の募集が複数あるときは、「募集」引数で対象を選びます（候補が表示されます）

### 複数募集の扱い

**メッセージ方式・両方方式の募集は、1チャンネルにつき受付中1件まで**です。
同じチャンネルに複数あると、「行く」がどの募集への発言か判断できなくなるためです。
ボタン方式はボタンが募集ごとに分かれているので、同じチャンネルにいくつあっても大丈夫です。
この制限は DB の部分 UNIQUE インデックスでも保証しています。

### AI判定の方針

- 誤って参加登録しないことを最優先にしています。曖昧な発言、質問、第三者についての発言、雑談はすべて NONE（何もしない）です
- `confidence` が `AI_CONFIDENCE_THRESHOLD`（既定 0.85）以上の JOIN / LEAVE だけを反映します
- 判定には、対象の発言に加えて **直前の発言（最大5件・10分以内）** を文脈として送ります。発言者名とIDは `TARGET` / `U1` などに置き換え、送信しません
- Gemini API のエラー、タイムアウト、不正な応答はすべて「何もしない」として扱います
- 判定の方針は [bot/prompts/intent_classifier.md](bot/prompts/intent_classifier.md) で調整できます（変更後は Bot を再起動してください）

> **プライバシー**: メッセージ方式の募集が受付中のチャンネルでは、発言（200文字以内）が判定のために Gemini API へ送信されます。サーバーメンバーへ事前に周知してください。ボタン方式だけを使う場合、発言は送信されません。

---

## ディレクトリ構成

```
bot/
├─ __main__.py          起動エントリ（python -m bot）
├─ main.py              Bot本体・Intents・Cog登録
├─ config.py            環境変数の読み込み
├─ logging_setup.py     ログ設定（秘密情報を伏せ字にする）
├─ models.py            ドメインモデル
├─ cogs/                Discordのコマンド・イベント処理
├─ views/               Embed・ボタン・メッセージ更新
├─ services/            業務ロジック（Discordに依存しない）
│  ├─ recruitment_service.py   参加・取消・定員・ロック
│  ├─ message_intent.py        発言 → 判定 → 反映
│  ├─ context_buffer.py        直近発言のリングバッファ
│  └─ classifier/              分類器（base / gemini / fake）
├─ repositories/        SQLite（schema.sql / 接続 / SQL）
└─ prompts/             Gemini の System Instruction
deploy/                 systemd unit
scripts/                動作確認用スクリプト
tests/                  pytest（Gemini APIを呼ばずに実行可能）
```

分類器は `IntentClassifier` Protocol（`bot/services/classifier/base.py`）を実装すれば差し替えられます。OpenAI などに替える場合は、実装を1ファイル追加して `bot/main.py` で生成している部分を変更してください。

---

## Discord Bot の作成（Developer Portal）

1. https://discord.com/developers/applications で **New Application** を作成
2. **Bot** タブで **Reset Token** を押してトークンを取得（`DISCORD_TOKEN` に使います。他人に見せないでください）
3. **Bot** タブの **Privileged Gateway Intents** で次を設定
   - **MESSAGE CONTENT INTENT: ON**（メッセージ方式を使う場合のみ必要）
   - SERVER MEMBERS INTENT / PRESENCE INTENT: OFF のままで構いません
   - ※ `GEMINI_API_KEY` が空のときは Message Content Intent を要求しないので、OFF のままでも起動できます
4. **OAuth2 → URL Generator** で次を選び、生成された URL からサーバーに招待
   - Scopes: `bot`, `applications.commands`
   - Bot Permissions（最小限）:
     - View Channels / Send Messages / Embed Links / Read Message History / Add Reactions
     - スレッド内で使う場合は Send Messages in Threads も追加
   - 招待URLの例（`CLIENT_ID` を置き換え。permissions=85056 は上の5つ）:
     `https://discord.com/oauth2/authorize?client_id=CLIENT_ID&scope=bot+applications.commands&permissions=85056`

管理者権限（Administrator）は不要です。付与しないでください。

## Gemini API Key の取得

1. https://aistudio.google.com/apikey で API Key を作成
2. `GEMINI_API_KEY` に設定
3. 使用するモデルは `GEMINI_MODEL` で変更できます（既定: `gemini-3.1-flash-lite`）。最新のモデル名は https://ai.google.dev/gemini-api/docs/models で確認してください

SDK は Google 公式の `google-genai` を使用しています（旧 `google-generativeai` は使っていません）。構造化出力には `response_mime_type="application/json"` と `response_json_schema` を指定し、受け取った値は pydantic で検証しています。

---

## Windows での開発（PowerShell）

```powershell
# 1. Python の確認（3.10 以上）
python --version

# 2. プロジェクトへ移動し venv を作成
cd D:\work\spl3\private_bot
python -m venv .venv

# 3. venv を有効化（スクリプト実行がブロックされる場合は次の行を先に実行）
# Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
.\.venv\Scripts\Activate.ps1

# 4. 依存パッケージをインストール（テストも行う場合は requirements-dev.txt）
pip install -r requirements-dev.txt

# 5. .env を作成
Copy-Item .env.example .env
notepad .env
```

6. `.env` に **`DISCORD_TOKEN`** を設定します（上の「Discord Bot の作成」を参照）
   - 開発中は `DEV_GUILD_ID` にテスト用サーバーのIDを入れると、コマンドがすぐ反映されます（サーバーIDは、Discord の開発者モードをONにしてサーバーを右クリック →「IDをコピー」で取得できます）
7. `.env` に **`GEMINI_API_KEY`** を設定します（メッセージ方式を使わないなら空のままで構いません）

```powershell
# 8. Bot を起動（Ctrl+C で停止）
python -m bot

# テスト（Gemini API は呼びません）
python -m pytest -q

# AI判定だけを試す（実 API を呼ぶので料金が少し発生する場合があります）
python -m scripts.try_classifier
python -m scripts.try_classifier "21時ならやる"
```

---

## Git

`main` ブランチを本番用とします。

```powershell
# リモートを後から設定（GitHub などで空のリポジトリを作ってから）
git remote add origin https://github.com/<you>/splatoon-recruit-bot.git
git push -u origin main
```

`.env`、`*.db`、`.venv/` は `.gitignore` 済みです。コミット前に、秘密情報が含まれていないか確認してください。

```powershell
git status                     # .env が出てこないこと
git ls-files | Select-String "\.env$|\.db$"   # 何も出ないこと
```

---

## Ubuntu（ConoHa VPS）へのデプロイ

Ubuntu 22.04 / 24.04 を想定しています（Python 3.10 以上）。以下は、sudo できる一般ユーザーで実行します。

### 初回セットアップ

```bash
# パッケージ
sudo apt update
sudo apt install -y python3 python3-venv git

# Bot 実行専用ユーザー（ログイン不可）
sudo useradd --system --no-create-home --shell /usr/sbin/nologin splatbot

# コードを配置（private リポジトリの場合は deploy key 等を設定）
sudo mkdir -p /opt/splatoon-recruit-bot
sudo chown "$USER": /opt/splatoon-recruit-bot
git clone https://github.com/<you>/splatoon-recruit-bot.git /opt/splatoon-recruit-bot
cd /opt/splatoon-recruit-bot

# venv と依存パッケージ
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt

# DB の置き場所は splatbot だけが書き込めるようにする
sudo chown splatbot:splatbot data
sudo chmod 750 data

# 秘密情報（Git 管理外）
sudo mkdir -p /etc/splatoon-recruit-bot
sudo cp .env.example /etc/splatoon-recruit-bot/env
sudo nano /etc/splatoon-recruit-bot/env      # DISCORD_TOKEN / GEMINI_API_KEY を設定、DEV_GUILD_ID は空に
sudo chown root:splatbot /etc/splatoon-recruit-bot/env
sudo chmod 640 /etc/splatoon-recruit-bot/env

# systemd へ登録
sudo cp deploy/splatoon-recruit-bot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now splatoon-recruit-bot
```

コードはデプロイ用ユーザーが所有し、Bot（`splatbot`）は読み取りだけできます。書き込めるのは `data/` だけです（systemd の `ProtectSystem=strict` と `ReadWritePaths` で制限しています）。root では実行しません。

### 操作・確認

```bash
sudo systemctl start splatoon-recruit-bot      # 起動
sudo systemctl stop splatoon-recruit-bot       # 停止
sudo systemctl restart splatoon-recruit-bot    # 再起動
systemctl status splatoon-recruit-bot          # 状態（メモリ使用量も表示される）
journalctl -u splatoon-recruit-bot -f          # ログをリアルタイム表示
journalctl -u splatoon-recruit-bot --since "1 hour ago"
```

### 更新手順

```bash
cd /opt/splatoon-recruit-bot
git pull
.venv/bin/pip install -r requirements.txt   # requirements.txt が変わった場合
sudo systemctl restart splatoon-recruit-bot
```

`deploy/splatoon-recruit-bot.service` を変更した場合は、もう一度 `sudo cp ...` と `sudo systemctl daemon-reload` を実行してください。

### メモリについて

- メッセージキャッシュとメンバーキャッシュを無効にし、Members Intent も使っていません
- 文脈用の発言は、メッセージ方式の募集があるチャンネルごとに最大5件だけメモリに保持します
- systemd で `MemoryMax=300M` を設定しています。実際の使用量は `systemctl status` で確認できます
- メモリに余裕がない場合は、VPS に swap（例: 1GB）を作っておくと安全です

---

## 設定一覧（環境変数）

[.env.example](.env.example) を参照してください。主な項目:

| 変数 | 既定値 | 説明 |
|---|---|---|
| `DISCORD_TOKEN` | （必須） | Bot トークン |
| `GEMINI_API_KEY` | 空 | 空ならメッセージ方式を無効にする |
| `GEMINI_MODEL` | `gemini-3.1-flash-lite` | 判定に使うモデル |
| `GEMINI_THINKING_LEVEL` | 空（モデル既定） | `minimal` などにすると速く・安くなる |
| `AI_CONFIDENCE_THRESHOLD` | `0.85` | この値以上の JOIN / LEAVE だけ反映 |
| `AI_CONTEXT_MESSAGES` | `5` | 文脈として送る直前の発言数 |
| `AI_MAX_MESSAGE_CHARS` | `200` | これより長い発言は判定しない |
| `AI_USER_COOLDOWN_SEC` | `5` | 同じ人の連投は判定を間引く |
| `RECRUIT_TTL_HOURS` | `6` | 締切を指定しない募集を自動終了するまでの時間 |
| `DEV_GUILD_ID` | 空 | 開発用。指定したサーバーにだけ即時コマンド同期 |

## ログ

- 起動、募集作成、JOIN / LEAVE、AI判定結果、Gemini API エラー、Discord API エラーを記録します
- **メッセージ本文は記録しません**（ID と判定結果だけを記録します）。Gemini の判定理由は `LOG_LEVEL=DEBUG` のときだけ出力します
- トークンや API Key は、ログ出力時に伏せ字にしています
- AI判定の履歴（本文なし）は SQLite の `ai_judgements` テーブルに残るので、しきい値の調整に使えます

## トラブルシューティング

| 症状 | 対処 |
|---|---|
| `PrivilegedIntentsRequired` で起動しない | Developer Portal で MESSAGE CONTENT INTENT を ON にする（または `GEMINI_API_KEY` を空にする） |
| コマンドが出てこない | グローバル同期は反映に時間がかかることがある。開発中は `DEV_GUILD_ID` を使う |
| テスト用サーバーでコマンドが2重に表示される | `DEV_GUILD_ID` で登録したギルドコマンドと、本番のグローバルコマンドが両方表示されている。テスト用サーバーと本番サーバーは分けて使う |
| メッセージ方式で反応しない | Bot に「リアクションの追加」「メッセージ履歴を読む」の権限があるか確認する。ログに `Gemini API error` が出ていないか確認する |
