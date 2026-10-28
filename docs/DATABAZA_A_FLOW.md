# База данных и процесс обновления — Battery Revenue Index SK

Реализовано в ветке `feature/db-pipeline`: схема `bri` (`bri/sql/001_schema.sql`), пакет `bri/`,
worker в Docker, тесты `tests/`. Рынки: DA, IDM15, IDM60, IMB, aFRR (capacity + energy).
Конфигурации батареи: 1 h / 2 h × 1 / 2 цикла в день. Часовой пояс: `Europe/Bratislava`.

## 1. Ответы на замечания

| Замечание | Решение |
|---|---|
| «Чей аккаунт OneDrive?» | OneDrive **Personal** владельца проекта (называется явно). Участники загружают со своих аккаунтов в общую папку. Worker — delegated OAuth владельца через rclone, токен в приватной папке `secrets/rclone` (запись только для worker). Порядок передачи — `deploy/CRON.md`. |
| «DAM 15 min od 10/2025» | Цены хранятся с родными интервалами из API (`deliveryStart`/`deliveryEnd`): до 30.09.2025 часовые, с 01.10.2025 четвертьчасовые; дни перевода времени 23/25 ч и 92/100 интервалов. Расчёт берёт длину шага из интервалов дня (энергия = мощность × длительность). Старые часовые средние после 01.10.2025 не переносятся как родные данные: DA/IDM/IMB заново загружены из API OKTE. |
| «batch_id nesedí» | Разделено: `source_artifact` = неизменяемое исходное содержимое (файл или ответ API, архив `raw/<sha256>`), `import_batch` = одна попытка импорта (running/success/failed, версия парсера, ошибка), `calc_run` = один расчёт. Строки цен — неизменяемые ревизии; расчёт фиксирует точный снимок входов (`calc_input`). |

## 2. Компоненты

| Компонент | Роль |
|---|---|
| PostgreSQL (`db`) | все входы, статусы, результаты, журнал |
| `worker` | supercronic, **16:00 Europe/Bratislava**, `python -m bri.pipeline`; единственный планировщик |
| `dashboard` | только читает `published_daily_total` |
| OneDrive `BatteryIndex` | ручная загрузка XLSX SEPS/Damas, `STAV.txt` |

## 3. Схема `bri`

### Справочники
- `market` (DA, IDM15, IDM60, IMB, AFRR), `market_component` (prices / capacity / energy / total)
- `data_source` (OKTE, ENTSOE, SEPS_DAMAS), `imb_evaluation` (proxy < daily < monthly < final)
- `battery_config`, `market_param` — **неизменяемые** (триггер запрещает UPDATE; новые параметры = новая строка). Длина интервала здесь не хранится.

### Происхождение
- `source_artifact (artifact_id, source_id, transport api/onedrive/local/legacy, content_sha256, original_name, raw_archive_path, received_at, source_metadata)` — UNIQUE (source, sha256)
- `import_batch (batch_id, artifact_id, parser_version, started_at, finished_at, status, rows_written, error_summary)` — максимум один `success` на (artifact, parser_version); неудачная попытка остаётся `failed` (пишется в отдельной транзакции), зависшие `running` помечаются `failed`.

### Входы — таблица на рынок, строки = ревизии
- `price_da`, `price_idm15`, `price_idm60` (`interval_start`, `interval_end`, `price_eur_mwh`, `batch_id`), PK (interval_start, batch_id)
- `price_imb` (+ `evaluation`)
- `afrr_capacity` (`delivery_day`, `direction` POS/NEG, `block_start_h`, `block_end_h`, `price_eur_mw_h`, `procured_mw`, `batch_id`)
- `afrr_energy` (интервал, up/down объём и цена) — **сырые** значения SEPS; очистка выбросов (> 10 000 EUR/MWh) делается в модели и входит в её версию.

Новая строка пишется только при изменении значения. Текущее значение — новейшая успешная ревизия (представления `*_current`; для IMB — лучшая оценка, затем новейшая). Пересекающиеся интервалы отклоняются.

