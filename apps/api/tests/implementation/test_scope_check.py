"""The diff-versus-approved-scope check.

Pure functions over a real `git diff` shape and a frozen scope. This is the one
place the blocked-surface rule is enforced, so the tests here are what prove a
blocked path stays readable and never reaches an accepted diff.
"""

from __future__ import annotations

from app.gate.scope import DependencyPolicy
from app.implementation.scope_check import (
    ViolationKind,
    added_manifest_lines,
    check_diff_against_scope,
    parse_unified_diff,
)
from tests.implementation.factories import saved_views_scope, shopalytics_checks

PERMITTED_DIFF = """diff --git a/src/lib/dashboard/saved-views.ts b/src/lib/dashboard/saved-views.ts
new file mode 100644
index 0000000..1111111
--- /dev/null
+++ b/src/lib/dashboard/saved-views.ts
@@ -0,0 +1,3 @@
+export interface SavedConversionView {
+  id: string;
+}
diff --git a/src/components/dashboard/ConversionDashboard.tsx b/src/components/dashboard/ConversionDashboard.tsx
index 2222222..3333333 100644
--- a/src/components/dashboard/ConversionDashboard.tsx
+++ b/src/components/dashboard/ConversionDashboard.tsx
@@ -12,6 +12,7 @@ import { useState } from "react";
+import { SavedViewsBar } from "@/components/dashboard/SavedViewsBar";
-import { nothing } from "@/lib/nothing";
"""

BLOCKED_DIFF = """diff --git a/src/lib/utrace/chart-state.ts b/src/lib/utrace/chart-state.ts
index 4444444..5555555 100644
--- a/src/lib/utrace/chart-state.ts
+++ b/src/lib/utrace/chart-state.ts
@@ -1,1 +1,2 @@
+// the attempt must never change a blocked code surface
"""

MANIFEST_DIFF = """diff --git a/package.json b/package.json
index 6666666..7777777 100644
--- a/package.json
+++ b/package.json
@@ -30,6 +30,7 @@
+    "zustand": "5.0.0",
"""


def _scope(**overrides: int) -> object:
    return saved_views_scope(checks=shopalytics_checks(), **overrides)


def test_the_parser_counts_added_and_removed_lines_per_file() -> None:
    """A diff header starts a file; the hunk lines are what count."""
    files = parse_unified_diff(PERMITTED_DIFF)
    by_path = {item.path: item for item in files}
    assert set(by_path) == {
        "src/lib/dashboard/saved-views.ts",
        "src/components/dashboard/ConversionDashboard.tsx",
    }
    assert by_path["src/lib/dashboard/saved-views.ts"].added_lines == 3
    assert by_path["src/components/dashboard/ConversionDashboard.tsx"].added_lines == 1
    assert by_path["src/components/dashboard/ConversionDashboard.tsx"].removed_lines == 1


def test_an_in_scope_diff_passes() -> None:
    """Every changed path sits under a permitted surface and inside the bounds."""
    scope = saved_views_scope(checks=shopalytics_checks())
    result = check_diff_against_scope(
        diff=PERMITTED_DIFF, scope=scope, added_dependency_lines=()
    )
    assert result.passed
    assert result.violations == ()
    assert result.scope_digest == scope.digest()


def test_a_blocked_code_surface_in_the_diff_is_caught() -> None:
    """A blocked path is readable, but it can never appear in the accepted diff."""
    result = check_diff_against_scope(
        diff=BLOCKED_DIFF,
        scope=saved_views_scope(checks=shopalytics_checks()),
        added_dependency_lines=(),
    )
    assert not result.passed
    assert [item.kind for item in result.violations] == [ViolationKind.BLOCKED_CODE_SURFACE]
    assert result.violations[0].subject == "src/lib/utrace/chart-state.ts"


def test_a_path_outside_every_permitted_surface_is_caught() -> None:
    """Scope never grows inside the workspace."""
    diff = BLOCKED_DIFF.replace("src/lib/utrace/chart-state.ts", "src/app/page.tsx")
    result = check_diff_against_scope(
        diff=diff, scope=saved_views_scope(checks=shopalytics_checks()), added_dependency_lines=()
    )
    assert [item.kind for item in result.violations] == [
        ViolationKind.OUTSIDE_PERMITTED_PATHS
    ]


def test_the_file_bound_is_enforced() -> None:
    """The approved scope's file bound refuses a diff that grew past it."""
    scope = saved_views_scope(checks=shopalytics_checks(), changed_files=1)
    result = check_diff_against_scope(
        diff=PERMITTED_DIFF, scope=scope, added_dependency_lines=()
    )
    kinds = {item.kind for item in result.violations}
    assert kinds == {ViolationKind.FILE_BOUND_EXCEEDED}


def test_a_dependency_addition_under_a_none_policy_is_caught() -> None:
    """`none` permits no addition, and the added manifest line names it."""
    scope = saved_views_scope(checks=shopalytics_checks())
    assert scope.dependency_policy is DependencyPolicy.NONE
    added = added_manifest_lines(MANIFEST_DIFF, scope)
    assert added == ('"zustand": "5.0.0",',)
    result = check_diff_against_scope(
        diff=PERMITTED_DIFF, scope=scope, added_dependency_lines=added
    )
    assert [item.kind for item in result.violations] == [ViolationKind.DEPENDENCY_POLICY]


def test_a_manifest_edit_that_adds_nothing_is_not_a_policy_event() -> None:
    """Touching a manifest is not the same as adding a dependency."""
    scope = saved_views_scope(checks=shopalytics_checks())
    assert added_manifest_lines(PERMITTED_DIFF, scope) == ()
    assert check_diff_against_scope(
        diff=PERMITTED_DIFF, scope=scope, added_dependency_lines=()
    ).passed
