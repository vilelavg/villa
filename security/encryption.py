"""
Villa — Criptografia de dados sensíveis
AES-256 via Fernet para dados em repouso no PostgreSQL.
Usado para: tokens de API, dados de cartão, credenciais de clientes.
"""

import base64
import hashlib
import secrets

from cryptography.fernet import Fernet, InvalidToken

from core.config import settings


class EncryptionService:
    """
    Serviço de criptografia para dados sensíveis.

    Uso:
        crypto = EncryptionService()
        encrypted = crypto.encrypt("dados sensíveis")
        original = crypto.decrypt(encrypted)
    """

    def __init__(self, key: str | None = None):
        raw_key = key or settings.encryption_key
        # Fernet exige chave base64 de 32 bytes
        # Se a chave do .env não é Fernet válida, deriva uma
        self._fernet = self._build_fernet(raw_key)

    def _build_fernet(self, raw_key: str) -> Fernet:
        """Constrói instância Fernet a partir de uma chave qualquer."""
        try:
            # Tenta usar diretamente (se já é uma chave Fernet válida)
            return Fernet(raw_key.encode() if isinstance(raw_key, str) else raw_key)
        except (ValueError, Exception):
            # Deriva uma chave Fernet válida a partir da string fornecida
            derived = hashlib.sha256(raw_key.encode()).digest()
            fernet_key = base64.urlsafe_b64encode(derived)
            return Fernet(fernet_key)

    def encrypt(self, plaintext: str) -> str:
        """
        Criptografa uma string. Retorna o texto cifrado em base64.

        Args:
            plaintext: Texto a criptografar

        Returns:
            String criptografada (base64, segura para armazenar no banco)
        """
        if not plaintext:
            return ""
        token = self._fernet.encrypt(plaintext.encode("utf-8"))
        return token.decode("utf-8")

    def decrypt(self, ciphertext: str) -> str:
        """
        Descriptografa uma string previamente criptografada.

        Args:
            ciphertext: Texto cifrado em base64

        Returns:
            Texto original

        Raises:
            InvalidToken: Se a chave estiver errada ou o dado corrompido
        """
        if not ciphertext:
            return ""
        try:
            plaintext = self._fernet.decrypt(ciphertext.encode("utf-8"))
            return plaintext.decode("utf-8")
        except InvalidToken:
            raise ValueError(
                "Falha ao descriptografar: chave incorreta ou dado corrompido. "
                "Verifique se ENCRYPTION_KEY no .env não foi alterada."
            )

    def encrypt_dict(self, data: dict, fields: list[str]) -> dict:
        """
        Criptografa campos específicos de um dicionário.
        Útil para criptografar seletivamente dados de um lead/cliente.

        Args:
            data: Dicionário com os dados
            fields: Lista de campos a criptografar

        Returns:
            Dicionário com os campos especificados criptografados
        """
        result = data.copy()
        for field in fields:
            if field in result and result[field]:
                result[field] = self.encrypt(str(result[field]))
        return result

    def decrypt_dict(self, data: dict, fields: list[str]) -> dict:
        """
        Descriptografa campos específicos de um dicionário.

        Args:
            data: Dicionário com dados criptografados
            fields: Lista de campos a descriptografar

        Returns:
            Dicionário com os campos especificados descriptografados
        """
        result = data.copy()
        for field in fields:
            if field in result and result[field]:
                try:
                    result[field] = self.decrypt(str(result[field]))
                except ValueError:
                    pass  # Campo não estava criptografado ou chave errada
        return result

    @staticmethod
    def generate_key() -> str:
        """
        Gera uma nova chave Fernet válida.
        Usar para gerar o valor de ENCRYPTION_KEY no .env.

        Uso no terminal:
            python -c "from security.encryption import EncryptionService; print(EncryptionService.generate_key())"
        """
        return Fernet.generate_key().decode("utf-8")

    @staticmethod
    def generate_secret(length: int = 64) -> str:
        """Gera um secret aleatório para JWT_SECRET_KEY ou webhook secrets."""
        return secrets.token_urlsafe(length)


# Campos sensíveis que devem ser criptografados por tabela
SENSITIVE_FIELDS = {
    "leads": ["phone", "email"],
    "clients": ["contact_phone", "contact_email"],
}

# Instância global (singleton via import)
encryption = EncryptionService()
