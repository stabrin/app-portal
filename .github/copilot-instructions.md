# Инструкции для Copilot по проекту app-portal

## Обзор проекта

**app-portal** — это многосервисная экосистема для работы с кодами DataMatrix, отслеживания табачных товаров, выполнения операций агрегации и управления настольными приложениями. Архитектура включает:
- **Веб-сервисы**: Flask-приложения, контейнеризованные с помощью Docker
- **Настольное приложение**: Клиент на базе PySide6 (Qt) для локальных операций с БД
- **Общая база данных**: PostgreSQL с поддержкой мультитенантности (главная БД портала + БД клиентов)
- **Слой кеша**: Redis для состояния сессий и отслеживания агрегации в реальном времени
- **Обратный прокси**: Nginx для маршрутизации к сервисам

### Основные приложения

1. **portal** — главная точка входа; хаб аутентификации пользователей
2. **datamatrix-app** — веб-интерфейс для обработки DataMatrix из файлов, многоуровневая агрегация и управление заказами
3. **manual-aggregation-app** — интерфейс для ручного сканирования в реальном времени с использованием Redis для состояния сессии
4. **dmkod-integration-app** — слой интеграции с внешним API
5. **desktop-app** — клиент Windows для административных задач (построен с помощью Nuitka)

## Критические паттерны архитектуры

### Инициализация сервиса: паттерн Application Factory

Все Flask-приложения используют паттерн фабрики (функция `create_app()`) в файле `__init__.py`:
```python
def create_app():
    app = Flask(__name__)
    app.config['SECRET_KEY'] = os.getenv('DMKOD_SECRET_KEY')
    login_manager.init_app(app)
    app.register_blueprint(main_blueprint)
    return app
```

**Зачем**: Обеспечивает гибкую конфигурацию, предотвращает циклические импорты и поддерживает развертывание через Gunicorn (`gunicorn "app:create_app()"`).

### Паттерны подключения к БД

**Веб-приложения (portal, datamatrix-app, manual-aggregation, dmkod-integration)**:
- Используют переменную окружения `DATABASE_URL`
- Вызывают `get_db_connection()` в `app/db.py` — возвращает необработанное соединение `psycopg2`
- Всегда оборачивают в try/finally для закрытия подключений: `if conn: conn.close()`

**Настольное приложение (desktop-app)**:
- Реализует пулирование соединений через `src/db_connector.py`
- Главная БД: `get_main_db_connection()` контекстный менеджер (пул потоков с SSL-сертификатом)
- БД клиентов: `get_client_db_connection(user_info)` динамически пулирует по клиентам с логикой fallback (SSL → без SSL)
- Логика fallback: сначала пытается SSL-подключение, затем отключает SSL при необходимости

**Критический момент**: Настольное приложение жестко кодирует учетные данные и хост в `db_connector.py` (строка 46+). Веб-приложения читают из `.env` через `load_dotenv()`.

### Паттерн Service Layer

Бизнес-логика извлекается в отдельные модули сервисов (не в роуты):
- `datamatrix-app/app/services/`: aggregation_service, product_service, view_service, admin_service, task_service
- `manual-aggregation-app/app/services/`: order_service, state_service (state machine на основе Redis)
- Каждый сервис импортирует `get_db_connection()` и управляет своим жизненным циклом курсора

Пример из `datamatrix-app/app/main.py`:
```python
from app.services.aggregation_service import run_aggregation_process
from app.db import get_db_connection

# Роут вызывает сервис, сервис получает подключение
result = run_aggregation_process(file_path, aggregation_mode)
```

### Redis для управления состоянием сессии

**manual-aggregation-app** использует Redis как механизм состояния для многошаговой агрегации:
- Класс `EmployeeStateManager` в `state_service.py` управляет состоянием с ключами `employee_state:{token_id}`
- Состояния: IDLE, AGGREGATING_SET, AGGREGATING_BOX, ASSIGNED_TO_PALLET, ASSIGNED_TO_CONTAINER
- Каждый HTTP-запрос читает/обновляет состояние Redis
- Исключает необходимость отправки форм между шагами
- `state_service.py` предоставляет интерфейс state machine с блокировками для предотвращения конфликтов

