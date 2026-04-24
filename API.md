# Browser Worker API

HTTP API для управления живой браузерной сессией Playwright внутри Docker-контейнера.

## Base URL

```text
http://localhost:8000
```

## Content Type

Для `POST`-запросов используйте:

```http
Content-Type: application/json
```

## Общая модель работы

1. Поднять контейнер с API.
2. Создать браузерную сессию через `POST /session/start`.
3. Отправлять отдельные команды по мере необходимости.
4. После каждого запроса API выполняет ровно одно действие и снова ждёт следующую команду.
5. Закрыть сессию через `POST /session/close` или остановить контейнер.

## Общие ответы

### Session State

Многие методы возвращают текущее состояние сессии:

```json
{
  "session_active": true,
  "current_url": "https://example.org/dashboard",
  "page_title": "Dashboard",
  "run_timestamp": "20260422_194512_123",
  "run_directory": "/app/output/20260422_194512_123",
  "last_action": "navigate",
  "last_error": null,
  "last_artifacts": {
    "screenshot": {
      "path": "/app/output/20260422_194512_123/001_after_navigate_20260422_194520_111.png",
      "url": "https://example.com/storage/browser-worker/20260422_194512_123/001_after_navigate_20260422_194520_111.png"
    }
  },
  "session_uptime_ms": 18234
}
```

### Ошибка

При ошибке API возвращает `HTTP 400`:

```json
{
  "detail": "Target button not found within timeout. div_text='ООО \"КАСТОМ КРАФТ\"' current_url=https://example.org/dashboard matching_div_count=0"
}
```

## Health

### `GET /health`

Проверка, что API запущен.

#### Response

```json
{
  "status": "ok",
  "api_host": "0.0.0.0",
  "api_port": 8000,
  "session": {
    "session_active": false,
    "current_url": null,
    "page_title": null,
    "run_timestamp": null,
    "run_directory": null,
    "last_action": null,
    "last_error": null,
    "session_uptime_ms": null
  }
}
```

## Session Management

### `GET /session/state`

Возвращает текущее состояние живой браузерной сессии.

#### Response

См. `Session State`.

### `POST /session/start`

Создаёт новую браузерную сессию. Если сессия уже существует, можно вернуть текущую или принудительно её перезапустить.

#### Request Body

```json
{
  "target_url": "https://example.org",
  "restart_if_running": true
}
```

#### Fields

- `target_url` `string | null`: URL, который нужно открыть после старта браузера.
- `restart_if_running` `boolean`: если `true`, существующая сессия будет закрыта и создана заново.

#### Response

См. `Session State`.

#### Example

```powershell
Invoke-RestMethod `
  -Method Post `
  -Uri http://localhost:8000/session/start `
  -ContentType "application/json" `
  -Body '{"target_url":"https://example.org","restart_if_running":true}'
```

### `POST /session/close`

Закрывает браузер, контекст, Playwright runtime и виртуальный дисплей, если он был запущен.

#### Response

См. `Session State`.

## Navigation And Waiting

### `POST /actions/navigate`

Открывает указанный URL в текущей сессии.

#### Request Body

```json
{
  "url": "https://example.org/dashboard",
  "wait_for_networkidle": true,
  "extra_wait_ms": 3000
}
```

#### Fields

- `url` `string`: URL для перехода.
- `wait_for_networkidle` `boolean`: ждать ли `networkidle` после перехода.
- `extra_wait_ms` `integer | null`: дополнительная пауза после загрузки.

#### Response

См. `Session State`.

### `POST /actions/wait`

Явное ожидание загрузки текущей страницы.

#### Request Body

```json
{
  "wait_for_networkidle": true,
  "extra_wait_ms": 5000,
  "timeout_ms": 60000,
  "snapshot_name": "after_manual_wait"
}
```

#### Fields

- `wait_for_networkidle` `boolean`: ждать ли `networkidle`.
- `extra_wait_ms` `integer`: дополнительная пауза.
- `timeout_ms` `integer | null`: timeout на ожидание.
- `snapshot_name` `string`: имя snapshot-артефакта.

