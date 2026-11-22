from sourcesentinel.sentinel import build_upstream_report


def test_build_upstream_report():
    r = build_upstream_report(
        repo="WatchCodes/nastech-repo",
        upstream="Owner/Repo",
        upstream_sha="b" * 40,
        behind_by=3,
    )
    assert r["behind_by"] == 3
    assert r["repo"] == "WatchCodes/nastech-repo"
