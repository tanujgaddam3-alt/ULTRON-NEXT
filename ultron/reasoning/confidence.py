class ConfidenceCalculator:
    def calculate(self, path):

        if not path:
            return 0

        steps = len(path) // 2

        if steps == 1:
            return 0.95

        if steps == 2:
            return 0.90

        if steps <= 4:
            return 0.80

        return 0.60
