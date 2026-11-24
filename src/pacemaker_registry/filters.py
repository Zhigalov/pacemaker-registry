"""Validated registry filters shared by routes and navigation links."""
from dataclasses import dataclass, replace
from decimal import Decimal
from typing import Annotated
from urllib.parse import urlencode

from fastapi import Depends, Query

from pacemaker_registry.rating import RankingFormula


@dataclass(frozen=True, slots=True)
class RegistryFilters:
    event_id: int = 0
    min_events: int = 1
    distance_km: Decimal | None = None
    q: str = ''
    formula: RankingFormula = RankingFormula.MEAN

    def url(self, path='/', **extra) -> str:
        params = {'event_id': self.event_id or 0, 'min_events': self.min_events}
        if self.distance_km:
            params['distance_km'] = format(self.distance_km.normalize(), 'f')
        if self.q:
            params['q'] = self.q
        if self.formula != RankingFormula.MEAN:
            params['formula'] = self.formula.value
        return path + '?' + urlencode(params | extra)

    def formula_url(self, path: str, **params) -> str:
        if self.formula != RankingFormula.MEAN:
            params['formula'] = self.formula.value
        return path + '?' + urlencode(params) if params else path

    def without_search(self):
        return replace(self, q='')


def registry_filters(
    event_id: Annotated[int, Query(ge=0)] = 0,
    min_events: Annotated[int, Query(ge=1, le=10000)] = 1,
    distance_km: Annotated[Decimal | None, Query(ge=0, le=Decimal('99999.999'), decimal_places=3)] = None,
    q: Annotated[str, Query(max_length=160)] = '',
    formula: RankingFormula = RankingFormula.MEAN,
) -> RegistryFilters:
    return RegistryFilters(event_id, min_events, distance_km or None, ' '.join(q.split()), formula)


Filters = Annotated[RegistryFilters, Depends(registry_filters)]