**Пример рабочего процесса**:
1. Пользователь начинает агрегацию → Redis хранит `{user_id: {current_box: X, scanned_items: []}}`
2. Пользователь сканирует код → HTTP-запрос получает состояние Redis, проверяет, обновляет, возвращает JSON
3. Состояние сохраняется при обновлении браузера

## Сборка и развертывание

### Рабочий процесс Docker Compose

Все сервисы указаны в `docker-compose.yml` (корень):
```bash
docker-compose up -d           # Запустить все сервисы с томами
docker-compose logs -f [service]  # Отслеживать логи сервиса
docker-compose down            # Остановить и удалить контейнеры
docker-compose down -v         # Также удалить тома (разрушительно!)
```

**Проверки здоровья**: Сервисы зависят от того, что `postgres` и `redis` становятся `healthy` перед запуском.

**Инициализация БД**:
- Каждое приложение имеет `init_db.py` или `init_ma_db.py`, которые создают схему при первом запуске
- Должны быть выполнены вручную после `docker-compose up` если необходимо: `docker exec [container] python init_db.py`

### Сборка настольного приложения (только Windows)

Использует **Nuitka** (не PyInstaller):
```bash
# Запустить задачу сборки из VS Code или терминала
python -m nuitka --standalone --mingw64 --enable-plugin=pyside6 \
  --windows-console-mode=disable --output-dir=build \
  --output-filename=TildaKod.exe \
  --include-package=pylibdmtx,jinja2,babel,psycopg2,PIL,pandas,requests,bcrypt,dotenv,barcode \
  --include-data-file=${workspaceFolder}/.venv/Lib/site-packages/pylibdmtx/libdmtx-64.dll=libdmtx-64.dll \
  --include-data-file=desktop-app/.env=.env \
  --include-data-file=desktop-app/msvcr120.dll=msvcr120.dll \
  --include-data-dir=secrets=secrets \
  desktop-app/run.py
```

**Критическое исправление в `desktop-app/run.py` (строки 6-35)**: 
- Обрабатывает разрешение пути DLL для режима IDE и скомпилированного exe
- Добавляет `base_dir` в `os.add_dll_directory()` и `PATH` для загрузки Windows DLL
- Необходимо для работы библиотеки `libdmtx` и других зависимостей

## Специфичные для проекта паттерны и соглашения

### Парсинг кодов DataMatrix

Реализованы два стандарта парсинга:
- **Табак**: фиксированный формат из 29 символов (GS_SEPARATOR не используется) → `parse_tobacco_dm()` в `tobacco_service.py`
- **DMKOD**: переменной длины, использует `GS (\x1d)` как разделитель полей → `parse_datamatrix()` в `aggregation_service.py`

### Генерация представлений для интеграции Bartender

`datamatrix-app/app/services/view_service.py` и `desktop-app/src/aggregation_service.py`:
- Динамически создает PostgreSQL VIEWs для каждого заказа
- Имена представлений очищены: `sanitize_view_name()` удаляет спецсимволы
- Bartender читает эти представления для генерации этикеток

### Безопасность SQL-запросов

Паттерн по всей кодовой базе:
```python
from psycopg2 import sql

query = sql.SQL("SELECT {field} FROM {table} WHERE id = %s").format(
    field=sql.Identifier(field_name),
    table=sql.Identifier(table_name)
)
cur.execute(query, (id_value,))
```

**Никогда не используйте конкатенацию строк** для идентификаторов или имен таблиц.

### Рабочий процесс Git (из GIT_WORKFLOW.md)

- **main**: Только стабильный, готовый к продакшену код
- **feature-development**: Интеграционная ветка для объединенных функций
- Ветки функций: `feature/description` создаются из `main`
- Ветки горячего исправления: `bugfix/description` объединяются прямо в `main` при необходимости
- Прямые коммиты в `main` запрещены, кроме срочных исправлений

Синхронизируйте ветки функций ежедневно: `git pull origin main && git merge main`

## Конфигурация окружения

### Ключевые переменные окружения

