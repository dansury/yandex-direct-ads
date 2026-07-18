# Yandex.Direct API v5 — быстрый референс

Что нужно знать модели, чтобы не дёргать лишние вызовы и не ловить валидацию.

## Endpoints
- Боевой: `https://api.direct.yandex.com/json/v5/<service>`
- Песочница: `https://api-sandbox.direct.yandex.com/json/v5/<service>`
- Отчёты: `.../json/v5/reports` (отдельный путь, TSV).

Все вызовы — POST, тело `{"method": "...", "params": {...}}`.
Заголовки: `Authorization: Bearer <token>`, `Accept-Language: ru`,
`Content-Type: application/json; charset=utf-8`, опц. `Client-Login` (агентство).

> `scripts/direct_api.py` инкапсулирует всё это. Используй `client.call(service, method, params)`.

## Деньги
В большинстве полей деньги — в **микро-единицах** (1 руб = 1 000 000).
Скрипты сами умножают/делят. Отчёты возвращают деньги в обычных единицах
(заголовок `returnMoneyInMicros: false`).

## Баллы (units)
Каждый вызов тратит баллы из суточной квоты. Остаток — в заголовке `Units`
(скрипты логируют как `client.last_units`). Код ошибки `152` = баллы кончились.
Минимизируй вызовы: батчи (до 1000–10000 объектов за раз), отчёты вместо опросов.

## Ключевые сервисы и методы

| Сервис | Методы | Назначение |
|--------|--------|-----------|
| `campaigns` | add, update, get, suspend, resume, archive, delete | Кампании, стратегии, бюджет |
| `adgroups` | add, update, get, delete | Группы, регионы, минус-слова группы |
| `ads` | add, update, get, moderate, suspend, resume | Объявления |
| `keywords` | add, update, get, suspend, resume, delete | Ключевые фразы |
| `keywordbids` | set, setAuto, get | Ставки по ключам |
| `sitelinks` | add, get | Наборы быстрых ссылок |
| `adextensions` | add, get | Уточнения (callouts) |
| `dictionaries` | get | Справочники: регионы, валюты и т.п. |
| `audiencetargets` | add, get | Ретаргетинг/аудитории |
| `changes` | check | Что изменилось с момента T (для синка) |

## Структура объекта (текстовая кампания)
```
Campaign (TextCampaign)
└── AdGroup (RegionIds)
    ├── Keyword[]            # фразы + минус-слова через '-'
    ├── Ad (TextAd)[]        # заголовки/текст/ссылки
    └── NegativeKeywords     # минус-слова уровня группы
```

## Лимиты объектов (частые)
- Keyword: ≤ 4096 символов, ≤ 7 слов (без операторов).
- Объявлений в группе: до 50.
- Ключей в группе: до 200.
- Минус-слов на группу/кампанию: ограничение по суммарной длине.
- Быстрых ссылок в наборе: до 8. Уточнений: до 4 на показ.

(Лимиты символов текстов объявлений — в `ad-copy-rules.md`.)

## Стратегии (BiddingStrategy)
Задаётся отдельно для `Search` и `Network`:
- `HIGHEST_POSITION` — ручное управление на поиске (ставим ставки сами).
- `AVERAGE_CPA` — автостратегия по целевому CPA (нужен `GoalId`, `AverageCpa` в микро).
- `AVERAGE_ROI` — автостратегия по ROI (нужен `GoalId`, `Roi`, `ReserveReturn`).
- `WB_MAXIMUM_CLICKS` / `WB_MAXIMUM_CONVERSION_RATE` — недельный бюджет/пакетные.
- `NETWORK_DEFAULT` / `MAXIMUM_COVERAGE` — для РСЯ (Network).

`scripts/campaigns.py` собирает эти блоки: `--strategy manual|auto_cpa|auto_roi`.

## Отчёты (Reports)
ReportType, который используем:
- `CAMPAIGN_PERFORMANCE_REPORT`
- `ADGROUP_PERFORMANCE_REPORT`
- `CRITERIA_PERFORMANCE_REPORT` (по ключам — основа оптимизации)
- `AD_PERFORMANCE_REPORT`
- `SEARCH_QUERY_PERFORMANCE_REPORT` (поисковые запросы → минус-слова)

Поля: `Impressions, Clicks, Ctr, Cost, AvgCpc, Conversions, CostPerConversion`,
плюс id'шники. Конверсии считаются по `Goals` + `AttributionModels` (напр. `LSC`).
Отчёт асинхронный: HTTP 201/202 = «готовится», поллинг по `retryIn`
(`direct_api.report()` делает это сам).

## Частые коды ошибок
| Код | Смысл | Что делать |
|-----|-------|-----------|
| 8000 | Невалидный токен | Перевыпустить (setup-guide) |
| 8800 | Токен другого приложения | Перевыпустить под нужный ClientID |
| 53 | Нет прав на объект | Проверить Client-Login / агентский доступ |
| 58 | Нет доступа к API | Подать заявку на боевой API |
| 152 | Кончились баллы | Подождать сброса / увеличить квоту |
| 1000+ | Валидация | Проверить лимиты полей |

## Дока
Официально: https://yandex.ru/dev/direct/doc/dg/concepts/about.html
