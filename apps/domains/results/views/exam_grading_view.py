# PATH: apps/domains/results/views/exam_grading_view.py
# ────────────────────────────────────────────────────
# This module previously contained dead view classes
# (AutoGradeSubmissionView, ManualGradeSubmissionView,
#  FinalizeResultView, MyExamResultListView, ExamResultAdminListView)
# that were never connected to any URL pattern.
#
# Grading functionality is served by:
#   - results/services/grading_service.py (service layer)
#   - results/views/admin_exam_*_score_view.py (active endpoints)
#
# Removed 2026-04-15 during full-product audit cleanup.
