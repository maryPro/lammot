# Lammot — музыкальный плеер в Telegram

Telegram Mini App: поиск музыки на YouTube Music, бесконечное радио, избранное, плейлисты со ссылками для друзей, светлая и тёмная темы, 15 акцентных цветов, свои обои. Бэкенд на FastAPI + SQLite, фронтенд на чистом HTML/CSS/JS, деплой на Railway через Docker.

Как всё устроено внутри — в [ARCHITECTURE.md](ARCHITECTURE.md), дизайн — в [UI_SPEC.md](UI_SPEC.md).

---

## 0. Что поставить на Mac

```bash
brew install python@3.12 deno ngrok
```

- **Python 3.12** (3.10+ подойдёт, 3.9 — нет).
- **Deno** — JavaScript-рантайм. С ноября 2025 yt-dlp без него почти не может получать музыку с YouTube.
- **ngrok** — даёт локальному серверу публичный HTTPS-адрес: Telegram открывает Mini App только по HTTPS. Один раз зарегистрируйся на ngrok.com и выполни `ngrok config add-authtoken <токен-из-кабинета-ngrok>`.

---

## 1. Создать бота и получить токен

1. В Telegram открой **@BotFather** → `/newbot`.
2. Введи имя (например, `Lammot`) и username, который заканчивается на `bot` (например, `lammot_music_bot`).
3. BotFather пришлёт токен вида `1234567890:AA...`.

**Токен — это пароль от бота.** Он хранится только в `.env` (локально) или в Variables на Railway. Никогда не коммить его и не вставляй в чаты. Если токен утёк, отправь BotFather `/revoke` и обнови его везде.

Совет: заведи **двух ботов** — тестового для локальной разработки и основного для Railway. У бота может быть только один вебхук, и тот, кто запустился последним, «перехватывает» бота.

---

## 2. Локальный запуск

```bash
cd ~/lammot-main
python3.12 -m venv venv
source venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env
```

Открой `.env` и впиши `BOT_TOKEN=` (токен тестового бота).

Во **втором терминале** запусти туннель:

```bash
ngrok http 8000
```

Скопируй адрес вида `https://xxxx.ngrok-free.app` и впиши его в `.env`:

```
PUBLIC_URL=https://xxxx.ngrok-free.app
```

В первом терминале запусти сервер:

```bash
uvicorn app.main:app --env-file .env --port 8000 --reload
```

В логе должна появиться строка `Telegram webhook and menu button set for https://...`. Приложение само зарегистрировало вебхук и кнопку меню у бота.

Проверка: напиши своему боту `/start` и нажми **Open Lammot 🎵**.

> Бесплатный адрес ngrok меняется при каждом перезапуске ngrok. Тогда обнови `PUBLIC_URL` в `.env` и перезапусти uvicorn.

---

## 3. Тесты

```bash
pytest -m "not integration" -v
```

Это все тесты, которые не ходят в интернет (YouTube, CDN и Telegram подменены). Интеграционные тесты поиска (`tests/test_search.py`) обращаются к настоящему YouTube Music:

```bash
pytest -m integration -v
```

Правило из плейбука остаётся: красные тесты — не коммитим.

---

## 4. Деплой на Railway

Названия кнопок в интерфейсе Railway иногда меняются. Если что-то называется иначе, ищи по смыслу.

1. Зайди на **railway.com** через GitHub → **New Project** → **Deploy from GitHub repo** → выбери `maryPro/lammot`. Railway найдёт `Dockerfile` и `railway.json` и начнёт сборку.
2. Открой сервис → **Settings → Networking → Generate Domain**. Получится адрес вида `https://lammot-production.up.railway.app`.
3. **Variables** — добавь переменные:

   | Переменная | Значение |
   |---|---|
   | `BOT_TOKEN` | токен основного бота |
   | `PUBLIC_URL` | `https://<твой-домен>.up.railway.app` (без `/` в конце) |
   | `DATABASE_PATH` | `/data/lammot.db` |

4. **Volume** (обязательно): добавь к сервису Volume с mount path **`/data`**. Без него база стирается при каждом деплое.
5. Сделай **Redeploy**. В **Deploy Logs** должна быть строка `Telegram webhook and menu button set for ...`, а `https://<домен>/healthz` должен отвечать `{"ok":true}`.

**Деньги.** Railway считает CPU, память, диск и исходящий трафик. Музыка идёт через сервер (так устроен аудио-прокси, см. ARCHITECTURE.md), это примерно 5 МБ трафика на песню. Пробный период даёт немного кредитов, дальше для постоянной работы реалистичен план Hobby. Актуальные цены смотри на railway.com.

---

## 5. Настроить Mini App в BotFather

Кнопку `/start` и кнопку меню сервер настраивает сам. Ссылкам на плейлисты (`t.me/<бот>?startapp=...`) нужна **Main Mini App**:

1. @BotFather → `/mybots` → выбери бота → **Bot Settings** → **Configure Mini App** → **Enable Mini App**.
2. Отправь URL: тот же `PUBLIC_URL`.

