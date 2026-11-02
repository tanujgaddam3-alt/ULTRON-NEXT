import uuid

from ultron.infrastructure.container import container


def main():

    session_id = str(uuid.uuid4())

    conversation = container.conversation

    print()
    print("================================")
    print("        ULTRON ONLINE")
    print("================================")
    print()

    print(
        "Session:",
        session_id
    )

    print(
        "Type 'exit' to disconnect."
    )


    while True:

        try:

            user_input = input(
                "\nYou: "
            )


            if user_input.lower() in [
                "exit",
                "quit",
                "shutdown",
            ]:

                print(
                    "ULTRON: Session terminated."
                )

                break


            response = conversation.process(
                message=user_input,
                session_id=session_id,
            )


            print(
                "\nULTRON:"
            )

            if isinstance(response, dict):

                print(
                    response.get(
                        "message",
                        response
                    )
                )

            else:

                print(response)



        except KeyboardInterrupt:

            print(
                "\nULTRON: Interrupted."
            )

            break


        except Exception as error:

            print(
                "\nULTRON ERROR:",
                error
            )



if __name__ == "__main__":

    main()
