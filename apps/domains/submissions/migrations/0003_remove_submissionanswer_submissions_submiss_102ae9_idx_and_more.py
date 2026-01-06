from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("submissions", "0002_submissionanswer_contract_v2"),
    ]

    operations = [
        # -------------------------------------------------
        # 🔴 NO-OP MIGRATION
        #
        # 이 migration은 다음 문제로 인해 비워둔다:
        # - question_id 제거
        # - exam_question_id 추가
        # - index/unique 변경
        #
        # 이 모든 작업은 이미
        #   - DB 레벨에서 완료되었거나
        #   - 이전 migration에서 처리되었음
        #
        # Django migration state 정합성만 맞추기 위한 migration
        # -------------------------------------------------
    ]
