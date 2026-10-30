"""Deterministic sample repair, not an LLM or a static success animation."""

from relayforge.providers.templates import TEMPLATES


class MockCodingProvider:
    name = "mock"

    @staticmethod
    def plan(template: str, source: str) -> str:
        if template not in TEMPLATES:
            raise ValueError("Unknown deterministic task template")
        repair = TEMPLATES[template]
        if source.count(repair.before) != 1:
            raise ValueError("Expected sample bug not found; cannot claim a repair")
        return f"Repair {repair.title}, run trusted sample tests, review the diff."

    @staticmethod
    def patch(template: str, source: str) -> str:
        MockCodingProvider.plan(template, source)
        repair = TEMPLATES[template]
        return source.replace(repair.before, repair.after, 1)
