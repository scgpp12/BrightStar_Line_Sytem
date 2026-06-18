"""要員（スタッフ）データ層.

Phase 2。営業の提案で「どの要員をどの現場へ出すといくら掛かるか」を一括比較するため、
要員の最寄駅と稼働ステータスを持つ。

★個人情報への配慮★
  住所などの詳細は持たず、**最寄駅だけ**を保持する（提案コスト試算にはこれで足りる）。
  氏名・部署は社内システム BrightStar の花名册(roster)由来。

★差し替え契約（保存層）★
  業務層は StaffRepository 抽象だけに依存する。今は SQLite 実装で単体起動できるが、
  将来 BrightStar の DynamoDB roster を読む実装に差し替えても業務層は無改修。
  要員の主キー staff_id は BrightStar roster の empId（E001…）に合わせる。
"""

from .models import Staff, StaffStatus, StaffNotFoundError
from .repository import StaffRepository
from .sqlite_repository import SQLiteStaffRepository

__all__ = [
    "Staff",
    "StaffStatus",
    "StaffNotFoundError",
    "StaffRepository",
    "SQLiteStaffRepository",
]
