from django.apps import apps


def test_push_models_are_registered_during_django_startup() -> None:
    registered_models = {
        model.__name__
        for model in apps.get_app_config("teacher_app").get_models()
    }

    assert {"PushNotificationConfig", "PushSubscription"} <= registered_models