### Полнота дня
- `market_day (market, day, component, status complete/incomplete/missing/invalid, detail, input_max_batch_id, input_sha256)`
- Полнота = интервалы без пропусков и наложений покрывают местный день `[00:00, 24:00)` в реальном времени (без «всегда 24/96»).
- aFRR: отдельно `capacity` и `energy`; считается только при обоих `complete`.
- IDM без сделок в части продуктов: `incomplete`, модель интерполирует пропуски, если их ≤ 25 %.

### Результаты и версии
- `calc_run (run_id, status, trigger, code_version, model_version, error_summary)`
- `calc_input (run_id, market, day, component, input_max_batch_id, input_sha256)` — точный снимок входов: ревизии ≤ `input_max_batch_id` воспроизводят значения с хешем `input_sha256`.
- `result_daily` PK (run_id, config, market, day, component); `result_interval` PK (run_id, config, market, component, interval_start) — история всех запусков.
- `result_publication (config, market, day → run_id)` — одна показываемая версия. Публикация делается одной транзакцией вместе с `calc_run.status = success`; неудачный запуск не меняет опубликованное.
- `published_daily_total` — только `component = 'total'` (компоненты не суммируются).

### Эксплуатация
- `pipeline_run` — каждый запуск worker и его сообщения.
- `inbox_file` — файлы из OneDrive: imported / duplicate / archive_pending / archived / rejected.

## 4. Ежедневный процесс

1. Блокировка `pg_try_advisory_lock` (действует между контейнерами).
2. OneDrive: повтор незавершённых переносов → новые файлы `1_Nahrať` (старше 10 мин, версия не изменилась за время скачивания) → sha256 → архив → импорт → после commit перенос в `2_Spracované/<время>_<sha8>_<имя>`; ошибка формата → `3_Chyby` + `.chyba.txt`; сеть/авторизация → файл остаётся, повтор позже.
3. API OKTE: DA/IDM15/IDM60 для неполных и последних 3 дней; IMB — пока нет `final` (окно 100 дней). ENTSO-E: aFRR capacity.
4. `market_day` для окна.
5. Отбор: снимок входов или версия модели отличаются от опубликованного результата.
6. Расчёт (`bri/models.py`), запись, публикация.
7. `STAV.txt`: время попытки, последняя публикация, недостающие дни aFRR energy, ошибки.

## 5. Модель

- Оптовые рынки: исправленная быстрая модель спредов. Исправлена ошибка единиц, из-за которой батареи 2 МВт·ч торговали выше мощности (IDM, 2 h: −10…35 %). Отличие от исходной циклической модели ISEA: она никогда не торгует в первом интервале дня; на выборке 60 дней×рынков×конфигураций после исправления 53 совпадают до цента, 7 выше на 0,002–1,5 %.
- IDM60 считается на родных часовых продуктах (раньше — 4 одинаковые четверти).
- aFRR: capacity по реальной длине 4-часового блока (в дни перевода 3/5 ч), energy по реальной длине интервала.
- Версия модели: `native-intervals-v1`; legacy-результаты из JSON — `legacy-json-v1`.

## 6. Автоматически и вручную

| Данные | Источник | Как |
|---|---|---|
| DA, IDM15, IDM60, IMB | OKTE API | автоматически, 16:00 |
| aFRR capacity | ENTSO-E API (`ENTSOE_API_KEY`) | автоматически, 16:00 |
| aFRR energy | SEPS/Damas XLSX | **вручную** в `BatteryIndex/1_Nahrať` |

## 7. Перенос (выполнено один раз)

`python -m bri.migrate`: aFRR capacity CSV (600 дней), 8 оригинальных XLSX SEPS (2025-01-01 … 2026-05-26),
11 388 legacy JSON → `calc_run` `legacy-json-v1` (опубликован), затем догон с 01.03.2025 по новой модели;
новые результаты заменяют legacy там, где входы полные.
