# Переезд Lammot в EU West (Railway)

Runbook для переноса прод-сервиса из US West в EU West **без потери данных**.
Выполняется человеком в Railway UI + Railway CLI. Агент готовит команды и
проверяет результат, но не нажимает кнопки за владельца.

## Что важно знать до начала

- **Volume привязан к региону.** У сервиса может быть только один volume.
  Переключение `Regions & Replicas` в настройках сервиса **не переносит**
  `/data`: приложение стартует с пустой БД и без cookies.
- **БД в WAL-режиме.** Копировать один `lammot.db` нельзя — потеряются
  закоммиченные строки из `-wal`. Используй `python -m app.migration`
  (SQLite backup API), он уже есть в Docker-образе.
- **Один вебхук на токен.** Два сервиса с прод-токеном одновременно
  перехватывают вебхук. Стейджинг — только с **тестовым** ботом, US West
  останавливается до cutover.
- **Реплика одна.** Кэш ссылок, rate-limit и семафоры резолвера живут в
  памяти процесса; 2+ реплики их разъедут и создадут борьбу за SQLite.
- **Лимиты Railway.** На Trial: 3 volume, 0.5 GB. Второй сервис + volume
  расходуют остаток кредитов («30 дней или $5» на момент планирования).
- **Домен может измениться.** Старые inline-кнопки в уже отправленных
  сообщениях с прежним `*.up.railway.app` перестанут открываться; новые
  `/start` будут корректны.

## 0. Preflight (10 минут)

Установи CLI (если ещё нет) и войди:

```bash
brew install railway
railway login
railway link
```

Выбери project `alert-motivation`, environment `production`, service `lammot`.

Собери факты:

```bash
railway volume list
railway ssh -s lammot -- sh -c 'du -sh /data && ls -la /data'
```

Запиши:

- имя текущего volume (например `lammot-volume`, mount `/data`);
- текущий домен и значение `PUBLIC_URL`;
- список файлов в `/data` (`lammot.db`, `cookies.txt`, `cookies-reserve.txt`,
  `cache/`).

Проверь Variables сервиса `lammot`:

- `DATABASE_PATH=/data/lammot.db`
- `YTDLP_COOKIES_FILE=/data/cookies.txt`
- `YTDLP_RESERVE_COOKIES_FILE=/data/cookies-reserve.txt` (если используется)
- `YTDLP_CACHE_DIR=/data/cache`
- `CANARY_VIDEO_IDS`, `OWNER_TG_ID`, `YTDLP_PLAYER_CLIENTS`

Сделай Railway Backup текущего volume: сервис → **Backups → Create backup**.
Это откат на уровне платформы.

## 1. Консистентный бэкап БД (US West)

Сервис можно не останавливать: backup API читает консистентный снимок.

```bash
railway ssh -s lammot -- python -m app.migration \
  --source /data/lammot.db \
  --dest /data/lammot-backup.db
```

В выводе должно быть `"integrity": "ok"` и counts (`users`, `favorites`,
`playlists`, `play_history`). Exit code должен быть `0`.

Скачай backup и cookies (работает даже при остановленном сервисе):

```bash
railway volume files --volume lammot-volume download /lammot-backup.db ./lammot-backup.db
railway volume files --volume lammot-volume download /cookies.txt ./cookies.txt
railway volume files --volume lammot-volume download /cookies-reserve.txt ./cookies-reserve.txt
```

Проверь копию локально:

```bash
/Users/mariia/lammot-main/venv/bin/python -m app.migration --verify ./lammot-backup.db
```

Ожидаемо: `"integrity": "ok"` и те же counts, что на сервере.

> Не коммить `lammot.db`, `lammot-backup.db`, `cookies*.txt` и `.env`.
> После миграции удали локальные копии.

## 2. Новый сервис в EU West

В Railway UI:

1. Project → **New → GitHub Repo** → `maryPro/lammot`, branch `main`.
2. Имя сервиса: `lammot-eu`.
3. **Settings → Regions & Replicas** → `EU West (Amsterdam, Netherlands)`,
   **Replicas: 1**.
4. **Variables** — скопируй значения с `lammot`, но для стейджинга:
   - `BOT_TOKEN` = токен **тестового** бота;
   - `PUBLIC_URL` = временный домен EU-сервиса (см. шаг 5);
   - `DATABASE_PATH=/data/lammot.db`;
   - `YTDLP_COOKIES_FILE=/data/cookies.txt`;
   - `YTDLP_RESERVE_COOKIES_FILE=/data/cookies-reserve.txt`;
   - `YTDLP_CACHE_DIR=/data/cache`;
   - `CANARY_VIDEO_IDS`, `OWNER_TG_ID`, `YTDLP_PLAYER_CLIENTS` — как на проде.
