# API внешних источников задач

API предназначен для скриптов и адаптеров, которые самостоятельно разбирают Jira,
файлы, базы данных или другие системы и передают в Секретарь уже сформированные
задачи. Qwen для таких задач не вызывается.

Доступ к API защищён тем же mTLS, что Android-приложение и веб-панель. Клиенту
нужны сертификат и закрытый ключ, выпущенные доверенной клиентской CA сервера.
Интерактивная схема доступна в `/docs`.

## 1. Регистрация источника

```http
PUT /api/v1/external-task-sources/{source_id}
Content-Type: application/json

{
  "label": "Jira Architecture",
  "enabled": true,
  "tag_ids": []
}
```

`source_id` содержит латинские буквы, цифры, `_` и `-`, максимум 128 символов.
Повторный `PUT` обновляет источник. Источник также виден в веб-панели и удаляется
там вместе с импортированными задачами.

Пример:

```bash
curl --cert client.pem --key client.key \
  -X PUT https://secretary.example.test/api/v1/external-task-sources/jira-architecture \
  -H 'Content-Type: application/json' \
  -d '{"label":"Jira Architecture","enabled":true,"tag_ids":[]}'
```

## 2. Идемпотентная загрузка задач

```http
POST /api/v1/external-task-sources/{source_id}/tasks:batch
Content-Type: application/json

{
  "tasks": [
    {
      "external_id": "ARCH-142",
      "title": "Согласовать целевую архитектуру",
      "description": "Проверить схему и оставить решение в Jira",
      "status": "NEW",
      "priority": "HIGH",
      "due_at": "2026-09-18T18:00:00+03:00",
      "source_url": "https://jira.example.test/browse/ARCH-142",
      "evidence": "Статус и исполнитель получены из Jira",
      "occurred_at": "2026-09-14T10:00:00+03:00",
      "source_updated_at": "2026-09-14T12:30:00+03:00"
    }
  ],
  "close_missing": false
}
```

Допустимые статусы: `NEEDS_CONFIRMATION`, `NEW`, `IN_PROGRESS`,
`POSSIBLY_COMPLETED`, `COMPLETED`, `CANCELLED`. Приоритеты: `LOW`, `NORMAL`,
`HIGH`, `CRITICAL`. Все даты передаются в RFC 3339 с часовым поясом.

Пара `source_id + external_id` уникальна. Повторная отправка тех же данных не
создаёт дубль и возвращает `action: "unchanged"`. В ответе для каждой записи есть
текущий статус задачи и `active`. Активными считаются все статусы, кроме
`COMPLETED` и `CANCELLED`.

```json
{
  "source_id": "jira-architecture",
  "created": 0,
  "updated": 0,
  "unchanged": 1,
  "closed_missing": 0,
  "items": [
    {
      "external_id": "ARCH-142",
      "task_id": "00000000-0000-0000-0000-000000000000",
      "action": "unchanged",
      "status": "NEW",
      "active": true
    }
  ]
}
```

Если пользователь завершил задачу локально, повтор тех же исходных данных вернёт
`active: false` и не откроет её снова. Изменившаяся задача обновляется, когда
изменились поля запроса или `source_updated_at`. Более старая ревизия игнорируется.

`close_missing: true` означает полный снимок источника: активные задачи этого
источника, отсутствующие в переданной пачке, получают статус `CANCELLED`. Значение
по умолчанию `false`, поэтому обычная частичная загрузка ничего не закрывает.

## 3. Пример адаптера на Python

```python
import requests

BASE_URL = "https://secretary.example.test/api/v1"
SOURCE_ID = "jira-architecture"
CLIENT_CERT = ("client.pem", "client.key")

parsed_tasks = [
    {
        "external_id": "ARCH-142",
        "title": "Согласовать целевую архитектуру",
        "status": "NEW",
        "priority": "HIGH",
        "source_url": "https://jira.example.test/browse/ARCH-142",
        "source_updated_at": "2026-09-14T12:30:00+03:00",
    }
]

requests.put(
    f"{BASE_URL}/external-task-sources/{SOURCE_ID}",
    json={"label": "Jira Architecture", "enabled": True, "tag_ids": []},
    cert=CLIENT_CERT,
    timeout=30,
).raise_for_status()

response = requests.post(
    f"{BASE_URL}/external-task-sources/{SOURCE_ID}/tasks:batch",
    json={"tasks": parsed_tasks, "close_missing": False},
    cert=CLIENT_CERT,
    timeout=30,
)
response.raise_for_status()

for item in response.json()["items"]:
    print(item["external_id"], item["action"], item["status"], item["active"])
```

Новые активные задачи отправляют обычный технический push-сигнал, попадают в
ранжирование и план дня. В карточке сохраняются название внешнего источника,
описание и ссылка на оригинал.
