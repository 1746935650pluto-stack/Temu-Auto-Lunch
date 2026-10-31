"""Fair, work-conserving edit admission; publishing uses separate capacity."""

GLOBAL_EDIT_LIMIT = 24
ACCOUNT_EDIT_LIMIT = 8


def linked_account_edit_limit(remote_limit, hard_limit=ACCOUNT_EDIT_LIMIT):
    """Keep local edit workers close to the currently usable remote lane.

    A worker performs several SQLite reads/writes around each MiaoShou call.
    Letting eight workers run while the account request gate has backed off to
    one only creates lock waiters.  Keep one small look-ahead buffer, then
    reopen progressively as the adaptive request gate proves healthy again.
    """
    if type(hard_limit) is not int or hard_limit < 1:
        raise ValueError('编辑并发上限不正确')
    if type(remote_limit) is not int or remote_limit < 1:
        return hard_limit
    return min(hard_limit, max(2, remote_limit * 2))


def admission_order(accounts, owned, cursor=0):
    """Least-served active accounts first, rotating ties without reserving idle slots."""
    if not accounts:
        return []
    pivot = cursor % len(accounts)
    rotated = accounts[pivot:] + accounts[:pivot]
    return sorted(rotated, key=lambda account: owned.get(account['id'], 0))
