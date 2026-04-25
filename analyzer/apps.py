from django.apps import AppConfig


class AnalyzerConfig(AppConfig):
    name = "analyzer"

    def ready(self) -> None:
        # Importing the module registers its @receiver decorators with
        # Django's signal dispatcher.
        from . import signals  # noqa: F401
