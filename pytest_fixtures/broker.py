import pytest
from broker.broker import VMBroker

from robottelo.constants import BROKER_RHEL77
from robottelo.hosts import Capsule, ContentHost, Satellite


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
def satellite69_latest():
    """A fixture that provides a latest Satellite 6.9"""
    with VMBroker(
        host_classes={'host': Satellite},
        workflow='deploy-sat-lite',
        target_template='tpl-sat-lite-6.9.0-3.0-rhel-7.9',
    ) as sat:
        yield sat


@pytest.fixture
def capsule69_latest():
    """A fixture that provides an unconfigured latest Capsule 6.9"""
    with VMBroker(
        host_classes={'host': Capsule},
        workflow='deploy-sat-lite',
        target_template='tpl-sat-lite-6.9.0-3.0-rhel-7.9',
    ) as sat:
        yield sat
