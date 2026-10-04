"""GitHub App delivery: branch, commit, and one idempotent draft pull request.

Implements the ordered delivery protocol in
[Case File, client dashboard, and GitHub](../../../../docs/arch-components/client-outputs.md#github-delivery):
preflight, create the draft PR with a stable hidden marker, reconcile the exact branch and
head, then persist verified identities. Retries reuse the same command identity and adopt
only an exact marker, branch, and head match; an unknown outcome reconciles before any
repetition. Nothing here merges, force-pushes, closes a human-visible draft, or responds to
review comments.

Authentication follows GitHub's App flow: an RS256 JWT signed with the App's private key
is exchanged for a short-lived installation access token. The private key reaches the
process as `UTRACE_GITHUB_PRIVATE_KEY`, a Supabase Compute project secret when deployed; it is
never written to disk, logged, or attached to a failure.

Commits are built with the Git Data API (blob, tree, commit, ref) rather than a working
checkout, so delivery applies the exact validated content without needing a clone.
"""

from __future__ import annotations

import base64
import time
from collections.abc import Mapping, Sequence
from types import TracebackType
from typing import Final, Self

import httpx
import jwt
import structlog
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.connectors.failures import (
    ConnectorFailure,
    EffectState,
    FailureDetail,
    RetryAdvice,
    TechnicalCategory,
    default_retry_advice,
    redacted_parameters,
    technical_category_for_status,
)

__all__ = [
    "DeliveredPullRequest",
    "FilePatch",
    "GitHubAppConfigurationError",
    "GitHubAppDelivery",
    "github_app_from_environment",
    "idempotency_marker",
]

_logger = structlog.get_logger(__name__)
_PROVIDER: Final[str] = "github"
_API_ROOT: Final[str] = "https://api.github.com"
_ACCEPT: Final[str] = "application/vnd.github+json"
_API_VERSION: Final[str] = "2022-11-28"
_JWT_LIFETIME_SECONDS: Final[int] = 540
"""GitHub rejects an App JWT whose lifetime exceeds ten minutes; nine leaves clock slack."""
_TOKEN_REFRESH_MARGIN_SECONDS: Final[int] = 300
"""Installation tokens last an hour; refresh early so a long delivery never expires mid-flight."""
_MARKER_PREFIX: Final[str] = "<!-- utrace:delivery:"
_MARKER_SUFFIX: Final[str] = " -->"


def idempotency_marker(*, command_id: str) -> str:
    """Build the stable hidden marker a delivery writes into its pull request body.

    The marker is derived from the delivery Command's id, so a replayed Command produces
    the identical marker and finds its own earlier pull request instead of opening a
    second one. It is an HTML comment, so it is invisible in the rendered body while
    remaining exactly matchable in the raw body.
    """
    return f"{_MARKER_PREFIX}{command_id}{_MARKER_SUFFIX}"


class FilePatch(BaseModel):
    """One file change in a delivered commit.

    `content` is the complete new file content, not a diff hunk: the Git Data API stores
    whole blobs, and delivery applies the exact validated source rather than re-deriving
    it from a patch against a moving base.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    path: str
    content: bytes | None
    """`None` deletes the path."""
    mode: str
    """Git file mode, `100644` for a regular file and `100755` for an executable."""


class DeliveredPullRequest(BaseModel):
    """The verified identities a sealed Case File records for its one PR."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    number: int
    node_id: str
    html_url: str
    head_branch: str
    head_sha: str
    base_branch: str
    base_sha: str
    draft: bool
    idempotency_marker: str


