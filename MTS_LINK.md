# MTS Link: восстановленное API веб- и мобильного клиента

## Статус документа

Машиночитаемое описание находится в [`mts-link.openapi.yaml`](./mts-link.openapi.yaml).
Спецификация использует OpenAPI 3.1, содержит 41 операцию и 38 схем и проходит
проверку Redocly без ошибок.

Это результат анализа трафика, а не официальный контракт МТС Линк. Описаны
только операции, попавшие в две записи HAR. Реальные email, токены, device ID,
push-токены и идентификаторы пользователей из примеров удалены.

## Область исследования

В спецификацию включены first-party вызовы, связанные с:

- SAML/SSO-авторизацией;
- созданием веб- и мобильной сессии;
- профилем пользователя и организацией;
- конфигурацией приложения и feature flags;
- расписанием и постоянными комнатами;
- состояниями записей и расшифровок;
- содержимым расшифровки, резюме и задачами;
- регистрацией мобильного push-токена;
- проверкой RTC/SFU-соединения.

Не включены статика, CORS `OPTIONS`, браузерные сервисы Google, Firebase,
Sentry, Mixpanel, CarrotQuest, VK Analytics и другие сторонние системы.
WebSocket-соединения зафиксированы, но не описаны в OpenAPI, поскольку OpenAPI
не задаёт полноценный протокол обмена WebSocket-сообщениями.

## Основные серверы

| Сервер | Назначение |
| --- | --- |
| `https://gw.mts-link.ru` | Основной REST/RPC gateway, авторизация, встречи и расшифровки |
| `https://my.mts-link.ru` | SAML ACS bridge и web-конфигурация |
| `https://prod-gw-chat.mts-link.ru` | Получение feature flags чат-системы |
| `wss://prod-ws-chat.mts-link.ru` | WebSocket мобильного чата; в HAR соединение было без активной пользовательской сессии |
| `https://sfu.mts-link.ru` | RTC connectivity check и определение сетевого расположения клиента |
| `https://msg-edge-*.webinar.ru` | Realtime/message endpoint, адрес которого возвращает API мобильной сессии |
| `https://events-storage.webinar.ru` | Изображения и другие файлы событий |

## Авторизация

### Общая схема SSO

1. Клиент определяет SSO-организацию и доступный способ входа.
2. Клиент открывает `GET /sso/saml/login`.
3. Gateway перенаправляет браузер на корпоративный Identity Provider.
4. IdP отправляет `SAMLResponse` в `POST https://my.mts-link.ru/api/saml/acs`.
5. ACS выполняет `307` на `POST https://gw.mts-link.ru/sso/saml/return`, сохраняя тело формы.
6. Gateway возвращает `302` на клиентский callback с параметром `authCode`.
7. Клиент обменивает код через `POST /accountUcaas/AccountUcaas.LoginByAuthCode`.
8. В ответе приходят `accessToken` и `refreshToken`; сервер также устанавливает
   cookies `access` и `refresh`.

`authCode`, SAML assertion и оба токена необходимо считать секретами. Их нельзя
писать в application logs, crash reports или аналитику.

### Поиск SSO-организации

Веб-клиент использует:

```http
POST /ssoExternal/ExternalSSO.GetLoginOrganizationByHost
Content-Type: application/json

{"host":"tenant.mts-link.ru"}
```

Мобильное приложение использует:

```http
POST /ssoExternal/ExternalSSO.GetLoginOrganizationsByEmail
Content-Type: application/json

{"email":"user@example.com"}
```

Оба endpoint возвращают сведения об организации и методах входа в RPC-обёртке.
Внутри метода присутствует `connectionToken`, который затем передаётся как
query-параметр `token` в `/sso/saml/login`.

### Запуск SAML

Основные параметры `GET /sso/saml/login`:

| Параметр | Назначение |
| --- | --- |
| `email` | Email пользователя |
| `returnUrl` | Callback веб- или мобильного клиента |
| `token` | `connectionToken` выбранного SSO-метода |
| `params` | Дополнительное клиентское состояние в Base64 |

Для Android наблюдался callback:

```text
mtslink://mobile/login?authCode={authCode}
```

Значение `authCode` из callback точно совпало со значением, переданным далее в
`AccountUcaas.LoginByAuthCode`.

### Обмен `authCode`

