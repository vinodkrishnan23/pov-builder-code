"""JourneyRunner — drives a real headless browser through the "primary
user journey" new.md's Phase 7 spec calls for (open app -> search ->
select -> view -> act -> verify state change), for `integration_validator`.

The journey's STEPS come from an LLM call over the actual specification
(see prompts/integration_validator.py's PrimaryUserJourneyOutput) — never
hardcoded here. This module only knows how to EXECUTE a given step list
against a real browser; it has no opinion about what the steps should be.
"""

from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel, Field


class JourneyStep(BaseModel):
    action: str  # "goto" | "click" | "fill" | "assert_text" | "assert_visible"
    selector: str = ""
    value: str = ""
    description: str = ""


class JourneyStepResult(BaseModel):
    step: JourneyStep
    passed: bool
    evidence: str = ""


class JourneyResult(BaseModel):
    passed: bool
    steps: list[JourneyStepResult] = Field(default_factory=list)


class JourneyRunner(Protocol):
    def run(self, *, base_url: str, steps: list[JourneyStep]) -> JourneyResult:
        """Executes `steps` in order against `base_url`. Stops at the
        first failed step — later steps are recorded as not attempted
        rather than silently marked as passed."""
        ...


class PlaywrightJourneyRunner:
    """Real implementation — Playwright's SYNC api (this codebase is
    synchronous throughout, no async elsewhere). Requires one-time setup:
    `uv run playwright install chromium` (see README)."""

    def run(self, *, base_url: str, steps: list[JourneyStep]) -> JourneyResult:
        from playwright.sync_api import sync_playwright

        results: list[JourneyStepResult] = []
        overall_passed = True

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            try:
                for step in steps:
                    if not overall_passed:
                        break
                    passed, evidence = self._execute_step(page, base_url, step)
                    results.append(JourneyStepResult(step=step, passed=passed, evidence=evidence))
                    if not passed:
                        overall_passed = False
            finally:
                browser.close()

        return JourneyResult(passed=overall_passed and bool(results), steps=results)

    def _execute_step(self, page, base_url: str, step: JourneyStep) -> tuple[bool, str]:
        try:
            if step.action == "goto":
                url = step.value if step.value.startswith("http") else f"{base_url.rstrip('/')}{step.value}"
                page.goto(url, timeout=15000)
                return True, f"navigated to {url}"
            if step.action == "click":
                page.click(step.selector, timeout=10000)
                return True, f"clicked {step.selector}"
            if step.action == "fill":
                page.fill(step.selector, step.value, timeout=10000)
                return True, f"filled {step.selector}"
            if step.action == "assert_visible":
                page.wait_for_selector(step.selector, state="visible", timeout=10000)
                return True, f"{step.selector} is visible"
            if step.action == "assert_text":
                content = page.text_content(step.selector, timeout=10000) or ""
                if step.value in content:
                    return True, f"{step.selector} contains {step.value!r}"
                return False, f"{step.selector} content was {content!r}, expected to contain {step.value!r}"
            return False, f"unknown action {step.action!r}"
        except Exception as exc:  # noqa: BLE001 — a Playwright timeout/error IS the failure signal
            return False, f"{step.action} on {step.selector!r} raised: {exc}"
