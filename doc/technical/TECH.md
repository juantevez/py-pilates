# Pilates Studio: documentación técnica

> Estado: octubre 2026 (migración `e3a9c7d1f5b2`, outbox de eventos).
> Alcance: backend `py-pilates`, y el front `pilates-ui` en lo que interactúa con la API.
> Los diagramas están en [Mermaid](https://mermaid.js.org/), que GitHub y la mayoría de los IDEs renderizan.

---

## Índice

1. [Visión general](#1-visión-general)
2. [Despliegue y componentes](#2-despliegue-y-componentes)
3. [Arquitectura del código](#3-arquitectura-del-código)
4. [Kernel compartido (`shared`)](#4-kernel-compartido-shared)
5. [Contexto `identity`](#5-contexto-identity)
6. [Contexto `scheduling`](#6-contexto-scheduling)
7. [Contexto `booking`](#7-contexto-booking)
8. [Contexto `notifications`](#8-contexto-notifications)
9. [Calendario de reservas (horizonte y apertura)](#9-calendario-de-reservas-horizonte-y-apertura)
10. [Flujos principales](#10-flujos-principales)
11. [Concurrencia y consistencia](#11-concurrencia-y-consistencia)
12. [Eventos de dominio y tareas asíncronas](#12-eventos-de-dominio-y-tareas-asíncronas)
13. [Modelo de datos](#13-modelo-de-datos)
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
- **Lista de espera:** cuando se libera un lugar se avisa por email a todos los anotados. Reserva el primero que entra.
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
- El `EventRegistry` con todas las suscripciones (ver [§12](#12-eventos-de-dominio-y-tareas-asíncronas)).
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
    U->>OB: notify(ids) — Celery: send_task por fila; si falla, sólo se loguea
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
4. `commit()` junta los eventos de todos los agregados trackeados más los extra y llama a `_commit(events)`. La implementación SQL los agrega a la tabla `outbox` (una fila por par evento-handler) **en la misma sesión**, hace `session.commit()` y recién entonces avisa al despachador (ver [§12.3](#123-outbox-transaccional)).
5. Un `IntegrityError` en el commit se convierte en `ConflictError("integrity_conflict")`. Es la última línea de defensa ante carreras (ver [§11](#11-concurrencia-y-consistencia)).
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
  3. Llama a `GetCurrentUser`, que consulta la base **en cada request** y verifica que el usuario exista y no esté bloqueado.
- **`deps.require_admin`.** Exige `role == "admin"` y, si no, responde 403.
- Los aliases `CurrentUser` y `AdminUser` se usan como anotaciones en los endpoints.

---

## 5. Contexto `identity`

### 5.1 Modelo

```mermaid
classDiagram
    class User {
        +UUID id
        +Email email
        +str full_name
        +str password_hash
        +Role role
        +UserStatus status
        +datetime created_at
        +str? blocked_reason
        +register(...)$ User
        +ensure_active()
        +change_password(hash)
        +block(reason)
        +unblock()
    }
    class Email {
        <<value object>>
        +str value
    }
    class PasswordResetToken {
        +UUID id
        +UUID user_id
        +str token_hash
        +datetime expires_at
        +datetime? used_at
        +is_valid(now) bool
        +consume(now)
    }
    class Role {
        <<enum>>
        alumno
        admin
    }
    class UserStatus {
        <<enum>>
        activo
        bloqueado
    }
    User --> Email
    User --> Role
    User --> UserStatus
    PasswordResetToken ..> User : user_id
```

Reglas:

- **Email.** Se valida con una regex simple y se normaliza (`invalid_email`). En HTTP, además, el registro valida con `EmailStr`, que rechaza TLDs reservados como `.local`.
- **Nombre.** Es obligatorio (`invalid_name`).
- **Contraseña.** Mínimo 8 caracteres (`weak_password`). bcrypt sólo considera los primeros 72 bytes.
- **Bloqueo.** No se puede bloquear a un admin (`cannot_block_admin`). Bloquear a alguien que ya está bloqueado no hace nada. El bloqueo emite `UserBlocked`.
- **Usuario bloqueado.**
  - `ensure_active()` lanza `PermissionDenied("user_blocked")`.
  - No puede loguearse, ni usar un token emitido antes del bloqueo, ni reservar.
- **Token de reset.**
  - Es de un solo uso y tiene vencimiento (`invalid_reset_token`).
  - En la base sólo se guarda el **hash**; el token en claro viaja únicamente en el email.

### 5.2 Casos de uso

| Caso de uso | Qué hace | Eventos / auditoría |
|---|---|---|
| `RegisterUser` | Alta pública (rol alumno; `cli create-admin` lo usa con rol admin). `email_taken` si existe | `UserRegistered` (sin handlers) |
| `CreateUserByAdmin` | Alta por recepción: contraseña aleatoria (nadie la conoce) + token con TTL `INVITE_TTL_HOURS` (72 h) | `UserInvited` → email de bienvenida con link a `/reset-password?token=…`; audit `user.created_by_admin` |
| `Login` | Verifica bcrypt y estado; emite JWT. Mensaje genérico para no revelar si el email existe | — |
| `GetCurrentUser` | Usado por `deps.current_user` | — |
| `RequestPasswordReset` | Invalida tokens previos del usuario y crea uno nuevo (TTL `PASSWORD_RESET_TTL_MINUTES`). **Nunca** revela si el email existe; ignora bloqueados | `PasswordResetRequested` → email |
| `ResetPassword` | Consume token (`SELECT … FOR UPDATE`), cambia hash | — |
| `BlockUser` / `UnblockUser` | Con lock del usuario | `UserBlocked` → cancela turnos futuros; audit `user.blocked` / `user.unblocked` |
| `ListUsers` | Búsqueda por nombre/email (`ILIKE`), filtro por estado, paginado (máx. 200). Incluye `asistencias`, `ausencias`, `reservas_activas` | — |

### 5.3 Adaptadores de seguridad

- **`BcryptPasswordHasher`.** Usa `bcrypt.gensalt()` con el costo por defecto (12). Es la operación más cara del sistema (ver [§19.2](#192-prueba-de-carga-locust)).
- **`JwtTokenService`.** HS256. Claims: `sub` (user id), `role`, `iat` y `exp`. Duración: `JWT_EXPIRES_MINUTES`, por defecto 720 (12 h).
- **`Sha256SecretTokenGenerator`.** `secrets.token_urlsafe(32)` y su hash SHA-256.

---

## 6. Contexto `scheduling`

### 6.1 Modelo

```mermaid
classDiagram
    class Clase {
        +UUID id
        +date fecha
        +time start_time
        +time end_time
        +TimeBlock bloque
        +int max_capacity
        +ClaseStatus status
        +str? instructor_name
        +str? cancel_reason
        +schedule(fecha, slot, grid)$ Clase
        +starts_at(tz) datetime
        +cancel(motivo)
        +reactivate()
        +close_for_closure(motivo)
        +reopen()
        +change_capacity(n, ocupados)
        +assign_instructor(nombre)
    }
    class CierreGimnasio {
        +UUID id
        +date fecha
        +TipoCierre tipo
        +str? motivo
        +UUID creado_por
        +datetime creado_en
        +descripcion str
    }
    class GridTemplate {
        <<value object>>
        +Slot[] slots
        +timedelta duration
        +int default_capacity
    }
    class Slot {
        +time start
        +TimeBlock block
    }
    class BookingCalendar {
        <<domain service>>
        +time opening_time
        +bool allow_same_day
        +target_date(now, closed)
        +opening_at(fecha, now, closed)
        +is_bookable_date(fecha, now, closed)
        +bookable_dates(now, closed)
    }
    class TipoCierre {
        <<enum>>
        feriado_nacional
        vacaciones_gym
        enfermedad_instructor
        mantenimiento
        otro
    }
    GridTemplate *-- Slot
    Clase ..> GridTemplate : schedule()
```

### 6.2 Estados de una clase

```mermaid
stateDiagram-v2
    [*] --> activa : schedule() (job nocturno / generar grilla)
    activa --> cancelada : cancel(motivo) [admin, antes del inicio]
    cancelada --> activa : reactivate() [sin cierre ese día, antes del inicio]
    activa --> feriado : close_for_closure(motivo) [se carga un cierre]
    feriado --> activa : reopen() [se elimina el cierre]
    cancelada --> cancelada : close_for_closure() no la toca
    feriado --> feriado : reactivate() → class_closed_by_closure
```

- **Clase `cancelada` y cierres.** Una clase cancelada a mano no cambia cuando se carga un cierre. Por eso, al eliminar el cierre sigue cancelada: `reopen()` sólo reabre las que están en `feriado`.
- **Capacidad.** `change_capacity` no permite bajarla por debajo de los lugares ocupados (`capacity_below_bookings`) ni dejarla en menos de 1.

### 6.3 Casos de uso

| Caso de uso | Detalle |
|---|---|
| `GenerateDailyClasses.execute(fecha=None, notify=False)` | Corazón del job nocturno. Ver [§10.3](#103-apertura-nocturna-job-de-las-2000). Con `fecha` explícita (admin "Generar grilla") genera ese día. Valida `not_a_weekday` y `date_in_past`. Si la fecha tiene cierre devuelve `estado="cerrado"` |
| `ensure_daily_grid(uow, grid, fecha)` | Idempotente: crea los slots faltantes (único `(fecha, start_time)`) y reabre los `feriado` cuyo cierre ya no existe. Devuelve `(creadas, ids_reabiertos)` |
| `ListClassesForDate` | Read model admin: clase + `reservados` + `lista_espera` |
| `UpdateClase` | Capacidad y/o instructor, con lock de la clase. Audit `class.updated` |
| `CancelClase` | Sólo antes del inicio (`class_already_started`). `release_class_bookings` cancela los turnos en la misma transacción y vacía la lista de espera. Audit `class.cancelled` |
| `ReactivateClase` | Sólo antes del inicio y si el día no tiene cierre (`day_closed`). Emite `ClasesReabiertas` |
| `CreateCierres(desde, hasta?, tipo, motivo)` | Rango ≤ 366 días, sin pasado, sólo días hábiles (`invalid_range`). Falla entero si algún día ya tiene cierre (`closure_exists`). Por día: crea cierre, pasa clases a `feriado`, cancela turnos. Audit `closure.created` por día. Si el día **ya estaba habilitado** (hoy, o la apertura nocturna ya pasó), avisa `DiaSinClases` a los demás alumnos en el momento; y si el cierre adelanta el próximo día reservable, genera su grilla y avisa `TurnosHabilitados` (ver [§10.4](#104-carga-y-eliminación-de-cierres)) |
| `UpdateCierre` | Cambia tipo/motivo. Audit `closure.updated` |
| `DeleteCierre.execute(cierre_id)` / `.execute_range(desde, hasta)` | Elimina y reabre. Si el día ya tenía grilla **o** queda dentro del horizonte reservable, llama a `ensure_daily_grid`. Emite `ClasesReabiertas` con los ids reabiertos. Audit `closure.deleted` |
| `ListCierres`, `GetCalendar` (admin, ≤ 92 días) | Read models |
| `GetStudentCalendar` | Hoy + 30 días por defecto. Por día: `hay_clases` y, si hay cierre, `tipo_cierre`, `motivo`, `etiqueta` legible ("Feriado - Sin clases"). No expone quién cargó el cierre |

---

## 7. Contexto `booking`

### 7.1 Modelo

```mermaid
classDiagram
    class Turno {
        +UUID id
        +UUID user_id
        +UUID clase_id
        +TurnoStatus status
        +datetime booked_at
        +datetime? cancelled_at
        +str? cancel_reason
        +datetime? attendance_marked_at
        +datetime? reminder_sent_at
        +ReservadoPor booked_by
        +bool cancelled_with_class
        +reserve(user, clase, now, by)$ Turno
        +rebook(now, by)
        +cancel_by_student(now, starts_at, min_notice)
        +cancel_by_admin(now, ends_at, motivo)
        +cancel_by_gym(now, motivo)
        +cancel_with_class(now, motivo)
        +mark_attendance(attended, now, class_date)
        +mark_reminder_sent(now)
    }
    class WaitlistEntry {
        +UUID id
        +UUID user_id
        +UUID clase_id
        +datetime created_at
        +datetime? notified_at
    }
    class ClassSnapshot {
        <<ACL>>
        +UUID id
        +date fecha
        +datetime starts_at
        +datetime ends_at
        +str bloque
        +int capacity
        +bool is_active
    }
    class BookingPolicy {
        <<domain service>>
        +int max_per_day
        +int max_per_block
        +timedelta min_advance
        +timedelta cancel_min_notice
        +ensure_can_book(...)
        +ensure_admin_can_book(...)
        +ensure_can_join_waitlist(...)
        +can_book(...) bool
        +can_cancel(...) bool
    }
    Turno ..> ClassSnapshot : reglas usan
    BookingPolicy ..> ClassSnapshot
```

### 7.2 Estados de un turno

```mermaid
stateDiagram-v2
    [*] --> confirmada : reserve() [alumno o gimnasio]
    confirmada --> cancelada : cancel_by_student [≥ 1 h antes del inicio]
    confirmada --> cancelada_por_admin : cancel_by_admin [antes del fin de la clase] cancel_with_class [clase cancelada / cierre] cancel_by_gym [cuenta suspendida]
    cancelada --> confirmada : rebook()
    cancelada_por_admin --> confirmada : rebook()
    confirmada --> asistio : mark_attendance(true) [desde el día de la clase]
    confirmada --> no_asistio : mark_attendance(false)
    asistio --> no_asistio : corrección
    no_asistio --> asistio : corrección
```

Puntos importantes:

- **Una fila por `(user_id, clase_id)`.** Hay una restricción única. Si el alumno vuelve a reservar una clase que había cancelado, `rebook()` **reutiliza la fila**: resetea `cancelled_*`, `reminder_sent_at` y `cancelled_with_class`, y emite `TurnoReservado` de nuevo.
- **Lugares ocupados.** Cuentan los estados `confirmada`, `asistio` y `no_asistio` (`_OCCUPYING`). Así, marcar asistencia no libera el cupo ni el límite diario. Esta definición se usa en tres lugares:
  - `count_occupied`, para el cupo.
  - `list_occupied_by_user_on_date`, para los límites diarios.
  - Los conteos de la grilla y de los listados admin.
- **`booked_by`.** Indica quién reservó: `alumno` o `gimnasio`.
- **`cancelled_with_class = true`.** Se marca sólo cuando el turno se cayó porque el gimnasio canceló o cerró **la clase entera**. Si después la clase se reactiva o el cierre se elimina, el aviso "volvió la clase" va sólo a esos alumnos. No llega a quienes cancelaron por su cuenta ni a quienes recepción les canceló el turno puntual.
- **Variantes de cancelación.**

  | Método | Estado | `por` | `con_clase` | Validaciones | Uso |
  |---|---|---|---|---|---|
  | `cancel_by_student` | `cancelada` | alumno | false | activo, ≥ `cancel_min_notice` antes | `POST /turnos/{id}/cancelar` |
  | `cancel_by_admin` | `cancelada_por_admin` | gimnasio | false | activo, antes de `ends_at` | `POST /admin/turnos/{id}/cancelar` |
  | `cancel_with_class` | `cancelada_por_admin` | gimnasio | **true** | no-op si no está activo | `release_class_bookings` |
  | `cancel_by_gym` | `cancelada_por_admin` | gimnasio | false | no-op si no está activo | bloqueo de usuario ("Cuenta suspendida") |

### 7.3 Política de reserva

Orden de validación en `ensure_can_book` (alumno):

```mermaid
flowchart TD
    A[clase.is_active?] -- no --> E1[422 class_not_available]
    A -- sí --> B[fecha dentro de la ventana?<br/>BookingCalendar.is_bookable_date]
    B -- no --> E2[422 outside_booking_window]
    B -- sí --> C[now < starts_at - min_advance?]
    C -- no --> E3[422 booking_closed]
    C -- sí --> D[ocupados < capacidad?]
    D -- no --> E4[409 class_full]
    D -- sí --> F[turnos del día < max_per_day?]
    F -- no --> E5[422 daily_limit_reached]
    F -- sí --> G[turnos en el mismo bloque < max_per_block?]
    G -- no --> E6[422 block_limit_reached]
    G -- sí --> OK[✔ reservar]
```

Antes de la política, el caso de uso también rechaza estos casos:

- `already_booked` (409): el alumno ya tiene un turno activo en esa clase.
- `user_blocked` (403): la cuenta está bloqueada.

**`ensure_admin_can_book`** (recepción) sólo exige tres cosas:

1. Que la clase esté activa.
2. Que `now < ends_at`. Recepción puede anotar a alguien en una clase en curso, pero no en una terminada (`class_finished`).
3. Que haya cupo.

Saltea la ventana de reserva, la anticipación y los límites diarios y por bloque.

**`ensure_can_join_waitlist`** pide que la clase sea reservable (activa, en ventana, no empezada) y que esté **llena**. Si queda lugar responde `class_not_full`.

### 7.4 Casos de uso

| Caso de uso | Locks | Notas |
|---|---|---|
| `BookClass` | `users` FOR UPDATE → `clases` FOR UPDATE | Ver [§11](#11-concurrencia-y-consistencia). Si estaba en lista de espera, la entrada se borra |
| `AdminBookClass` | mismo orden | `booked_by=gimnasio`; audit `booking.created_by_admin` |
| `CancelBooking` | `turnos` FOR UPDATE, `clases` FOR UPDATE | Valida que el turno sea del usuario (`permission_denied`) |
| `AdminCancelBooking` | `turnos` FOR UPDATE, `clases` FOR UPDATE | Motivo opcional; audit `booking.cancelled_by_admin` |
| `JoinWaitlist` / `LeaveWaitlist` | — | Única `(user_id, clase_id)` en `lista_espera` |
| `MarkAttendance` | `turnos` FOR UPDATE | Audit `booking.attendance` |
| `CancelUpcomingBookingsOfBlockedUser` | — | Handler de `UserBlocked` (worker) |
| `release_class_bookings` | — | API para `scheduling`: `cancel_with_class` por turno + `waitlist.delete_by_clase` |

Consultas (read models):

- **`GetAvailability`.** Arma la pantalla "Reservar" del alumno. Ver [§9](#9-calendario-de-reservas-horizonte-y-apertura). Por cada día devuelve un `estado`:
  - `abierta`
  - `pendiente_apertura`, con la hora de `apertura`.
  - `cerrado`, con `motivo_cierre` y `proximo_dia_habil`.
  - `sin_clases`

  Por cada clase devuelve `capacidad`, `reservados`, `disponibles`, `completa` y `mi_turno_id`, más tres flags: `puede_reservar`, `puede_cancelar` y `puede_anotarse_lista_espera`. El front se basa sólo en esos flags y no replica reglas.
- **`GetMyBookings`.** Devuelve `proximas` en orden ascendente, con `puede_cancelar`, y `pasadas` en orden descendente.
- **`GetAttendanceSheet`.** Por cada clase del día, lista los alumnos con turno `confirmada`, `asistio` o `no_asistio`.

---

## 8. Contexto `notifications`

`NotificationHandlers` reacciona a eventos y **encola** los correos: no habla con el SMTP. Por cada destinatario agrega un evento `CorreoSolicitado(to, subject, body)` a la UnitOfWork y hace commit, así cada correo queda como una fila propia del outbox. El único que envía es `DeliverEmail` (handler `notifications.enviar_correo`), a través del puerto `EmailSender`.

```mermaid
flowchart LR
    E["TurnosHabilitados<br/>(1 fila del outbox)"] --> H[on_turnos_habilitados]
    H -- "1 transacción" --> C1["CorreoSolicitado → a@"] & C2["CorreoSolicitado → b@"] & C3["CorreoSolicitado → c@"]
    C1 & C2 & C3 --> D[DeliverEmail → SMTP]
```

- **Ventaja:** si el SMTP rechaza un correo, sólo esa fila suma un intento y se reintenta. Los demás destinatarios no lo reciben dos veces.
- **Marcas atómicas:** las marcas de "ya avisado" (`lista_espera.notified_at`, `turnos.reminder_sent_at`) se guardan en la misma transacción que los correos encolados.

Hay dos adaptadores de `EmailSender`: `SmtpEmailSender` (timeout de 15 s, STARTTLS y login opcionales) y `ConsoleEmailSender` (loguea el mail y lo guarda en su lista `outbox`; se usa en tests).

| Evento | Handler | Destinatarios | Plantilla (asunto) |
|---|---|---|---|
| `TurnoReservado` | `on_turno_reservado` | el alumno | "Reserva confirmada: {fecha} {hora} hs" |
| `TurnoCancelado` (`por=gimnasio`, `con_clase=true`) | `on_turno_cancelado` | el alumno | "Clase cancelada: …" (`cancelacion_gimnasio`) |
| `TurnoCancelado` (`por=gimnasio`, `con_clase=false`) | `on_turno_cancelado` | el alumno | "Turno cancelado: …" (`turno_cancelado_gimnasio`) |
| `TurnoCancelado` (cualquiera) | `on_turno_cancelado` | **toda** la lista de espera (no bloqueados), si la clase está activa, no empezó y quedó lugar | "¡Se liberó un lugar! …" + link `/clases/{id}`; marca `notified_at` |
| `ClasesReabiertas` | `on_clases_reabiertas` | alumnos con turnos `cancelled_with_class` en esas clases (agrupado por alumno) | "¡Buenas noticias! …" + link `/reservar` |
| `TurnosHabilitados` | `on_turnos_habilitados` | todos los alumnos activos | "Ya podés reservar tus turnos del {fecha}" |
| `DiaSinClases` | `on_dia_sin_clases` | todos los alumnos activos, menos `excluir_user_ids` (los que ya reciben la cancelación de su turno) | "Sin clases el {fecha}" (con el próximo día hábil) |
| `PasswordResetRequested` | `on_password_reset_requested` | el usuario | "Recuperación de contraseña" |
| `UserInvited` | `on_user_invited` | el alumno nuevo | "Bienvenido/a al estudio de Pilates" |

**`SendClassReminders`** corre como tarea periódica cada 10 minutos. Busca los turnos `confirmada` sin recordatorio, de clases activas, que empiezan dentro de `[now, now + REMINDER_HOURS_BEFORE]`. Los toma con `FOR UPDATE SKIP LOCKED`, para que dos ejecuciones simultáneas no manden duplicados. Encola el recordatorio y marca `reminder_sent_at` en la misma transacción. Si el SMTP falla, sólo se reintenta ese correo y la próxima pasada no reenvía los que ya salieron.

Las plantillas (`templates.py`) son texto plano en español rioplatense y escriben las fechas largas a mano, por ejemplo "miércoles 21 de octubre", sin depender del locale.

---

## 9. Calendario de reservas (horizonte y apertura)

`BookingCalendar` (en `scheduling/domain/calendar.py`) es un servicio de dominio sin estado. Recibe `now` y el conjunto `closed`, que son las fechas con cierre cargado en los próximos 90 días según `closed_dates_from(uow, hoy)`.

| Función | Definición |
|---|---|
| `next_open_weekday(d, closed)` | Primer día hábil > `d` que no está en `closed` (tope 90 saltos) |
| `previous_open_weekday(d, closed)` | Último día hábil < `d` que no está en `closed` |
| `target_date(now, closed)` | `next_open_weekday(hoy, closed)`: el día que se puede reservar "para mañana" |
| `opening_at(fecha, now, closed)` | `previous_open_weekday(fecha) @ BOOKING_OPENING_TIME` |
| `is_bookable_date(fecha, now, closed)` | Hábil **y** (`fecha == hoy` y `allow_same_day`) **o** (`fecha == target` y `now ≥ opening_at`) |
| `bookable_dates(now, closed)` | `[hoy?] + [días hábiles cerrados intermedios] + [target]`, lo que muestra la pantalla "Reservar" |

### 9.1 Ejemplos

Sin cierres:

| Ahora | Fechas mostradas | Estado del target |
|---|---|---|
| Lun 18:40 | Lun (hoy), Mar | Mar `pendiente_apertura` (apertura Lun 20:00) |
| Lun 20:05 | Lun, Mar | Mar `abierta` |
| Vie 20:05 | Vie, Lun | Lun `abierta` |
| Sáb 10:00 | Lun | Lun `abierta` (abrió el Vie 20:00); el sábado no es hábil |

Con el **martes 20 cerrado** (feriado), el caso que se vio en producción el 19/10/2026:

```mermaid
gantt
    title Lunes 19 → miércoles 21 con el martes 20 cerrado
    dateFormat YYYY-MM-DD HH:mm
    axisFormat %a %d %H:%M
    section Lun 19
    Clases del día (reservables si no empezaron) :a1, 2026-10-19 08:00, 10h
    Apertura de los turnos del Mié 21          :milestone, m1, 2026-10-19 20:00, 1m
    section Mar 20
    Cerrado (feriado)                           :crit, c1, 2026-10-20 00:00, 24h
    section Mié 21
    Reservable desde el Lun 20:00               :active, b1, 2026-10-19 20:00, 36h
```

- A las 18:40 del lunes, la pantalla muestra:
  - Lun 19: `abierta`.
  - Mar 20: `cerrado`, con próximo día hábil Mié 21.
  - Mié 21: `pendiente_apertura`, con apertura Lun 20:00.
- A las 20:00 corre el job:
  - Genera la grilla del 21 (idempotente).
  - Emite `DiaSinClases(20)` y `TurnosHabilitados(21)`.
  - El 21 pasa a `abierta`.
- El martes 20 a las 20:00 el job vuelve a correr, porque beat corre de lunes a viernes:
  - `target_date` sigue siendo el 21.
  - Su `opening_at` fue el lunes, no hoy, así que **no reenvía** los emails.
  - Sólo garantiza que la grilla exista.

---

## 10. Flujos principales

### 10.1 Reserva de un alumno

```mermaid
sequenceDiagram
    autonumber
    actor A as Alumno
    participant API as POST /turnos
    participant UC as BookClass
    participant DB as PostgreSQL
    participant Q as Redis/Celery
    participant N as NotificationHandlers

    A->>API: {clase_id}
    API->>UC: execute(user_id, clase_id)
    UC->>DB: SELECT users WHERE id=… FOR UPDATE
    UC->>DB: SELECT clases WHERE id=… FOR UPDATE
    UC->>DB: turno existente (user, clase)
    UC->>DB: turnos ocupados del alumno ese día
    UC->>DB: SELECT cierres próximos 90 días
    UC->>DB: COUNT ocupados de la clase
    UC->>UC: BookingPolicy.ensure_can_book(...)
    alt había turno cancelado
        UC->>DB: UPDATE turnos (rebook)
    else
        UC->>DB: INSERT turnos
    end
    UC->>DB: DELETE lista_espera (si estaba)
    UC->>DB: INSERT outbox + COMMIT
    UC->>Q: dispatch_outbox(fila TurnoReservado → notifications.confirmacion)
    API-->>A: 201 TurnoDTO
    Q->>N: on_turno_reservado
    N-->>A: email "Reserva confirmada"
```

### 10.2 Cancelación y lista de espera

```mermaid
sequenceDiagram
    autonumber
    actor A as Alumno A (con turno)
    actor B as Alumnos en lista de espera
    participant API
    participant DB
    participant W as Worker

    A->>API: POST /turnos/{id}/cancelar
    API->>DB: lock turno + clase, valida ≥ 1 h antes, UPDATE status=cancelada
    API->>DB: COMMIT
    API->>W: TurnoCancelado(por=alumno)
    W->>DB: clase activa, no empezó, ocupados < capacidad?
    W-->>B: email "¡Se liberó un lugar!" a todos
    W->>DB: lista_espera.notified_at = now
    Note over B: Reserva el primero que hace POST /turnos.<br/>Al reservar, su entrada de lista de espera se borra.
```

### 10.3 Apertura nocturna (job de las 20:00)

```mermaid
flowchart TD
    S([beat: L–V a BOOKING_OPENING_TIME]) --> T[pilates.open_next_day]
    T --> G["GenerateDailyClasses.execute(notify=True)"]
    G --> C["closed = cierres de los próximos 90 días"]
    C --> F["fecha = target_date(now, closed)"]
    F --> NN{"opening_at(fecha).date() == hoy?"}
    NN -- no --> N0["notify = False<br/>ya se avisó otro día"]
    NN -- sí --> N1[notify = True]
    N0 --> CI
    N1 --> CI{"¿fecha tiene cierre?<br/>sólo si hay más de 90 días cerrados"}
    CI -- sí --> X[DiaSinClases si notify] --> R1([estado=cerrado])
    CI -- no --> EG[ensure_daily_grid: crea faltantes / reabre 'feriado']
    EG --> NT{notify?}
    NT -- sí --> SK[DiaSinClases por cada día hábil cerrado<br/>entre hoy y fecha, si NOTIFY_CLOSED_DAY] --> TH[TurnosHabilitados fecha]
    NT -- no --> RE
    TH --> RE{¿reabrió clases?}
    RE -- sí --> CR[ClasesReabiertas]
    RE -- no --> CM
    CR --> CM[COMMIT → publicar eventos]
    CM --> R2(["estado=generada, creadas=N"])
```

Resultado del log de Celery el 19/10/2026 a las 20:00:

```
Task pilates.open_next_day[…] succeeded in 0.43s: {'fecha': '2026-10-21', 'estado': 'generada', 'creadas': 0}
```

Luego se ejecutaron dos despachos de eventos (`dispatch_event`, el formato previo al outbox): `DiaSinClases(20)` y `TurnosHabilitados(21)`. Mailpit recibió 4 + 4 correos, uno de cada tipo por alumno activo.

### 10.4 Carga y eliminación de cierres

```mermaid
sequenceDiagram
    autonumber
    actor R as Recepción
    participant API
    participant SCH as CreateCierres / DeleteCierre
    participant BK as release_class_bookings
    participant DB
    participant W as Worker

    R->>API: POST /admin/cierres {desde, hasta, tipo, motivo}
    API->>SCH: CreateCierres
    loop cada día hábil del rango
        SCH->>DB: INSERT cierres_gimnasio
        SCH->>DB: clases del día → status=feriado
        SCH->>BK: cancelar turnos activos (cancel_with_class) + vaciar lista de espera
        SCH->>DB: audit closure.created
    end
    SCH->>DB: COMMIT
    SCH->>W: TurnoCancelado(con_clase=true) por turno → email "Clase cancelada"

    R->>API: DELETE /admin/cierres/{id} (o ?desde=&hasta=)
    API->>SCH: DeleteCierre
    SCH->>DB: DELETE cierre + audit closure.deleted
    alt el día tenía grilla o está en el horizonte reservable
        SCH->>DB: ensure_daily_grid (crea / reabre 'feriado' → 'activa')
        SCH->>W: ClasesReabiertas → email a turnos cancelled_with_class
    end
```

¿Quién se entera de un cierre?

| Momento en que se carga | Alumnos con turno ese día | Resto de los alumnos |
|---|---|---|
| Antes de la apertura del día (ej. el lunes 18:00 cierra el martes) | "Clase cancelada: …" (sólo si un admin les había reservado) | "Sin clases el …" en el job de las 20:00 |
| Con el día ya habilitado (ej. el lunes 21:00 cierra el martes, o cierra hoy) | "Clase cancelada: …" con el motivo | "Sin clases el …" en el momento |

En el segundo caso el cierre puede adelantar el próximo día reservable: si el lunes 21:00 se cierra el martes, el miércoles queda habilitado de inmediato (su apertura pasa a ser el lunes 20:00). Se genera su grilla y se envía "Ya podés reservar tus turnos del miércoles…", porque el job del martes no lo va a anunciar.

Las reservas canceladas **no se restauran** al reabrir, porque el lugar pudo haberlo tomado otro alumno. Lo que se hace es avisar a los afectados para que vuelvan a reservar.

### 10.5 Alta de alumno por recepción

```mermaid
sequenceDiagram
    autonumber
    actor R as Recepción
    actor A as Alumno nuevo
    participant API
    participant DB
    participant W as Worker

    R->>API: POST /admin/usuarios {email, full_name}
    API->>DB: INSERT users (hash de contraseña aleatoria), flush
    API->>DB: INSERT password_reset_tokens (hash, TTL 72 h)
    API->>DB: audit user.created_by_admin, COMMIT
    API->>W: UserInvited(set_password_url)
    W-->>A: email "Bienvenido/a" con /reset-password?token=…
    A->>API: POST /auth/reset-password {token, new_password}
    API->>DB: consume token, UPDATE password_hash
    Note over R,API: Recepción puede reservarle turnos desde ya<br/>(POST /admin/turnos), aunque no haya definido su contraseña.
```

### 10.6 Bloqueo de usuario

1. `POST /admin/usuarios/{id}/bloquear` pasa el estado a `bloqueado`, emite `UserBlocked` y registra el audit `user.blocked`.
2. El worker ejecuta `CancelUpcomingBookingsOfBlockedUser`: aplica `cancel_by_gym("Cuenta suspendida")` a cada turno confirmado cuya clase empieza desde ahora.
3. Cada cancelación emite `TurnoCancelado`, que avisa al alumno y a la lista de espera.
4. A partir de ahí, cualquier request con su token responde `403 user_blocked`, porque `GetCurrentUser` verifica el estado.

---

## 11. Concurrencia y consistencia

### 11.1 Reservas simultáneas

Hay dos invariantes que proteger:

- **Cupo.** Nunca más de `max_capacity` lugares ocupados.
- **Límites diarios.** Como mucho 2 turnos por día y 1 por bloque por alumno, aunque el alumno mande dos requests en paralelo para clases distintas.

La solución es tomar locks pesimistas **siempre en el mismo orden**:

```mermaid
sequenceDiagram
    participant T1 as Tx alumno X (clase C)
    participant T2 as Tx alumno Y (clase C)
    participant T3 as Tx alumno X (clase D)
    participant DB

    T1->>DB: SELECT users X FOR UPDATE ✔
    T2->>DB: SELECT users Y FOR UPDATE ✔
    T3->>DB: SELECT users X FOR UPDATE ⏳ (espera a T1)
    T1->>DB: SELECT clases C FOR UPDATE ✔
    T2->>DB: SELECT clases C FOR UPDATE ⏳ (espera a T1)
    T1->>DB: count/validar/INSERT, COMMIT
    T2->>DB: ✔ obtiene C, recuenta (ve el turno de T1)
    T3->>DB: ✔ obtiene X, ve el turno de X en C (límites)
```

- **Lock del usuario.** Serializa las reservas del mismo alumno, y con eso protege los límites por día y por bloque.
- **Lock de la clase.** Serializa las reservas de la misma clase, y con eso protege el cupo.
- **Orden fijo (usuario → clase).** `BookClass` y `AdminBookClass` toman los locks en el mismo orden, así que no hay deadlocks entre ellos.
- **Aislamiento.** Postgres trabaja en READ COMMITTED: después de obtener el lock, el `COUNT` ve las filas que confirmaron las transacciones anteriores.
- **Última defensa.** La restricción única `uq_turnos_user_clase` impide dos filas para el mismo alumno y clase. Si se viola, el `IntegrityError` se convierte en `409 integrity_conflict`.
- **Cancelaciones y asistencia.** Toman el lock del turno (`turnos.get` usa `FOR UPDATE`). La cancelación también toma el de la clase.
- **Recordatorios.** Usan `FOR UPDATE SKIP LOCKED`.

Hay dos tests que lo verifican en `tests/application/test_concurrency.py`. Lanzan N hilos contra una clase de capacidad limitada y comprueban que nunca se supera el cupo:

- Sólo alumnos.
- Alumnos y recepción mezclados.

### 11.2 Consistencia inmediata y eventual

| Inmediata (misma transacción) | Eventual (evento → worker) |
|---|---|
| Cancelar los turnos de una clase cancelada o cerrada | Todos los emails |
| Vaciar la lista de espera de esa clase | Cancelar los turnos futuros de un usuario bloqueado |
| Auditoría de acciones admin | Aviso a la lista de espera |
| Borrar la entrada de lista de espera al reservar | Aviso de clases reabiertas |

---

## 12. Eventos de dominio y tareas asíncronas

### 12.1 Catálogo de eventos

| Evento | Emisor | Campos | Handlers (nombre en el registry) |
|---|---|---|---|
| `UserRegistered` | `User.register` | `user_id` | — |
| `UserInvited` | `CreateUserByAdmin` | `user_id`, `set_password_url` | `notifications.bienvenida` |
| `UserBlocked` | `User.block` | `user_id`, `reason` | `booking.cancelar_turnos_bloqueado` |
| `PasswordResetRequested` | `RequestPasswordReset` | `user_id`, `reset_url` | `notifications.reset_password` |
| `TurnoReservado` | `Turno.reserve` / `rebook` | `turno_id`, `user_id`, `clase_id` | `notifications.confirmacion` |
| `TurnoCancelado` | `Turno._cancel` | `turno_id`, `user_id`, `clase_id`, `por`, `motivo`, `con_clase` | `notifications.cancelacion_y_lista_espera` |
| `TurnosHabilitados` | `GenerateDailyClasses`, `CreateCierres` | `fecha` | `notifications.apertura` |
| `DiaSinClases` | `GenerateDailyClasses`, `CreateCierres` | `fecha`, `motivo`, `proximo_dia_habil`, `excluir_user_ids` | `notifications.dia_sin_clases` |
| `ClasesReabiertas` | `ReactivateClase`, `DeleteCierre`, `GenerateDailyClasses` | `clase_ids` | `notifications.clases_reabiertas` |
| `CorreoSolicitado` | handlers de `notifications`, `SendClassReminders` | `to`, `subject`, `body` | `notifications.enviar_correo` |

Todos heredan `occurred_at`.

### 12.2 Celery

Configuración (`worker/celery_app.py`):

- **Serialización.** `timezone=TIMEZONE`, `enable_utc=False`, serialización JSON.
- **Confiabilidad.** `task_acks_late=True` y `task_reject_on_worker_lost=True`: si el worker muere a mitad de una tarea, la tarea se reencola.
- **Reparto.** `worker_prefetch_multiplier=1`.
- **Resultados.** `backend=None`: no se guardan los resultados de las tareas.
- **Publicación.** `task_publish_retry_policy` con 2 reintentos cortos: si Redis no responde, el request no se traba más de ~1 s. El evento ya está en el outbox.

| Tarea | Disparador | Reintentos | Qué hace |
|---|---|---|---|
| `pilates.dispatch_outbox(message_id)` | `CeleryOutboxNotifier` (tras el commit, o el relay) | `autoretry_for=Exception`, backoff exponencial, máx. 5 | Procesa **una** fila del outbox: un evento para un handler |
| `pilates.relay_outbox` | beat `outbox-relay`: cada minuto | — | Reenvía las filas pendientes cuyo aviso se perdió y purga las procesadas |
| `pilates.dispatch_event(event_name, handler_name, payload)` | — (formato anterior) | igual que `dispatch_outbox` | Sólo para mensajes encolados antes del deploy del outbox |
| `pilates.open_next_day` | beat `apertura-turnos`: `crontab(hour=HH, minute=MM, day_of_week="mon-fri")` | máx. 3 | `GenerateDailyClasses.execute(notify=True)` |
| `pilates.send_reminders` | beat `recordatorios`: cada 10 min | — | `SendClassReminders.execute()` |

Hay **una fila (y una tarea) por par (evento, handler)**. Así, si falla un handler (por ejemplo, el SMTP del aviso a la lista de espera), al reintentarlo no se reenvía la confirmación ni se repiten otros efectos.

### 12.3 Outbox transaccional

```mermaid
sequenceDiagram
    autonumber
    participant UC as Caso de uso
    participant DB as PostgreSQL
    participant R as Redis
    participant W as Worker (dispatch_outbox)
    participant RL as Beat (relay_outbox, cada minuto)

    UC->>DB: cambios + INSERT outbox, COMMIT
    UC->>R: send_task(dispatch_outbox, id)
    alt Redis responde
        R->>W: dispatch_outbox(id)
    else Redis caído / mensaje perdido
        Note over UC: se loguea un warning, el request responde OK
        RL->>DB: filas pendientes con más de 2 min (sin reenviar en los últimos 5)
        RL->>R: send_task(dispatch_outbox, id) y marca last_relayed_at
        R->>W: dispatch_outbox(id)
    end
    W->>DB: SELECT … WHERE processed_at IS NULL FOR UPDATE SKIP LOCKED
    alt ya procesada o tomada por otro
        W-->>W: no hace nada (idempotente)
    else pendiente
        W->>W: ejecuta el handler
        alt OK
            W->>DB: processed_at = now, COMMIT
        else falla
            W->>DB: attempts + 1, last_error; Celery reintenta con backoff
        end
    end
```

| Constante (`outbox.py`) | Valor | Para qué |
|---|---|---|
| `RELAY_GRACE` | 2 min | No reenviar lo que el aviso inmediato todavía está procesando |
| `RELAY_REQUEUE_AFTER` | 5 min | No reenviar mientras Celery sigue con sus reintentos |
| `MAX_ATTEMPTS` | 10 | Después, la fila queda pendiente para revisión manual y el relay loguea un error |
| `RELAY_BATCH` | 500 | Filas por pasada |
| `RETENTION` | 7 días | Se borran las filas procesadas más viejas |

Garantía: **al menos una vez**. Un handler se puede repetir si el proceso muere entre que termina y se marca `processed_at`; como cada fila de `notifications.enviar_correo` es un único correo, el peor caso es que **un** destinatario reciba un mail repetido.

Para ver qué quedó trabado:

```sql
SELECT event_name, handler_name, attempts, last_error, created_at
FROM outbox WHERE processed_at IS NULL ORDER BY created_at;
```

---

## 13. Modelo de datos

### 13.1 Diagrama entidad-relación

```mermaid
erDiagram
    users ||--o{ turnos : "user_id"
    users ||--o{ lista_espera : "user_id"
    users ||--o{ password_reset_tokens : "user_id (CASCADE)"
    users ||--o{ admin_audit_log : "admin_id"
    users ||--o{ cierres_gimnasio : "creado_por"
    clases ||--o{ turnos : "clase_id"
    clases ||--o{ lista_espera : "clase_id (CASCADE)"

    users {
        uuid id PK
        varchar email UK
        varchar password_hash
        varchar full_name
        varchar role "alumno|admin"
        varchar status "activo|bloqueado"
        varchar blocked_reason
        timestamptz created_at
    }
    clases {
        uuid id PK
        date fecha "idx"
        time start_time "UK(fecha,start_time)"
        time end_time
        varchar bloque "manana|tarde"
        int max_capacity
        varchar instructor_name
        varchar status "activa|cancelada|feriado"
        varchar cancel_reason
    }
    turnos {
        uuid id PK
        uuid user_id FK "idx"
        uuid clase_id FK "idx(clase_id,status)"
        varchar status "confirmada|cancelada|cancelada_por_admin|asistio|no_asistio"
        timestamptz booked_at
        timestamptz cancelled_at
        varchar cancel_reason
        timestamptz attendance_marked_at
        timestamptz reminder_sent_at
        varchar booked_by "alumno|gimnasio"
        bool cancelled_with_class
    }
    lista_espera {
        uuid id PK
        uuid user_id FK
        uuid clase_id FK "idx"
        timestamptz created_at
        timestamptz notified_at
    }
    cierres_gimnasio {
        uuid id PK
        date fecha UK
        varchar tipo
        varchar motivo
        uuid creado_por FK
        timestamptz creado_en
    }
    password_reset_tokens {
        uuid id PK
        uuid user_id FK "idx"
        varchar token_hash UK
        timestamptz expires_at
        timestamptz created_at
        timestamptz used_at
    }
    outbox {
        uuid id PK
        varchar event_name
        varchar handler_name
        json payload
        timestamptz created_at "idx parcial: pendientes"
        timestamptz processed_at
        int attempts
        text last_error
        timestamptz last_relayed_at
    }
    admin_audit_log {
        uuid id PK
        uuid admin_id FK "idx"
        varchar action "idx"
        json details
        timestamptz created_at
    }
```

### 13.2 Restricciones e índices relevantes

| Restricción / índice | Propósito |
|---|---|
| `uq_clases_fecha_start_time` | Idempotencia de `ensure_daily_grid` (un slot por día y hora) |
| `uq_turnos_user_clase` | Un turno por alumno y clase; habilita `rebook` y es la última defensa contra carreras |
| `ix_turnos_clase_status (clase_id, status)` | `count_occupied`, conteos de grilla |
| `ix_turnos_user_id` | Mis turnos, límites diarios, bloqueo |
| `uq_lista_espera_user_clase` | Una entrada por alumno y clase |
| `cierres_gimnasio.fecha UNIQUE` | Un cierre por día |
| `ix_outbox_pending (created_at) WHERE processed_at IS NULL` | Índice parcial para el relay: sólo indexa las filas pendientes |
| `password_reset_tokens.token_hash UNIQUE` | Búsqueda por hash |
| Enums como `VARCHAR(32)` + `CHECK` | `native_enum=False, create_constraint=True`: agregar un valor requiere una migración del CHECK, no un `ALTER TYPE` |

### 13.3 Fechas y zonas horarias

- **Columnas de instante.** Todas son `timestamptz`.
- **Clases.** `clases.fecha` (`date`) y `start_time`/`end_time` (`time`, sin zona) están en **hora local del estudio** (`TIMEZONE`). El dominio arma `starts_at` combinando esos valores con la tz del `Clock`.
- **Comparaciones en SQL.** Las consultas por rango (recordatorios, turnos futuros) convierten `now` a hora local antes de comparar con `fecha` + `start_time`.

### 13.4 Migraciones (Alembic)

| Revisión | Archivo | Cambios |
|---|---|---|
| `04edcad12a0b` | `20261015_04edcad12a0b_initial_schema.py` | Esquema inicial (7 tablas) |
| `7c1e4b9a2d3f` | `20261019_7c1e4b9a2d3f_turnos_booked_by.py` | `turnos.booked_by` (`alumno` por defecto) |
| `b5d2f8e1c4a7` | `20261019_b5d2f8e1c4a7_turnos_cancelled_with_class.py` | `turnos.cancelled_with_class`. Backfill: `true` para los `cancelada_por_admin` cuyo motivo no es "Cuenta suspendida" |
| `e3a9c7d1f5b2` | `20261020_e3a9c7d1f5b2_outbox.py` | Tabla `outbox` + índice parcial `ix_outbox_pending (created_at) WHERE processed_at IS NULL` |

La API ejecuta `alembic upgrade head` cada vez que arranca. Para crear una revisión nueva:

```bash
.venv/bin/alembic revision --autogenerate -m "descripcion"   # con DATABASE_URL apuntando a una base migrada
```

El nombre de archivo sigue `file_template = YYYYMMDD_<rev>_<slug>`.

---

## 14. API HTTP

- **Base:** `http://<host>:8000`.
- **Documentación interactiva:** `/docs` (Swagger) y `/redoc`.
- **Autenticación:** `Authorization: Bearer <JWT>`.
- **Formato:** cuerpos y respuestas en JSON. Las fechas van en ISO-8601 (`AAAA-MM-DD`) y las horas como `HH:MM:SS`.

### 14.1 Auth (`/auth`)

| Método | Ruta | Auth | Body | Respuesta |
|---|---|---|---|---|
| POST | `/auth/register` | — | `{email, full_name, password(8–128)}` | 201 `UserDTO` · 409 `email_taken` · 422 |
| POST | `/auth/login` | — | `{email, password}` | 200 `{access_token, token_type, user}` · 401 · 403 `user_blocked` |
| POST | `/auth/forgot-password` | — | `{email}` | 202 siempre (mensaje genérico) |
| POST | `/auth/reset-password` | — | `{token, new_password}` | 204 · 422 `invalid_reset_token` / `weak_password` |
| GET | `/auth/me` | usuario | — | `UserDTO` |

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
| 401 | `invalid_token` | JWT inválido o expirado |
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
| `REMINDER_HOURS_BEFORE` | `12` | |
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
- **JWT.** No hay refresh ni revocación. Como `current_user` consulta la base en cada request, igual se aplica al instante:
  - Un usuario bloqueado pierde el acceso aunque su token no haya vencido.
  - Si se borra un usuario, su token deja de servir.
- **Autorización.** Se resuelve en el borde HTTP (`AdminUser`). Las operaciones sobre recursos propios (cancelar un turno) también verifican la pertenencia en el caso de uso.
- **Auditoría.** Todas las acciones administrativas que modifican datos quedan registradas con `admin_id`.
- **CORS.** Es configurable. En producción conviene listar los orígenes exactos en lugar de `*`.
- **Contenedor.** Corre con un usuario no root.
- **Pendientes.** Ver [§21](#21-limitaciones-conocidas-y-mejoras-posibles): rate limiting, HTTPS y rotación del secreto.

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
.venv/bin/pytest                 # 59 tests
```

| Carpeta | Tipo | Qué cubre |
|---|---|---|
| `tests/unit/test_calendar.py` | Unitario (dominio puro) | Días hábiles, target, ventana, mismo día, grilla, **salteo de días cerrados** |
| `tests/unit/test_booking_domain.py` | Unitario | Política (cupo, ventana, límites), estados del turno, rebook, reglas de reactivación, reserva y cancelación admin, `cancelled_with_class` |
| `tests/unit/test_identity_domain.py` | Unitario | Email, bloqueo, admin no bloqueable, token de un solo uso |
| `tests/application/test_api_flows.py` | Integración (TestClient + Postgres real) | Flujos end-to-end por HTTP: registro y login, apertura, cupo y lista de espera, límites, ventana de cancelación, cierres (incluido cerrar un día ya habilitado: aviso general y apertura del siguiente), job nocturno (incluido "mañana cerrado → abre el siguiente"), asistencia, recordatorios, bloqueo, reset, calendario, reactivación, rangos de vacaciones, alta por admin, reserva y cancelación admin, cupo con asistencia marcada |
| `tests/application/test_outbox.py` | Integración | El evento sobrevive a Redis caído y el relay lo entrega; un handler fallido queda pendiente y se reintenta; el despacho es idempotente; las filas que agotaron reintentos no se reenvían; en un envío masivo o en los recordatorios, un fallo de SMTP sólo reintenta al destinatario que falló |
| `tests/application/test_concurrency.py` | Integración con hilos | Nunca se supera el cupo (alumnos, y alumnos + admin) |

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
locust -f tests/load/locustfile.py --host http://localhost:8000 \
    --users 40 --spawn-rate 5 --run-time 3m --headless --only-summary
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
| Los emails no llegan | Revisar que `worker` esté arriba y sus logs (los reintentos de `dispatch_outbox` usan backoff). Consultar las filas pendientes de `outbox` y su `last_error` (ver [§12.3](#123-outbox-transaccional)) |
| "Mañana" aparece `pendiente_apertura` antes de las 20:00 | Es lo esperado: la apertura es a `BOOKING_OPENING_TIME` |
| 422 en `/auth/register` con emails `.local` | `EmailStr` rechaza dominios reservados; usar un dominio real o `example.com` en pruebas |

---

## 21. Limitaciones conocidas y mejoras posibles

Ordenadas por impacto estimado.

1. **Latencia de login y registro en ráfagas.** bcrypt (costo 12, ~0,5 s por hash) satura la CPU con muchos logins simultáneos. La API ya corre con 4 workers, pero eso sólo mejora los demás endpoints (ver [§19.3](#193-workers-de-uvicorn)). *Mejoras posibles:*
   - Más CPU, o bajar el costo de bcrypt a 10 (~4 veces más rápido; decisión de seguridad).
   - Rate limiting en `/auth/login`, `/auth/register` y `/auth/forgot-password`, para que no se pueda saturar la CPU a propósito.
2. **Recordatorios y apertura nocturna.**
   - Con `REMINDER_HOURS_BEFORE=12` y apertura a las 20:00, una reserva hecha a la noche para una clase de las 08:00 ya entra en la ventana. El recordatorio llega en la siguiente pasada (≤ 10 min).
   - *Mejora:* no recordar reservas hechas hace menos de X horas, o recordar la mañana de la clase.
3. **Lista de espera sin prioridad.** Se avisa a todos y gana el más rápido. Es lo que se decidió, pero no hay reserva temporal para el primero de la lista.
4. **JWT sin revocación ni refresh.** El logout sólo borra el token del cliente. La consulta del usuario en cada request mitiga el caso de bloqueo, a costa de una query por request.
5. **Horizonte de cierres de 90 días.** `closed_dates_from` y `next_open_weekday` miran 90 días hacia adelante. Un cierre continuo más largo no se saltea del todo (caso borde).
6. **Duplicación menor.** `scheduling.application.services.next_open_day` (consulta por día) y `calendar.next_open_weekday` (sobre un conjunto en memoria) resuelven lo mismo. Se podrían unificar.
7. **Pool y sesiones.** Engine síncrono con pool de 5 + 10 por proceso (configurable). Con 4 workers de uvicorn y el worker de Celery el máximo teórico ronda las 90 conexiones, cerca del límite de 100 de Postgres; si se suman workers, bajar `DB_MAX_OVERFLOW` o usar PgBouncer.
8. **Sin observabilidad estructurada.** Hay logs de texto, sin métricas ni tracing. Los resultados de Celery no se guardan (`backend=None`). *Mejora:* logs JSON, Flower o métricas de Celery, y un healthcheck que incluya la base y Redis.
