# Playwright Browser Worker API

Dockerized сервис для управления Chromium через Playwright на Ubuntu Server. Контейнер запускает HTTP API, а браузерная сессия живёт внутри сервиса до явного закрытия. Это удобно для ручного пошагового управления: открыть сайт, пройти авторизацию, подождать нужный экран, нажать кнопку, перейти дальше и только потом запускать поиск.

## Что изменилось

Сервис больше не работает как одноразовый script-runner по умолчанию. Теперь основной режим такой:

1. Поднимается контейнер с API.
2. Вы создаёте браузерную сессию через HTTP.
3. Отправляете команды в нужный момент.
4. Сессия не закрывается сама, пока вы её явно не завершите.

## Быстрый старт

1. Создайте `.env`:

```bash
cp .env.example .env
```

2. Запустите контейнер:

```bash
docker compose up --build
```

3. Проверьте API:

```powershell
Invoke-RestMethod http://localhost:8000/health
```

## Интеграция с Laravel и доменом

Если скриншоты и другие артефакты нужно открывать прямо с домена Laravel-приложения, а не скачивать с сервера вручную, закладывайте это сразу через volume и публичный base URL.

Рекомендуемая схема:

1. Монтировать `/app/output` в Laravel storage:

```text
<laravel-project>/storage/app/public/browser-worker
```

2. В Laravel выполнить:

```bash
php artisan storage:link
```

3. В `.env` сервиса указать:

```dotenv
OUTPUT_DIR=/app/output
ARTIFACT_PUBLIC_BASE_URL=https://example.com/storage/browser-worker
```

Тогда API начнёт возвращать не только внутренние пути артефактов, но и публичные ссылки вида:

```text
https://example.com/storage/browser-worker/<run_timestamp>/<file>
```

Это проще и надёжнее, чем пытаться отдельно скачивать скрины с контейнера.

## Запуск сессии

Старт браузера и открытие сайта:

```powershell
Invoke-RestMethod `
  -Method Post `
  -Uri http://localhost:8000/session/start `
  -ContentType "application/json" `
  -Body '{"target_url":"https://example.org","restart_if_running":true}'
```

Текущее состояние сессии:

```powershell
Invoke-RestMethod http://localhost:8000/session/state
```

Ответ покажет:

- активна ли сессия;
- текущий URL;
- заголовок страницы;
- папку артефактов;
- последнее действие;
- последнюю ошибку.

## Авторизация по шагам

### 1. Ввести телефон

Если селекторы уже лежат в `.env`, можно использовать дефолты:

```powershell
Invoke-RestMethod `
  -Method Post `
  -Uri http://localhost:8000/actions/auth/phone `
  -ContentType "application/json" `
  -Body '{}'
```

Или передать значения прямо в запросе:

```powershell
Invoke-RestMethod `
  -Method Post `
  -Uri http://localhost:8000/actions/auth/phone `
  -ContentType "application/json" `
  -Body '{"phone":"79990000000","selector":"input[name=''phone'']","submit_selector":"button[type=''submit'']"}'
```

### 2. Ввести код подтверждения

```powershell
Invoke-RestMethod `
  -Method Post `
  -Uri http://localhost:8000/actions/auth/code `
  -ContentType "application/json" `
  -Body '{"code":"123456"}'
```

Если нужно, можно передать свои селекторы:

```powershell
Invoke-RestMethod `
  -Method Post `
  -Uri http://localhost:8000/actions/auth/code `
  -ContentType "application/json" `
  -Body '{"code":"123456","selector":"input[name=''code'']","submit_selector":"button[type=''submit'']","wait_for_networkidle":true,"extra_wait_ms":5000}'
```

## Ожидание загрузки после авторизации

Если после логина вы хотите просто дождаться полной загрузки текущей страницы:

```powershell
Invoke-RestMethod `
  -Method Post `
  -Uri http://localhost:8000/actions/wait `
  -ContentType "application/json" `
  -Body '{"wait_for_networkidle":true,"extra_wait_ms":5000,"snapshot_name":"after_manual_wait"}'
```

## Поиск кнопки, внутри которой есть div с текстом

Найти кнопку:

```powershell
Invoke-RestMethod `
  -Method Post `
  -Uri http://localhost:8000/actions/find-button-by-div-text `
  -ContentType "application/json" `
  -Body '{"div_text":"ООО \"КАСТОМ КРАФТ\"","click":false,"timeout_ms":60000}'
```

Найти и сразу нажать:

```powershell
Invoke-RestMethod `
  -Method Post `
  -Uri http://localhost:8000/actions/find-button-by-div-text `
  -ContentType "application/json" `
  -Body '{"div_text":"ООО \"КАСТОМ КРАФТ\"","click":true,"timeout_ms":60000,"post_click_wait_ms":3000}'
```

Если элемент не найден, API вернёт ошибку, а в артефактах появятся:

- скриншот текущего экрана;
- HTML;
- текст страницы.

Дополнительно в ошибке теперь возвращается:

- текущий URL;
- число `div`, которые совпали по тексту;
- сам текст, который искали.

## Ручные live-команды

### Перейти на URL

```powershell
Invoke-RestMethod `
  -Method Post `
  -Uri http://localhost:8000/actions/navigate `
  -ContentType "application/json" `
  -Body '{"url":"https://example.org/dashboard","wait_for_networkidle":true,"extra_wait_ms":3000}'
```

### Заполнить поле

```powershell
Invoke-RestMethod `
  -Method Post `
  -Uri http://localhost:8000/actions/fill `
  -ContentType "application/json" `
  -Body '{"selector":"input[name=''query'']","value":"test value","action_name":"search input"}'
```

### Кликнуть по селектору

```powershell
Invoke-RestMethod `
  -Method Post `
  -Uri http://localhost:8000/actions/click `
  -ContentType "application/json" `
  -Body '{"selector":"button[type=''submit'']","action_name":"submit search","wait_for_networkidle":false,"extra_wait_ms":2000}'
```

### Нажать клавишу на элементе

```powershell
Invoke-RestMethod `
  -Method Post `
  -Uri http://localhost:8000/actions/press `
  -ContentType "application/json" `
  -Body '{"selector":"input[name=''query'']","key":"Enter","action_name":"submit by enter"}'
```

### Поиск текста на текущей странице

```powershell
Invoke-RestMethod `
  -Method Post `
  -Uri http://localhost:8000/actions/search-text `
  -ContentType "application/json" `
  -Body '{"text":"Нужное значение"}'
```

### Сохранить ручной snapshot

```powershell
Invoke-RestMethod `
  -Method Post `
  -Uri http://localhost:8000/actions/snapshot `
  -ContentType "application/json" `
  -Body '{"name":"manual_checkpoint","save_html":true,"save_text":true}'
```

## Как повторить выполнение, если контейнер уже работает

Теперь повторный запуск делается не через перезапуск контейнера, а через API.

Если браузер уже открыт и нужно понять, где вы находитесь:

```powershell
Invoke-RestMethod http://localhost:8000/session/state
```

Если нужно начать заново и сбросить текущую вкладку/контекст:

```powershell
Invoke-RestMethod `
  -Method Post `
  -Uri http://localhost:8000/session/start `
  -ContentType "application/json" `
  -Body '{"target_url":"https://example.org","restart_if_running":true}'
```

Если нужно просто продолжить текущую живую сессию, ничего перезапускать не надо. Просто отправляйте следующие команды.

## Как закрыть сессию

Закрыть только браузерную сессию, оставив API-контейнер жить:

```powershell
Invoke-RestMethod `
  -Method Post `
  -Uri http://localhost:8000/session/close
```

Полностью остановить контейнер:

```bash
docker compose down
```

## Артефакты

Все артефакты по-прежнему пишутся в:

```text
output/<run_timestamp>/
```

Там будут:

- application log;
- browser console log;
- network log;
- error log;
- screenshots;
- HTML dumps;
- text dumps;
- JSON results;
- storage state;
- Playwright trace.

## Главные env-переменные

### Базовые

- `TARGET_URL`
- `HEADLESS`
- `BROWSER_TIMEOUT_MS`
- `EXTRA_WAIT_MS`
- `OUTPUT_DIR`
- `LOG_LEVEL`
- `API_HOST`
- `API_PORT`
- `ARTIFACT_PUBLIC_BASE_URL`

### Авторизация

- `AUTH_PHONE`
- `AUTH_PHONE_SELECTOR`
- `AUTH_PHONE_SUBMIT_SELECTOR`
- `AUTH_CODE_SELECTOR`
- `AUTH_CODE_SUBMIT_SELECTOR`
- `AUTH_WAIT_AFTER_PHONE_SUBMIT_MS`
- `AUTH_WAIT_AFTER_CODE_SUBMIT_MS`

### Поиск кнопки

- `TARGET_BUTTON_DIV_TEXT`
- `TARGET_BUTTON_TIMEOUT_MS`
- `CLICK_TARGET_BUTTON`
- `TARGET_BUTTON_CLICK_WAIT_MS`

## Что делать с текущим timeout по кнопке

Ваш прошлый traceback означал:

- страница уже жива;
- браузер не умер;
- просто не нашлась нужная кнопка по заданному условию.

Теперь для диагностики правильный порядок такой:

1. `GET /session/state` чтобы понять текущий URL.
2. `POST /actions/wait` если экран ещё догружается.
3. `POST /actions/snapshot` чтобы сохранить ручной checkpoint.
4. Повторить `POST /actions/find-button-by-div-text`.
5. Если снова timeout, смотреть HTML/text dump из свежей папки артефактов.

Это уже значительно лучше одноразового сценария, потому что не надо терять сессию после каждой ошибки.
