# Video infrastructure - Batch only. No SQS adapter.
from .processor import process_video

__all__ = ["process_video"]