class GitHubAppDelivery:
    """Connector for one GitHub App installation on one authorized repository.

    A delivery instance is bound to a single `owner/repository`, so a mis-scoped call
    cannot reach another client's repository.
    """

    def __init__(
        self,
        *,
        app_id: str,
        installation_id: str,
        private_key_pem: str,
        owner: str,
        repository: str,
        request_timeout_seconds: float,
    ) -> None:
        self._app_id = app_id
        self._installation_id = installation_id
        self._private_key_pem = private_key_pem
        self._owner = owner
        self._repository = repository
        self._http = httpx.AsyncClient(
            base_url=_API_ROOT, timeout=request_timeout_seconds
        )
        self._token: str | None = None
        self._token_expires_at: float = 0.0

    async def __aenter__(self) -> Self:
        """Enter the connector's HTTP client scope."""
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Close the HTTP transport."""
        await self._http.aclose()

    @property
    def repository_full_name(self) -> str:
        """The one authorized repository this instance may write to."""
        return f"{self._owner}/{self._repository}"

    def build_app_jwt(self) -> str:
        """Sign a short-lived App JWT with the App's private key.

        `iat` is backdated by sixty seconds because GitHub rejects a token whose `iat` is
        in the future by even a small clock skew.
        """
        now = int(time.time())
        try:
            return jwt.encode(
                {"iat": now - 60, "exp": now + _JWT_LIFETIME_SECONDS, "iss": self._app_id},
                self._private_key_pem,
                algorithm="RS256",
            )
        except (ValueError, TypeError) as error:
            raise ConnectorFailure(
                FailureDetail(
                    provider=_PROVIDER,
                    operation="build_app_jwt",
                    category=TechnicalCategory.CONFIGURATION,
                    retry_advice=RetryAdvice.AFTER_CONDITION_CHANGE,
                    effect_state=EffectState.ABSENT,
                    message=f"App private key could not sign a JWT: {type(error).__name__}",
                    request_parameters=redacted_parameters({"app_id": self._app_id}),
                )
            ) from error

    async def installation_token(self) -> str:
        """Return a current installation access token, minting one when needed."""
        if self._token is not None and time.time() < self._token_expires_at:
            return self._token
        app_jwt = self.build_app_jwt()
        response = await self._request(
            operation="installation_token",
            method="POST",
            path=f"/app/installations/{self._installation_id}/access_tokens",
            authorization=f"Bearer {app_jwt}",
            json_body=None,
            effect_on_transport_failure=EffectState.ABSENT,
        )
        payload = response.json()
        token = payload.get("token")
        if not isinstance(token, str):
            raise self._contract_violation(
                operation="installation_token",
                message="access token response carried no token",
                parameters={"installation_id": self._installation_id},
            )
        self._token = token
        self._token_expires_at = time.time() + 3600 - _TOKEN_REFRESH_MARGIN_SECONDS
        _logger.info(
            "github.installation_token.minted",
            app_id=self._app_id,
            installation_id=self._installation_id,
            repository=self.repository_full_name,
            permissions=sorted(payload.get("permissions", {})),
        )
        return token

    async def read_default_branch(self) -> str:
        """Return the repository's normal branch name.

        Used by preflight to confirm the installation can see the authorized repository.
        Movement of this branch after validation does not invalidate historical proof and
        does not create newly validated code.
        """
        response = await self._authorized_request(
            operation="read_default_branch",
            method="GET",
            path=f"/repos/{self._owner}/{self._repository}",
            json_body=None,
            effect_on_transport_failure=EffectState.ABSENT,
        )
        branch = response.json().get("default_branch")
        if not isinstance(branch, str):
            raise self._contract_violation(
                operation="read_default_branch",
                message="repository response carried no default_branch",
                parameters={"repository": self.repository_full_name},
            )
        return branch

    async def resolve_branch_head(self, *, branch: str) -> str | None:
        """Return the commit SHA a branch points at, or `None` when it does not exist."""
        try:
            response = await self._authorized_request(
                operation="resolve_branch_head",
                method="GET",
                path=f"/repos/{self._owner}/{self._repository}/git/ref/heads/{branch}",
                json_body=None,
                effect_on_transport_failure=EffectState.ABSENT,
            )
        except ConnectorFailure as failure:
            if failure.category is TechnicalCategory.NOT_FOUND:
                return None
            raise
        sha = response.json().get("object", {}).get("sha")
        if not isinstance(sha, str):
            raise self._contract_violation(
                operation="resolve_branch_head",
                message="ref response carried no object.sha",
                parameters={"branch": branch},
            )
        return sha

    async def verify_head(self, *, branch: str, expected_head_sha: str) -> bool:
        """Confirm the branch still points at the exact validated commit."""
        actual = await self.resolve_branch_head(branch=branch)
        matches = actual == expected_head_sha
        _logger.info(
            "github.head.verified",
            repository=self.repository_full_name,
            branch=branch,
            expected_head_sha=expected_head_sha,
            actual_head_sha=actual,
            matches=matches,
        )
        return matches

    async def create_branch_commit(
        self,
        *,
        branch: str,
        base_sha: str,
        patches: Sequence[FilePatch],
        commit_message: str,
    ) -> str:
        """Create a tree, commit, and branch ref from the validated content.

        Returns the new head SHA. Refuses to move an existing branch: a delivery that finds
        its branch already present reconciles through `resolve_branch_head` and adopts an
        exact match, because uTrace never force-pushes.
        """
        existing = await self.resolve_branch_head(branch=branch)
        if existing is not None:
            raise ConnectorFailure(
                FailureDetail(
                    provider=_PROVIDER,
                    operation="create_branch_commit",
                    category=TechnicalCategory.CONFLICT,
                    retry_advice=RetryAdvice.AFTER_CONDITION_CHANGE,
                    effect_state=EffectState.ABSENT,
                    message=(
                        f"branch {branch} already exists at {existing}; reconcile the "
                        "existing head instead of replacing it"
                    ),
                    request_parameters=redacted_parameters(
                        {"repository": self.repository_full_name, "branch": branch}
                    ),
                )
            )
        tree_entries: list[dict[str, object]] = []
        for patch in patches:
            if patch.content is None:
                tree_entries.append(
                    {"path": patch.path, "mode": patch.mode, "type": "blob", "sha": None}
                )
                continue
            blob = await self._authorized_request(
                operation="create_blob",
                method="POST",
                path=f"/repos/{self._owner}/{self._repository}/git/blobs",
                json_body={
                    "content": base64.b64encode(patch.content).decode("ascii"),
                    "encoding": "base64",
                },
                effect_on_transport_failure=EffectState.INDETERMINATE,
            )
            tree_entries.append(
                {
                    "path": patch.path,
                    "mode": patch.mode,
                    "type": "blob",
                    "sha": blob.json()["sha"],
                }
            )

        tree = await self._authorized_request(
            operation="create_tree",
            method="POST",
            path=f"/repos/{self._owner}/{self._repository}/git/trees",
            json_body={"base_tree": base_sha, "tree": tree_entries},
            effect_on_transport_failure=EffectState.INDETERMINATE,
        )
        commit = await self._authorized_request(
            operation="create_commit",
            method="POST",
            path=f"/repos/{self._owner}/{self._repository}/git/commits",
            json_body={
                "message": commit_message,
                "tree": tree.json()["sha"],
                "parents": [base_sha],
            },
            effect_on_transport_failure=EffectState.INDETERMINATE,
        )
        head_sha = commit.json()["sha"]
        await self._authorized_request(
            operation="create_ref",
            method="POST",
            path=f"/repos/{self._owner}/{self._repository}/git/refs",
            json_body={"ref": f"refs/heads/{branch}", "sha": head_sha},
            effect_on_transport_failure=EffectState.INDETERMINATE,
        )
        _logger.info(
            "github.branch.created",
            repository=self.repository_full_name,
            branch=branch,
            base_sha=base_sha,
            head_sha=head_sha,
            file_count=len(patches),
        )
        return str(head_sha)

    async def find_existing_delivery(self, *, marker: str) -> DeliveredPullRequest | None:
        """Return the open pull request whose body carries this exact marker.

        Only an exact marker match is adopted. A pull request that merely touches the same
        branch is not this delivery.
        """
        response = await self._authorized_request(
            operation="find_existing_delivery",
            method="GET",
            path=(
                f"/repos/{self._owner}/{self._repository}/pulls"
                "?state=all&per_page=100&sort=created&direction=desc"
            ),
            json_body=None,
            effect_on_transport_failure=EffectState.ABSENT,
        )
        for candidate in response.json():
            body = candidate.get("body") or ""
            if marker in body:
                return _to_delivered(payload=candidate, marker=marker)
        return None

    async def create_draft_pull_request(
        self, *, head_branch: str, base_branch: str, title: str, body: str, marker: str
    ) -> DeliveredPullRequest:
        """Open one draft pull request carrying the hidden idempotency marker.

        The marker is appended to the body so the delivery can find its own pull request
        after an uncertain response. The body excludes customer identity, transcript
        excerpts, telemetry, preview credentials, and internal evaluation; this connector
        writes exactly what the caller composed plus the marker.
        """
        if marker in body:
            composed_body = body
        else:
            composed_body = f"{body}\n\n{marker}\n"
        response = await self._authorized_request(
            operation="create_draft_pull_request",
            method="POST",
            path=f"/repos/{self._owner}/{self._repository}/pulls",
            json_body={
                "title": title,
                "body": composed_body,
                "head": head_branch,
                "base": base_branch,
                "draft": True,
            },
            effect_on_transport_failure=EffectState.INDETERMINATE,
        )
        delivered = _to_delivered(payload=response.json(), marker=marker)
        _logger.info(
            "github.pull_request.created",
            repository=self.repository_full_name,
            number=delivered.number,
            head_branch=delivered.head_branch,
            head_sha=delivered.head_sha,
            base_branch=delivered.base_branch,
            draft=delivered.draft,
        )
        return delivered

    async def _authorized_request(
        self,
        *,
        operation: str,
        method: str,
        path: str,
        json_body: Mapping[str, object] | None,
        effect_on_transport_failure: EffectState,
    ) -> httpx.Response:
        """Issue a request with a current installation token."""
        token = await self.installation_token()
        return await self._request(
            operation=operation,
            method=method,
            path=path,
            authorization=f"Bearer {token}",
            json_body=json_body,
            effect_on_transport_failure=effect_on_transport_failure,
        )

    async def _request(
        self,
        *,
        operation: str,
        method: str,
        path: str,
        authorization: str,
        json_body: Mapping[str, object] | None,
        effect_on_transport_failure: EffectState,
    ) -> httpx.Response:
        """Issue one GitHub REST request and map its failure modes."""
        headers = {
            "Authorization": authorization,
            "Accept": _ACCEPT,
            "X-GitHub-Api-Version": _API_VERSION,
        }
        try:
            response = await self._http.request(
                method, path, headers=headers, json=json_body
            )
        except httpx.TimeoutException as error:
            raise self._transport_failure(
                operation=operation,
                path=path,
                category=TechnicalCategory.TIMEOUT,
                effect=effect_on_transport_failure,
                error=error,
            ) from error
        except httpx.HTTPError as error:
            raise self._transport_failure(
                operation=operation,
                path=path,
                category=TechnicalCategory.PROVIDER_UNAVAILABLE,
                effect=effect_on_transport_failure,
                error=error,
            ) from error
        if response.status_code >= 400:
            category = technical_category_for_status(response.status_code)
            raise ConnectorFailure(
                FailureDetail(
                    provider=_PROVIDER,
                    operation=operation,
                    category=category,
                    retry_advice=default_retry_advice(category),
                    effect_state=EffectState.ABSENT,
                    message="GitHub rejected the request",
                    request_parameters=redacted_parameters(
                        {
                            "repository": self.repository_full_name,
                            "method": method,
                            "path": path,
                        }
                    ),
                    status_code=response.status_code,
                    response_body=response.text,
                )
            )
        return response

    def _transport_failure(
        self,
        *,
        operation: str,
        path: str,
        category: TechnicalCategory,
        effect: EffectState,
        error: Exception,
    ) -> ConnectorFailure:
        """Build the failure for a call that produced no GitHub verdict."""
        advice = (
            RetryAdvice.AFTER_DELAY
            if effect is EffectState.ABSENT
            else RetryAdvice.AFTER_CONDITION_CHANGE
        )
        return ConnectorFailure(
            FailureDetail(
                provider=_PROVIDER,
                operation=operation,
                category=category,
                retry_advice=advice,
                effect_state=effect,
                message=f"GitHub request failed in transport: {type(error).__name__}",
                request_parameters=redacted_parameters(
                    {"repository": self.repository_full_name, "path": path}
                ),
            )
        )

    def _contract_violation(
        self, *, operation: str, message: str, parameters: Mapping[str, object]
    ) -> ConnectorFailure:
        """Build the failure for a GitHub answer uTrace cannot accept."""
        return ConnectorFailure(
            FailureDetail(
                provider=_PROVIDER,
                operation=operation,
                category=TechnicalCategory.VALIDATION,
                retry_advice=RetryAdvice.NEVER,
                effect_state=EffectState.INDETERMINATE,
                message=message,
                request_parameters=redacted_parameters(parameters),
            )
        )


def _to_delivered(*, payload: Mapping[str, object], marker: str) -> DeliveredPullRequest:
    """Convert a pull request payload into the verified delivery record."""
    head = payload.get("head")
    base = payload.get("base")
    if not isinstance(head, Mapping) or not isinstance(base, Mapping):
        raise ConnectorFailure(
            FailureDetail(
                provider=_PROVIDER,
                operation="read_pull_request",
                category=TechnicalCategory.VALIDATION,
                retry_advice=RetryAdvice.NEVER,
                effect_state=EffectState.INDETERMINATE,
                message="pull request payload carried no head or base object",
            )
        )
    return DeliveredPullRequest(
        number=int(str(payload["number"])),
        node_id=str(payload["node_id"]),
        html_url=str(payload["html_url"]),
        head_branch=str(head["ref"]),
        head_sha=str(head["sha"]),
        base_branch=str(base["ref"]),
        base_sha=str(base["sha"]),
        draft=bool(payload.get("draft", False)),
        idempotency_marker=marker,
    )


class GitHubAppConfigurationError(RuntimeError):
    """The GitHub App's identity or key is not configured."""

    def __init__(self, missing: Sequence[str]) -> None:
        super().__init__("the GitHub App is not configured; set " + ", ".join(sorted(missing)))