- `DATABASE_URL`: Строка подключения, используемая веб-приложениями (формат: `postgres://user:pass@host:port/dbname`)
- `DB_HOST`, `DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_PORT`: Используются настольным приложением и некоторыми init-скриптами
- `DB_SSL_MODE`: Опционально, одно из `disable|allow|prefer|require|verify-ca|verify-full`
- `DMKOD_SECRET_KEY`: Секрет Flask сессии (общий для всех портальных приложений)
- `APP_VERSION`: Внедряется в шаблоны через context processor
- `.env` файл в корне: Загружается всеми сервисами Docker

### Секреты и сертификаты

- SSL-сертификат для настольного приложения → `secrets/postgres/server.crt`
- Конфиги БД клиентов хранятся в таблице портала `clients`: `{id, name, db_host, db_port, db_name, db_user, db_password, db_ssl_cert}`

## Типичные задачи разработки

### Добавление функции в datamatrix-app

1. Создайте роут в `app/main.py` (как функция, украшенная `@datamatrix_bp.route()`)
2. Извлеките бизнес-логику в `app/services/new_service.py`, если сложно
3. Сервис вызывает `get_db_connection()`, выполняет запрос, возвращает dict/list
4. Добавьте HTML-шаблон в `app/templates/`
5. Тестируйте локально: `docker-compose up -d`, затем `http://localhost:5000`

### Изменение UI настольного приложения

1. Отредактируйте `desktop-app/src/admin_ui_qt.py`, `supervisor_ui_qt.py` или основное окно
2. На основе PySide6; макет использует QVBoxLayout, QHBoxLayout и т.д.
3. Сервисы в `src/` (aggregation_service, printing_service, api_service и т. д.)
4. Пересоберите с помощью задачи Nuitka для тестирования поведения исполняемого файла

### Отладка проблем БД

- Проверьте логи Postgres: `docker-compose logs postgres`
- Подключитесь напрямую: `psql -U portal_user -d app_portal -h localhost`
- Для веб-приложений включите логирование SQL: добавьте debug логирование в `get_db_connection()` или методы сервисов
- Настольное приложение: проверьте `.log` файлы или включите debug-вывод в `db_connector.py`

### Развертывание и резервные копии

- Смотрите `DEPLOYMENT_PLAN.md` для процедуры резервной копии перед развертыванием
- Перед слиянием в `main`: проверьте в ветке feature-development с полным стеком `docker-compose`
- Резервные копии: `docker exec [db_container] pg_dumpall -c -U [user] > dump_YYYY-MM-DD.sql`
- Восстановление: `cat dump.sql | docker exec -i [db_container] psql -U [user] -d postgres`

## Типичные проблемы и решения

1. **"DLL not found" в настольном приложении**: Запустите `desktop-app/run.py` для активации исправления пути (строки 6-35); необходимо для IDE и exe
2. **"DATABASE_URL not set"**: Веб-приложения ожидают этого в `.env`, а не в отдельных переменных DB_*
3. **Циклические импорты**: Всегда используйте Flask application factory для отложенной регистрации blueprint
4. **Несогласованность состояния Redis**: manual-aggregation-app не очищает заброшенные сессии; мониторьте память Redis
5. **Ошибки сборки Nuitka**: Убедитесь, что `--include-package=` охватывает все зависимости psycopg2, jinja2, babel, pandas, requests, bcrypt, dotenv, barcode
6. **Сбои многотенантного подключения**: настольное приложение `get_client_db_connection()` сначала пытается SSL, затем fallback на небезопасное; проверьте конфиг клиента в БД портала

## Справочник ключевых файлов

- `GIT_WORKFLOW.md` - Стратегия ветвления
- `DEPLOYMENT_PLAN.md` - Шаги развертывания в продакшене
- `DOCKER_COMMANDS.md` - Типичные операции Docker
- `.env.example` - Шаблон переменных окружения (если присутствует)
- `docker-compose.yml` - Оркестрация сервисов и монтирование томов
- `datamatrix-app/init_db.py` - Определение схемы для главного приложения
- `manual-aggregation-app/init_ma_db.py` - Схема для ручной агрегации
- `desktop-app/src/db_connector.py` - Пулирование соединений и логика SSL
- `manual-aggregation-app/app/services/state_service.py` - Управление состоянием через Redis
