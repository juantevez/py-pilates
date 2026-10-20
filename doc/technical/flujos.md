# Pilates Studio: Calendario de reservas y flujos principales

> [Documentación técnica](TECH.md) · [Contextos `identity` y `scheduling`](identity-scheduling.md) · [Contextos `booking` y `notifications`](booking-notifications.md) · **Calendario de reservas y flujos principales** · [Concurrencia, eventos y modelo de datos](consistencia-eventos-datos.md)

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
    Reservable desde el Lun a las 20 h          :active, b1, 2026-10-19 20:00, 36h
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
    API->>DB: COMMIT (+ outbox)
    API->>W: TurnoCancelado → PromoteFromWaitlist
    alt falta más de 1 h para la clase
        loop por orden de llegada, mientras quede lugar
            W->>DB: lock usuario → lock clase, recuenta ocupados
            alt el alumno puede tomarlo (límites diarios y de bloque)
                W->>DB: reserva (desde_lista_espera) + borra su entrada, COMMIT
                W-->>B: email "¡Tenés lugar! Te reservamos la clase…"
            else llegó a su límite
                Note over W: sigue anotado, se prueba con el siguiente
            end
        end
    else última hora (ya no podría cancelarlo)
        W->>W: LugarLiberado
        W-->>B: email "¡Se liberó un lugar!" a todos, marca notified_at
        Note over B: Reserva el primero que hace POST /turnos
    end
```

- **Por qué en el worker y una transacción por candidato.** Cada intento toma los locks en el mismo orden que `BookClass` (usuario → clase) y vuelve a contar los lugares, así que una reserva directa que llegue al mismo tiempo no puede superar el cupo (`test_waitlist_promotion_racing_direct_bookings_never_exceeds_capacity`).
- **Si nadie puede tomarlo** (todos llegaron a su límite), el lugar queda libre para cualquiera y los anotados siguen en la lista.
- **El turno asignado es uno más:** se puede cancelar hasta 1 h antes, recibe recordatorio y cuenta para los límites. `booked_by` queda `alumno`, porque lo pidió él al anotarse.
- **Disparadores:** cualquier `TurnoCancelado` con la clase activa: cancelación del alumno, del admin (turno puntual) o por bloqueo de cuenta. Cancelar o cerrar la clase entera vacía la lista de espera.

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
3. Cada cancelación emite `TurnoCancelado`, que avisa al alumno y le pasa el lugar al primero de la lista de espera.
4. A partir de ahí, cualquier request con su token responde `403 user_blocked`, porque `GetCurrentUser` verifica el estado.