```http
POST /accountUcaas/AccountUcaas.LoginByAuthCode
Content-Type: application/json

{"authCode":"<authorization-code>"}
```

Ответ имеет фактический media type `text/plain`, но содержит JSON:

```json
{
  "type": "Tokens",
  "value": {
    "accessToken": "<access-token>",
    "refreshToken": "<refresh-token>"
  }
}
```

Одновременно сервер устанавливает:

- `access`: `Domain=mts-link.ru; Path=/; Secure; SameSite=None`;
- `refresh`: `Domain=mts-link.ru; Path=/; HttpOnly; Secure; SameSite=None`.

У cookie `access` в захваченном ответе не было флага `HttpOnly`; у `refresh`
он был.

### Веб-авторизация

Веб-клиент авторизует последующие вызовы cookie `access`. Cookie `refresh`
предназначена для продления сессии, но сам endpoint обновления токена в HAR не
попал.

Проверка текущего пользователя:

```http
GET /api/login
Cookie: access=<access-token>
```

До входа этот endpoint вернул `404`, после успешного входа — профиль пользователя.

### Мобильная авторизация

Android-приложение не полагается на cookie для бизнес-вызовов. Оно копирует
`value.accessToken` из `LoginByAuthCode` без изменений в заголовок:

```http
Authorization: Bearer <access-token>
```

Совпадение токена подтверждено для всех захваченных авторизованных мобильных
вызовов:

- `AccountUcaas.GetLoginData`;
- `/api/mobile/users/sessions`;
- `/api/mobile/pushtoken`;
- `/api/mobile/installation` при вызове с авторизацией;
- `/api/mobile/eventsessions/schedule`;
- `/api/mobile/eventsessions/endless`.

Access-токен имеет формат JWT:

- алгоритм подписи: `RS256`;
- наблюдавшиеся claims: `cid`, `exp`, `iat`, `jti`, `oid`, `sso`, `sub`, `uid`;
- наблюдавшийся срок `exp - iat`: `302400` секунд, то есть 3,5 дня.

Этот срок отражает один захваченный токен и не должен быть захардкожен как
гарантированный контракт. Клиент должен ориентироваться на claim `exp`.

Refresh-токен непрозрачный, состоит не из JWT-сегментов и ни в одном исходящем
запросе HAR не использовался. Поэтому URL, тело и правила refresh-запроса пока
не определены. Также не были зафиксированы logout, отзыв токена и обработка
истёкшего access-токена.

### Мобильные служебные заголовки

Мобильные запросы дополнительно содержат:

| Заголовок | Содержание |
| --- | --- |
| `X-App-Version` | Версия и канал сборки приложения |
| `X-Device` | Модель устройства |
| `X-Device-Id` | Идентификатор устройства; в HAR — 16 hex-символов |
| `X-Os` | ОС и версия |
| `X-Platform` | Маркер платформы; наблюдались `Android` и `AndroidRoot` |

Эти заголовки дают контекст устройству, но не заменяют Bearer-аутентификацию.

## Bootstrap мобильного приложения

`GET /api/mobile/installation` вызывается до и после входа. Endpoint работал
как без `Authorization`, так и с Bearer-токеном. Ответы были идентичны и содержали:

- версию серверной конфигурации;
- список capabilities;
- `installationToken`;
- адреса API gateway, telemetry, blackhole, chats config и records delivery;
- флаги `isSaas`, `isEncrypted`, `isMobileUsePlatformAuthEnabled`.

В захваченном ответе `isMobileUsePlatformAuthEnabled` был `true`. Полученный
`installationToken` не был найден ни в одном последующем исходящем запросе,
поэтому его назначение и схема применения не установлены.

После авторизации приложение вызывает `GET /api/mobile/users/sessions`. Ответ
содержит:

- сокращённый профиль пользователя;
- organization ID;
- разрешения на встречи и личный кабинет;
- URL message/realtime-сервера;
- список realtime-каналов.

Затем `POST /api/mobile/pushtoken` регистрирует push-токен. В HAR использовались
`platform=android`, `provider=gms`, пустой `sessionId`; сервер ответил `201` и
вернул тот же push-токен. Push-токен также является чувствительным значением.

## Встречи

### Расписание веб-клиента

```http
GET /api/eventsessions/schedule
```

