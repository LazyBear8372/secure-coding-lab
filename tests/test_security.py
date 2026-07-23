from secure_coding_lab.security import (
    CSRF_MAX_AGE_SECONDS,
    hash_password,
    is_valid_csrf_token,
    make_csrf_token,
    verify_password,
)


def test_argon2_password_hash_verification() -> None:
    password_hash = hash_password("correct horse battery staple")

    assert password_hash.startswith("$argon2id$")
    assert verify_password(password_hash, "correct horse battery staple")
    assert not verify_password(password_hash, "wrong password")


def test_csrf_token_rejects_tampering() -> None:
    token = make_csrf_token("test-secret")

    assert is_valid_csrf_token(token, token, "test-secret")
    assert not is_valid_csrf_token(f"{token}x", token, "test-secret")
    assert not is_valid_csrf_token(token, token, "different-secret")


def test_csrf_token_expires() -> None:
    token = make_csrf_token("test-secret")
    timestamp = int(token.split(".")[1])

    assert not is_valid_csrf_token(
        token,
        token,
        "test-secret",
        now=timestamp + CSRF_MAX_AGE_SECONDS + 1,
    )
