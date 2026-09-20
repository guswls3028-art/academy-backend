import datetime

from tests import test_clinic_midnight_migration_lock_timeout as migration_contract


class ClinicOvernightMigrationLockTimeoutTests(migration_contract.ClinicMidnightMigrationLockTimeoutTests):
    migrate_from = ("clinic", "0021_allow_booking_range_to_end_at_midnight")
    migrate_to = ("clinic", "0022_allow_overnight_booking_range")
    permitted_end_time = datetime.time(1, 0)
