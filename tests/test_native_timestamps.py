import datetime as dt
import unittest

from featurevisor.conditions import _to_datetime, value_is_matched


class NoOffset(dt.tzinfo):
    def utcoffset(self, value):
        return None


class InvalidOffset(dt.tzinfo):
    def utcoffset(self, value):
        return dt.timedelta(hours=24)


class NativeTimestampTests(unittest.TestCase):
    def matches(self, actual, operator, expected):
        return value_is_matched(actual, {"operator": operator, "value": expected}, lambda *_: None)

    def test_native_precision_and_offsets(self):
        for stamp in ("2026-01-01T00:00:00Z", "1969-12-31T23:59:59.999Z"):
            boundary = dt.datetime.fromisoformat(stamp.replace("Z", "+00:00"))
            for hours in (-14, 0, 14):
                host = (boundary + dt.timedelta(microseconds=999)).astimezone(dt.timezone(dt.timedelta(hours=hours)))
                self.assertEqual(_to_datetime(host), boundary)
                for operator in ("before", "after"):
                    self.assertFalse(self.matches(host, operator, stamp))
                    self.assertEqual(self.matches(host, operator, (boundary + dt.timedelta(milliseconds=1)).isoformat()), operator == "before")
                    self.assertEqual(self.matches(host, operator, (boundary - dt.timedelta(milliseconds=1)).isoformat()), operator == "after")

    def test_fractional_native_offset_truncates_the_utc_fraction(self):
        offset = dt.timezone(dt.timedelta(microseconds=500))
        host = dt.datetime(1970, 1, 1, tzinfo=offset)
        self.assertEqual(_to_datetime(host), dt.datetime(1969, 12, 31, 23, 59, 59, 999000, tzinfo=dt.timezone.utc))
        self.assertFalse(self.matches(host, "after", "1969-12-31T23:59:59.999Z"))
        self.assertTrue(self.matches(host, "before", "1970-01-01T00:00:00Z"))

    def test_bounds_and_invalid_types(self):
        for host in (dt.datetime.min.replace(tzinfo=dt.timezone.utc), dt.datetime.max.replace(tzinfo=dt.timezone.utc)):
            self.assertEqual(_to_datetime(host), host.replace(microsecond=host.microsecond // 1000 * 1000))
        invalid = [dt.date(2026, 1, 1), dt.datetime(2026, 1, 1), dt.datetime(2026, 1, 1, tzinfo=NoOffset()),
                   dt.datetime(2026, 1, 1, tzinfo=InvalidOffset()),
                   dt.datetime.min.replace(tzinfo=dt.timezone(dt.timedelta(hours=1))),
                   dt.datetime.max.replace(tzinfo=dt.timezone(dt.timedelta(hours=-1))),
                   "2026-01-01", "2026-01-01T00:00:00", 1767225600000, None]
        for seconds in (-54000, -50401, 50401, 54000):
            invalid.append(dt.datetime(2026, 1, 1, tzinfo=dt.timezone(dt.timedelta(seconds=seconds))))
        for actual in invalid:
            for operator in ("before", "after"):
                with self.subTest(actual=actual, operator=operator):
                    self.assertFalse(self.matches(actual, operator, "2026-01-01T00:00:00Z"))
                    self.assertFalse(self.matches(dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc), operator, actual))
