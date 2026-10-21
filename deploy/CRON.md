# Ежедневное обновление данных (cron на сервере)

Раз в день сервис `updater` (`tools/daily_update.py`):

1. импортирует Excel-файлы SEPS/Damas, положенные в `inbox/`;
2. скачивает новые данные из открытых API;
3. пересчитывает выручку за дни, у которых появились или изменились входные данные (все 4 конфигурации батареи);
4. записывает новые результаты в базу, и дашборд сразу их показывает;
5. обновляет `inbox/MISSING_MANUAL_DATA.txt` со списком того, что нужно докачать вручную.

## Источники данных

| Рынок | Источник | Как обновляется |
|---|---|---|
| DA | OKTE API `isot.okte.sk/api/v1/dam/results` | автоматически |
| IDM15, IDM60 | OKTE API `isot.okte.sk/api/v1/idm/results` | автоматически |
| IMB | OKTE API `iszo.okte.sk/api/v1/SystemImbalance` | автоматически, см. ниже |
| aFRR Capacity | ENTSO-E API (нужен `ENTSOE_API_KEY` в `.env`) | автоматически |
| **aFRR Energy** | **SEPS/Damas, отчёт «Regulačná elektrina (denná)», XLSX** | **вручную** |
| FCR | нет стабильного источника | не обновляется |

**IMB.** OKTE публикует несколько оценок: `daily` (предварительная, на следующий день), затем `monthly` и `final` (примерно через 2–3 месяца после конца месяца). Updater берёт лучшую доступную оценку и последние `--lookback` дней (по умолчанию 120) перекачивает, пока не появится `final`. Пересчитываются только дни, где цены действительно изменились. Статус по дням хранится в `marketdata/IMB/_evaluation_status.json`.

## Что докачивать вручную: aFRR Energy

1. Скачай из SEPS/Damas отчёт **«Regulačná elektrina (denná)»** в формате XLSX за недостающие дни. Список дней всегда есть в `inbox/MISSING_MANUAL_DATA.txt`. Это тот же отчёт, что лежит в `marketdata/SepsDamasEnergy/raw/`.
2. Положи файл(ы) в папку `inbox/` в корне проекта на сервере:
   ```bash
   scp regulacna_elektrina_2026_06_10.xlsx user@server:/srv/battery_revenue_index_sk/inbox/
   ```
3. Ничего больше делать не нужно. При следующем запуске (или сразу, вручную `docker compose run --rm updater`) файл импортируется и переносится в `inbox/processed/`, а aFRR за эти дни досчитывается и попадает на сайт.

Пока за день нет файла SEPS/Damas, рынок **aFRR за этот день не считается**: на графике его нет, а остальные рынки за этот день показываются.

## Установка на сервере

```bash
cd /srv/battery_revenue_index_sk
cp .env.example .env          # пароль базы и ENTSOE_API_KEY
mkdir -p inbox
docker compose up -d --build  # база + дашборд
docker compose run --rm updater python tools/daily_update.py --from 2026-03-02   # первый догон данных
crontab -e                    # добавить строку из deploy/crontab.example
```

Первый догон с `--from` считает все дни с указанной даты, поэтому может занять долго (минуты–часы, зависит от числа ядер). Дальше cron обрабатывает только новые и изменившиеся дни.

## Полезные команды

```bash
docker compose run --rm updater                       # запустить обновление вручную
docker compose run --rm updater python tools/daily_update.py --skip-download   # только пересчёт + база
tail -n 50 /var/log/battery_index_update.log          # лог cron
cat inbox/MISSING_MANUAL_DATA.txt                     # что докачать вручную
```

Данные (`marketdata`, `results`) лежат в томах Docker и не теряются при `git pull` и пересборке образа.
