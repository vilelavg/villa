"""Custom exceptions do Client OS."""


class ClientOSError(Exception):
    """Erro genérico do Client OS."""


class ClientNotFoundError(ClientOSError):
    """Cliente não encontrado pelo slug fornecido."""