Ключевые query-параметры:

- `page`, `perPage`;
- `from`, `to` в формате date-time со смещением;
- `eventType[0]`, `eventType[1]`, `eventType[2]` — типы `meeting`, `webinar`, `training`.

Ответ — массив элементов расписания. Каждый элемент содержит дату, отношение
пользователя к событию и объект event session с ID, названием, временем,
статусом, типом, создателем и настройками доступа.

### Расписание мобильного клиента

```http
GET /api/mobile/eventsessions/schedule
Authorization: Bearer <access-token>
```

Мобильный клиент сериализует типы повторяющимся bracket-параметром:

```text
eventType[]=meeting&eventType[]=webinar&eventType[]=training
```

### Постоянные комнаты

Веб и мобильный клиенты используют соответственно:

- `GET /api/eventsessions/endless`;
- `GET /api/mobile/eventsessions/endless`.

Поддерживаются `page`, `perPage` и `filters[visibility][eq]`. Ответ содержит
event session, ссылку входа в комнату, количество участников, наличие файлов и
расшифровок, comet-каналы и папку записей. Мобильный вариант дополнительно
содержал `unreadCounters`.

### Дополнительные операции встреч

| Endpoint | Назначение |
| --- | --- |
| `GET /api/event-sessions/creators` | Список создателей для фильтра событий |
| `GET /api/users/current/default-event-session-for-integration` | Комната по умолчанию для интеграций |
| `GET /api/onboarding-v2/meetings_list` | Состояние onboarding списка встреч |

Операции создания, изменения, запуска и удаления встречи в HAR не попали.

## Записи и расшифровки

### Получение ID расшифровки

Сначала клиент получает список встреч. Затем он пакетно запрашивает состояние
расшифровок:

```http
GET /api/event-sessions/activity-sessions/transcript-states
```

Параметры сериализуются как индексированный массив:

```text
items[0][eventSessionId]=<event-session-id>
items[0][activitySessionId]=<activity-session-id>
items[1][eventSessionId]=<event-session-id>
```

`activitySessionId` может отсутствовать. Ответ содержит:

- `eventSessionId` и `activitySessionId`;
- `transcriptId`;
- `status` и `visibility`;
- `isDisabled` и `isPublished`;
- comet-канал изменения состояния.

Именно `transcriptId` используется во всех дальнейших запросах.

### Состояние записи

```http
GET /api/event-sessions/activity-sessions/record-states
```

Формат batch-параметров такой же. Ответ содержит `recordFileId`, состояние,
настройки доступа, возможность просмотра/запроса доступа и comet-каналы.

### Операции с расшифровкой

| Endpoint | Результат |
| --- | --- |
| `GET /api/transcript/{transcriptId}/details` | Название, владелец, организация, дата создания и visibility |
| `GET /api/transcript/{transcriptId}` | Реплики с автором, текстом и временем |
| `GET /api/transcript/{transcriptId}/summary` | AI-резюме и использованный шаблон |
| `GET /api/transcript/{transcriptId}/tasks` | Общие и назначенные пользователю задачи |
| `GET /api/transcript/{transcriptId}/summary/reaction` | Реакция пользователя на резюме |
| `GET /api/transcript/{transcriptId}/download` | Полный текст с media type `text/plain` |

Прямая API-ссылка скачивания формируется так:

```text
https://gw.mts-link.ru/api/transcript/{transcriptId}/download
```

Отдельного endpoint, возвращающего готовую URL пользовательской страницы
расшифровки, в HAR не обнаружено. Ссылка постоянной комнаты из
`/eventsessions/endless` не является ссылкой на расшифровку.

## Профиль, организация и конфигурация

