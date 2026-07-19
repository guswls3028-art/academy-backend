"""Runtime job-type registry for AI and tools workers.

Literal type aliases in contract modules are useful for editors, but production
routing and tier enforcement need one runtime SSOT so queues, workers, and
status policy do not drift apart.
"""

AI_JOB_TYPES = frozenset({
    "ocr",
    "question_segmentation",
    "handwriting_analysis",
    "embedding",
    "problem_generation",
    "homework_video_analysis",
    "omr_grading",
    "excel_parsing",
    "attendance_excel_export",
    "staff_excel_export",
    "ppt_generation",
    "problem_studio_package",
    "problem_studio_transfer",
    "problem_studio_transcription",
    "matchup_analysis",
    "matchup_index_exam",
    "matchup_search_qna",
    "matchup_manual_index",
    "matchup_public_cleanup",
})

TOOL_WORKER_JOB_TYPES = frozenset({
    "ppt_generation",
    "problem_studio_transfer",
    "excel_parsing",
    "attendance_excel_export",
    "staff_excel_export",
})

LITE_ALLOWED_JOB_TYPES = frozenset({
    "ocr",
})

BASIC_ALLOWED_JOB_TYPES = frozenset({
    "ocr",
    "omr_grading",
    "homework_video_analysis",
    "excel_parsing",
    "attendance_excel_export",
    "staff_excel_export",
    "ppt_generation",
    "problem_studio_package",
    "problem_studio_transfer",
    "problem_studio_transcription",
    "question_segmentation",
    "matchup_analysis",
    "matchup_index_exam",
    "matchup_search_qna",
    "matchup_manual_index",
    "matchup_public_cleanup",
})

DETERMINISTIC_JOB_TYPES = TOOL_WORKER_JOB_TYPES | frozenset({
    "matchup_index_exam",
    "matchup_manual_index",
    "matchup_public_cleanup",
})