#### Response

См. `Session State`.

## Generic UI Actions

### `POST /actions/fill`

Заполняет поле по CSS-селектору.

#### Request Body

```json
{
  "selector": "input[name='query']",
  "value": "test value",
  "action_name": "search input"
}
```

#### Fields

- `selector` `string`: CSS-селектор элемента.
- `value` `string`: значение для ввода.
- `action_name` `string`: имя действия для логов и артефактов.

#### Response

См. `Session State`.

### `POST /actions/click`

Кликает по элементу по CSS-селектору.

#### Request Body

```json
{
  "selector": "button[type='submit']",
  "action_name": "submit form",
  "wait_for_networkidle": false,
  "extra_wait_ms": 2000
}
```

#### Fields

- `selector` `string`: CSS-селектор элемента.
- `action_name` `string`: имя действия для логов и артефактов.
- `wait_for_networkidle` `boolean`: ждать ли `networkidle` после клика.
- `extra_wait_ms` `integer`: дополнительная пауза после клика.

#### Response

См. `Session State`.

### `POST /actions/press`

Нажимает клавишу на элементе.

#### Request Body

```json
{
  "selector": "input[name='query']",
  "key": "Enter",
  "action_name": "submit by enter"
}
```

#### Fields

- `selector` `string`: CSS-селектор элемента.
- `key` `string`: клавиша, например `Enter`, `Tab`, `ArrowDown`.
- `action_name` `string`: имя действия для логов и артефактов.

#### Response

См. `Session State`.

## Authorization Actions

### `POST /actions/auth/phone`

Вводит номер телефона и подтверждает отправку.

#### Request Body

```json
{
  "phone": "79990000000",
  "selector": "input[name='phone']",
  "submit_selector": "button[type='submit']",
  "wait_ms": 3000
}
```

#### Fields

- `phone` `string | null`: номер телефона. Если `null`, используется `AUTH_PHONE` из `.env`.
- `selector` `string | null`: селектор поля телефона.
- `submit_selector` `string | null`: селектор кнопки отправки. Если `null`, используется `Enter`.
- `wait_ms` `integer | null`: дополнительная пауза после отправки.

#### Response

См. `Session State`.

### `POST /actions/auth/code`

Вводит код подтверждения и подтверждает вход.

#### Request Body

```json
{
  "code": "123456",
  "selector": "input[name='code']",
  "submit_selector": "button[type='submit']",
  "wait_for_networkidle": true,
  "extra_wait_ms": 5000
}
```

#### Fields

- `code` `string`: код подтверждения.
- `selector` `string | null`: селектор поля кода.
- `submit_selector` `string | null`: селектор кнопки отправки. Если `null`, используется `Enter`.
- `wait_for_networkidle` `boolean`: ждать ли `networkidle` после отправки.
- `extra_wait_ms` `integer | null`: дополнительная пауза после отправки.

#### Response

См. `Session State`.

## Targeted UI Actions

### `POST /actions/find-button-by-div-text`

Ищет `button`, внутри которого есть `div` с указанным текстом. Может только найти или найти и кликнуть.

#### Request Body

```json
{
  "div_text": "ООО \"КАСТОМ КРАФТ\"",
  "click": false,
  "timeout_ms": 60000,
  "post_click_wait_ms": 3000
}
```

#### Fields

- `div_text` `string`: текст внутри вложенного `div`.
- `click` `boolean`: кликать ли по найденной кнопке.
- `timeout_ms` `integer | null`: timeout на поиск.
- `post_click_wait_ms` `integer | null`: ожидание после клика.

#### Response

```json
{
  "session_active": true,
  "current_url": "https://example.org/dashboard",
  "page_title": "Dashboard",
  "run_timestamp": "20260422_194512_123",
  "run_directory": "/app/output/20260422_194512_123",
  "last_action": "find_button_by_div_text",
  "last_error": null,
  "session_uptime_ms": 18234,
  "target_button_text": "ООО \"КАСТОМ КРАФТ\"",
  "target_button_clicked": false
}
```