| Endpoint | Назначение |
| --- | --- |
| `GET /api/login` | Полный профиль текущего веб-пользователя |
| `POST /accountUcaas/AccountUcaas.GetLoginData` | Основные ID и данные текущего логина; cookie для web, Bearer для mobile |
| `POST /httpMember/MemberService.GetMember` | Профиль участника организации |
| `GET /api/user/sso-transfer-organizations` | Организации, доступные для SSO transfer |
| `GET /api/login/options` | Способы входа и настройки tenant host |
| `GET /api/organizations/{organizationId}/brandings/default` | Брендинг организации |
| `GET /api/superbrandings` | Брендинг по host до входа |
| `POST /orgHttp/OrgHttp.GetProjectManagement` | Доступные проекты/продукты |
| `GET /api/users/{userId}/featureSettings` | Эффективные feature flags пользователя |
| `POST /Featurer.GetFeatures` | Feature flags через chat gateway |
| `GET /api/user/comet` | Realtime и streaming-конфигурация |
| `GET /api/user/{userId}/notifications` | Уведомления пользователя |
| `GET /api/users/qualifications` | Qualification/onboarding-ответы |
| `GET /api/user/activity/{activityName}` | Состояние UI/onboarding-маркера |
| `GET /api/user/tariff/request/{tariffCode}` | Состояние запроса тарифа или trial |

## RTC и сетевые проверки

`GET https://sfu.mts-link.ru/whoami` возвращает публичный IP и приблизительные
ASN/географические сведения. В исследованном сценарии авторизация не требовалась.

`POST https://sfu.mts-link.ru/rtc/conncheck/connect` обменивает SDP offer/answer
для проверки доступности SFU. Этот endpoint не описывает фактическую передачу
медиа внутри встречи.

## Особенности протокола

### RPC поверх HTTP

Ряд endpoint имеет имена вида `Service.Method`, например:

- `AccountUcaas.LoginByAuthCode`;
- `AccountUcaas.GetLoginData`;
- `ExternalSSO.GetLoginOrganizationsByEmail`;
- `MemberService.GetMember`.

Запросы обычно передают JSON, но ответы имеют `Content-Type: text/plain` и при
этом содержат JSON-обёртку:

```json
{
  "type": "ResultType",
  "value": {}
}
```

Клиенту нужно учитывать фактический media type и самостоятельно разбирать тело
как JSON, если используемая HTTP-библиотека не делает этого автоматически.

### Идентификаторы

В разных API одни и те же логические ID могут передаваться как JSON `integer`
или `string`. Не следует без проверки приводить все ID к 32-битному числу:
встречаются значения, требующие как минимум 64-битного типа.

### Даты и query-параметры

- Используются ISO-подобные date-time со смещениями `+0300`, `+03:00` и `+0000`.
- Веб и mobile по-разному сериализуют массив `eventType`.
- Batch-запросы состояний используют PHP-style bracket notation.
- Генератор клиента должен сохранять буквальные имена bracket-параметров из
  OpenAPI либо иметь специальный сериализатор.

## Минимальный мобильный сценарий

```text
GET  /api/mobile/installation
POST /ssoExternal/ExternalSSO.GetLoginOrganizationsByEmail
GET  /sso/saml/login
     -> IdP -> /api/saml/acs -> /sso/saml/return
     -> mtslink://mobile/login?authCode=...
POST /accountUcaas/AccountUcaas.LoginByAuthCode
POST /accountUcaas/AccountUcaas.GetLoginData      [Bearer]
GET  /api/mobile/users/sessions                   [Bearer]
POST /api/mobile/pushtoken                        [Bearer]
GET  /api/mobile/eventsessions/schedule           [Bearer]
GET  /api/mobile/eventsessions/endless            [Bearer]
```

## Минимальный сценарий получения расшифровки

```text
GET /api/eventsessions/schedule
GET /api/event-sessions/activity-sessions/transcript-states
    -> transcriptId
GET /api/transcript/{transcriptId}/details
GET /api/transcript/{transcriptId}
GET /api/transcript/{transcriptId}/summary
GET /api/transcript/{transcriptId}/tasks
GET /api/transcript/{transcriptId}/download
```

## Что ещё необходимо записать для полного контракта

В имеющихся HAR отсутствуют или не подтверждены:

- refresh access-токена;
- logout и отзыв сессии;
- обработка `401` после истечения JWT;
- создание, изменение и удаление встреч;
- вход непосредственно в комнату и media signaling;
- создание и публикация расшифровки;
- изменение visibility и reaction;
- пагинация расшифровки при объёме больше `perPage`;
- полный протокол WebSocket чата и comet-событий;
- поведение API для разных ролей и недостаточных прав;
- стабильные enum-значения статусов встреч, записей и расшифровок.

До появления соответствующих записей трафика эти операции не следует выводить
из имён существующих endpoint или считать совместимыми с публичным API продукта.
