import hashlib
import hmac

from cryptography.fernet import Fernet, InvalidToken


class TokenCipherConfigurationError(RuntimeError):
    """Raised when token encryption is not configured correctly."""


class BotTokenCipher:
    def __init__(self, key: str | None) -> None:
        if not key:
            raise TokenCipherConfigurationError("BOT_TOKEN_ENCRYPTION_KEY is not configured")
        try:
            self._fernet = Fernet(key.encode("ascii"))
        except (ValueError, UnicodeEncodeError) as exc:
            raise TokenCipherConfigurationError("BOT_TOKEN_ENCRYPTION_KEY is invalid") from exc

    def encrypt(self, token: str) -> str:
        return self._fernet.encrypt(token.encode("utf-8")).decode("ascii")

    def decrypt(self, ciphertext: str) -> str:
        try:
            return self._fernet.decrypt(ciphertext.encode("ascii")).decode("utf-8")
        except (InvalidToken, UnicodeEncodeError) as exc:
            raise TokenCipherConfigurationError("Stored bot token cannot be decrypted") from exc


def hash_webhook_secret(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def webhook_secret_matches(secret: str | None, expected_hash: str) -> bool:
    if not secret:
        return False
    return hmac.compare_digest(hash_webhook_secret(secret), expected_hash)
