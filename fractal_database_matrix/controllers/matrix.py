import docker
from clicz import cli_method
from fractal_database.utils import use_django


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

    @use_django
    @cli_method
    def init(self, url: str, *args, **kwargs):
        """
        ---
        Args:
            url: URL of the homeserver.
        """
        from fractal_database.models import Database
        from fractal_database_matrix.models import MatrixHomeserver

        try:
            current_database = Database.current_db()
        except Database.DoesNotExist:
            print("Database not found. Get started by creating your database with")
            print("fractal database init")

        homeserver = MatrixHomeserver.create(url=url)

        # self._launch_server()


Controller = MatrixController
