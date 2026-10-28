# Worker, OneDrive и ежедневное обновление

Один планировщик: **supercronic внутри контейнера `worker`**, ежедневно в **16:00 Europe/Bratislava**
(`docker/crontab`, часовой пояс из `TZ` и tzdata образа). Внешний cron не используется.

Команда одного запуска: `python -m bri.pipeline` (OneDrive → API → полнота дней → расчёт →
публикация → `STAV.txt`). Параллельный запуск исключён блокировкой PostgreSQL
(`pg_try_advisory_lock`), она действует между всеми контейнерами.

## Компоненты

| Сервис | Что делает | Доступ к токену OneDrive |
|---|---|---|
| `db` | PostgreSQL, схема `bri` | нет |
| `worker` | pipeline по расписанию | да, `./secrets/rclone` (запись) |
| `dashboard` | только читает опубликованные результаты | нет |
| `migrate` (профиль `jobs`) | одноразовый перенос legacy-данных и догон | да |

## OneDrive Personal: владелец и доступ

- **Владелец**: Microsoft account, в чьём OneDrive лежит папка `BatteryIndex`.
  Он отвечает за квоту, восстановление доступа и повторную авторизацию сервера.
  *Владельца нужно назвать явно в документации проекта.*
- Участники загружают файлы со **своих** аккаунтов в общую папку `BatteryIndex/1_Nahrať`
  (доступ «Môže upravovať»). Пароль владельца не передаётся.
- Worker авторизуется **под владельцем** (delegated OAuth: один интерактивный вход, дальше
  refresh token). Отзыв доступа или истёкший токен требует нового входа; worker при этом
  ничего не удаляет и пишет в `STAV.txt` и `pipeline_run.messages` ошибку авторизации.
- Права OneDrive (`Files.ReadWrite`) шире одной папки: путь `ONEDRIVE_REMOTE` не ограничивает
  токен. Поэтому рекомендуется отдельный проектный Microsoft account.

```text
BatteryIndex/
├── 1_Nahrať/      ← сюда кладут XLSX SEPS/Damas «Regulačná elektrina (denná)»
├── 2_Spracované/  ← worker переносит сюда после записи в БД
├── 3_Chyby/       ← файл с ошибкой формата + <файл>.chyba.txt
├── STAV.txt       ← последний запуск, что не хватает, ошибки
└── NÁVOD.txt      ← где скачать отчёт (заполняет команда)
```

### Авторизация (один раз, на компьютере с браузером)

```powershell
mkdir secrets\rclone
.\tools\bin\rclone.exe config --config secrets\rclone\rclone.conf
```

Ответы: `n` (new remote) → имя `onedrive` → тип `onedrive` → client_id/secret пусто →
region `global` → advanced `n` → auto config `y` (откроется браузер, войти **владельцем**) →
`onedrive` (OneDrive Personal) → выбрать диск → `y`.

Проверка:

```powershell
.\tools\bin\rclone.exe lsf onedrive:BatteryIndex --config secrets\rclone\rclone.conf
```

Для сервера без браузера: `rclone authorize "onedrive"` на машине с браузером, токен
вставить в `rclone config` на сервере. Токен передавать только приватно.

### Хранение токена

- `secrets/rclone/rclone.conf` — **не** в git и **не** в образе (`.gitignore`, `.dockerignore`).
- Каталог подключён к `worker` **на запись**: rclone обновляет токен через временный файл и
  переименование. На Linux: каталог `0700`, файл `0600`, владелец UID 1000 (appuser).
- `dashboard` каталог не получает.

## Первый запуск

```bash
cp .env.example .env            # пароль БД, ENTSOE_API_KEY, ONEDRIVE_REMOTE=onedrive:BatteryIndex
docker compose up -d db
docker compose run --rm migrate # legacy-данные + догон с BRI_FIRST_DAY (DA/IDM/IMB заново из API)
docker compose up -d            # worker (16:00) + dashboard
```

## Обычная работа

| Действие | Команда |
|---|---|
| Запустить сейчас | `docker compose exec worker python -m bri.pipeline` |
| Догнать период | `docker compose exec worker python -m bri.pipeline --from 2026-01-01 --to 2026-01-31` |
| Только пересчёт | `docker compose exec worker python -m bri.pipeline --skip-api --skip-onedrive` |
| Логи | `docker compose logs -f worker` |
| Тесты | `docker compose run --rm -e TEST_DATABASE_URL=postgresql://bri:bri@db:5432/bri_test worker python -m pytest tests` |

Окно обычного запуска: последние 100 дней до вчера. DA/IDM перекачиваются для неполных и
последних 3 дней; IMB — пока нет оценки `final` (порядок `final` > `monthly` > `daily`).
Пересчёт идёт только там, где изменился снимок входов или версия модели.

## Передача проекта

1. Бэкап БД: `docker compose exec db pg_dump -U bri battery_revenue_index > backup.sql`.
2. Архив исходников: том `raw_archive` (`/data/raw`, файлы `<sha256>.<ext>`).
3. Список настроек без токенов (`.env` без паролей).
4. Новый владелец OneDrive: перенести/поделиться папкой, удалить `secrets/rclone/rclone.conf`,
   заново пройти авторизацию.

## Локально за прокси с проверкой TLS

Если внутри Docker внешние API отвечают `CERTIFICATE_VERIFY_FAILED`, положите корпоративный
CA в `secrets/rclone/ca.pem` и создайте `docker-compose.override.yml` (не в git):

```yaml
services:
  worker:
    environment:
      SSL_CERT_FILE: /config/rclone/ca.pem
```
