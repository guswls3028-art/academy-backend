from rest_framework.routers import DefaultRouter

from .boards.views import (
    BoardCategoryViewSet,
    BoardPostViewSet,
    BoardReadStatusViewSet,
)
from .counseling.views import CounselingViewSet
from .questions.views import QuestionViewSet, AnswerViewSet
from .materials.views import (
    MaterialViewSet,
    MaterialCategoryViewSet,
    MaterialAccessViewSet,
)

router = DefaultRouter()

# boards
router.register("board-categories", BoardCategoryViewSet)
router.register("board-posts", BoardPostViewSet)
router.register("board-read-status", BoardReadStatusViewSet)

# counseling
router.register("counselings", CounselingViewSet)

# questions
router.register("questions", QuestionViewSet)
router.register("answers", AnswerViewSet)

# materials
router.register("materials", MaterialViewSet)
router.register("material-categories", MaterialCategoryViewSet)
router.register("material-accesses", MaterialAccessViewSet)

urlpatterns = router.urls
