class FinancialMemoryManager:
    """
    Uses stored financial experiences.
    """


    def __init__(
        self,
        store,
    ):

        self.store = store



    def recall_company(
        self,
        company,
    ):

        records = (
            self.store
            .search(company)
        )


        return records



    def has_history(
        self,
        company,
    ):

        return len(
            self.recall_company(company)
        ) > 0
