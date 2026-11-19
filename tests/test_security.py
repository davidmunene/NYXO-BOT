from pathlib import Path

import pytest

from nyxo.security import policy
from nyxo.security.redact import contains_secret, redact
from nyxo.system import runner


def test_redaction():
    assert "[REDACTED" in redact("my key is sk-ant-api03-abcdefghijklmnopqrstuv")
    assert redact("password: hunter2") == "password: [REDACTED]"
    assert redact("api_key=abc123") == "api_key=[REDACTED]"
    assert "ghp_" not in redact("token ghp_abcdefghijklmnopqrstuvwxyz123")
    assert redact("open firefox") == "open firefox"
    assert contains_secret("my password is swordfish")


def test_shell_binaries_blocked():
    for bad in ("sudo", "bash", "rm", "sh", "python3", "curl", "pkexec"):
        with pytest.raises(policy.ValidationError):
            policy.check_binary(bad)
    with pytest.raises(policy.ValidationError):
        runner.run(["sudo", "ls"])
    with pytest.raises(policy.ValidationError):
        runner.run(["bash", "-c", "echo hi"])


def test_custom_commands_validation():
    with pytest.raises(policy.ValidationError):
        policy.custom_argv(["sudo", "apt", "upgrade"])
    with pytest.raises(policy.ValidationError):
        policy.custom_argv(["rm", "-rf", "/"])
    with pytest.raises(policy.ValidationError):
        policy.custom_argv(["ls;rm", "-rf"])
    with pytest.raises(policy.ValidationError):
        policy.custom_argv(["definitely-not-a-real-program-xyz"])
    assert policy.custom_argv(["ls", "-la"]) == ["ls", "-la"]


def test_sensitive_paths():
    home = Path.home()
    assert policy.is_sensitive(home / ".ssh" / "id_rsa")
    assert policy.is_sensitive(home / ".gnupg")
    assert not policy.is_sensitive(home / "Documents")
    with pytest.raises(policy.ValidationError):
        policy.user_writable_path("/etc/passwd")
    with pytest.raises(policy.ValidationError):
        policy.user_writable_path(str(home))
    with pytest.raises(policy.ValidationError):
        policy.user_writable_path("~/.bashrc")
    with pytest.raises(policy.ValidationError):
        policy.readable_path("/proc/self/environ")


def test_branch_and_url_validation():
    assert policy.branch_name("feature/x-1") == "feature/x-1"
    for bad in ("-rf", "a..b", "x y", "", "trailing/", "name.lock", "a;rm"):
        with pytest.raises(policy.ValidationError):
            policy.branch_name(bad)
    assert policy.url("https://ubuntu.com") == "https://ubuntu.com"
    for bad in ("file:///etc/passwd", "javascript:alert(1)", "https://x.com/\"; rm"):
        with pytest.raises(policy.ValidationError):
            policy.url(bad)
