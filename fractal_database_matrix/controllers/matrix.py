from getpass import getpass

import docker
from clicz import cli_method
from fractal.cli.controllers.registration import RegistrationController
from fractal_database.utils import use_django

from .replicate import ReplicationController


class MatrixController:
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
        from fractal_database_matrix.models import MatrixHomeserver

        try:
            Database.current_db()
        except Database.DoesNotExist:
            print("Database not found. Get started by creating your database with")
            print("fractal database init")

        homeserver = MatrixHomeserver.create(url=url)

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

        # TODO: launch worker for current device


Controller = MatrixController
