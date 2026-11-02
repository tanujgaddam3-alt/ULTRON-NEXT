from datetime import datetime


class FinancialExperienceMemory:
    """
    Stores financial analysis experiences.
    """


    def __init__(self):

        self.records = []



    def remember(
        self,
        company,
        analysis,
        decision=None,
    ):

        self.records.append(
            {
                "company": company,

                "analysis": analysis,

                "decision": decision,

                "timestamp":
                    datetime.now()
                    .isoformat()
            }
        )



    def history(self):

        return self.records



    def search(
        self,
        company,
    ):

        return [
            item
            for item in self.records
            if item["company"] == company
        ]
