"""
Worker settings 환경에서 Django boot + 모든 모델의 FK resolution 검증.

test_worker_settings_drift.py가 subprocess로 실행. 이 스크립트는 worker settings로
Django.setup()한 뒤 모든 INSTALLED_APPS의 모델 _meta.fields를 순회하며 ForeignKey/
OneToOne/ManyToMany의 related_model 접근 시 'Related model X.Y cannot be resolved'를
유발할 수 있으면 실패하고 종료.

별도 프로세스로 분리하는 이유:
  - pytest는 이미 base.py settings로 setup() 완료 — 같은 프로세스에서 settings 변경 불가
  - 단순 import만으로는 FK 지연 평가 — explicit 접근 필요

성공 시: exit 0, "OK" 출력
실패 시: exit 1, ValueError 메시지 출력
"""
from __future__ import annotations

import sys


def main() -> int:
    try:
        import django
        django.setup()
    except Exception as e:  # noqa: BLE001
        print(f"DJANGO_SETUP_FAIL: {type(e).__name__}: {e}")
        return 1

    from django.apps import apps as django_apps
    from django.db.models import ForeignKey, OneToOneField, ManyToManyField

    failures: list[str] = []
    model_count = 0
    fk_count = 0

    for app_config in django_apps.get_app_configs():
        for model in app_config.get_models():
            model_count += 1
            for field in model._meta.get_fields():
                if not isinstance(field, (ForeignKey, OneToOneField, ManyToManyField)):
                    # 다른 방향 (related_objects)이나 m2m through 자동 생성 등은 위에서 잡힘
                    continue
                fk_count += 1
                try:
                    related = field.related_model
                    if related is None or isinstance(related, str):
                        failures.append(
                            f"{model._meta.label}.{field.name}: related_model unresolved ({related!r})"
                        )
                except Exception as e:  # noqa: BLE001
                    failures.append(
                        f"{model._meta.label}.{field.name}: {type(e).__name__}: {e}"
                    )

    if failures:
        print(f"FK_RESOLUTION_FAIL ({len(failures)} failures, {model_count} models, {fk_count} FK fields):")
        for f in failures[:30]:
            print(f"  - {f}")
        return 1

    print(f"OK: {model_count} models, {fk_count} FK fields all resolved.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
