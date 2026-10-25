"""No-network tests for SIP redial mitigation and resource limits."""
import unittest
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parent
sys.path[:0]=[str(ROOT),str(ROOT/'vendor')]

from phonebridge.inbound_abuse_guard import (
    InboundAbuseGuard, caller_identity, COOLDOWN_SECONDS, GLOBAL_LIMIT)

SENDER='<sip:+12025550123@carrier.example>;tag=callertag'
OTHER='<sip:+12025550124@carrier.example>;tag=callertag'


class FakeClock:
    def __init__(self):self.t=1000.0
    def __call__(self):return self.t
    def advance(self,amount):self.t+=amount


class AbusePolicyTests(unittest.TestCase):
    def setUp(self):
        self.clock=FakeClock()
        self.guard=InboundAbuseGuard(clock=self.clock,secret=b"dev-testing-key-has-32-bytes!" )

    def rapid_hangups(self,identity=SENDER,count=3):
        for _ in range(count):
            self.assertEqual(self.guard.check_invite(identity),(None,0))
            self.clock.advance(1.3)
            self.guard.record_remote_end(identity,elapsed=1.3)
            self.clock.advance(1)

    def test_caller_parsing_no_arbitrary_digits(self):
        self.assertEqual(caller_identity(SENDER),"+12025550123")
        self.assertEqual(caller_identity('"Anonymous" <sip:anonymous@invalid.example>'),None)
        self.assertIsNone(caller_identity('<sip:not-a-number@carrier.example>;tag=+12025550123'))
        self.assertIsNone(caller_identity(''))
        self.assertIsNone(caller_identity('junk +12025550123'))

    def test_three_fast_confirmed_hangups_trigger_cooldown(self):
        self.rapid_hangups()
        why,seconds=self.guard.check_invite(SENDER)
        self.assertEqual(why,"repeat_short_calls")
        self.assertGreater(seconds,50)
        self.assertLessEqual(seconds,COOLDOWN_SECONDS)
        self.clock.advance(61)
        self.assertEqual(self.guard.check_invite(SENDER),(None,0))

    def test_other_caller_is_not_blocked(self):
        self.rapid_hangups()
        self.assertEqual(self.guard.check_invite(OTHER),(None,0))

    def test_legitimate_long_calls_are_never_strikes(self):
        for _ in range(7):
            self.assertEqual(self.guard.check_invite(SENDER),(None,0))
            self.clock.advance(6)
            self.assertFalse(self.guard.record_remote_end(SENDER,elapsed=6))
        self.assertEqual(self.guard.check_invite(SENDER),(None,0))

    def test_incomplete_network_disconnect_not_counted(self):
        # The gateway never calls record_remote_end() for dropped TCP/TLS.
        for _ in range(8):
            self.guard.check_invite(SENDER)
            self.clock.advance(12)
        self.assertEqual(self.guard.check_invite(SENDER),(None,0))

    def test_unknown_identity_no_shared_lockout(self):
        for _ in range(7):
            self.guard.check_invite('"Anonymous" <sip:anonymous@invalid>')
            self.guard.record_remote_end('"Anonymous" <sip:anonymous@invalid>',elapsed=1)
            self.clock.advance(1)
        self.assertEqual(self.guard.check_invite(SENDER),(None,0))

    def test_global_flood_cap_expires(self):
        for _ in range(GLOBAL_LIMIT):
            self.assertEqual(self.guard.check_invite(OTHER),(None,0))
        self.assertEqual(self.guard.check_invite(OTHER),("global_burst",5))
        self.clock.advance(10.2)
        self.assertEqual(self.guard.check_invite(OTHER),(None,0))

    def test_repeat_short_ends_outside_window_not_penalized(self):
        for _ in range(6):
            self.guard.check_invite(SENDER)
            self.guard.record_remote_end(SENDER,elapsed=2)
            self.clock.advance(95)
        self.assertEqual(self.guard.check_invite(SENDER),(None,0))

    def test_state_bounded_and_no_plain_identity_stored(self):
        self.rapid_hangups()
        blob=repr(self.guard.__dict__)
        self.assertNotIn("+12025550123",blob)
        self.assertNotIn("callertag",blob)

    def test_clock_goes_monotonic_cooldown(self):
        self.rapid_hangups()
        self.clock.advance(30)
        self.assertEqual(self.guard.check_invite(SENDER)[0],"repeat_short_calls")
        self.clock.advance(31)
        self.assertEqual(self.guard.check_invite(SENDER),(None,0))


if __name__=="__main__": unittest.main(verbosity=2)
