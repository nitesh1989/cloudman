from unittest import TestCase

from cm.instance import Instance
from cm.instance import TIME_IN_PAST
from cm.util import instance_states

from test_utils import TestApp
from test_utils import MockBotoInstance
from test_utils import DEFAULT_MOCK_BOTO_INSTANCE_ID
from test_utils import instrument_time


class MasterInstanceTestCase(TestCase):

    def setUp(self):
        self.__setup_app()

    def __setup_app(self, ud={}):
        self.app = TestApp(ud=ud)
        self.inst = MockBotoInstance()
        self.instance = Instance(self.app, inst=self.inst)
        self.app.manager.worker_instances = [self.instance]

    def test_id(self):
        assert self.instance.id == DEFAULT_MOCK_BOTO_INSTANCE_ID

    def test_get_cloud_instance_object(self):
        instance = self.instance

        # Without deep=True, just returned cached boto inst object.
        assert instance.get_cloud_instance_object() is self.inst

        # With deep=True should fetch new instance.
        fresh_instance = self.__seed_fresh_instance()
        assert instance.get_cloud_instance_object(deep=True) is fresh_instance

    def test_get_m_state(self):
        assert self.instance.m_state == None
        self.__seed_fresh_instance(state=instance_states.RUNNING)
        assert self.instance.get_m_state() == instance_states.RUNNING
        assert self.instance.m_state == instance_states.RUNNING

    def test_reboot(self):
        """
        Check reboot was called on boto instance, time_rebooted is
        updated, and reboot_count is incremeneted.
        """
        assert self.instance.time_rebooted is TIME_IN_PAST
        assert self.instance.reboot_count == 0
        self.instance.reboot()
        assert self.inst.was_rebooted
        assert self.instance.time_rebooted is not TIME_IN_PAST
        assert self.instance.reboot_count == 1

        # Check successive calls continue to increment reboot_count.
        self.instance.reboot()
        assert self.instance.reboot_count == 2

    def test_terminate_success(self):
        self.__expect_terminatation(success=True)
        assert self.instance.terminate_attempt_count == 0
        assert self.instance.inst is not None
        assert self.instance in self.app.manager.worker_instances
        thread = self.instance.terminate()
        thread.join()
        assert self.instance.inst is None
        assert self.instance.terminate_attempt_count == 1
        assert not self.instance in self.app.manager.worker_instances

    def test_terminate_failure(self):
        self.__expect_terminatation(success=False)
        assert self.instance.terminate_attempt_count == 0
        self.__seed_fresh_instance()   # Needed for log statement in failure
        thread = self.instance.terminate()
        thread.join()
        # inst is only set to None after success
        assert self.instance.inst is not None
        assert self.instance.terminate_attempt_count == 1
        assert self.instance in self.app.manager.worker_instances

    def test_maintain_reboot_stuck(self):
        """ Test method verifies instance is rebooted after stuck
        in PENDING state for 1000 seconds."""
        with instrument_time() as time:
            self.__assert_maintain_does_not_reboot(with_state=instance_states.PENDING)

            # Not rebooted after 100 seconds
            time.set_offset(seconds=100)
            self.__assert_maintain_does_not_reboot(with_state=instance_states.PENDING)

            # Does reboot after 600 seconds
            time.set_offset(seconds=600)
            self.__assert_maintain_reboots(with_state=instance_states.PENDING)

    def test_maintain_retry_reboot(self):
        with instrument_time() as time:
            self.__assert_maintain_does_not_reboot(with_state=instance_states.PENDING)

            # Maintain at 500 seconds determines it is stuck, attempts reboot
            time.set_offset(seconds=500)
            self.__assert_maintain_reboots(with_state=instance_states.PENDING)

            # Maintain at 600 is still stuck, but waiting for reboot.
            time.set_offset(seconds=700)
            self.__assert_maintain_does_not_reboot(with_state=instance_states.PENDING)

            # Maintain at 900 seconds, still stuck retries reboot
            time.set_offset(seconds=900)
            self.__assert_maintain_reboots(with_state=instance_states.PENDING)

    def test_maintain_extend_reboot_timeout(self):
        self.__setup_app(ud={"instance_reboot_timeout": 500})
        with instrument_time() as time:
            self.__assert_maintain_does_not_reboot(with_state=instance_states.PENDING)

            # Maintain at 500 seconds determines it is stuck, attempts reboot
            time.set_offset(seconds=500)
            self.__assert_maintain_reboots(with_state=instance_states.PENDING)

            # Maintain at 600 is still stuck, but waiting for reboot.
            time.set_offset(seconds=700)
            self.__assert_maintain_does_not_reboot(with_state=instance_states.PENDING)

            # Maintain at 900 seconds, would normally reboot but timeout is
            # extended so it won't.
            time.set_offset(seconds=900)
            self.__assert_maintain_does_not_reboot(with_state=instance_states.PENDING)

            # Will eventually reboot again though...
            time.set_offset(seconds=1200)
            self.__assert_maintain_reboots(with_state=instance_states.PENDING)

    def test_maintain_state_change(self):
        with instrument_time() as time:
            self.__assert_maintain_does_not_reboot(with_state=instance_states.PENDING)

            # 350 is past reboot timeout (300), but wait for 400 seconds for state change
            # so no reboot.
            time.set_offset(seconds=350)
            self.__assert_maintain_does_not_reboot(with_state=instance_states.PENDING)

    def test_maintain_extend_state_change_wait(self):
        self.__setup_app(ud={"instance_state_change_wait": 700})
        with instrument_time() as time:
            self.__assert_maintain_does_not_reboot(with_state=instance_states.PENDING)

            # Does not reboot after 600 seconds, waiting for state change.
            time.set_offset(seconds=600)
            self.__assert_maintain_does_not_reboot(with_state=instance_states.PENDING)

            # Does eventually reboot though
            time.set_offset(seconds=800)
            self.__assert_maintain_reboots(with_state=instance_states.PENDING)

    def test_maintain_reboots_on_error(self):
        self.__assert_maintain_reboots(with_state=instance_states.ERROR)

    def test_maintain_reboots_after_comm_loss(self):
        with instrument_time() as time:
            self.instance.handle_message("TEST")
            self.__assert_maintain_does_not_reboot(with_state=instance_states.RUNNING)

            time.set_offset(seconds=500)
            self.__assert_maintain_reboots(with_state=instance_states.RUNNING)

    def test_extend_comm_timeout(self):
        # Same test as above, but extend the comm timeout to verify it prevents
        # instance from being rebooted.
        self.__setup_app(ud={"instance_comm_timeout": 700})
        with instrument_time() as time:
            self.instance.handle_message("TEST")
            self.__assert_maintain_does_not_reboot(with_state=instance_states.RUNNING)

            time.set_offset(seconds=500)
            self.__assert_maintain_does_not_reboot(with_state=instance_states.RUNNING)

    def test_maintain_no_reboot_if_comm_active(self):
        with instrument_time() as time:
            self.instance.handle_message("TEST")
            self.__assert_maintain_does_not_reboot(with_state=instance_states.RUNNING)

            time.set_offset(seconds=350)
            self.instance.handle_message("TEST")

            time.set_offset(seconds=500)
            self.__assert_maintain_does_not_reboot(with_state=instance_states.RUNNING)

    def test_terminates_after_enough_reboots(self):
        for _ in range(4):
            self.__assert_maintain_reboots(with_state=instance_states.ERROR)

        self.__expect_terminatation(success=False)
        self.__assert_maintain_does_not_reboot(with_state=instance_states.ERROR)

    def test_change_reboot_attempts(self):
        self.__setup_app(ud={"instance_reboot_attempts": 2})
        for _ in range(2):
            self.__assert_maintain_reboots(with_state=instance_states.ERROR)

        self.__expect_terminatation(success=False)
        self.__assert_maintain_does_not_reboot(with_state=instance_states.ERROR)

    # Test is flaky, need to join thread somehow.
    def test_terminate_attempts(self):
        for _ in range(4):
            self.__assert_maintain_reboots(with_state=instance_states.ERROR)

        for _ in range(4):
            self.__expect_terminatation(success=False)
            self.__assert_maintain_does_not_reboot(with_state=instance_states.ERROR)
            assert self.instance in self.app.manager.worker_instances

        # Ultimately gives on terminates and just reboots.
        self.__assert_maintain_does_not_reboot(with_state=instance_states.ERROR)
        assert self.instance not in self.app.manager.worker_instances

    def test_modify_terminate_attempts(self):
        self.__setup_app(ud={"instance_terminate_attempts": 2})
        for _ in range(4):
            self.__assert_maintain_reboots(with_state=instance_states.ERROR)

        for _ in range(2):
            self.__expect_terminatation(success=False)
            self.__assert_maintain_does_not_reboot(with_state=instance_states.ERROR)
            assert self.instance in self.app.manager.worker_instances

        # Ultimately gives on terminates and just reboots.
        self.__assert_maintain_does_not_reboot(with_state=instance_states.ERROR)
        assert self.instance not in self.app.manager.worker_instances

    def __assert_maintain_reboots(self, with_state):
        inst = self.__maintain_with_instance(state=with_state)
        assert inst.was_rebooted

    def __assert_maintain_does_not_reboot(self, with_state):
        inst = self.__maintain_with_instance(state=with_state)
        assert not inst.was_rebooted

    def __maintain_with_instance(self, **instance_kwds):
        inst = self.__seed_fresh_instance(**instance_kwds)
        self.instance.maintain()
        return inst

    def __expect_terminatation(self, success):
        self.app.cloud_interface.expect_terminatation( \
            DEFAULT_MOCK_BOTO_INSTANCE_ID, spot_request_id=None, success=success)

    def __seed_fresh_instance(self, state=None):
        fresh_instance = MockBotoInstance()
        self.app.cloud_interface.set_mock_instances([fresh_instance])
        if state:
            fresh_instance.state = state
        return fresh_instance