Если вместо этого ты создала приложение через `/newapp` с коротким именем, впиши его в переменную `MINI_APP_SHORT_NAME`.

---

## 6. Проверка end-to-end (в настоящем Telegram)

1. `/start` → бот отвечает кнопкой **Open Lammot 🎵**.
2. Поиск: вводишь «Земфира», появляются результаты.
3. Тап по песне: она играет, появляется мини-плеер, в полноэкранном плеере очередь «Up next» из похожих песен.
4. `+` у песни → короткая вибрация, значок меняется на ✓, песня появляется в Favorites.
5. Profile → **New playlist** → в поиске `⋯` → **Add to playlist**.
6. Открой плейлист → **Share** → отправь ссылку себе в «Избранное» → ссылка открывает плейлист в режиме только для чтения.
7. Settings: тема, цвет, обои — всё меняется сразу.
8. Проверь на iPhone и на Android: на старых iPhone приложение само переключается на формат AAC.

---

## 7. Как влить код в репозиторий

Код спринтов 5–8 лежит в архиве целиком. Твоя ветка `sprint-5-database` уже есть; её версии `main.py` и `test_api.py` заменяются этими.

```bash
cd ~/lammot-main
git checkout sprint-5-database
git branch -m sprints-5-8                      # имя ветки по содержимому
cp -R ~/Downloads/lammot/. ~/lammot-main/      # путь — туда, куда распаковала архив
git status                                      # нет ли .env, *.db, venv/
pytest -m "not integration" -v                  # всё зелёное?
```

Изменения просмотри во вкладке **Source Control** в VS Code. Дальше три коммита — по одному на спринт:

```bash
git add app/ tests/ pytest.ini requirements.txt requirements-dev.txt .env.example .gitignore
git commit -m "feat: sprint 5 — sqlite database, user id from initData, audio relay, data deletion"

git add frontend/
git commit -m "feat: sprints 6-7 — frontend, player, themes, playlists, settings"

git add Dockerfile docker-entrypoint.sh .dockerignore railway.json ARCHITECTURE.md README.md
git commit -m "feat: sprint 8 — docker image, railway config, docs"

git push -u origin sprints-5-8
```

Затем на GitHub создай Pull Request `sprints-5-8 → main`, проверь вкладку **Files changed** и сделай merge. Railway пересоберёт приложение из `main`.

---

## 8. Если что-то не работает

**`/start` молчит.** Проверь, что `PUBLIC_URL` задан и начинается с `https://`, и найди в логах строку про webhook. Если там `Telegram setup failed: setWebhook: ...`, причина в тексте. Частая история: тот же токен запущен в другом месте (локально и на Railway одновременно).

**«Your session has expired».** Telegram-данные входа действуют 24 часа. Закрой Mini App и открой её снова из бота.

**Песни не играют, в логах `yt-dlp failed`.** Обнови yt-dlp (`pip install -U "yt-dlp[default]"`, на Railway — Redeploy) и проверь, что Deno установлен (`deno --version`). YouTube часто меняется, старые версии yt-dlp перестают работать.

**В логах «Sign in to confirm you're not a bot».** YouTube блокирует IP сервера. Можно указать `YTDLP_COOKIES_FILE` с cookies **отдельного, одноразового** Google-аккаунта: это данные входа, их нельзя коммитить, а аккаунт могут заблокировать. Подробности — в FAQ yt-dlp.

**База пропала после деплоя.** Не подключён Volume на `/data` или `DATABASE_PATH` указывает не туда.

**Ссылка на плейлист открывает чат, а не плеер.** Не включена Main Mini App в BotFather (шаг 5).

**Вне Telegram видно «Open Lammot in Telegram».** Так и задумано: без данных входа Telegram API не отвечает.

---

## 9. Данные и безопасность

- **Что хранится:** Telegram id, имя и username, избранное, плейлисты, последние 50 поисков и 200 прослушиваний. Старая история удаляется автоматически.
- **Удаление:** Settings → **Clear search/play history** или **Delete all my data** (удаляет всё, ссылки на плейлисты перестают работать).
- **Обои** хранятся только на устройстве (localStorage) и не загружаются на сервер.
- **Идентичность:** id пользователя берётся только из подписанных Telegram initData, а не из URL. Чужой плейлист выглядит как несуществующий (404).
- **Секреты:** единственный секрет — `BOT_TOKEN`. Ключи аудиоссылок и секрет вебхука выводятся из него, поэтому `/revoke` в BotFather разом отзывает всё. Токен не попадает в логи.
- **Сервер:** контейнер работает не от root. Включены CSP и ограничение частоты запросов на поиск и воспроизведение.

## 10. Ограничения

- Музыку достаёт yt-dlp. Правила YouTube такое использование не разрешают. Для учебного и личного проекта решение за тобой, для публичного запуска это риск.
- Один процесс: кэш ссылок и лимиты запросов живут в памяти. Для этого проекта этого достаточно; для масштабирования понадобится Redis.
