from typing import ClassVar


class SecurityTestRunner:
    """
    Runs ULTRON security tests.
    """

    def __init__(self):

        self.tests = []

    def register(
        self,
        test,
    ):

        self.tests.append(test)

    def run(
        self,
    ):

        results: ClassVar = []

        for test in self.tests:
            result = test.run()

            results.append(result)

        return results
