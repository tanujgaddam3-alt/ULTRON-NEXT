import os

TEST_MODE = os.getenv("ULTRON_TEST_MODE", "0") == "1"


DRY_RUN = os.getenv("ULTRON_DRY_RUN", "0") == "1"
