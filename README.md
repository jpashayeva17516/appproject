# appproject

## ESP32 Device Registry

Отдельное приложение: регистрация ESP32-плат по регионам и сбор heartbeat
(онлайн/оффлайн, IP, время последней связи). Не связано с `cc2` — это
самостоятельный сервер + прошивка.

## Как это работает

1. При старте плата шлёт `POST /api/v1/devices/register` один раз, с
   `device_id` и `region`. Повторные регистрации безопасны (upsert).
2. Дальше плата раз в `HEARTBEAT_INTERVAL_MS` шлёт
   `POST /api/v1/devices/{device_id}/heartbeat`.
3. Сервер считает устройство `online`, если heartbeat пришёл не позже
   `HEARTBEAT_TIMEOUT_S` (90 с по умолчанию, `server/app.py`), иначе — `offline`.
4. Дашборд на `http://<сервер>:8000/` показывает устройства, сгруппированные
   по региону, автообновление каждые 5 секунд.

## Запуск сервера

```
cd server
python -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
uvicorn app:app --host 0.0.0.0 --port 8000
```

Откройте `http://<ip сервера>:8000/`. База — файл `server/devices.db`
(SQLite), создаётся автоматически при старте.

## API

| Метод | Путь | Тело | Назначение |
|---|---|---|---|
| POST | `/api/v1/devices/register` | `{device_id, region, name?}` | зарегистрировать/обновить устройство |
| POST | `/api/v1/devices/{device_id}/heartbeat` | `{ip?, fw_version?}` | отметить, что устройство живо |
| GET | `/api/v1/devices?region=...` | — | список устройств (со статусом) |
| GET | `/api/v1/regions` | — | сводка online/total по регионам |

## Логи устройств

Платы шлют логи на сервер, те сохраняются в SQLite (таблица `logs`, файл `server/devices.db`).
На дашборде вкладка **Логи**: фильтры по региону / устройству / уровню, поиск по тексту,
live-режим (обновление каждые 3 с), подгрузка старых записей, экспорт в CSV.
Клик по Device ID на вкладке «Устройства» открывает логи этой платы.

| Метод | Путь | Тело / параметры | Назначение |
| ----- | ---- | ---------------- | ---------- |
| POST | `/api/v1/devices/{device_id}/logs` | `{"logs":[{"level","message","uptime_ms?"}]}` (до 100 шт.) | записать пачку логов |
| GET | `/api/v1/logs` | `device_id, region, level, q, before_id, after_id, limit` | читать логи (новые первыми) |
| GET | `/api/v1/logs/export.csv` | те же фильтры | выгрузка в CSV |

Уровни: `debug` < `info` < `warn` < `error` (`level=warn` вернёт warn и error).

Переменные окружения сервера:

- `LOG_RETENTION_DAYS` — сколько дней хранить логи (по умолчанию 30, `0` — вечно)
- `REGISTRY_API_KEY` — если задан, платы обязаны слать заголовок `X-API-Key` (укажите тот же ключ в `API_KEY` в `config.h`)

```
REGISTRY_API_KEY=секрет LOG_RETENTION_DAYS=60 uvicorn app:app --host 0.0.0.0 --port 8000
```

Проверка API без плат: `cd server && pip install httpx && python test_smoke.py`.

## Прошивка (`firmware/esp32_heartbeat`)

Откройте `config.h` и укажите:

- `WIFI_SSID` / `WIFI_PASSWORD`
- `SERVER_HOST` / `SERVER_PORT` — где крутится сервер
- `DEVICE_ID` — уникальный для **каждой** платы (например `ESP32-BAKU-01`)
- `DEVICE_REGION` — регион/площадка, под которым плата будет видна в реестре
- `DEVICE_NAME` — необязательное человекочитаемое имя

В своём коде вызывайте `logf("info", "температура %.1f", t);` — сообщение уходит в Serial и
в буфер, раз в 5 с буфер отправляется на сервер (при обрыве связи логи копятся и досылаются).

Залейте через Arduino IDE (библиотека `WiFi.h`/`HTTPClient.h` входит в
ESP32-ядро) или PlatformIO. В логе должно быть:

```
=== ESP32-BAKU-01 (регион: baku) ===
[wifi] подключено, IP: 192.168.1.87
[register] код ответа: 200
[heartbeat] ok
```

Чтобы добавить новую плату — скопируйте `config.h`, смените `DEVICE_ID`
(и при необходимости `DEVICE_REGION`), залейте на новую плату. Регионы не
нужно создавать заранее — они появляются в реестре сами, как только придёт
первая регистрация с новым значением `region`.

## Что можно добавить дальше

- HTTPS + ключ устройства (как в `cc2`) вместо открытого HTTP
- Показания датчиков в теле heartbeat, если понадобится не только статус
- Алерты (email/Telegram) при уходе устройства в offline
