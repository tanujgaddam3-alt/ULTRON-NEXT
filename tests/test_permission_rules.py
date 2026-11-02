import pytest

from ultron.security.permission.engine import PermissionEngine
from ultron.security.permission.models import PermissionLevel
from ultron.security.permission.rules import PermissionRules
from ultron.security.testing.permission_tests import PermissionSecurityTest


@pytest.mark.parametrize(
    ("action", "expected"),
    [
        *(
            (action, PermissionLevel.ALLOW)
            for action in sorted(PermissionRules.SAFE_ACTIONS)
        ),
        *(
            (action, PermissionLevel.CONFIRM)
            for action in sorted(PermissionRules.CONFIRM_ACTIONS)
        ),
        *(
            (action, PermissionLevel.DENY)
            for action in sorted(PermissionRules.DENIED_ACTIONS)
        ),
        ("unclassified_action", PermissionLevel.CONFIRM),
    ],
)
def test_permission_rules_cover_allow_confirm_deny_and_unknown(action, expected):
    assert PermissionRules().check(action) is expected


def test_unscoped_filesystem_access_requires_confirmation():
    rules = PermissionRules()

    assert rules.check("read_file") is PermissionLevel.CONFIRM
    assert rules.check("list_directory") is PermissionLevel.CONFIRM


def test_read_only_analysis_remains_allowed():
    assert PermissionRules().check("analyze") is PermissionLevel.ALLOW


def test_permission_firewall_self_check_remains_truthful():
    result = PermissionSecurityTest(PermissionEngine()).run()

    assert result.passed is True
