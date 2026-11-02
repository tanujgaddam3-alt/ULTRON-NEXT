import threading

from ultron.knowledge.repository.sqlite import SQLiteKnowledgeRepository


def test_knowledge_repository_supports_gateway_thread_access(tmp_path):
    repository = SQLiteKnowledgeRepository(tmp_path / "knowledge.db")
    results = []
    failures = []

    def search_from_gateway_thread():
        try:
            results.extend(repository.search("gateway acceptance"))
        except Exception as exc:
            failures.append(exc)

    gateway_thread = threading.Thread(target=search_from_gateway_thread)
    gateway_thread.start()
    gateway_thread.join(timeout=5)

    assert not gateway_thread.is_alive()
    assert not failures
    assert results == []
