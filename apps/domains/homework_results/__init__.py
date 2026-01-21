# apps/domains/homework_results/__init__.py
# homework_results domain package
#
# DESIGN:
# - Runtime 결과(스냅샷) 전용 도메인
# - Homework 정의/정책(homework 도메인)과 분리하여
#   results(exam 결과)와 같은 레이어링을 만든다.
