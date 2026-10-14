from ned_agent.logging_utils import sanitize_error


def test_error_redaction_covers_common_secret_forms():
    error = RuntimeError(
        "api_key=example-secret client_secret:another password=hunter2 "
        "key=query-secret token=oauth-secret AIzaabcdefghijklmnopqrstuvwxyz123456"
    )
    cleaned = sanitize_error(error)
    for secret in (
        "example-secret",
        "another",
        "hunter2",
        "query-secret",
        "oauth-secret",
        "AIzaabcdefghijklmnopqrstuvwxyz123456",
    ):
        assert secret not in cleaned
