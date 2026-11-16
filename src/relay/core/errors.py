"""Erros do relay. A mensagem de cada um já está pronta pra mostrar ao usuário."""

from __future__ import annotations


class RelayError(Exception):
    """Erro esperado, com mensagem em português pro usuário."""


class ConfigError(RelayError):
    """Config ausente, insegura ou inválida."""


class CommandError(RelayError):
    """Comando bloqueado, inexistente ou com metadados inválidos."""


class ParamError(RelayError):
    """Parâmetro inválido pra um comando."""


class ProtocolError(RelayError):
    """Mensagem malformada, grande demais ou de versão diferente."""


class AuthError(RelayError):
    """Assinatura inválida, máquina desconhecida ou mensagem fora de ordem."""


class RemoteError(RelayError):
    """A outra máquina respondeu com erro (assinado)."""

    def __init__(self, message: str, code: str = ""):
        super().__init__(message)
        self.code = code


class ConnectionFailed(RelayError):
    """Não deu pra falar com a outra máquina."""
