"""The authenticated control-plane client.

The harness holds no database credentials for ordinary work: it signs in as the
demo staff user through Supabase Auth and reads and writes through the control
plane's own routes, which is the same authority path the client dashboard uses.
A refusal is raised with the route, the status, and the control plane's typed
error, never swallowed or retried into a different outcome.
"""

from __future__ import annotations

from types import TracebackType
from typing import Final, Self
from uuid import UUID

import httpx
from pydantic import BaseModel, ConfigDict, Field

from demo_harness.models import (
    AllowanceView,
    CaseDetail,
    CaseFileDetail,
    CaseFileList,
    CaseList,
    CaseOutreach,
    ConfigurationRelease,
    CustomerAccountList,
    DeliveryView,
    IngestedIntakeMessage,
    IntakeItemList,
    OffersView,
    PreviewRuntimeAllocationList,
    PreviewVersionDetail,
    StaffClientList,
    ValidationRecord,
)
from demo_harness.settings import ControlPlaneSettings

__all__ = ["ControlPlane", "ControlPlaneError", "StaffSession", "sign_in"]

_TIMEOUT_SECONDS: Final[float] = 30.0


class ControlPlaneError(RuntimeError):
    """The control plane refused a request, or answered something unreadable."""

    def __init__(self, *, method: str, path: str, status: int, detail: str) -> None:
        """Carry enough of the exchange to act on without re-running it."""
        super().__init__(f"{method} {path} failed with {status}: {detail}")
        self.method = method
        self.path = path
        self.status = status
        self.detail = detail


class RefusalBody(BaseModel):
    """The single error shape every control-plane refusal uses."""

    model_config = ConfigDict(extra="ignore")

    error: str
    detail: str
    subject: str | None = None


class TokenResponse(BaseModel):
    """The Supabase Auth password-grant response."""

    model_config = ConfigDict(extra="ignore")

    access_token: str = Field(min_length=1)
    token_type: str
    expires_in: int


class StaffSession(BaseModel):
    """One signed-in demo staff identity."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    email: str
    access_token: str

    def authorization(self) -> dict[str, str]:
        """Return the Authorization header this session presents."""
        return {"Authorization": f"Bearer {self.access_token}"}


async def sign_in(settings: ControlPlaneSettings) -> StaffSession:
    """Sign the demo staff user in through Supabase Auth's password grant."""
    url = f"{settings.supabase_url}/auth/v1/token"
    async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as http:
        response = await http.post(
            url,
            params={"grant_type": "password"},
            headers={
                "apikey": settings.supabase_publishable_key,
                "Content-Type": "application/json",
            },
            json={"email": settings.staff_email, "password": settings.staff_password},
        )
    if response.status_code != httpx.codes.OK:
        raise ControlPlaneError(
            method="POST",
            path="/auth/v1/token",
            status=response.status_code,
            detail=(
                f"Supabase Auth refused the demo staff sign-in for {settings.staff_email}: "
                f"{response.text}"
            ),
        )
    token = TokenResponse.model_validate(response.json())
    return StaffSession(email=settings.staff_email, access_token=token.access_token)


