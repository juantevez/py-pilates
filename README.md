# Pilates Studio — Backend

API de reservas para un estudio de Pilates. Está hecha con **FastAPI**, **PostgreSQL**, **SQLAlchemy 2** y **Celery + Redis**. Sigue una **arquitectura hexagonal** (puertos y adaptadores) con **DDD**: es un monolito modular con 4 bounded contexts.

## Levantar el entorno

```bash
cp .env.example .env          # ajustar JWT_SECRET y ADMIN_* como mínimo
docker compose up -d --build
```

| Servicio | URL / rol |
|---|---|
| `api` | http://localhost:8000/docs (Swagger). Al arrancar corre `alembic upgrade head` y crea el admin inicial. Corre 4 procesos de uvicorn (`WEB_CONCURRENCY`) |
| `worker` | Celery: procesa los eventos de dominio (envía correos y reacciona a los bloqueos de usuarios) |
| `beat` | Celery Beat: apertura nocturna (L–V a `BOOKING_OPENING_TIME`) y recordatorios (cada 10 min) |
| `db` | PostgreSQL 16 |
| `redis` | Broker de Celery |
| `mailpit` | http://localhost:8025, bandeja para ver los correos en desarrollo |

Admin inicial: el definido en `ADMIN_EMAIL` / `ADMIN_PASSWORD` (en `.env.example`: `admin@pilates.local` / `admin12345`).

Comandos útiles:

```bash
docker compose exec api python -m pilates.cli open-next-day    # dispara la apertura manualmente
docker compose exec api python -m pilates.cli send-reminders
docker compose exec api python -m pilates.cli relay-outbox     # reenvía eventos pendientes del outbox
```

> **Primer arranque:** la grilla de cada día la genera la apertura nocturna del día hábil anterior. Si levantás el stack durante el día, las clases de hoy no existen todavía. Generalas desde el front (Admin → Clases → "Generar grilla") o con `POST /admin/clases/generar {"fecha": "AAAA-MM-DD"}`.

### Frontend y acceso desde la red local

