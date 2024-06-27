from django.apps import AppConfig
from django.db import models


class FractalDatabaseMatrixConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "fractal_database_matrix"

    def ready(self) -> None:
        import fractal_database_matrix.signals
        from fractal_database.models import Database
        from fractal_database_matrix.signals import (
            create_matrix_replication_target_for_new_database,
        )

        subclasses = Database.get_subclasses()

        # connect signal to create matrix replication target for new databases
        models.signals.post_save.connect(
            create_matrix_replication_target_for_new_database, sender=Database
        )
        for subclass in subclasses:
            models.signals.post_save.connect(
                create_matrix_replication_target_for_new_database, sender=subclass
            )
