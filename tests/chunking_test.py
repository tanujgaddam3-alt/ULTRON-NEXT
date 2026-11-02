from ultron.learning.reader.manager import (
    DocumentReaderManager,
)

from ultron.learning.understanding.chunking.knowledge_chunker import (
    KnowledgeChunker,
)


doc = DocumentReaderManager().read(
    "tests/learning_data/test_physics.md"
)


chunker = KnowledgeChunker()


chunks = chunker.chunk(
    doc.content
)


print(
    "Chunks:",
    len(chunks)
)


for chunk in chunks[:10]:

    print()
    print(
        "TYPE:",
        chunk["type"]
    )

    print(
        chunk["content"][:150]
    )
