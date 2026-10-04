"""uTrace domain area: lifecycle enumerations and the shared read records.

Schemas for this area are generated from these bindings by
``packages/utrace-contracts/python/scripts/export_domain_schemas.py`` into
``packages/utrace-contracts/schemas/domain``. Edit the bindings, never the
generated schema documents.
"""

from utrace_contracts.domain import lifecycle, records
from utrace_contracts.domain.lifecycle import *  # noqa: F403
from utrace_contracts.domain.records import *  # noqa: F403

__all__ = [*lifecycle.__all__, *records.__all__]
