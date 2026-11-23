"""Run pytest with the CI skip policy."""

import sys

import pytest


WORKBOOK_REASON = "IPO_data.xlsx is a local gitignored dataset and is not in this checkout"
WORKBOOK_TESTS = {
    "tests/test_skill_runtime.py::test_subscription_history_uses_versioned_local_dataset",
    "tests/test_skill_runtime.py::test_sentiment_matches_local_history_without_structured_fallback",
    "tests/test_skill_runtime.py::test_valuation_uses_versioned_historical_peers_by_industry",
}


class SkipGate:
    def __init__(self):
        self.unexpected = set()

    def pytest_runtest_logreport(self, report):
        if report.skipped and not (
            report.nodeid in WORKBOOK_TESTS
            and isinstance(report.longrepr, tuple)
            and report.longrepr[2] == f"Skipped: {WORKBOOK_REASON}"
        ):
            self.unexpected.add(report.nodeid)

    def pytest_collectreport(self, report):
        if report.skipped:
            self.unexpected.add(report.nodeid)

    def pytest_terminal_summary(self, terminalreporter):
        if self.unexpected:
            terminalreporter.write_sep("=", "Unexpected pytest skips")
            for nodeid in sorted(self.unexpected):
                terminalreporter.write_line(nodeid)

    def pytest_sessionfinish(self, session, exitstatus):
        if self.unexpected and exitstatus == pytest.ExitCode.OK:
            session.exitstatus = pytest.ExitCode.TESTS_FAILED


if __name__ == "__main__":
    sys.exit(pytest.main(sys.argv[1:], plugins=[SkipGate()]))