class ControlPlane:
    """Typed reads and the two writes the harness is allowed to make."""

    def __init__(self, *, base_url: str, session: StaffSession) -> None:
        """Bind one signed-in staff session to one target control plane."""
        self._base_url = base_url.rstrip("/")
        self._session = session
        self._http = httpx.AsyncClient(
            base_url=self._base_url,
            timeout=_TIMEOUT_SECONDS,
            headers=session.authorization(),
        )

    @property
    def base_url(self) -> str:
        """Return the target this client is bound to."""
        return self._base_url

    async def __aenter__(self) -> Self:
        """Enter the client's connection scope."""
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Close the underlying connection pool."""
        await self._http.aclose()

    async def _get(self, path: str) -> object:
        """Issue one authenticated GET and return its decoded body."""
        response = await self._http.get(path)
        return self._decoded(method="GET", path=path, response=response)

    async def _post(self, path: str, body: dict[str, object]) -> object:
        """Issue one authenticated POST and return its decoded body."""
        response = await self._http.post(path, json=body)
        return self._decoded(method="POST", path=path, response=response)

    def _decoded(self, *, method: str, path: str, response: httpx.Response) -> object:
        """Return the decoded body, or raise the control plane's typed refusal."""
        if response.status_code == httpx.codes.OK:
            return response.json()
        try:
            refusal = RefusalBody.model_validate(response.json())
        except (ValueError, TypeError):
            raise ControlPlaneError(
                method=method,
                path=path,
                status=response.status_code,
                detail=response.text[:2000],
            ) from None
        subject = "" if refusal.subject is None else f" [{refusal.subject}]"
        raise ControlPlaneError(
            method=method,
            path=path,
            status=response.status_code,
            detail=f"{refusal.error}: {refusal.detail}{subject}",
        )

    async def health(self) -> str:
        """Return the target's health status, proving it is reachable."""
        response = await self._http.get("/health")
        body = self._decoded(method="GET", path="/health", response=response)
        if not isinstance(body, dict) or "status" not in body:
            raise ControlPlaneError(
                method="GET", path="/health", status=200, detail=f"unreadable body: {body!r}"
            )
        return str(body["status"])

    async def staff_clients(self) -> StaffClientList:
        """Return every client the signed-in staff identity may act for."""
        return StaffClientList.model_validate(await self._get("/v1/clients"))

    async def configuration(self, client_id: UUID) -> ConfigurationRelease:
        """Return the client's active immutable configuration release."""
        body = await self._get(f"/v1/clients/{client_id}/configuration")
        if not isinstance(body, dict):
            raise ControlPlaneError(
                method="GET",
                path=f"/v1/clients/{client_id}/configuration",
                status=200,
                detail=f"unreadable body: {body!r}",
            )
        return ConfigurationRelease.model_validate(body["release"])

    async def customer_accounts(self, client_id: UUID) -> CustomerAccountList:
        """Return the client's customer accounts and their billing mapping."""
        return CustomerAccountList.model_validate(
            await self._get(f"/v1/clients/{client_id}/customer-accounts")
        )

    async def allowance(self, client_id: UUID, currency: str) -> AllowanceView:
        """Return the client's issuance allowance in one currency."""
        return AllowanceView.model_validate(
            await self._get(f"/v1/clients/{client_id}/offers/budget?currency={currency}")
        )

    async def authorize_allowance(
        self, client_id: UUID, *, currency: str, authorized_minor_units: int
    ) -> AllowanceView:
        """Authorize or raise the client's issuance ceiling. Owner only."""
        return AllowanceView.model_validate(
            await self._post(
                f"/v1/clients/{client_id}/offers/budget",
                {"currency": currency, "authorized_minor_units": authorized_minor_units},
            )
        )

    async def offers(self, client_id: UUID, currency: str) -> OffersView:
        """Return the allowance and every offer written against it."""
        return OffersView.model_validate(
            await self._get(f"/v1/clients/{client_id}/offers/dispositions?currency={currency}")
        )

    async def cases(self, client_id: UUID) -> CaseList:
        """Return every case for the client, newest first."""
        return CaseList.model_validate(await self._get(f"/v1/clients/{client_id}/cases"))

    async def case(self, client_id: UUID, product_signal_id: UUID) -> CaseDetail:
        """Return one case with its gate, attempt, and validation records."""
        return CaseDetail.model_validate(
            await self._get(f"/v1/clients/{client_id}/cases/{product_signal_id}")
        )

    async def case_outreach(self, client_id: UUID, product_signal_id: UUID) -> CaseOutreach:
        """Return the case's Outreach Attempts and the invitations they sent."""
        return CaseOutreach.model_validate(
            await self._get(f"/v1/clients/{client_id}/cases/{product_signal_id}/outreach-attempts")
        )

    async def preview_runtime_allocations(
        self, client_id: UUID, product_signal_id: UUID
    ) -> PreviewRuntimeAllocationList:
        """Return the Preview Runtime Allocations the case claimed."""
        return PreviewRuntimeAllocationList.model_validate(
            await self._get(
                f"/v1/clients/{client_id}/cases/{product_signal_id}/preview-runtime-allocations"
            )
        )

    async def intake_items(self, client_id: UUID) -> IntakeItemList:
        """Return the client's recent Intake Items and their outcomes."""
        return IntakeItemList.model_validate(
            await self._get(f"/v1/clients/{client_id}/intake-items")
        )

    async def ingest_intake_message(
        self, client_id: UUID, *, channel_ref: str, provider_message_id: str
    ) -> IngestedIntakeMessage:
        """Re-read one watched-channel message through intake. Staff only."""
        return IngestedIntakeMessage.model_validate(
            await self._post(
                f"/v1/clients/{client_id}/intake/messages",
                {
                    "channel_ref": channel_ref,
                    "provider_message_id": provider_message_id,
                },
            )
        )

    async def validation(self, client_id: UUID, product_signal_id: UUID) -> ValidationRecord:
        """Return the case's most recent Validation Attempt and its proofs."""
        return ValidationRecord.model_validate(
            await self._get(f"/v1/clients/{client_id}/cases/{product_signal_id}/validation")
        )

    async def case_files(self, client_id: UUID) -> CaseFileList:
        """Return the client's published Case Files."""
        return CaseFileList.model_validate(
            await self._get(f"/v1/clients/{client_id}/case-files")
        )

    async def case_file(self, client_id: UUID, case_file_id: UUID) -> CaseFileDetail:
        """Return one published Case File and its immutable core."""
        return CaseFileDetail.model_validate(
            await self._get(f"/v1/clients/{client_id}/case-files/{case_file_id}")
        )

    async def delivery(self, client_id: UUID, case_file_id: UUID) -> DeliveryView:
        """Return the draft PR delivered for one Case File."""
        return DeliveryView.model_validate(
            await self._get(f"/v1/clients/{client_id}/case-files/{case_file_id}/delivery")
        )

    async def preview_version(
        self, client_id: UUID, preview_version_id: UUID
    ) -> PreviewVersionDetail:
        """Return one immutable candidate build."""
        return PreviewVersionDetail.model_validate(
            await self._get(f"/v1/clients/{client_id}/preview-versions/{preview_version_id}")
        )