### `POST /actions/hover-profile-menu-select`

Наводится на кнопку текущего кабинета внутри блока `ProfileView`, ждёт раскрытия соседнего меню, ищет нужный `li` по тексту и кликает на него.

#### Когда использовать

Для UI, где нужная кнопка/меню появляется только после `hover`.

#### Request Body

```json
{
  "profile_selector": ".ProfileView",
  "current_button_text": "Кабинет 1",
  "target_menu_text": "Кабинет 2",
  "timeout_ms": 60000,
  "hover_wait_ms": 500,
  "post_click_wait_ms": 3000
}
```

#### Fields

- `profile_selector` `string`: селектор контейнера профиля.
- `current_button_text` `string | null`: текст текущей кнопки кабинета. Если `null`, будет использована первая кнопка внутри `ProfileView`.
- `target_menu_text` `string`: текст нужного пункта в раскрывшемся меню.
- `timeout_ms` `integer | null`: timeout на поиск.
- `hover_wait_ms` `integer`: небольшая пауза после наведения, чтобы меню успело отрисоваться.
- `post_click_wait_ms` `integer`: пауза после клика по пункту меню.

#### Response

```json
{
  "session_active": true,
  "current_url": "https://example.org/dashboard",
  "page_title": "Dashboard",
  "run_timestamp": "20260422_194512_123",
  "run_directory": "/app/output/20260422_194512_123",
  "last_action": "hover_profile_menu_and_click_item",
  "last_error": null,
  "session_uptime_ms": 18234,
  "profile_selector": ".ProfileView",
  "current_button_text": "Кабинет 1",
  "target_menu_text": "Кабинет 2"
}
```

#### Example

```powershell
Invoke-RestMethod `
  -Method Post `
  -Uri http://localhost:8000/actions/hover-profile-menu-select `
  -ContentType "application/json" `
  -Body '{"profile_selector":".ProfileView","current_button_text":"Кабинет 1","target_menu_text":"Кабинет 2","timeout_ms":60000,"hover_wait_ms":500,"post_click_wait_ms":3000}'
```

### `POST /actions/click-anchor-by-span-text`

Ищет структуру вида `li -> div -> a -> div -> span[text]` и кликает на сам `a`.

#### Когда использовать

Для скрытых или вложенных пунктов навигации, где опорным текстом служит `span`.

#### Request Body

```json
{
  "text": "Нужный раздел",
  "scope_selector": ".SomeContainer",
  "timeout_ms": 60000,
  "post_click_wait_ms": 3000
}
```

#### Fields

- `text` `string`: текст `span`, который нужно найти.
- `scope_selector` `string | null`: необязательный ограничивающий контейнер для поиска.
- `timeout_ms` `integer | null`: timeout на поиск.
- `post_click_wait_ms` `integer`: пауза после клика.

#### Response

```json
{
  "session_active": true,
  "current_url": "https://example.org/section",
  "page_title": "Section",
  "run_timestamp": "20260422_194512_123",
  "run_directory": "/app/output/20260422_194512_123",
  "last_action": "click_anchor_by_span_text",
  "last_error": null,
  "session_uptime_ms": 18234,
  "clicked_span_text": "Нужный раздел",
  "scope_selector": ".SomeContainer"
}
```

#### Example

```powershell
Invoke-RestMethod `
  -Method Post `
  -Uri http://localhost:8000/actions/click-anchor-by-span-text `
  -ContentType "application/json" `
  -Body '{"text":"Нужный раздел","scope_selector":".SomeContainer","timeout_ms":60000,"post_click_wait_ms":3000}'
```

## Search And Snapshots

### `POST /actions/search-text`

Ищет текст на текущей странице без учёта регистра.

#### Request Body

```json
{
  "text": "Нужное значение"
}
```

#### Response

```json
{
  "search_text": "Нужное значение",
  "found": true,
  "matches_count": 2,
  "positions": [120, 820],
  "current_url": "https://example.org/page",
  "run_timestamp": "20260422_194512_123"
}
```

### `POST /actions/snapshot`

Сохраняет ручной snapshot: скриншот и, при необходимости, HTML и текст страницы.

