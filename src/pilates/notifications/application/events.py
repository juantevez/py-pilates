from dataclasses import dataclass

from pilates.shared.domain.events import DomainEvent


@dataclass(frozen=True, kw_only=True)
class CorreoSolicitado(DomainEvent):
    """Un email listo para enviar a un destinatario.

    Los handlers no hablan con el SMTP: encolan uno de estos por destinatario en el outbox, así
    un fallo de envío reintenta sólo ese correo y no reenvía a quienes ya lo recibieron.
    """

    to: str
    subject: str
    body: str
