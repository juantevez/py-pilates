"""Textos de los correos (español rioplatense)."""

from datetime import date, time

_DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
_MESES = [
    "enero", "febrero", "marzo", "abril", "mayo", "junio",
    "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
]
_FIRMA = "\n\n— El equipo del estudio de Pilates"


def fecha_larga(d: date) -> str:
    return f"{_DIAS[d.weekday()]} {d.day} de {_MESES[d.month - 1]}"


def hora(t: time) -> str:
    return t.strftime("%H:%M")


def confirmacion(nombre: str, fecha: date, inicio: time) -> tuple[str, str]:
    return (
        f"Reserva confirmada: {fecha_larga(fecha)} {hora(inicio)} hs",
        f"Hola {nombre},\n\nTu turno para la clase del {fecha_larga(fecha)} a las {hora(inicio)} hs quedó confirmado.\n"
        "Recordá que podés cancelar hasta 1 hora antes del inicio." + _FIRMA,
    )


def recordatorio(nombre: str, fecha: date, inicio: time) -> tuple[str, str]:
    return (
        f"Recordatorio: clase {fecha_larga(fecha)} {hora(inicio)} hs",
        f"Hola {nombre},\n\nTe recordamos tu clase del {fecha_larga(fecha)} a las {hora(inicio)} hs.\n"
        "Si no podés venir, cancelá tu turno para liberar el lugar." + _FIRMA,
    )


def cancelacion_gimnasio(nombre: str, fecha: date, inicio: time, motivo: str | None) -> tuple[str, str]:
    motivo_txt = f" por {motivo}" if motivo else ""
    return (
        f"Clase cancelada: {fecha_larga(fecha)} {hora(inicio)} hs",
        f"Hola {nombre},\n\nLamentamos informarte que la clase de Pilates del {fecha_larga(fecha)} a las "
        f"{hora(inicio)} hs ha sido cancelada{motivo_txt}. Tu reserva ha sido anulada y podrás reservar un "
        "nuevo turno en cuanto se habiliten." + _FIRMA,
    )


def turno_cancelado_gimnasio(nombre: str, fecha: date, inicio: time, motivo: str | None) -> tuple[str, str]:
    motivo_txt = f" Motivo: {motivo}." if motivo else ""
    return (
        f"Turno cancelado: {fecha_larga(fecha)} {hora(inicio)} hs",
        f"Hola {nombre},\n\nEl estudio canceló tu turno para la clase del {fecha_larga(fecha)} a las "
        f"{hora(inicio)} hs.{motivo_txt} Si querés, podés reservar otro turno desde la app." + _FIRMA,
    )


def lugar_asignado(nombre: str, fecha: date, inicio: time) -> tuple[str, str]:
    return (
        f"¡Tenés lugar! Te reservamos la clase del {fecha_larga(fecha)} {hora(inicio)} hs",
        f"Hola {nombre},\n\nSe liberó un lugar en la clase del {fecha_larga(fecha)} a las {hora(inicio)} hs y, como "
        "estabas primero en la lista de espera, ya te lo reservamos: no tenés que hacer nada más.\n"
        "Si no podés venir, cancelá el turno desde la app hasta 1 hora antes para que lo aproveche otro alumno."
        + _FIRMA,
    )


def lugar_liberado(nombre: str, fecha: date, inicio: time, url: str) -> tuple[str, str]:
    return (
        f"¡Se liberó un lugar! {fecha_larga(fecha)} {hora(inicio)} hs",
        f"Hola {nombre},\n\nSe liberó un lugar en la clase de las {hora(inicio)} hs del {fecha_larga(fecha)}.\n"
        f"Reservalo antes que otro alumno: {url}" + _FIRMA,
    )


def clases_reabiertas(nombre: str, clases: list[tuple[date, time]], url: str) -> tuple[str, str]:
    lista = "\n".join(f"  • {fecha_larga(f)} a las {hora(h)} hs" for f, h in sorted(clases))
    plural = len(clases) > 1
    return (
        "¡Buenas noticias! Se reabrieron clases" if plural else f"¡Buenas noticias! Vuelve la clase del {fecha_larga(clases[0][0])}",
        f"Hola {nombre},\n\n"
        + ("Estas clases que se habían cancelado vuelven a estar disponibles:\n" if plural
           else "La clase que se había cancelado vuelve a estar disponible:\n")
        + f"{lista}\n\nTu reserva anterior fue anulada, así que si querés asistir tenés que volver a reservar "
        f"(los lugares son limitados): {url}" + _FIRMA,
    )


def turnos_habilitados(nombre: str, fecha: date, url: str) -> tuple[str, str]:
    return (
        f"Ya podés reservar tus turnos del {fecha_larga(fecha)}",
        f"Hola {nombre},\n\n¡Ya están disponibles los turnos para el {fecha_larga(fecha)}! "
        f"Reservá el tuyo antes de que se llenen: {url}" + _FIRMA,
    )


def dia_sin_clases(nombre: str, fecha: date, motivo: str, proximo: date) -> tuple[str, str]:
    return (
        f"Sin clases el {fecha_larga(fecha)}",
        f"Hola {nombre},\n\nEl gimnasio permanecerá cerrado el {fecha_larga(fecha)} ({motivo}). "
        f"Los turnos se habilitarán normalmente para el {fecha_larga(proximo)}." + _FIRMA,
    )


def reset_password(nombre: str, url: str, minutos: int) -> tuple[str, str]:
    return (
        "Recuperación de contraseña",
        f"Hola {nombre},\n\nRecibimos un pedido para restablecer tu contraseña. Usá este enlace "
        f"(válido por {minutos} minutos, de un solo uso):\n{url}\n\n"
        "Si no fuiste vos, ignorá este correo." + _FIRMA,
    )


def bienvenida(nombre: str, url: str, horas: int) -> tuple[str, str]:
    return (
        "Bienvenido/a al estudio de Pilates",
        f"Hola {nombre},\n\nEl estudio te creó una cuenta para reservar tus clases. Para empezar, "
        f"definí tu contraseña con este enlace (válido por {horas} horas, de un solo uso):\n{url}\n\n"
        "Si el enlace venció, podés pedir uno nuevo desde \"Olvidé mi contraseña\"." + _FIRMA,
    )