#### Request Body

```json
{
  "name": "manual_checkpoint",
  "save_html": true,
  "save_text": true
}
```

#### Fields

- `name` `string`: имя snapshot.
- `save_html` `boolean | null`: сохранять ли HTML.
- `save_text` `boolean | null`: сохранять ли текст страницы.

#### Response

```json
{
  "screenshot": {
    "path": "/app/output/20260422_194512_123/012_manual_checkpoint_20260422_194700_111.png",
    "url": "https://example.com/storage/browser-worker/20260422_194512_123/012_manual_checkpoint_20260422_194700_111.png"
  },
  "html": {
    "path": "/app/output/20260422_194512_123/013_manual_checkpoint_source_20260422_194700_222.html",
    "url": "https://example.com/storage/browser-worker/20260422_194512_123/013_manual_checkpoint_source_20260422_194700_222.html"
  },
  "text": {
    "path": "/app/output/20260422_194512_123/014_manual_checkpoint_text_20260422_194700_333.txt",
    "url": "https://example.com/storage/browser-worker/20260422_194512_123/014_manual_checkpoint_text_20260422_194700_333.txt"
  }
}
```

## Публичные ссылки на артефакты

Если задать:

```dotenv
ARTIFACT_PUBLIC_BASE_URL=https://example.com/storage/browser-worker
```

API будет возвращать для каждого артефакта:

- `path`: путь внутри контейнера;
- `url`: публичную ссылку, которую можно открыть через домен.

### Рекомендуемая интеграция с Laravel

Самый удобный вариант:

1. Примонтировать `/app/output` в Laravel storage:

```text
<laravel-project>/storage/app/public/browser-worker
```

2. Убедиться, что в Laravel выполнено:

```bash
php artisan storage:link
```

3. Указать:

```dotenv
OUTPUT_DIR=/app/output
ARTIFACT_PUBLIC_BASE_URL=https://example.com/storage/browser-worker
```

Тогда каждый скриншот и HTML-дамп будет сразу доступен из Laravel по обычной публичной ссылке, без отдельного скачивания с сервера.

## Типичный сценарий работы

### 1. Старт сессии

```powershell
Invoke-RestMethod `
  -Method Post `
  -Uri http://localhost:8000/session/start `
  -ContentType "application/json" `
  -Body '{"target_url":"https://example.org","restart_if_running":true}'
```

### 2. Ввод телефона

```powershell
Invoke-RestMethod `
  -Method Post `
  -Uri http://localhost:8000/actions/auth/phone `
  -ContentType "application/json" `
  -Body '{}'
```

### 3. Ввод кода

```powershell
Invoke-RestMethod `
  -Method Post `
  -Uri http://localhost:8000/actions/auth/code `
  -ContentType "application/json" `
  -Body '{"code":"123456"}'
```

### 4. Смена кабинета через hover-меню

```powershell
Invoke-RestMethod `
  -Method Post `
  -Uri http://localhost:8000/actions/hover-profile-menu-select `
  -ContentType "application/json" `
  -Body '{"profile_selector":".ProfileView","current_button_text":"Кабинет 1","target_menu_text":"Кабинет 2"}'
```

### 5. Клик по нужному разделу по `span`

```powershell
Invoke-RestMethod `
  -Method Post `
  -Uri http://localhost:8000/actions/click-anchor-by-span-text `
  -ContentType "application/json" `
  -Body '{"text":"Нужный раздел"}'
```

### 6. Поиск текста

```powershell
Invoke-RestMethod `
  -Method Post `
  -Uri http://localhost:8000/actions/search-text `
  -ContentType "application/json" `
  -Body '{"text":"Нужное значение"}'
```

## Артефакты

Каждое значимое действие пишет артефакты в:

```text
/app/output/<run_timestamp>/
```

На хосте это соответствует:

```text
./output/<run_timestamp>/
```

Там будут:

- application logs
- console logs
- network logs
- error logs
- screenshots
- HTML dumps
- text dumps
- JSON search results
- storage state
- Playwright trace
