from ultron.infrastructure.container import container


def test_conversation_available():

    assert container.conversation is not None

    print(
        "Conversation layer connected"
    )


if __name__ == "__main__":

    test_conversation_available()
