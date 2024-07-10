import sys
from getpass import getpass

from asgiref.sync import async_to_sync
from clicz import cli_method
from fractal.cli.controllers.auth import AuthenticatedController, auth_required
from fractal.cli.controllers.registration import RegistrationController
from fractal.matrix import MatrixClient
from fractal_database.utils import use_django

from .replicate import ReplicationController


class MatrixController(AuthenticatedController):
    PLUGIN_NAME = "matrix"

    @use_django
    @cli_method
    def init(self, url: str, *args, **kwargs):
        """
        ---
        Args:
            url: URL of the homeserver.
        """

        from fractal_database.models import Database
        from fractal_database_matrix.exceptions import MatrixHomeserverAlreadyExists
        from fractal_database_matrix.models import MatrixHomeserver

        try:
            Database.current_db()
        except Database.DoesNotExist:
            print("Database not found. Get started by creating your database with")
            print("fractal database init")

        try:
            homeserver = MatrixHomeserver.create(url=url)
        except MatrixHomeserverAlreadyExists:
            print(f"A matrix homeserver with the url {url} already exists.", file=sys.stderr)
            exit(1)
        except Exception as err:
            print(f"Error creating homeserver: {err}", file=sys.stderr)
            exit(1)

        homeserver.config.apply()
        # prompt user for credentials for their account
        matrix_id = input(f"Enter your desired matrix ID (@userid:{homeserver.url}): ")
        password = getpass(f"Enter your desired password for {matrix_id}: ")

        # register their account (and login)
        # NOTE: Assuming that matrix is being launched locally for now
        reg_controller = RegistrationController()
        reg_controller.register(
            matrix_id=matrix_id,
            password=password,
            homeserver_url=homeserver.url,
            local=True,
        )

        # generate a registration token for the homeserver so that their devices can be registered
        registration_token = reg_controller.token("create")

        resp = input(
            "Would you like to replicate your data to this homeserver? (yes/no): "
        ).lower()
        if resp == "yes" or resp == "y":
            ReplicationController().to(
                homeserver.url, registration_token, confirm=True, set_as_origin=True
            )
        else:
            # TODO: Save the registration token!
            print(
                f"You can replicate your data later with `fractal replicate to {homeserver.url} {registration_token}`"
            )

    @auth_required
    @use_django
    @cli_method
    def make_admin(self, matrix_id: str, **kwargs):
        """
        Make a user an admin on the homeserver. Assumes that you are an admin to
        the homeserver that you are currently logged in as.

        ---
        Args:
            matrix_id: The matrix ID of the user to make an admin.
        """
        access_token, homeserver_url, _ = self.get_creds()  # type: ignore

        async def _make_admin():
            async with MatrixClient(homeserver_url, access_token) as client:
                await client.synapse_admin_make_user_admin(matrix_id)

        try:
            async_to_sync(_make_admin)()
        except Exception as err:
            print(f"Error making user {matrix_id} an admin: {err}", file=sys.stderr)
            exit(1)

        print(f"Successfully made {matrix_id} an admin on {homeserver_url}")


Controller = MatrixController
