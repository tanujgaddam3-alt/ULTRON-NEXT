import sqlite3


class KnowledgeStore:
    def __init__(self, path="knowledge.db"):

        self.conn = sqlite3.connect(path)

        self.create()

    def create(self):

        cur = self.conn.cursor()

        cur.execute(
            """
        CREATE TABLE IF NOT EXISTS knowledge(

            id INTEGER PRIMARY KEY,

            concept TEXT,

            statement TEXT,

            confidence REAL,

            source TEXT

        )
        """
        )

        self.conn.commit()

    def add(self, concept, statement, confidence, source):

        cur = self.conn.cursor()

        cur.execute(
            """
        INSERT INTO knowledge
        (
        concept,
        statement,
        confidence,
        source
        )
        VALUES(?,?,?,?)
        """,
            (concept, statement, confidence, source),
        )

        self.conn.commit()

    def update_confidence(self, statement, confidence):

        cur = self.conn.cursor()

        cur.execute(
            """
        UPDATE knowledge

        SET confidence=?

        WHERE statement=?

        """,
            (confidence, statement),
        )

        self.conn.commit()
