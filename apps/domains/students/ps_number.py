# PATH: apps/domains/students/ps_number.py
"""DRF 의존 없는 유틸 — AI Worker 등에서 사용."""

import random
import string


def generate_unique_ps_number() -> str:
    """영어 1자리 + 숫자 5자리 (예: A12345) 중복 없이 부여 (User.username 전역 유일)"""
    from academy.adapters.db.django import repositories_students as student_repo
    letters = string.ascii_uppercase
    for _ in range(200):
        letter = random.choice(letters)
        num = random.randint(0, 99999)
        candidate = f"{letter}{num:05d}"
        if not student_repo.user_filter_username_exists(candidate):
            return candidate
    raise ValueError("아이디 생성에 실패했습니다. 다시 시도해 주세요.")
