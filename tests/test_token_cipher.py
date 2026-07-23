from cryptography.fernet import Fernet

from booking_bot.services.token_cipher import (
    BotTokenCipher,
    hash_webhook_secret,
    webhook_secret_matches,
)


def test_bot_token_round_trip() -> None:
    cipher = BotTokenCipher(Fernet.generate_key().decode("ascii"))

    ciphertext = cipher.encrypt("123456:telegram-token")

    assert ciphertext != "123456:telegram-token"
    assert cipher.decrypt(ciphertext) == "123456:telegram-token"


def test_webhook_secret_comparison() -> None:
    expected = hash_webhook_secret("secret-value")

    assert webhook_secret_matches("secret-value", expected)
    assert not webhook_secret_matches("another-value", expected)
    assert not webhook_secret_matches(None, expected)
