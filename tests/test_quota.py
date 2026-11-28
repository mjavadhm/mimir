from mimir_pm_bot.adapters.quota import parse_usage


def test_quota_groups_and_resets():
    report = """
    Account: sensitive@example.test
    GEMINI MODELS
    Weekly Limit Remaining
    [####] 97.32%
    Refreshes in 167h 36m
    Five Hour Limit Remaining
    [    ] 0%
    Refreshes in 1m
    CLAUDE AND GPT MODELS
    Weekly Limit Remaining
    [####] 100.00%
    Quota available
    """
    assert parse_usage(report) == [
        "GEMINI MODELS · Weekly: 97.32٪ باقی‌مانده — Refreshes in 167h 36m",
        "GEMINI MODELS · Five Hour: 0٪ باقی‌مانده — Refreshes in 1m",
        "CLAUDE AND GPT MODELS · Weekly: 100.00٪ باقی‌مانده — Quota available",
    ]


def test_no_percentage_inferred_from_unrecognized_screen():
    assert parse_usage("Account: example\n[##] 50%\nQuota available") == []
