class MatrixHomeserverAlreadyExists(Exception):
    def __init__(self, homeserver_url: str):
        self.homeserver_url = homeserver_url
        super().__init__(f"Matrix homeserver with URL {homeserver_url} already exists.")
