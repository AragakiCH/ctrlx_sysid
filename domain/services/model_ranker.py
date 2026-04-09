from __future__ import annotations

from domain.models.identification_result import IdentificationResult


class ModelRanker:
    def rank(self, results: list[IdentificationResult]) -> list[IdentificationResult]:
        valid_results = [r for r in results if r is not None]
        return sorted(valid_results, key=lambda r: r.fit_quality, reverse=True)

    def best(self, results: list[IdentificationResult]) -> IdentificationResult | None:
        ranked = self.rank(results)
        return ranked[0] if ranked else None