El front está en `../../front-ui-code/pilates-ui` (HTML/CSS/JS vanilla, sirve en http://localhost:3000). Necesita dos variables del backend:

- `CORS_ORIGINS`: orígenes desde los que se abre el front, separados por coma.
- `FRONTEND_URL`: base de los links de los emails (`/reset-password?token=…`, `/reservar`, `/clases/{id}`).

Para usarlo desde el teléfono en la misma red Wi-Fi, usá la IP de la PC:

```env
CORS_ORIGINS=http://localhost:3000,http://192.168.0.2:3000
FRONTEND_URL=http://192.168.0.2:3000
```

Después corré `docker compose up -d` y abrí `http://192.168.0.2:3000` en el teléfono. Si el router le cambia la IP a la PC, actualizá estas dos líneas.

### Desarrollo local y tests

```bash
uv venv && uv pip install -e '.[dev]'
docker compose up -d db               # los tests de integración usan la base pilates_test (se crea sola)
.venv/bin/pytest
```

Con `EVENT_BUS=sync` y `EMAIL_BACKEND=console` se puede correr la API sin Redis ni SMTP:

```bash
uvicorn --factory pilates.main:create_app --reload
```

## Arquitectura

> Documentación técnica completa (diagramas, flujos, modelo de datos, API, errores, configuración): [`doc/technical/TECH.md`](doc/technical/TECH.md).

```
src/pilates/
├── shared/            Kernel compartido: AggregateRoot, DomainEvent, errores, UnitOfWork, Clock, outbox de eventos
│   ├── domain/ application/      (puertos)
│   ├── infrastructure/           (SQLAlchemy UoW, bus de eventos sync/Celery, reloj, auditoría)
│   └── http/                     (dependencias de auth y mapeo de errores de dominio a HTTP)
├── identity/          Usuarios, login JWT, recuperación de contraseña, bloqueo de alumnos
├── scheduling/        Grilla de clases, calendario de reservas, cierres del gimnasio
├── booking/           Turnos, reglas de reserva y cancelación, lista de espera, asistencia
├── notifications/     Handlers de eventos → correos, recordatorios
├── worker/            Celery app + tareas (dispatch_outbox, relay_outbox, open_next_day, send_reminders)
├── bootstrap.py       Raíz de composición (conecta puertos con adaptadores)
└── main.py            Factory de FastAPI
```

Cada contexto se divide en `domain/` (modelo puro, sin frameworks), `application/` (casos de uso y puertos) y `adapters/` (ORM, repositorios, HTTP, seguridad).

- **Aislamiento del dominio:** las entidades son dataclasses puras. Los modelos ORM viven en `adapters/orm.py` y los repositorios traducen entre ambos.
- **Eventos de dominio:** los agregados registran eventos (`TurnoReservado`, `TurnoCancelado`, `UserBlocked`, …). La UnitOfWork los guarda en la tabla `outbox` **en la misma transacción** que los cambios (una fila por par evento-handler) y después del commit los encola en Celery. Si Redis no responde, el relay (`relay_outbox`, cada minuto) los reenvía; el despacho es idempotente, así un reintento no duplica los otros correos.
- **Consistencia inmediata vs. eventual:**
  - Lo que protege invariantes ocurre en la misma transacción: al cancelar una clase o cargar un cierre se cancelan los turnos.
  - Los efectos secundarios van por eventos: los correos, y la liberación de turnos futuros al bloquear un usuario.
- **Concurrencia:** al reservar se toma un `SELECT … FOR UPDATE` sobre el usuario y luego sobre la clase. Esto serializa el cupo y los límites diarios. La restricción única `(user_id, clase_id)` actúa como última defensa. Ver `tests/application/test_concurrency.py`.
- **Lecturas (CQRS liviano):** las pantallas (grilla, historial, asistencia, calendario) usan queries dedicadas que devuelven DTOs.

## Reglas de negocio implementadas

Todas son configurables por variables de entorno (ver `.env.example`).

| Regla | Valor por defecto |
|---|---|
| Grilla | L–V, 08/09/10/11 hs (mañana) y 15/16/17 hs (tarde), 60 min, 4 lugares |
| Horizonte de reserva | **Hoy** (clases que no empezaron) y el **siguiente día hábil abierto** (los días con cierre se saltean). Los turnos del día siguiente se habilitan a las **20:00** del día hábil anterior (`BOOKING_OPENING_TIME`; el viernes 20:00 se abren los del lunes) |
| Mismo día | Permitido si quedan lugares y la clase no empezó (`ALLOW_SAME_DAY_BOOKING=true`) |
| Anticipación mínima | `MIN_BOOKING_ADVANCE_MINUTES=0`. Con 1440 se aplica la regla de "24 h" de la v1 del documento |
| Cancelación | Hasta **1 h** antes del inicio |
| Límites por alumno | Máx. **2 clases por día** y **1 por bloque** (mañana/tarde) |
| Lista de espera | Solo si la clase está llena. Al liberarse un lugar, se le reserva automáticamente al primero de la lista que pueda tomarlo (respetando sus límites) y se le avisa por correo; si no lo quiere, lo cancela. En la última hora antes de la clase se avisa a todos y reserva el primero que entra |
| Cierres | Carga manual (día o rango). Cancela los turnos del día (`cancelada_por_admin`), avisa a los alumnos y registra auditoría |
| Reactivar / reabrir | Una clase cancelada se puede **reactivar**. Un cierre (un día o un rango, ej. vacaciones) se **reabre** eliminándolo. Las clases vuelven a estar disponibles y se avisa por email a quienes habían perdido el turno. La reserva no se restaura automáticamente: el lugar pudo haberlo tomado otro alumno |
| Apertura nocturna | Genera la grilla (idempotente) y avisa a todos los alumnos. Si el día está cerrado, envía el aviso "sin clases" con el próximo día hábil |
| Recordatorio | El día de la clase: 2 h antes del inicio, nunca antes de las 07:00 (`REMINDER_HOURS_BEFORE`, `REMINDER_EARLIEST_TIME`). No se manda si se reservó después de esa hora |
| Reserva por recepción | El admin puede anotar a un alumno salteando ventana y límites: sólo valida el cupo y que la clase no haya terminado. También puede cancelar turnos puntuales |
| Rate limiting | Login: 20 intentos por minuto por IP y 5 fallos por email en 15 min (bloquea sólo esa cuenta). Registro: 10 por hora por IP. Recuperación de contraseña: 5 por hora por IP y 3 por email. Responde `429` con `Retry-After`. Contadores en Redis; `RATE_LIMIT_ENABLED=false` para pruebas de carga |
| Bloqueo de usuario | No puede loguearse ni reservar. Sus turnos futuros se cancelan y se liberan los cupos |
| Asistencia | El admin marca `asistio` / `no_asistio` desde el día de la clase. Las estadísticas aparecen en el listado de usuarios |

## Endpoints

| Método | Ruta | Rol |
|---|---|---|
| POST | `/auth/register`, `/auth/login`, `/auth/forgot-password`, `/auth/reset-password` | público |
| GET | `/auth/me` | autenticado |
| GET | `/calendario?desde=&hasta=` | alumno: días con/sin clases y cierres con etiqueta ("Feriado - Sin clases"). Por defecto hoy + 30 días |
| GET | `/clases/disponibles` | alumno: hoy y el próximo día hábil, cada uno con `estado` (`abierta` / `pendiente_apertura` / `cerrado` / `sin_clases`), cupos y flags `puede_reservar` / `puede_cancelar` / `puede_anotarse_lista_espera` |
| POST | `/turnos` · `/turnos/{id}/cancelar` | alumno |
| GET | `/turnos/mios` | alumno: próximas y pasadas |
| POST/DELETE | `/clases/{id}/lista-espera` | alumno |
| GET | `/admin/clases?fecha=` · POST `/admin/clases/generar` | admin |
| PATCH | `/admin/clases/{id}` (capacidad, instructor) · POST `/admin/clases/{id}/cancelar` · POST `/admin/clases/{id}/reactivar` | admin |
| GET/POST | `/admin/cierres` · PUT/DELETE `/admin/cierres/{id}` · DELETE `/admin/cierres?desde=&hasta=` (reabrir rango) · GET `/admin/calendario` | admin |
| GET | `/admin/asistencia?fecha=` · POST `/admin/turnos/{id}/asistencia` | admin |
| POST | `/admin/turnos` (reservar en nombre de un alumno: sólo valida cupo y que la clase no haya terminado) · `/admin/turnos/{id}/cancelar` (cancelar un turno puntual, con motivo opcional) | admin |
| GET/POST | `/admin/usuarios` (listar · alta de alumno con email de bienvenida) · POST `/admin/usuarios/{id}/bloquear` · `/desbloquear` | admin |

Los errores de dominio responden `{"error": "<codigo>", "detail": "<mensaje>"}`. Los códigos HTTP son:

- 404: no encontrado.
- 409: conflicto (`class_full`, `already_booked`, …).
- 422: regla de negocio violada (`outside_booking_window`, `cancellation_window_closed`, `daily_limit_reached`, …).
- 401 / 403: autenticación y permisos.
