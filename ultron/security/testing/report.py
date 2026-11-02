from datetime import UTC, datetime


class SecurityReportGenerator:
    """
    Generates security assessment reports.
    """

    def generate(
        self,
        results,
    ):

        total = len(results)

        passed = sum(1 for result in results if result.passed)

        failed = total - passed

        status = "SECURE" if failed == 0 else "ATTENTION_REQUIRED"

        return {
            "timestamp": datetime.now(UTC),
            "total_tests": total,
            "passed": passed,
            "failed": failed,
            "status": status,
            "details": [result.model_dump() for result in results],
        }
