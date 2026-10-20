# Pilates Studio: documentación técnica

> Estado: octubre 2026 (migración `e3a9c7d1f5b2`, outbox de eventos).
> Alcance: backend `py-pilates`, y el front `pilates-ui` en lo que interactúa con la API.
> Los diagramas están en [Mermaid](https://mermaid.js.org/), que GitHub y la mayoría de los IDEs renderizan.
> Algunas secciones están en archivos aparte (ver índice): GitHub corta por tiempo la carga de
> los diagramas cuando una sola página tiene muchos.

---

## Índice

1. [Visión general](#1-visión-general)
2. [Despliegue y componentes](#2-despliegue-y-componentes)
3. [Arquitectura del código](#3-arquitectura-del-código)
4. [Kernel compartido (`shared`)](#4-kernel-compartido-shared)
5. [Contexto `identity`](identity-scheduling.md#5-contexto-identity)
6. [Contexto `scheduling`](identity-scheduling.md#6-contexto-scheduling)
7. [Contexto `booking`](booking-notifications.md#7-contexto-booking)
8. [Contexto `notifications`](booking-notifications.md#8-contexto-notifications)
9. [Calendario de reservas (horizonte y apertura)](flujos.md#9-calendario-de-reservas-horizonte-y-apertura)
10. [Flujos principales](flujos.md#10-flujos-principales)
11. [Concurrencia y consistencia](consistencia-eventos-datos.md#11-concurrencia-y-consistencia)
12. [Eventos de dominio y tareas asíncronas](consistencia-eventos-datos.md#12-eventos-de-dominio-y-tareas-asíncronas)
13. [Modelo de datos](consistencia-eventos-datos.md#13-modelo-de-datos)
14. [API HTTP](#14-api-http)
15. [Catálogo de errores](#15-catálogo-de-errores)
16. [Configuración](#16-configuración)
17. [Seguridad](#17-seguridad)
18. [Frontend](#18-frontend)
19. [Testing y prueba de carga](#19-testing-y-prueba-de-carga)
20. [Operación](#20-operación)
21. [Limitaciones conocidas y mejoras posibles](#21-limitaciones-conocidas-y-mejoras-posibles)

---

## 1. Visión general

Sistema de reservas de turnos para un estudio de Pilates con grilla fija:

- **Grilla:** clases de 60 minutos de lunes a viernes. Por la mañana a las 08, 09, 10 y 11; por la tarde a las 15, 16 y 17. Cada clase tiene 4 lugares por defecto.
- **Alumnos:** reservan el **día hábil siguiente**. Los turnos se habilitan a las **20:00** del día hábil anterior. También pueden reservar **hoy** si queda lugar.
- **Límites por alumno:** 2 clases por día y 1 por bloque (mañana o tarde).
- **Cancelación:** hasta 1 hora antes de la clase.
- **Lista de espera con prioridad:** cuando se libera un lugar, el sistema se lo reserva automáticamente al primero de la lista que pueda tomarlo (respetando sus límites) y le avisa por email. En la última hora antes de la clase, como ya no podría cancelarlo, se avisa a todos los anotados y reserva el primero que entra.
- **Recepción (rol admin):**
  - Gestiona la grilla, los cierres (feriados, vacaciones) y la asistencia.
  - Da de alta alumnos, reserva en su nombre y cancela turnos puntuales.
  - Bloquea y desbloquea cuentas.
- **Notificaciones por email:**
  - Confirmación de reserva, recordatorio y cancelaciones.
  - Lugar liberado, apertura de turnos y día sin clases.
  - Clases reabiertas, recuperación de contraseña y bienvenida.

| Capa | Tecnología |
|---|---|
| API | Python 3.12, FastAPI, Pydantic v2, Uvicorn |
| Persistencia | PostgreSQL 16, SQLAlchemy 2 (síncrono, psycopg 3), Alembic |
| Asíncrono | Celery 5 + Redis 7 (worker + beat) |
| Email | SMTP (Mailpit en desarrollo) |
| Auth | JWT HS256 (PyJWT), bcrypt |
| Front | HTML, CSS y JS vanilla (ES modules), servido por nginx |
| Tests | pytest + httpx (TestClient), Locust para carga |

---

## 2. Despliegue y componentes

```mermaid
flowchart LR
    subgraph Cliente
        B[Navegador / teléfono]
    end

    subgraph Front["pilates-ui (docker: web)"]
        N[nginx :80 → host :3000<br/>SPA estática]
    end

    subgraph Back["py-pilates (docker compose)"]
        API[api<br/>uvicorn + FastAPI :8000]
        W[worker<br/>Celery, concurrency=2]
        BT[beat<br/>Celery Beat]
        DB[(db<br/>PostgreSQL 16)]
        R[(redis<br/>broker)]
        M[mailpit<br/>SMTP :1025 / UI :8025]
    end

    B -- HTML/JS --> N
    B -- "fetch JSON + Bearer JWT (CORS)" --> API
    API -- SQL --> DB
    API -- "send_task (eventos)" --> R
    BT -- "tareas programadas" --> R
    R --> W
    W -- SQL --> DB
    W -- SMTP --> M
```

| Servicio | Imagen | Comando | Notas |
|---|---|---|---|
| `db` | `postgres:16-alpine` | — | Volumen `pgdata`, `TZ=America/Argentina/Buenos_Aires`, puerto 5432 expuesto |
| `redis` | `redis:7-alpine` | — | Sólo broker; Celery corre **sin result backend** |
| `mailpit` | `axllent/mailpit` | — | UI en http://localhost:8025 |
| `api` | `py-pilates:latest` | `api` | `alembic upgrade head` → `pilates.cli create-admin` → `uvicorn --factory pilates.main:create_app --proxy-headers --workers ${WEB_CONCURRENCY}` (4 por defecto en el compose) |
| `worker` | `py-pilates:latest` | `worker` | `celery worker --concurrency=${WORKER_CONCURRENCY:-2}` |
| `beat` | `py-pilates:latest` | `beat` | `celery beat --schedule=/tmp/celerybeat-schedule` |

- `worker` y `beat` esperan a que `api` esté *healthy* (`GET /health`), así las migraciones ya están aplicadas cuando arrancan.
- La imagen corre con un usuario sin privilegios (`app`). El entrypoint es `docker/entrypoint.sh`.

---

## 3. Arquitectura del código

### 3.1 Estilo

Es un **monolito modular** con **arquitectura hexagonal** (puertos y adaptadores) y **DDD táctico**. Hay cuatro *bounded contexts* más un kernel compartido:

```
src/pilates/
├── shared/          kernel: AggregateRoot, DomainEvent, errores, UnitOfWork, Clock, AuditLog, outbox de eventos
├── identity/        usuarios, login, JWT, recuperación de contraseña, bloqueo, alta por admin
├── scheduling/      grilla de clases, calendario de reservas, cierres del gimnasio
├── booking/         turnos, políticas de reserva/cancelación, lista de espera, asistencia
├── notifications/   handlers de eventos → emails, recordatorios
├── worker/          Celery app + tareas
├── bootstrap.py     raíz de composición (Container)
├── main.py          factory FastAPI
├── cli.py           comandos de administración
└── config.py        Settings (pydantic-settings, .env)
```

Cada contexto tiene la misma estructura interna:

| Carpeta | Contenido | Depende de |
|---|---|---|
| `domain/` | Entidades/agregados (dataclasses puras), value objects, eventos, políticas, interfaces de repositorio | Sólo `shared.domain` |
| `application/` | Casos de uso (una clase por caso, método `execute`), DTOs, puertos (queries, servicios externos) | `domain`, puertos de `shared.application` |
| `adapters/` | ORM SQLAlchemy, implementaciones de repositorios y queries, routers HTTP, seguridad, SMTP | Frameworks |

### 3.2 Dependencias entre módulos

```mermaid
flowchart TB
    main[main.py / cli.py / worker] --> bootstrap
    bootstrap --> identity & scheduling & booking & notifications
    bootstrap --> infra[shared.infrastructure]

    subgraph contexts[" "]
        identity
        scheduling
        booking
        notifications
    end

    scheduling -- "release_class_bookings()<br/>(misma transacción)" --> booking
    booking -- "closed_dates_from(), next_open_day()<br/>BookingCalendar" --> scheduling
    booking -. "ClassCatalog (ACL: ClassSnapshot)" .-> scheduling
    notifications -- "lee eventos y repos" --> identity & scheduling & booking
    booking -- "UserBlocked (evento)" --> identity

    identity & scheduling & booking & notifications --> shared[shared.domain / shared.application]
    infra --> shared
```

Puntos de acoplamiento entre contextos, todos deliberados:

- **`scheduling → booking`.** `release_class_bookings(uow, clase_id, motivo, now)` es la API pública de reservas para cancelar en bloque los turnos de una clase. Se usa al cancelar una clase o cargar un cierre, dentro de la misma transacción.
- **`booking → scheduling`.**
  - `BookingCalendar` (dominio).
  - `closed_dates_from()`, en `scheduling/application/closures.py`. Está separado de `services.py` para evitar un import circular.
  - `next_open_day()`.
- **`booking ⇢ scheduling` (lectura).** El puerto `ClassCatalog` devuelve un `ClassSnapshot`, una vista inmutable de la clase propia de reservas. Funciona como *anti-corruption layer*: reservas nunca manipula el agregado `Clase`.
- **`identity → booking` (por evento).** `UserBlocked` lo consume `CancelUpcomingBookingsOfBlockedUser` en el worker.
- **Persistencia.** Algunos adaptadores leen tablas de otros contextos para armar *read models*. Por ejemplo, `SqlUserQueries` cuenta turnos y `SqlScheduleQueries` cuenta reservas.

### 3.3 Raíz de composición

`bootstrap.build_container(settings, *, engine, clock, email_sender, event_bus)` arma un `Container` (dataclass) con:

- La infraestructura: engine, session factory, clock, email sender y event bus.
- Los objetos de política: `GridTemplate`, `BookingCalendar` y `BookingPolicy`, construidos desde `Settings`.
- El `EventRegistry` con todas las suscripciones (ver [§12](consistencia-eventos-datos.md#12-eventos-de-dominio-y-tareas-asíncronas)).
- Una instancia de cada caso de uso, que recibe una **fábrica** de `UnitOfWork` (`uow()`).

Los parámetros opcionales permiten inyectar dobles en los tests: un reloj fijo y `ConsoleEmailSender`. Con `EVENT_BUS=sync` los eventos del outbox se procesan en el mismo proceso (`SyncOutboxNotifier`).

FastAPI guarda el container en `app.state.container`. Los endpoints lo reciben con `ContainerDep` (`shared/http/deps.py`). El worker crea uno por proceso (`lru_cache`).

### 3.4 Ciclo de vida de un request

```mermaid
sequenceDiagram
    autonumber
    participant C as Cliente
    participant R as Router FastAPI
    participant D as deps (current_user)
    participant UC as Caso de uso
    participant U as UnitOfWork
    participant DB as PostgreSQL
    participant OB as Outbox

    C->>R: POST /turnos {clase_id} + Bearer
    R->>D: JWT decode → GetCurrentUser (verifica que no esté bloqueado)
    D-->>R: UserDTO
    R->>UC: book_class.execute(user_id, clase_id)
    UC->>U: with uow() as uow
    U->>DB: BEGIN (sesión nueva)
    UC->>DB: SELECT … FOR UPDATE / INSERT/UPDATE vía repositorios
    UC->>U: uow.commit()
    U->>DB: INSERT outbox (una fila por evento × handler)
    U->>DB: COMMIT (cambios + eventos, atómico)
    U->>OB: notify(ids) — Celery: send_task por fila, si falla, sólo se loguea
    UC-->>R: DTO
    R-->>C: 201 JSON
    Note over U: __exit__ hace rollback (no-op si ya hubo commit) y cierra la sesión
```

Los errores de dominio (`DomainError`) se propagan sin capturar hasta el handler global (`shared/http/errors.py`). Ese handler los traduce a HTTP (ver [§15](#15-catálogo-de-errores)).

---

## 4. Kernel compartido (`shared`)

### 4.1 Dominio

- **`AggregateRoot`.** Acumula `DomainEvent`s en `_events` con `record()`. `pull_events()` los devuelve y vacía la lista.
- **`DomainEvent`.** Dataclass frozen con `occurred_at` (UTC). `name()` devuelve el nombre de la clase y se usa como clave del registry.
- **Errores.**

  | Clase | `code` por defecto | HTTP |
  |---|---|---|
  | `NotFoundError` | `not_found` | 404 |
  | `ConflictError` | `conflict` | 409 |
  | `BusinessRuleViolation` | `business_rule_violation` | 422 |
  | `AuthenticationError` | `authentication_failed` | 401 (+ `WWW-Authenticate: Bearer`) |
  | `PermissionDenied` | `permission_denied` | 403 |
  | otro `DomainError` | `domain_error` | 400 |

  Cada error puede llevar un `code` específico (`class_full`, `daily_limit_reached`, …). El cliente recibe `{"error": code, "detail": message}`.

### 4.2 Aplicación: puertos

```python
class Clock(ABC):      def now(self) -> datetime          # con tz del estudio
class AuditLog(ABC):   def record(self, admin_id, action, details) -> None
```

### 4.3 Unit of Work

`UnitOfWork` (abstracta) es el **límite transaccional único** para todos los contextos. Expone los repositorios y queries:

| Atributo | Tipo |
|---|---|
| `users`, `reset_tokens`, `user_queries` | identity |
| `clases`, `cierres`, `schedule_queries` | scheduling |
| `turnos`, `waitlist`, `class_catalog`, `booking_queries` | booking |
| `audit` | shared |

Mecánica:

1. `__enter__` abre una sesión SQLAlchemy nueva e instancia los repositorios sobre esa sesión.
2. Cada repositorio, al hacer `add` o `save`, llama a `uow.track(aggregate)`.
3. `add_event(e)` agrega eventos que no pertenecen a un agregado. Por ejemplo, `TurnosHabilitados` o `ClasesReabiertas`.
4. `commit()` junta los eventos de todos los agregados trackeados más los extra y llama a `_commit(events)`. La implementación SQL los agrega a la tabla `outbox` (una fila por par evento-handler) **en la misma sesión**, hace `session.commit()` y recién entonces avisa al despachador (ver [§12.3](consistencia-eventos-datos.md#123-outbox-transaccional)).
5. Un `IntegrityError` en el commit se convierte en `ConflictError("integrity_conflict")`. Es la última línea de defensa ante carreras (ver [§11](consistencia-eventos-datos.md#11-concurrencia-y-consistencia)).
6. `__exit__` hace rollback y cierra la sesión.

> Los cambios y sus eventos se guardan en la misma transacción: o se confirman los dos, o ninguno. Si después del commit Redis no responde, el request igual termina bien; el relay reenvía el evento.

### 4.4 Infraestructura

- **`db.py`.** `create_engine(url, pool_pre_ping=True)` con el pool por defecto (5 conexiones + 10 overflow). `sessionmaker(expire_on_commit=False, autoflush=True)`.
- **`SystemClock(tz)`.** `datetime.now(ZoneInfo(tz))`.
- **`SqlAuditLog`.** Inserta en `admin_audit_log` dentro de la transacción del caso de uso.
- **`event_bus.py`.**
  - `EventRegistry`: `subscribe(EventType, handler_name, handler)`. Serializa y deserializa eventos con `pydantic.TypeAdapter`, que maneja dataclasses, UUID y fechas.
- **`outbox.py`.** `Outbox` (escribe las filas y avisa), `OutboxDispatcher` (ejecuta un handler por fila), `OutboxRelay` (reenvía pendientes) y dos notificadores:
  - `SyncOutboxNotifier`: despacha en el mismo proceso. Loguea las excepciones y no las propaga; la fila queda pendiente. Se usa en dev y tests.
  - `CeleryOutboxNotifier`: por cada fila hace `send_task("pilates.dispatch_outbox", [id])`.

### 4.5 HTTP

- **`deps.current_user`.**
  1. Lee el header `Authorization: Bearer`.
  2. Hace `JwtTokenService.decode`.
  3. Llama a `GetCurrentUser`, que consulta la base **en cada request**: verifica que la sesión del token (`sid`) exista, sea de ese usuario y no esté revocada, y que el usuario exista y no esté bloqueado.
- **`deps.require_admin`.** Exige `role == "admin"` y, si no, responde 403.
- Los aliases `CurrentUser` y `AdminUser` se usan como anotaciones en los endpoints.

---

## 14. API HTTP

- **Base:** `http://<host>:8000`.
- **Documentación interactiva:** `/docs` (Swagger) y `/redoc`.
- **Autenticación:** `Authorization: Bearer <JWT>`.
- **Formato:** cuerpos y respuestas en JSON. Las fechas van en ISO-8601 (`AAAA-MM-DD`) y las horas como `HH:MM:SS`.

### 14.1 Auth (`/auth`)

| Método | Ruta | Auth | Body | Respuesta |
|---|---|---|---|---|
| POST | `/auth/register` | — | `{email, full_name, password(8–128)}` | 201 `UserDTO` · 409 `email_taken` · 422 · 429 |
| POST | `/auth/login` | — | `{email, password}` | 200 `{access_token, token_type, user}` · 401 · 403 `user_blocked` · 429 |
| POST | `/auth/forgot-password` | — | `{email}` | 202 siempre (mensaje genérico) · 429 |
| POST | `/auth/reset-password` | — | `{token, new_password}` | 204 · 422 `invalid_reset_token` / `weak_password` |
| GET | `/auth/me` | usuario | — | `UserDTO` |
| POST | `/auth/logout` | usuario | — | 204. Revoca la sesión de este dispositivo |

`UserDTO`: `{id, email, full_name, role, status, created_at, blocked_reason}`.

### 14.2 Alumno

| Método | Ruta | Descripción |
|---|---|---|
| GET | `/clases/disponibles` | Lista de `DayAvailabilityDTO` (ver abajo) |
| POST | `/turnos` | `{clase_id}` → 201 `TurnoDTO` |
| POST | `/turnos/{turno_id}/cancelar` | → `TurnoDTO` |
| GET | `/turnos/mios` | `{proximas: [MyBookingDTO], pasadas: [MyBookingDTO]}` |
| POST | `/clases/{clase_id}/lista-espera` | 201 `{detail}` |
| DELETE | `/clases/{clase_id}/lista-espera` | 204 |
| GET | `/calendario?desde=&hasta=` | Lista de `StudentCalendarDayDTO` (por defecto, hoy + 30 días; máximo 92) |

Ejemplo de `GET /clases/disponibles` (19/10 a las 20:05, con el 20 cerrado):

```json
[
  {"fecha": "2026-10-19", "estado": "abierta", "apertura": null, "clases": [ … ]},
  {"fecha": "2026-10-20", "estado": "cerrado", "motivo_cierre": "Feriado nacional",
   "proximo_dia_habil": "2026-10-21", "clases": []},
  {"fecha": "2026-10-21", "estado": "abierta", "apertura": "2026-10-19T20:00:00-03:00",
   "clases": [{
      "clase_id": "…", "fecha": "2026-10-21", "start_time": "08:00:00", "end_time": "09:00:00",
      "bloque": "manana", "instructor_name": null, "capacidad": 4, "reservados": 1,
      "disponibles": 3, "completa": false, "status": "activa",
      "mi_turno_id": null, "en_lista_espera": false,
      "puede_reservar": true, "puede_cancelar": false, "puede_anotarse_lista_espera": false
   }, …]}
]
```

`TurnoDTO`: `{id, clase_id, status, booked_at, cancelled_at, booked_by}`.

### 14.3 Admin (requieren `role=admin`)

**Usuarios**

| Método | Ruta | Descripción |
|---|---|---|
| GET | `/admin/usuarios?search=&estado=activo\|bloqueado&limit≤200&offset=` | Lista de `UserSummary` (incluye `asistencias`, `ausencias`, `reservas_activas`) |
| POST | `/admin/usuarios` | `{email, full_name}` → 201 `UserDTO`, envía la invitación |
| POST | `/admin/usuarios/{id}/bloquear` | `{motivo?}` → `UserDTO` |
| POST | `/admin/usuarios/{id}/desbloquear` | → `UserDTO` |

**Grilla y cierres**

| Método | Ruta | Descripción |
|---|---|---|
| GET | `/admin/clases?fecha=` | Lista de `AdminClassDTO` (con `reservados` y `lista_espera`) |
| POST | `/admin/clases/generar` | `{fecha?, notificar=false}` → `GenerationResultDTO` |
| PATCH | `/admin/clases/{id}` | `{max_capacity?(1–50), instructor_name?}` → 204 |
| POST | `/admin/clases/{id}/cancelar` | `{motivo}` → `{turnos_cancelados}` |
| POST | `/admin/clases/{id}/reactivar` | 204 |
| GET | `/admin/cierres?desde=&hasta=` | Lista de `CierreDTO` |
| POST | `/admin/cierres` | `{desde, hasta?, tipo, motivo?}` → 201 lista de `CierreDTO` |
| PUT | `/admin/cierres/{id}` | `{tipo, motivo?}` → `CierreDTO` |
| DELETE | `/admin/cierres/{id}` | 204 (reabre el día) |
| DELETE | `/admin/cierres?desde=&hasta=` | `{cierres_eliminados, clases_reabiertas}` |
| GET | `/admin/calendario?desde=&hasta=` | Lista de `CalendarDayDTO` (máximo 92 días) |

**Turnos y asistencia**

| Método | Ruta | Descripción |
|---|---|---|
| GET | `/admin/asistencia?fecha=` | Lista de `AttendanceClassDTO` (por defecto, hoy) |
| POST | `/admin/turnos` | `{user_id, clase_id}` → 201 `TurnoDTO` (`booked_by=gimnasio`) |
| POST | `/admin/turnos/{id}/cancelar` | `{motivo?}` → `TurnoDTO` |
| POST | `/admin/turnos/{id}/asistencia` | `{asistio: bool}` → `TurnoDTO` |

**Otros:** `GET /health` → `{"status": "ok"}`.

### 14.4 Acciones auditadas (`admin_audit_log.action`)

`user.created_by_admin`, `user.blocked`, `user.unblocked`, `class.updated`, `class.cancelled`, `class.reactivated`, `closure.created`, `closure.updated`, `closure.deleted`, `booking.created_by_admin`, `booking.cancelled_by_admin`, `booking.attendance`.

---

## 15. Catálogo de errores

Formato de respuesta: `{"error": "<code>", "detail": "<mensaje para mostrar>"}`. Los errores de validación de Pydantic responden 422 con el formato estándar de FastAPI (`detail` es una lista). El front contempla ambos formatos.

| HTTP | `error` | Cuándo |
|---|---|---|
| 401 | `not_authenticated` | Falta el header Bearer |
| 401 | `invalid_token` | JWT inválido, expirado o sin `sid` |
| 401 | `session_revoked` | La sesión del token se cerró (logout, contraseña restablecida, cuenta bloqueada) |
| 401 | `authentication_failed` | Credenciales inválidas / usuario inexistente |
| 403 | `permission_denied` | No es admin / el turno no es del usuario |
| 403 | `user_blocked` | Cuenta bloqueada |
| 404 | `not_found` | Usuario / clase / turno / cierre inexistente |
| 409 | `email_taken` | Email ya registrado |
| 409 | `already_booked` | Ya tiene turno activo en la clase |
| 409 | `class_full` | Sin cupo |
| 409 | `already_in_waitlist` | Ya está en la lista de espera |
| 409 | `booking_not_active` | Cancelar o marcar asistencia de un turno no activo |
| 409 | `class_already_cancelled` / `class_already_active` | Cancelar o reactivar dos veces |
| 409 | `closure_exists` | Algún día del rango ya tiene cierre |
| 409 | `integrity_conflict` | Violación de restricción única (carrera) |
| 422 | `outside_booking_window` | Fecha fuera del horizonte o antes de la apertura |
| 422 | `booking_closed` | La clase ya empezó (o dentro de `MIN_BOOKING_ADVANCE_MINUTES`) |
| 422 | `class_not_available` | Clase cancelada o cerrada |
| 422 | `class_finished` | Admin: reservar o cancelar en clase terminada |
| 422 | `daily_limit_reached` / `block_limit_reached` | Límites por alumno |
| 422 | `class_not_full` | Lista de espera con lugares libres |
| 422 | `cancellation_window_closed` | Cancelar con menos de 1 h |
| 422 | `class_not_started` | Marcar asistencia antes del día de la clase |
| 422 | `class_already_started` | Cancelar o reactivar una clase ya empezada |
| 422 | `class_closed_by_closure` / `day_closed` | Reactivar una clase de un día con cierre |
| 422 | `capacity_below_bookings` / `invalid_capacity` | Cambio de capacidad inválido |
| 422 | `not_a_weekday` / `date_in_past` / `invalid_range` | Fechas inválidas |
| 422 | `cannot_block_admin` | Bloquear a un admin |
| 422 | `invalid_email` / `invalid_name` / `weak_password` / `invalid_reset_token` | Identidad |
| 429 | `too_many_requests` | Rate limiting de login, registro o recuperación de contraseña. Header `Retry-After` en segundos; `detail` dice cuánto esperar (ver [§17.1](#171-rate-limiting)) |

---

## 16. Configuración

Todas las variables se leen de `.env` o del entorno con `pydantic-settings` (`config.py`). Los nombres no distinguen mayúsculas.

| Variable | Default | Descripción |
|---|---|---|
| `ENVIRONMENT` | `dev` | `dev` / `test` / `prod` |
| `LOG_LEVEL` | `INFO` | También lo usan worker y beat |
| `DATABASE_URL` | `postgresql+psycopg://pilates:pilates@localhost:5432/pilates` | |
| `DB_POOL_SIZE` / `DB_MAX_OVERFLOW` | `5` / `10` | Pool de SQLAlchemy **por proceso**. Con 4 workers de uvicorn la API puede abrir hasta 60 conexiones; sumadas al worker de Celery tienen que entrar en `max_connections` de Postgres (100) |
| `REDIS_URL` | `redis://localhost:6379/0` | Broker Celery |
| `JWT_SECRET` | `change-me` | **Obligatorio cambiar** en producción |
| `JWT_ALGORITHM` | `HS256` | |
| `JWT_EXPIRES_MINUTES` | `720` | 12 h |
| `TIMEZONE` | `America/Argentina/Buenos_Aires` | Reloj, beat, interpretación de horas de clase |
| `CLASS_CAPACITY` | `4` | Capacidad por defecto al generar |
| `CLASS_DURATION_MINUTES` | `60` | |
| `MORNING_SLOTS` | `08:00,09:00,10:00,11:00` | Bloque "mañana" |
| `AFTERNOON_SLOTS` | `15:00,16:00,17:00` | Bloque "tarde" |
| `BOOKING_OPENING_TIME` | `20:00` | Hora de apertura y del job nocturno |
| `ALLOW_SAME_DAY_BOOKING` | `true` | Reservar hoy si hay lugar |
| `MIN_BOOKING_ADVANCE_MINUTES` | `0` | Anticipación mínima (1440 para la regla de 24 h) |
| `CANCEL_MIN_NOTICE_MINUTES` | `60` | |
| `MAX_BOOKINGS_PER_DAY` | `2` | |
| `MAX_BOOKINGS_PER_BLOCK` | `1` | |
| `REMINDER_HOURS_BEFORE` | `2` | Horas antes del inicio de la clase |
| `REMINDER_EARLIEST_TIME` | `07:00` | El recordatorio nunca sale antes de esta hora del día de la clase |
| `NOTIFY_CLOSED_DAY` | `true` | Emails "Sin clases el …" |
| `EVENT_BUS` | `celery` | `sync`: handlers en proceso (sin Redis) |
| `EMAIL_BACKEND` | `smtp` | `console`: sólo log |
| `SMTP_HOST` / `SMTP_PORT` / `SMTP_USER` / `SMTP_PASSWORD` / `SMTP_USE_TLS` | `localhost` / `1025` / — / — / `false` | |
| `EMAIL_FROM` | `Pilates Studio <no-reply@pilates.local>` | |
| `FRONTEND_URL` | `http://localhost:3000` | Base de los links en emails |
| `PASSWORD_RESET_TTL_MINUTES` | `60` | |
| `INVITE_TTL_HOURS` | `72` | Link de bienvenida |
| `ADMIN_EMAIL` / `ADMIN_PASSWORD` / `ADMIN_NAME` | — / — / `Administrador` | Admin inicial (`cli create-admin`) |
| `CORS_ORIGINS` | `*` | Lista separada por comas |
| `RATE_LIMIT_ENABLED` | `true` | `false` para pruebas de carga (el compose lo pasa a la API) |
| `RATE_LIMIT_STORAGE` | `redis` | `memory`: contador por proceso (tests, un solo worker) |
| `LOGIN_ATTEMPTS_PER_IP_PER_MINUTE` | `20` | Intentos de login por IP por minuto |
| `LOGIN_FAILURES_PER_EMAIL` / `LOGIN_FAILURE_WINDOW_MINUTES` | `5` / `15` | Logins fallidos por email antes de bloquear esa cuenta durante la ventana |
| `REGISTRATIONS_PER_IP_PER_HOUR` | `10` | Registros públicos por IP por hora |
| `PASSWORD_RESETS_PER_IP_PER_HOUR` / `PASSWORD_RESETS_PER_EMAIL_PER_HOUR` | `5` / `3` | Pedidos de recuperación de contraseña |
| `WORKER_CONCURRENCY` | `2` | Sólo entrypoint del worker |
| `WEB_CONCURRENCY` | `4` en el compose (`1` si se corre el entrypoint sin compose) | Procesos de uvicorn de la API (ver [§19.3](#193-workers-de-uvicorn)) |

---

## 17. Seguridad

- **Contraseñas.** Se guardan con bcrypt (costo 12, salt por hash). Nunca se loguean.
- **Tokens de reset e invitación.**
  - Generan 32 bytes aleatorios con `secrets`. En la base sólo queda su SHA-256.
  - Son de un solo uso y vencen: 60 minutos el de reset, 72 horas el de invitación.
  - Pedir un reset nuevo invalida los anteriores.
- **Enumeración de usuarios.** `forgot-password` responde siempre 202 con el mismo mensaje. `login` devuelve "Credenciales inválidas" tanto si el email no existe como si la contraseña es incorrecta.
- **Sesiones revocables.** Cada login crea una fila en `sesiones` y el JWT lleva su id (`sid`). `current_user` la valida en cada request (una búsqueda por clave primaria), así que la revocación se aplica al instante:

  | Acción | Sesiones que se cierran |
  |---|---|
  | `POST /auth/logout` | Sólo la de ese dispositivo |
  | Restablecer la contraseña | Todas las del usuario |
  | Bloquear la cuenta | Todas las del usuario (desbloquear no las reabre) |
  | Borrar el usuario | Todas (`ON DELETE CASCADE`) |

  El vencimiento lo sigue controlando el JWT (12 h). Las sesiones vencidas hace más de un día se purgan en el siguiente login de ese usuario. No hay refresh token: al vencer, se vuelve a ingresar.
- **Autorización.** Se resuelve en el borde HTTP (`AdminUser`). Las operaciones sobre recursos propios (cancelar un turno) también verifican la pertenencia en el caso de uso.
- **Auditoría.** Todas las acciones administrativas que modifican datos quedan registradas con `admin_id`.
- **CORS.** Es configurable. En producción conviene listar los orígenes exactos en lugar de `*`.
- **Contenedor.** Corre con un usuario no root.
- **Pendientes.** Ver [§21](#21-limitaciones-conocidas-y-mejoras-posibles): HTTPS y rotación del secreto.

### 17.1 Rate limiting

Ventana fija con contadores en **Redis**, compartidos entre los 4 workers de uvicorn (`shared/infrastructure/rate_limit.py`). Al pasarse del límite la API responde `429 too_many_requests` con `Retry-After`.

| Endpoint | Clave | Límite por defecto | Qué cuenta |
|---|---|---|---|
| `POST /auth/login` | `login:ip:<ip>` | 20 por minuto | Todos los intentos |
| `POST /auth/login` | `login:fail:<sha256(email)>` | 5 en 15 minutos | Sólo los fallidos (401). Un login correcto lo resetea. Al llegar al límite, esa cuenta queda bloqueada aunque la contraseña sea correcta, y las demás cuentas siguen funcionando |
| `POST /auth/register` | `register:ip:<ip>` | 10 por hora | Todos los intentos |
| `POST /auth/forgot-password` | `forgot:ip:<ip>` | 5 por hora | Todos los pedidos |
| `POST /auth/forgot-password` | `forgot:email:<sha256(email)>` | 3 por hora | Todos los pedidos para ese email, **exista o no la cuenta**: el 429 no revela qué emails están registrados. Evita que alguien llene de mails la casilla de un alumno |

- **Fail open.** Si Redis no responde, se deja pasar la request y se deja de consultar Redis durante 30 s, así sólo una request por proceso paga el timeout (0,5 s, sin reintentos). Medido con Redis caído: el login tarda lo mismo que bcrypt (~0,53 s).
- **IP del cliente.** Es `request.client.host`. Los clientes que corren en la misma máquina que Docker llegan con la IP del bridge (ej. `172.21.0.1`) y comparten el contador; los de la red local (teléfonos) conservan la suya. Detrás de un proxy hay que configurar `--forwarded-allow-ips` para que uvicorn tome `X-Forwarded-For`.
- **NAT compartido.** Si muchos alumnos se registran desde el Wi-Fi del estudio salen por la misma IP pública: con más de 10 por hora conviene subir `REGISTRATIONS_PER_IP_PER_HOUR`, o que recepción los dé de alta (`POST /admin/usuarios`, sin límite).
- **Pruebas de carga.** Locust registra y loguea 40 usuarios desde la misma IP: correr con `RATE_LIMIT_ENABLED=false docker compose up -d api`.

---

## 18. Frontend

Repositorio aparte: `pilates-ui` (`../../front-ui-code/pilates-ui`). Es una SPA en JS vanilla con ES modules y sin build. En Docker la sirve nginx con *fallback* a `index.html`.

| Archivo | Rol |
|---|---|
| `config.js` | `window.APP_CONFIG.apiUrl`. Por defecto `protocolo://host-actual:8000`, así funciona igual con `localhost` que con la IP de la LAN. En Docker se regenera desde `API_URL` (`docker/40-write-config.sh`) |
| `js/api.js` | Cliente `fetch`: agrega el Bearer, convierte `{error, detail}` en `ApiError(status, code, message)` y hace logout ante un 401 con sesión |
| `js/session.js` | Token y usuario en `localStorage` (`pilates.session`) |
| `js/app.js` | Router con History API: rutas `guest` / `user` / `admin`, redirecciones y navegación |
| `js/theme.js` | Tema Sistema → Claro → Oscuro (`localStorage` `pilates.theme`). Un script inline en `index.html` lo aplica antes del primer pintado |
| `js/html.js`, `js/ui.js` | Templates `html\`\`` con escape, `mount`, `dialog()`, delegación `on()` |
| `js/views/*` | Pantallas |

| Ruta | Acceso | Pantalla | Endpoints principales |
|---|---|---|---|
| `/login`, `/registro`, `/olvide` | invitado | Auth | `/auth/*` |
| `/reset-password?token=` | cualquiera | Definir o restablecer contraseña (también la usa la invitación) | `/auth/reset-password` |
| `/reservar` (y `/clases/{id}` desde emails) | alumno | Grilla del día con acciones según los flags `puede_*` | `/clases/disponibles`, `/turnos`, `/clases/{id}/lista-espera` |
| `/mis-turnos` | alumno | Próximos y pasados | `/turnos/mios`, `/turnos/{id}/cancelar` |
| `/calendario` | alumno | Mes con días sin clases | `/calendario` |
| `/admin/clases` | admin | Grilla por fecha, generar, editar, cancelar o reactivar | `/admin/clases*` |
| `/admin/asistencia` | admin | Asistencia del día, **agregar alumno** a una clase, **cancelar turno** (✕ con motivo) | `/admin/asistencia`, `/admin/turnos*`, `/admin/usuarios?estado=activo` |
| `/admin/cierres` | admin | Alta, edición y reapertura de cierres y rangos | `/admin/cierres*`, `/admin/calendario` |
| `/admin/usuarios` | admin | Listado con estadísticas, **nuevo alumno**, bloquear y desbloquear | `/admin/usuarios*` |

La regla general es que el front **no duplica reglas de negocio**: decide qué botones mostrar a partir de los flags del backend. Hay una excepción, la pantalla de asistencia, que oculta acciones en clases terminadas comparando con la hora local. El backend igual valida (`class_finished`).

---

## 19. Testing y prueba de carga

### 19.1 Suite automatizada

```bash
docker compose up -d db          # los tests crean y usan la base pilates_test
.venv/bin/pytest                 # 82 tests
```

| Carpeta | Tipo | Qué cubre |
|---|---|---|
| `tests/unit/test_calendar.py` | Unitario (dominio puro) | Días hábiles, target, ventana, mismo día, grilla, **salteo de días cerrados** |
| `tests/unit/test_booking_domain.py` | Unitario | Política (cupo, ventana, límites), estados del turno, rebook, reglas de reactivación, reserva y cancelación admin, `cancelled_with_class` |
| `tests/unit/test_reminders.py` | Unitario | Horario del recordatorio el día de la clase (nunca antes de las 07:00) y omisión si se reservó después |
| `tests/unit/test_identity_domain.py` | Unitario | Email, bloqueo, admin no bloqueable, token de un solo uso |
| `tests/application/test_api_flows.py` | Integración (TestClient + Postgres real) | Flujos end-to-end por HTTP: registro y login, apertura, cupo y lista de espera (asignación automática por orden, salteando a quien llegó a su límite, y aviso a todos en la última hora), límites, ventana de cancelación, cierres (incluido cerrar un día ya habilitado: aviso general y apertura del siguiente), job nocturno (incluido "mañana cerrado → abre el siguiente"), asistencia, recordatorios, bloqueo, reset, calendario, reactivación, rangos de vacaciones, alta por admin, reserva y cancelación admin, cupo con asistencia marcada |
| `tests/application/test_sessions.py` | Integración | Logout cierra sólo ese dispositivo; restablecer la contraseña y bloquear cierran todas; desbloquear no reabre; tokens sin `sid` y `sid` de otro usuario rechazados |
| `tests/application/test_rate_limit.py` | Integración + unitario | Límite de registro por IP, recuperación de contraseña por email (sin revelar cuentas) y por IP, bloqueo por fallos de un email (sin afectar otras cuentas), reset al loguear bien, límite por IP, vencimiento de la ventana, fail open sin Redis y sin reintentos |
| `tests/application/test_outbox.py` | Integración | El evento sobrevive a Redis caído y el relay lo entrega; un handler fallido queda pendiente y se reintenta; el despacho es idempotente; las filas que agotaron reintentos no se reenvían; en un envío masivo o en los recordatorios, un fallo de SMTP sólo reintenta al destinatario que falló |
| `tests/application/test_concurrency.py` | Integración con hilos | Nunca se supera el cupo (alumnos; alumnos + admin; asignación desde la lista de espera compitiendo con reservas directas) |

En `conftest.py`:

- **Reloj.** Se usa un `FixedClock` controlable. La fecha base es el lunes 12/10/2026 a las 21:00.
- **Infraestructura.** `EVENT_BUS=sync` (los eventos pasan por la tabla `outbox` y se despachan en el mismo proceso) y `ConsoleEmailSender`; los asserts sobre emails leen su lista `outbox`.
- **Base.** Se limpia entre tests.

### 19.2 Prueba de carga (Locust)

`tests/load/locustfile.py` simula alumnos:

- Al iniciar, cada uno se registra (201 o 409 cuentan como éxito) y hace login una sola vez.
- Las tareas tienen estos pesos: ver la grilla (5), mis turnos (3), `/auth/me` (1), y reservar y cancelar de inmediato (2).
- Los rechazos de negocio (4xx) cuentan como éxito; sólo los 5xx cuentan como fallo.

```bash
RATE_LIMIT_ENABLED=false docker compose up -d api      # si no, el rate limiting corta los registros
locust -f tests/load/locustfile.py --host http://localhost:8000 \
    --users 40 --spawn-rate 5 --run-time 3m --headless --only-summary
docker compose up -d api                                # volver a activarlo
```

Resultados en el entorno de desarrollo (una PC, un solo proceso uvicorn), 19/10/2026, 40 usuarios durante 3 minutos:

| Corrida | Requests | Errores | Mediana | p95 | req/s | Notas |
|---|---|---|---|---|---|---|
| 1 | — | 0 | 56 ms | 180 ms | — | Sin clases reservables |
| 2 | — | 0 | 55 ms | 150 ms | — | Sin clases reservables |
| 3 | 4285 | **0** | 100 ms | 490 ms | 23,9 | **574 reservas + 574 cancelaciones reales** |

En la corrida 3:

| Endpoint | Mediana | p95 |
|---|---|---|
| `POST /turnos` | 130 ms | 400 ms |
| `POST /turnos/{id}/cancelar` | 100 ms | 320 ms |
| `POST /auth/login` | ≈ 5 s | 6,8 s |
| `POST /auth/register` | ≈ 3,7 s | 6,3 s |

- **CPU.** Postgres estuvo por debajo de 7%. (La cifra de CPU de la API que se reportó en esta corrida estaba mal calculada; ver la medición correcta en [§19.3](#193-workers-de-uvicorn).)
- **Cuello de botella:** bcrypt (~0,5 s por hash en esta máquina). Es CPU-bound: con 40 registros y 40 logins en ~8 s la CPU se satura y esas requests hacen cola. Mientras tanto, el p99 de otros endpoints subió a 2–4 s.

### 19.3 Workers de uvicorn

Misma prueba (40 usuarios, spawn 5/s, 90 s), dos corridas por configuración, en una máquina de 4 núcleos que también corre Locust y Postgres. Mediana / p95 / p99 en ms:

| Endpoint | 1 worker | 4 workers |
|---|---|---|
| `POST /auth/login` | 4400–4900 / 7300–7600 / 8500–8900 | 5300–5800 / 7900–8000 / 8400–8700 |
| `POST /auth/register` | 3300–3600 / 5900–6600 / 5900–6900 | 6100–6200 / 8200–8900 / 8400–9200 |
| `GET /clases/disponibles` | 120 / 760–880 / 3700–4700 | 70 / 320–410 / 990–1200 |
| `GET /turnos/mios` | 50 / 410–790 / 3800–4900 | 26 / 94–240 / 390–640 |
| `POST /turnos` | 140–230 / 750–930 / 1200–2000 | 110–120 / 510–560 / 2500–3400 |
| Total | 110–120 / 2300–2400 / 5600–6000 | 66–67 / 960–1000 / 7100 |

Sin errores en ninguna corrida. CPU máxima de la API: 309–321% con 1 worker, 284–310% con 4.

- **bcrypt ya usa varios núcleos con un solo proceso.** Libera el GIL: 4 verificaciones en paralelo tardan 725 ms, contra 526 ms una sola. Con la CPU saturada, más procesos no aceleran el login; el registro incluso empeora, porque entran más hashes a la vez.
- **El resto de la API sí mejora.** Serializar JSON y usar SQLAlchemy es Python puro, que dentro de un proceso no corre en paralelo. Con 1 worker, esas requests quedan detrás de la ráfaga de logins.
- **Decisión:** 4 workers (uno por núcleo) y bcrypt con costo 12. El login sólo se acelera con más CPU o bajando el costo de bcrypt, que es una decisión de seguridad.

---

## 20. Operación

### 20.1 Comandos habituales

```bash
docker compose up -d --build                                  # levantar / actualizar
docker compose logs -f api worker beat                        # logs
docker compose exec api python -m pilates.cli open-next-day   # forzar la apertura (genera + notifica)
docker compose exec api python -m pilates.cli send-reminders
docker compose exec api python -m pilates.cli relay-outbox    # reenviar eventos pendientes del outbox
docker compose exec api python -m pilates.cli create-admin
docker compose exec db psql -U pilates -d pilates             # consola SQL
```

Desarrollo sin Docker para la API:

```bash
uv venv && uv pip install -e '.[dev]'
EVENT_BUS=sync EMAIL_BACKEND=console uvicorn --factory pilates.main:create_app --reload
```

### 20.2 Cómo verificar el job nocturno

1. Buscar en `docker compose logs worker` las líneas `pilates.open_next_day[…] succeeded … {'fecha': …, 'estado': 'generada', …}` y los `dispatch_outbox` posteriores.
   - Los logs de los contenedores están en **UTC**: las 20:00 de Argentina aparecen como 23:00.
2. `GET /clases/disponibles` con un alumno: la fecha objetivo tiene que estar en `abierta`.
3. En Mailpit (http://localhost:8025, o `GET /api/v1/messages`) tienen que aparecer "Ya podés reservar tus turnos del …" y, si correspondía, "Sin clases el …".

### 20.3 Problemas frecuentes

| Síntoma | Causa / solución |
|---|---|
| Hoy no tiene clases al levantar por primera vez | La grilla la genera el job del día hábil anterior. Generala desde Admin → Clases o con `POST /admin/clases/generar {"fecha": "AAAA-MM-DD"}` |
| El front no conecta desde el teléfono | Agregar `http://<IP>:3000` a `CORS_ORIGINS` y usar `FRONTEND_URL=http://<IP>:3000`; recrear el contenedor `api` |
| Los emails no llegan | Revisar que `worker` esté arriba y sus logs (los reintentos de `dispatch_outbox` usan backoff). Consultar las filas pendientes de `outbox` y su `last_error` (ver [§12.3](consistencia-eventos-datos.md#123-outbox-transaccional)) |
| "Mañana" aparece `pendiente_apertura` antes de las 20:00 | Es lo esperado: la apertura es a `BOOKING_OPENING_TIME` |
| 422 en `/auth/register` con emails `.local` | `EmailStr` rechaza dominios reservados; usar un dominio real o `example.com` en pruebas |

---

## 21. Limitaciones conocidas y mejoras posibles

Ordenadas por impacto estimado.

1. **Latencia de login y registro en ráfagas.** bcrypt (costo 12, ~0,5 s por hash) satura la CPU con muchos logins simultáneos. La API ya corre con 4 workers, pero eso sólo mejora los demás endpoints (ver [§19.3](#193-workers-de-uvicorn)). *Mejoras posibles:*
   - Más CPU, o bajar el costo de bcrypt a 10 (~4 veces más rápido; decisión de seguridad).
   - Login, registro y recuperación de contraseña ya tienen rate limiting (ver [§17.1](#171-rate-limiting)).
2. **Horizonte de cierres de 90 días.** `closed_dates_from` y `next_open_weekday` miran 90 días hacia adelante. Un cierre continuo más largo no se saltea del todo (caso borde).
3. **Duplicación menor.** `scheduling.application.services.next_open_day` (consulta por día) y `calendar.next_open_weekday` (sobre un conjunto en memoria) resuelven lo mismo. Se podrían unificar.
4. **Pool y sesiones.** Engine síncrono con pool de 5 + 10 por proceso (configurable). Con 4 workers de uvicorn y el worker de Celery el máximo teórico ronda las 90 conexiones, cerca del límite de 100 de Postgres; si se suman workers, bajar `DB_MAX_OVERFLOW` o usar PgBouncer.
5. **Sin observabilidad estructurada.** Hay logs de texto, sin métricas ni tracing. Los resultados de Celery no se guardan (`backend=None`). *Mejora:* logs JSON, Flower o métricas de Celery, y un healthcheck que incluya la base y Redis.
