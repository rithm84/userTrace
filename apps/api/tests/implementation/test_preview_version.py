"""Preview Version publication: verification, the secret scan, and supersession.

Publication is a set of refusals. A failed or ambiguous check, a scope
violation, a missing artifact digest, an empty diff, or a key-shaped string in
the accepted diff each block the candidate; none of them can be relabelled as
success by the coding model.
"""

from __future__ import annotations

from app.implementation.preview_version import RefusalKind, scan_for_secrets

CLEAN_DIFF = """diff --git a/src/lib/dashboard/saved-views.ts b/src/lib/dashboard/saved-views.ts
new file mode 100644
--- /dev/null
+++ b/src/lib/dashboard/saved-views.ts
@@ -0,0 +1,2 @@
+export const SAVED_VIEWS_STORAGE_KEY = "shopalytics.saved-conversion-views";
+export const SAVED_VIEWS_SCHEMA_VERSION = 1;
"""


def _diff_adding(line: str) -> str:
    return (
        "diff --git a/src/lib/dashboard/saved-views.ts b/src/lib/dashboard/saved-views.ts\n"
        "--- a/src/lib/dashboard/saved-views.ts\n"
        "+++ b/src/lib/dashboard/saved-views.ts\n"
        "@@ -1,1 +1,2 @@\n"
        f"+{line}\n"
    )


def test_a_clean_diff_passes_the_secret_scan() -> None:
    """The normal candidate carries no credential-shaped string."""
    assert scan_for_secrets(CLEAN_DIFF) == ()


def test_an_added_credential_shape_blocks_publication() -> None:
    """Each known key shape is its own refusal, named by what it matched."""
    for name, line in (
        ("aws_access_key_id", 'const key = "AKIAIOSFODNN7EXAMPLE";'),
        ("github_token", 'const token = "ghp_' + "a" * 36 + '";'),
        ("openai_key", 'const key = "sk-' + "b" * 40 + '";'),
        ("private_key_block", "-----BEGIN RSA PRIVATE KEY-----"),
        (
            "jwt",
            'const jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dBjftJeZ4CVPmB92K27u";',
        ),
        ("bearer_literal", 'Authorization: Bearer ' + "c" * 32),
    ):
        refusals = scan_for_secrets(_diff_adding(line))
        assert [item.subject for item in refusals] == [name], line
        assert refusals[0].kind is RefusalKind.SECRET_DETECTED


def test_a_secret_already_at_the_base_revision_is_not_this_attempt_s_fault() -> None:
    """Only added lines are scanned: pre-existing content is integration health."""
    removed = (
        "diff --git a/src/lib/dashboard/saved-views.ts b/src/lib/dashboard/saved-views.ts\n"
        "--- a/src/lib/dashboard/saved-views.ts\n"
        "+++ b/src/lib/dashboard/saved-views.ts\n"
        "@@ -1,2 +1,1 @@\n"
        '-const key = "AKIAIOSFODNN7EXAMPLE";\n'
        "+const key = readFromEnvironment();\n"
    )
    assert scan_for_secrets(removed) == ()


def test_the_diff_markers_themselves_are_not_scanned() -> None:
    """`+++` is a diff header, not an added line."""
    header_only = (
        "diff --git a/AKIAIOSFODNN7EXAMPLE b/AKIAIOSFODNN7EXAMPLE\n"
        "+++ b/AKIAIOSFODNN7EXAMPLE\n"
    )
    assert scan_for_secrets(header_only) == ()
