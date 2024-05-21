from django.apps import AppConfig


class FractalDatabaseMatrixConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "fractal_database_matrix"

    def ready(self) -> None:
        import fractal_database_matrix.signals
