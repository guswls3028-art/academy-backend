"""text는 있는데 embedding 누락 problem 백필.

운영 사고(2026-04-30): 큰 PDF 처리에서 60min hard-exit으로 embedding 단계 못 끝낸 doc.
status=done으로 살리면 problem은 보이지만 임베딩 누락 → 매치업 검색/적중 보고서 후보 0건.
"""
import os
from django.core.management.base import BaseCommand

# tqdm progress bar (sentence-transformers 기본) → cp949 호환 안 되는 stderr 방지
os.environ.setdefault("TQDM_DISABLE", "1")
os.environ.setdefault("TRANSFORMERS_NO_ADVISORY_WARNINGS", "1")


class Command(BaseCommand):
    help = "text는 있는데 embedding 누락 MatchupProblem 백필"

    def add_arguments(self, parser):
        parser.add_argument("--tenant-id", type=int, required=True)
        parser.add_argument("--doc-id", type=int, action="append", default=[])
        parser.add_argument("--batch", type=int, default=32)
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **opts):
        from apps.domains.matchup.models import MatchupProblem

        qs = MatchupProblem.objects.filter(
            tenant_id=opts["tenant_id"], embedding__isnull=True,
        ).exclude(text="")
        if opts["doc_id"]:
            qs = qs.filter(document_id__in=opts["doc_id"])
        rows = list(qs.only("id", "text"))
        self.stdout.write(f"target: {len(rows)} problems")
        if not rows:
            return

        if opts["dry_run"]:
            return

        from sentence_transformers import SentenceTransformer
        model = SentenceTransformer("paraphrase-multilingual-MiniLM-L12-v2")

        bs = opts["batch"]
        applied = 0
        for start in range(0, len(rows), bs):
            chunk = rows[start:start + bs]
            texts = [(r.text or "").strip() for r in chunk]
            vectors = model.encode(texts, convert_to_numpy=False, show_progress_bar=False)
            for r, vec in zip(chunk, vectors):
                r.embedding = list(map(float, vec))
                r.save(update_fields=["embedding", "updated_at"])
                applied += 1
            self.stdout.write(f"  applied {applied}/{len(rows)}")

        self.stdout.write(f"DONE: {applied}")
