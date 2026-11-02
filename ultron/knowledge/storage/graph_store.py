import sqlite3


class GraphStore:
    def __init__(self, path="ultron_memory.db"):

        self.conn = sqlite3.connect(path)

        self.create_tables()

    def create_tables(self):

        cur = self.conn.cursor()

        cur.execute("""
        CREATE TABLE IF NOT EXISTS nodes(

            id INTEGER PRIMARY KEY,

            name TEXT UNIQUE

        )
        """)

        cur.execute("""
        CREATE TABLE IF NOT EXISTS edges(

            id INTEGER PRIMARY KEY,

            source TEXT,

            relation TEXT,

            target TEXT,

            explanation TEXT,

            confidence REAL,

            source_document TEXT,

            UNIQUE(source, relation, target)

        )
        """)

        self.conn.commit()

    def add_graph(self, graph):

        cur = self.conn.cursor()

        for node in graph["nodes"]:
            cur.execute(
                """
                INSERT OR IGNORE INTO nodes(name)
                VALUES(?)
                """,
                (node,),
            )

        for edge in graph["edges"]:
            cur.execute(
                """
                INSERT OR IGNORE INTO edges(
                    source,
                    relation,
                    target,
                    explanation,
                    confidence,
                    source_document
                )
                VALUES(?,?,?,?,?,?)
                """,
                (
                    edge["source"],
                    edge["relation"],
                    edge["target"],
                    edge.get("explanation", ""),
                    edge.get("confidence", 1.0),
                    edge.get("source_document", ""),
                ),
            )

        self.conn.commit()

    def query_node(self, name):

        cur = self.conn.cursor()
        try:
            cur.execute("ALTER TABLE edges ADD COLUMN explanation TEXT")
        except sqlite3.OperationalError:
            pass

        cur.execute(
            """
            SELECT *
            FROM edges
            WHERE source=?
            OR target=?
            """,
            (name, name),
        )

        return cur.fetchall()

    def clear(self):

        cur = self.conn.cursor()

        cur.execute("DELETE FROM nodes")

        cur.execute("DELETE FROM edges")

        self.conn.commit()
