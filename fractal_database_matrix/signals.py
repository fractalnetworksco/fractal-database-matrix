import os

from fractal_database_matrix.models import MatrixCredentials, MatrixReplicationTarget


def _generate_password() -> str:
    return os.urandom(20).hex()


def register_with_target(sender, *args, **kwargs):
    pass


def generate_target_credentails(
    sender: MatrixReplicationTarget,
    instance: MatrixReplicationTarget,
    created: bool,
    raw: bool,
    **kwargs
) -> None:
    creds = MatrixCredentials.objects.filter(target=instance)
    if creds.exists():
        return None

    password = _generate_password()
    MatrixCredentials.objects.create(
        matrix_id=os.environ.get("MATRIX_USER_ID"),
        password=_generate_password(),
        access_token=os.environ.get("MATRIX_ACCESS_TOKEN"),
        target=instance,
    )
