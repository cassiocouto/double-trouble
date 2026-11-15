"""Erros do ponte. A mensagem de cada um já está pronta pra mostrar ao usuário."""

from __future__ import annotations


class PonteError(Exception):
    """Erro esperado, com mensagem em português pro usuário."""


class ConfigError(PonteError):
    """Config ausente, insegura ou inválida."""


class CommandError(PonteError):
    """Comando bloqueado, inexistente ou com metadados inválidos."""


class ParamError(PonteError):
    """Parâmetro inválido pra um comando."""


class ProtocolError(PonteError):
    """Mensagem malformada, grande demais ou de versão diferente."""


class AuthError(PonteError):
    """Assinatura inválida, máquina desconhecida ou mensagem fora de ordem."""


class RemoteError(PonteError):
    """A outra máquina respondeu com erro (assinado)."""


class ConnectionFailed(PonteError):
    """Não deu pra falar com a outra máquina."""