class _GitHubAppSettings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore", case_sensitive=True)

    app_id: str = Field(validation_alias="UTRACE_GITHUB_APP_ID", min_length=1)
    installation_id: str = Field(validation_alias="UTRACE_GITHUB_INSTALLATION_ID", min_length=1)
    private_key_pem: str = Field(validation_alias="UTRACE_GITHUB_PRIVATE_KEY", min_length=1)


def github_app_from_environment(
    *, repository_ref: str, request_timeout_seconds: float
) -> GitHubAppDelivery:
    """Build the connector for one authorized `owner/name` from the process environment.

    A deployed compute receives `UTRACE_GITHUB_PRIVATE_KEY` as a project secret; the
    repository comes from the client's pinned configuration, never from the environment.
    """
    try:
        settings = _GitHubAppSettings()  # type: ignore[call-arg]
    except ValidationError as error:
        raise GitHubAppConfigurationError(
            [str(issue["loc"][0]) for issue in error.errors()]
        ) from None
    owner, _, repository = repository_ref.partition("/")
    if not owner or not repository:
        raise ValueError(f"repository reference '{repository_ref}' is not owner/name")
    return GitHubAppDelivery(
        app_id=settings.app_id,
        installation_id=settings.installation_id,
        private_key_pem=settings.private_key_pem,
        owner=owner,
        repository=repository,
        request_timeout_seconds=request_timeout_seconds,
    )