5. **Settings → Networking → Generate Domain.** Скопируй домен в
   `PUBLIC_URL` (без `/` в конце).
6. **Volume:** добавь volume в EU West, mount path `/data`, 0.5 GB.
   Имя, например, `lammot-volume-eu`.
7. **Deploy** и дождись `Application startup complete`.
   Проверь `https://<eu-домен>/healthz` → `{"ok":true}`.

На этом этапе EU-сервис работает с тестовым ботом и пустой БД. Прод не тронут.

## 3. Перенеси данные в EU volume

1. Останови EU-сервис: **Deployments → Stop** (или scale 0), чтобы приложение
   не писало в БД во время загрузки.
2. Загрузи backup и cookies:

```bash
railway volume files --volume lammot-volume-eu upload ./lammot-backup.db /lammot.db --overwrite
railway volume files --volume lammot-volume-eu upload ./cookies.txt /cookies.txt --overwrite
railway volume files --volume lammot-volume-eu upload ./cookies-reserve.txt /cookies-reserve.txt --overwrite
```

3. Запусти EU-сервис: **Deployments → Restart**. Entrypoint выставит права
   UID 10001 на `/data`.
4. Проверь counts на EU-сервере:

```bash
railway ssh -s lammot-eu -- python -m app.migration --verify /data/lammot.db
```

Counts должны совпасть с US West. Если `integrity` не `ok` — остановись и
восстанови volume из Railway Backup.

## 4. Staging-проверка и замер

В тестовом боте:

- `/start`, поиск, воспроизведение нового трека;
- повторный (кэшированный) трек;
- избранное, плейлист, радио;
- `/health` → `status: ok`, `active_account: primary`.

Замерь на телефоне (VPN off, Wi-Fi и мобильная сеть):

- tap-to-play для 5 новых и 3 кэшированных треков;
- в логах EU: `api_stream ... resolve_ms`, `audio_relay ... cdn_ms`,
  `first_chunk_ms`, `cdn_warm ... ttfb_ms`.

Сравни с US West. **Переезжай дальше, только если выигрыш ≥ 1 с** и
`/health` не показывает роста `bot_check`/`transient`. Иначе остановись:
оставь US West, EU-сервис удали.

## 5. Cutover (окно 5–15 минут)

1. **Останови US West** (`lammot`), чтобы он не перехватил вебхук.
2. На `lammot-eu` замени:
   - `BOT_TOKEN` → прод-токен;
   - `PUBLIC_URL` → финальный домен EU.
3. **Redeploy** EU-сервис.
4. В логах дождись `Telegram webhook and menu button set for ...`.
5. Проверь:
   - `https://<eu-домен>/healthz` → `{"ok":true}`;
   - `/health` → `status: ok`;
   - `/start`, воспроизведение, избранное, плейлист;
   - counts не изменились.
6. Если домен изменился — предупреди пользователей: старые inline-кнопки
   могут не работать. При наличии custom domain переключи DNS на EU.

US West сервис и volume **не удаляй 24–48 часов** — это откат.

## 6. Откат

- EU сломан: запусти US West с прод-токеном. Он выставит вебхук на старый
  домен; проверь `/healthz` и `/start`.
- Если после cutover в EU уже были записи (новые пользователи, плейлисты),
  сначала скачай EU `/lammot.db` через `app.migration --dest`, перенеси его
  обратно в US volume, иначе записи потеряются.
- Старый volume и Railway Backup не удаляй до стабилизации.

## 7. Очистка

Через 24–48 часов:

- удали US West сервис и volume (или оставь холодным бэкапом);
- удали временные переменные/домен тестового бота;
- обнови README/ARCHITECTURE фактическим регионом и цифрами;
- удали локальные `lammot-backup.db` и `cookies*.txt`.

## Чек-лист «не делать»

- Не менять регион через dropdown у прод-сервиса с данными.
- Не запускать два сервиса с прод-токеном одновременно.
- Не ставить больше 1 реплики.
- Не копировать `lammot.db` обычным `cp`/`scp` без backup API.
- Не коммитить БД, cookies, `.env`.
