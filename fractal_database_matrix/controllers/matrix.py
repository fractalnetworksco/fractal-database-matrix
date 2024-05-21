import docker
from clicz import cli_method


class MatrixController:
    PLUGIN_NAME = "matrix"

    def _generate_registration_token(self):
        """
        INSERT INTO registration_tokens VALUES ("pizza",null,0,0,null);
        ---
        """
        pass

    def _launch_server(self):
        print("Launching Matrix Homeserver")

    @cli_method
    def init(self):
        """
        ---
        """
        self._launch_server()


Controller = MatrixController
