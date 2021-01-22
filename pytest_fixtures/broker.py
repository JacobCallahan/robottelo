import pytest
from broker.broker import VMBroker

from robottelo.constants import BROKER_RHEL77
from robottelo.hosts import Capsule
from robottelo.hosts import ContentHost
from robottelo.hosts import Satellite


@pytest.fixture(scope="session")
def satellite_factory():
    def factory(retry_limit=3, sleep=300, **broker_args):
        vmb = VMBroker(host_classes={'host': Satellite}, workflow='deploy-sat-lite', **broker_args)
        retries = 0
        while not (sat := vmb.checkout()) and (retries < retry_limit):
            retries += 1
            import time

            time.sleep(sleep)
        return sat

    return factory


@pytest.fixture(scope="session")
def capsule_factory():
    def factory(retry_limit=3, sleep=300, **broker_args):
        vmb = VMBroker(
            host_classes={'host': Capsule}, workflow='deploy-sat-capsule', **broker_args
        )
        retries = 0
        while not (cap := vmb.checkout()) and (retries < retry_limit):
            retries += 1
            import time

            time.sleep(sleep)
        return cap

    return factory


@pytest.fixture
def rhel7_host():
    """A function-level fixture that provides a host object based on the rhel7 nick"""
    with VMBroker(nick='rhel7') as host:
        yield host


@pytest.fixture
def rhel7_contenthost():
    """A fixture that provides a content host object based on the rhel7 nick"""
    with VMBroker(nick='rhel7', host_classes={'host': ContentHost}) as host:
        yield host


@pytest.fixture(scope="module")
def rhel77_host_module():
    """A module-level fixture that provides a host object"""
    with VMBroker(**BROKER_RHEL77) as host:
        yield host


@pytest.fixture(scope="module")
def rhel77_contenthost_module():
    """A module-level fixture that provides a ContentHost object"""
    with VMBroker(host_classes={'host': ContentHost}, **BROKER_RHEL77) as host:
        yield host


@pytest.fixture(scope="class")
def rhel77_contenthost_class(request):
    """A fixture for use with unittest classes. Provided a ContentHost object"""
    with VMBroker(host_classes={'host': ContentHost}, **BROKER_RHEL77) as host:
        request.cls.content_host = host
        yield


@pytest.fixture
def satellite_latest():
    """A fixture that provides a latest Satellite"""
    with VMBroker(host_classes={'host': Satellite}, workflow='deploy-sat-lite') as sat:
        yield sat


@pytest.fixture
def capsule_latest():
    """A fixture that provides an unconfigured latest Capsule"""
    with VMBroker(host_classes={'host': Capsule}, workflow='deploy-sat-capsule') as cap:
        yield cap
