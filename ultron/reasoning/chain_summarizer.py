class ChainSummarizer:
    def summarize(self, path):

        if len(path) < 2:
            return None

        first = path[0]

        last = path[-1]

        return f"{first['from']} affects {last['to']} through {first['to']}."
