import base64
import hashlib


class SecurityEncryption:
    """
    Handles security data encryption.
    """

    def __init__(
        self,
        key: str,
    ):

        self.key = hashlib.sha256(key.encode()).digest()

    def encrypt(
        self,
        data: str,
    ):

        encoded = data.encode()

        encrypted = bytes(
            value ^ self.key[index % len(self.key)]
            for index, value in enumerate(encoded)
        )

        return base64.b64encode(encrypted).decode()

    def decrypt(
        self,
        encrypted_data: str,
    ):

        encrypted = base64.b64decode(encrypted_data)

        decrypted = bytes(
            value ^ self.key[index % len(self.key)]
            for index, value in enumerate(encrypted)
        )

        return decrypted.decode()
