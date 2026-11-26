"""Waking a device: the packet is sent again until the device answers, and the outcome says whether it did."""
from couchctl import power
from couchctl.config import Device


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


def test_broadcasts_add_the_devices_own_network():
    assert power.broadcasts("192.0.2.10") == ["255.255.255.255", "192.0.2.255"]
    assert power.broadcasts("tv.example.org") == ["255.255.255.255"]
    assert power.broadcasts("") == ["255.255.255.255"]


def test_wake_keeps_sending_until_the_device_answers():
    clock, sent, answers = Clock(), [], iter([False, False, True])
    dev = Device(name="living room", kind="samsung", host="192.0.2.10", mac="aa:bb:cc:dd:ee:ff")
    outcome, why = power.wake(dev, wait=25, every=2, send=lambda mac, to: sent.append(to),
                              is_on=lambda: next(answers), sleep=clock.sleep, clock=clock)
    assert outcome == "success" and why == "living room is on, after 6 s"
    assert sent == ["255.255.255.255", "192.0.2.255"] * 3


def test_wake_says_unknown_and_not_failure_when_the_device_has_not_answered_yet():
    # silence at the end of the wait is not a measurement of "it did not come on": a television that is starting can answer
    # after the wait, and one that does not listen for the packet never answers, and from outside the two look the same
    clock, sent = Clock(), []
    dev = Device(name="bedroom", kind="samsung", host="192.0.2.11", mac="aa:bb:cc:dd:ee:ff")
    outcome, why = power.wake(dev, wait=10, every=2, send=lambda mac, to: sent.append(to),
                              is_on=lambda: False, sleep=clock.sleep, clock=clock)
    assert outcome == "unknown"
    assert why.startswith("bedroom has not answered after 5 wake packets over 10 s, so it is not known whether it will come on.")
    assert "can take longer than this wait" in why
    assert "Power on with mobile" in why
    assert "did not come on" not in why
    assert len(sent) == 10


def test_a_device_that_answers_on_the_last_check_is_still_a_success():
    clock, answers = Clock(), iter([False, False, False, False, True])
    dev = Device(name="bedroom", kind="samsung", host="192.0.2.11", mac="aa:bb:cc:dd:ee:ff")
    outcome, why = power.wake(dev, wait=10, every=2, send=lambda mac, to: None, is_on=lambda: next(answers),
                              sleep=clock.sleep, clock=clock)
    assert outcome == "success" and why == "bedroom is on, after 10 s"


def test_wake_without_a_mac_is_refused_and_sends_nothing():
    sent = []
    outcome, _ = power.wake(Device(name="box", kind="androidtv"), send=lambda mac, to: sent.append(to))
    assert outcome == "refused" and sent == []


def test_a_packet_that_cannot_be_sent_is_a_failure():
    def broken(mac, to):
        raise OSError("network is unreachable")
    clock = Clock()
    outcome, why = power.wake(Device(name="tv", kind="samsung", host="192.0.2.12", mac="aa:bb:cc:dd:ee:ff"),
                              send=broken, is_on=lambda: False, sleep=clock.sleep, clock=clock)
    assert outcome == "failure" and why == "could not send the wake packet: network is unreachable"
