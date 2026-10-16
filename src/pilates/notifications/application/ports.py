from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class EmailMessage:
    to: str
    subject: str
    body: str


class EmailSender(ABC):
    @abstractmethod
    def send(self, message: EmailMessage) -> None: ...
