# API состояния компонентов

API позволяет внешнему загрузчику сообщить, что он работает, занят, работает с
ограничениями или не может получить данные. Доступ защищён общим HTTPS/mTLS-шлюзом
Серкетаря. Один и тот же `PUT` обновляет текущую запись компонента.

## Передача heartbeat или ошибки

```http
PUT /api/v1/system/components/{component_id}
Content-Type: application/json

{
  "label": "Загрузчик TABS",
  "component_type": "external_loader",
  "status": "ERROR",
  "message": "Не удалось авторизоваться",
  "metrics": {
    "attempts": 3,
    "loaded_items": 0
  },
  "ttl_seconds": 300
}
```

`component_id` содержит латинские буквы, цифры, `_` и `-`, максимум 128 символов.
Допустимые состояния для отправки: `OK`, `BUSY`, `DEGRADED`, `ERROR`, `UNKNOWN`.
Допустимые типы: `external`, `external_loader`, `integration`.
`metrics` содержит до 100 числовых значений. Текст ошибки ограничен 2000 символами.

`observed_at` можно передать в RFC 3339 с часовым поясом. Без него сервер использует
текущее время. `ttl_seconds` задаёт срок актуальности от 30 секунд до суток. Когда
новый heartbeat не поступил до истечения TTL, агрегированный статус становится
`STALE`. Значение `null` отключает срок действия для редко меняющихся компонентов.
Более старый отчёт не перезаписывает уже полученный новый.

При успешном восстановлении загрузчик отправляет новое состояние:

```bash
curl --cert client.pem --key client.key \
  -X PUT https://secretary.example.test/api/v1/system/components/tabs-loader \
  -H 'Content-Type: application/json' \
  -d '{
    "label":"Загрузчик TABS",
    "component_type":"external_loader",
    "status":"OK",
    "metrics":{"loaded_items":42,"duration_seconds":8.4},
    "ttl_seconds":300
  }'
```

Не передавайте в `message` и `metrics` пароли, токены, содержимое писем или другие
секреты. Поле `message` предназначено для короткой причины, например ошибки
авторизации или сетевой недоступности.

## Общий снимок состояния

```http
GET /api/v1/system/status
```

Ответ объединяет внешние heartbeat с вычисляемыми состояниями:

- загрузчиков событий IMAP, Exchange и МТС Линк;
- Ollama и наличия настроенной модели;
- очередей анализа событий, чата и контекста встреч;
- основного worker;
- PostgreSQL-семафора Ollama, включая лимит, занятый слот и число ожидающих.

`overall_status` принимает значения `OK`, `BUSY`, `UNKNOWN`, `DEGRADED`, `STALE`
или `ERROR`. Android-клиент обновляет снимок автоматически и показывает это
состояние цветным индикатором в верхней панели на каждой вкладке.
