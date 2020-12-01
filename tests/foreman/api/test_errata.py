"""API Tests for the errata management feature

:Requirement: Errata

:CaseAutomation: Automated

:CaseLevel: System

:CaseComponent: ErrataManagement

:Assignee: tpapaioa

:TestType: Functional

:CaseImportance: High

:Upstream: No
"""
# For ease of use hc refers to host-collection throughout this document
from time import sleep

import pytest
from nailgun import entities

from robottelo.api.utils import enable_rhrepo_and_fetchid
from robottelo.api.utils import promote
from robottelo.cli.factory import setup_org_for_a_custom_repo
from robottelo.cli.factory import setup_org_for_a_rh_repo
from robottelo.config import settings
from robottelo.constants import DEFAULT_ARCHITECTURE
from robottelo.constants import DEFAULT_RELEASE_VERSION
from robottelo.constants import DISTRO_RHEL6
from robottelo.constants import DISTRO_RHEL7
from robottelo.constants import DISTRO_RHEL8
from robottelo.constants import FAKE_1_CUSTOM_PACKAGE
from robottelo.constants import FAKE_1_CUSTOM_PACKAGE_NAME
from robottelo.constants import FAKE_2_CUSTOM_PACKAGE
from robottelo.constants import FAKE_2_ERRATA_ID
from robottelo.constants import FAKE_3_ERRATA_ID
from robottelo.constants import FAKE_3_YUM_ERRATUM_COUNT
from robottelo.constants import FAKE_9_YUM_ERRATUM
from robottelo.constants import FAKE_9_YUM_ERRATUM_COUNT
from robottelo.constants import FAKE_9_YUM_OUTDATED_PACKAGES
from robottelo.constants import PRDS
from robottelo.constants import REAL_0_ERRATA_ID
from robottelo.constants import REAL_0_RH_PACKAGE
from robottelo.constants import REAL_1_ERRATA_ID
from robottelo.constants import REAL_2_ERRATA_ID
from robottelo.constants import REPOS
from robottelo.constants import REPOSET
from robottelo.constants.repos import CUSTOM_SWID_TAG_REPO
from robottelo.constants.repos import FAKE_3_YUM_REPO
from robottelo.constants.repos import FAKE_9_YUM_REPO
from robottelo.helpers import add_remote_execution_ssh_key
from robottelo.products import RepositoryCollection
from robottelo.products import YumRepository
from robottelo.vm import VirtualMachine

pytestmark = [
    pytest.mark.run_in_one_thread,
    pytest.mark.skipif((not settings.repos_hosting_url), reason='Missing repos_hosting_url'),
]

CUSTOM_REPO_URL = FAKE_9_YUM_REPO
CUSTOM_REPO_ERRATA_ID = FAKE_2_ERRATA_ID


@pytest.fixture(scope='module')
def activation_key(module_org, module_lce):
    activation_key = entities.ActivationKey(
        environment=module_lce, organization=module_org
    ).create()
    return activation_key


@pytest.fixture(scope='module')
def rh_repo(module_org, module_lce, module_cv, activation_key):
    setup_org_for_a_rh_repo(
        {
            'product': PRDS['rhel'],
            'repository-set': REPOSET['rhst7'],
            'repository': REPOS['rhst7']['name'],
            'organization-id': module_org.id,
            'content-view-id': module_cv.id,
            'lifecycle-environment-id': module_lce.id,
            'activationkey-id': activation_key.id,
        },
        force_manifest_upload=True,
    )
    return rh_repo


@pytest.fixture(scope='module')
def custom_repo(module_org, module_lce, module_cv, activation_key):
    custom_entities = setup_org_for_a_custom_repo(
        {
            'url': FAKE_9_YUM_REPO,
            'organization-id': module_org.id,
            'content-view-id': module_cv.id,
            'lifecycle-environment-id': module_lce.id,
            'activationkey-id': activation_key.id,
        }
    )
    return custom_entities


def _install_package(
    module_org, clients, host_ids, package_name, via_ssh=True, rpm_package_name=None
):
    """Install package via SSH CLI if via_ssh is True, otherwise
    install via http api: PUT /api/v2/hosts/bulk/install_content
    """
    if via_ssh:
        for client in clients:
            result = client.run(f'yum install -y {package_name}')
            assert result.return_code == 0
            result = client.run(f'rpm -q {package_name}')
            assert result.return_code == 0
    else:
        entities.Host().install_content(
            data={
                'organization_id': module_org.id,
                'included': {'ids': host_ids},
                'content_type': 'package',
                'content': [package_name],
            }
        )
        _validate_package_installed(clients, rpm_package_name)


def _validate_package_installed(hosts, package_name, expected_installed=True, timeout=120):
    """Check whether package was installed on the list of hosts."""
    for host in hosts:
        for _ in range(timeout // 15):
            result = host.run(f'rpm -q {package_name}')
            if (
                result.return_code == 0
                and expected_installed
                or result.return_code != 0
                and not expected_installed
            ):
                break
            sleep(15)
        else:
            pytest.fail(
                'Package {} was not {} host {}'.format(
                    package_name,
                    'installed on' if expected_installed else 'removed from',
                    host.hostname,
                )
            )


def _validate_errata_counts(module_org, host, errata_type, expected_value, timeout=120):
    """Check whether host contains expected errata counts."""
    for _ in range(timeout // 5):
        host = host.read()
        if host.content_facet_attributes['errata_counts'][errata_type] == expected_value:
            break
        sleep(5)
    else:
        pytest.fail(
            'Host {} contains {} {} errata, but expected to contain '
            '{} of them'.format(
                host.name,
                host.content_facet_attributes['errata_counts'][errata_type],
                errata_type,
                expected_value,
            )
        )


def _fetch_available_errata(module_org, host, expected_amount, timeout=120):
    """Fetch available errata for host."""
    errata = host.errata()
    for _ in range(timeout // 5):
        if len(errata['results']) == expected_amount:
            return errata['results']
        sleep(5)
        errata = host.errata()
    else:
        pytest.fail(
            'Host {} contains {} available errata, but expected to '
            'contain {} of them'.format(host.name, len(errata['results']), expected_amount)
        )


@pytest.mark.upgrade
@pytest.mark.tier3
def test_positive_bulk_install_package(module_org, activation_key, custom_repo, rh_repo):
    """Bulk install package to a collection of hosts

    :id: c5167851-b456-457a-92c3-59f8de5b27ee

    :Steps: PUT /api/v2/hosts/bulk/install_content

    :expectedresults: package is installed in the hosts.

    :BZ: 1528275

    :CaseLevel: System
    """
    with VirtualMachine(distro=DISTRO_RHEL7) as client:
        client.install_katello_ca()
        client.register_contenthost(module_org.label, activation_key.name)
        assert client.subscribed
        client.enable_repo(REPOS['rhst7']['id'])
        client.install_katello_agent()
        host_id = entities.Host().search(query={'search': f'name={client.hostname}'})[0].id
        _install_package(
            module_org,
            clients=[client],
            host_ids=[host_id],
            package_name=FAKE_1_CUSTOM_PACKAGE_NAME,
            via_ssh=False,
            rpm_package_name=FAKE_2_CUSTOM_PACKAGE,
        )


@pytest.mark.upgrade
@pytest.mark.tier3
def test_positive_install_in_hc(module_org, activation_key, custom_repo, rh_repo):
    """Install errata in a host-collection

    :id: 6f0242df-6511-4c0f-95fc-3fa32c63a064

    :Setup: Errata synced on satellite server.

    :Steps: PUT /api/v2/hosts/bulk/update_content

    :expectedresults: errata is installed in the host-collection.

    :CaseLevel: System
    """
    with VirtualMachine(distro=DISTRO_RHEL7) as client1, VirtualMachine(
        distro=DISTRO_RHEL7
    ) as client2:
        clients = [client1, client2]
        for client in clients:
            client.install_katello_ca()
            client.register_contenthost(module_org.label, activation_key.name)
            assert client.subscribed
            client.enable_repo(REPOS['rhst7']['id'])
            client.install_katello_agent()
        host_ids = [
            entities.Host().search(query={'search': f'name={client.hostname}'})[0].id
            for client in clients
        ]
        _install_package(
            module_org, clients=clients, host_ids=host_ids, package_name=FAKE_1_CUSTOM_PACKAGE_NAME
        )
        entities.Host().install_content(
            data={
                'organization_id': module_org.id,
                'included': {'ids': host_ids},
                'content_type': 'errata',
                'content': [CUSTOM_REPO_ERRATA_ID],
            }
        )
        _validate_package_installed(clients, FAKE_2_CUSTOM_PACKAGE)


@pytest.mark.tier3
def test_positive_install_in_host(module_org, activation_key, custom_repo, rh_repo):
    """Install errata in a host

    :id: 1e6fc159-b0d6-436f-b945-2a5731c46df5

    :Setup: Errata synced on satellite server.

    :Steps: PUT /api/v2/hosts/:id/errata/apply

    :expectedresults: errata is installed in the host.

    :CaseLevel: System
    """
    with VirtualMachine(distro=DISTRO_RHEL7) as client:
        client.install_katello_ca()
        client.register_contenthost(module_org.label, activation_key.name)
        assert client.subscribed
        client.enable_repo(REPOS['rhst7']['id'])
        client.install_katello_agent()
        host_id = entities.Host().search(query={'search': f'name={client.hostname}'})[0].id
        _install_package(
            module_org, clients=[client], host_ids=[host_id], package_name=FAKE_1_CUSTOM_PACKAGE
        )
        entities.Host(id=host_id).errata_apply(data={'errata_ids': [CUSTOM_REPO_ERRATA_ID]})
        _validate_package_installed([client], FAKE_2_CUSTOM_PACKAGE)


@pytest.mark.tier3
def test_positive_install_multiple_in_host(module_org, activation_key, custom_repo, rh_repo):
    """For a host with multiple applicable errata install one and ensure
    the rest of errata is still available

    :id: 67b7e95b-9809-455a-a74e-f1815cc537fc

    :customerscenario: true

    :BZ: 1469800

    :expectedresults: errata installation task succeeded, available errata
        counter decreased by one; it's possible to schedule another errata
        installation

    :CaseLevel: System
    """
    with VirtualMachine(distro=DISTRO_RHEL7) as client:
        client.install_katello_ca()
        client.register_contenthost(module_org.label, activation_key.name)
        assert client.subscribed
        client.enable_repo(REPOS['rhst7']['id'])
        client.install_katello_agent()
        host = entities.Host().search(query={'search': f'name={client.hostname}'})[0]
        for package in FAKE_9_YUM_OUTDATED_PACKAGES:
            _install_package(
                module_org, clients=[client], host_ids=[host.id], package_name=package
            )
        host = host.read()
        applicable_errata_count = host.content_facet_attributes['errata_counts']['total']
        assert applicable_errata_count > 1
        for errata in FAKE_9_YUM_ERRATUM[:2]:
            host.errata_apply(data={'errata_ids': [errata]})
            host = host.read()
            applicable_errata_count -= 1
            assert (
                host.content_facet_attributes['errata_counts']['total'] == applicable_errata_count
            )


@pytest.mark.tier3
@pytest.mark.skipif((not settings.repos_hosting_url), reason='Missing repos_hosting_url')
def test_positive_list(module_org, custom_repo):
    """View all errata specific to repository

    :id: 1efceabf-9821-4804-bacf-2213ac0c7550

    :Setup: Errata synced on satellite server.

    :Steps: Create two repositories each synced and containing errata

    :expectedresults: Check that the errata belonging to one repo is not
        showing in the other.

    :CaseLevel: System
    """
    repo1 = entities.Repository(id=custom_repo['repository-id']).read()
    repo2 = entities.Repository(product=entities.Product().create(), url=FAKE_3_YUM_REPO).create()
    repo2.sync()
    repo1_errata_ids = [
        errata['errata_id'] for errata in repo1.errata(data={'per_page': '1000'})['results']
    ]
    repo2_errata_ids = [
        errata['errata_id'] for errata in repo2.errata(data={'per_page': '1000'})['results']
    ]
    assert len(repo1_errata_ids) == FAKE_9_YUM_ERRATUM_COUNT
    assert len(repo2_errata_ids) == FAKE_3_YUM_ERRATUM_COUNT
    assert CUSTOM_REPO_ERRATA_ID in repo1_errata_ids
    assert CUSTOM_REPO_ERRATA_ID not in repo2_errata_ids
    assert FAKE_3_ERRATA_ID in repo2_errata_ids
    assert FAKE_3_ERRATA_ID not in repo1_errata_ids


@pytest.mark.tier3
def test_positive_list_updated(module_org):
    """View all errata in an Org sorted by Updated

    :id: 560d6584-70bd-4d1b-993a-cc7665a9e600

    :Setup: Errata synced on satellite server.

    :Steps: GET /katello/api/errata

    :expectedresults: Errata is filtered by Org and sorted by Updated date.

    :CaseLevel: System
    """
    repo = entities.Repository(name=REPOS['rhva6']['name']).search(
        query={'organization_id': module_org.id}
    )
    if repo:
        repo = repo[0]
    else:
        repo_with_cves_id = enable_rhrepo_and_fetchid(
            basearch=DEFAULT_ARCHITECTURE,
            org_id=module_org.id,
            product=PRDS['rhel'],
            repo=REPOS['rhva6']['name'],
            reposet=REPOSET['rhva6'],
            releasever=DEFAULT_RELEASE_VERSION,
        )
        repo = entities.Repository(id=repo_with_cves_id)
    assert repo.sync()['result'] == 'success'
    erratum_list = entities.Errata(repository=repo).search(
        query={'order': 'updated ASC', 'per_page': '1000'}
    )
    updated = [errata.updated for errata in erratum_list]
    assert updated == sorted(updated)


@pytest.mark.tier3
def test_positive_filter_by_cve(module_org):
    """Filter errata by CVE

    :id: a921d4c2-8d3d-4462-ba6c-fbd4b898a3f2

    :Setup: Errata synced on satellite server.

    :Steps: GET /katello/api/errata

    :expectedresults: Errata is filtered by CVE.

    :CaseLevel: System
    """
    repo = entities.Repository(name=REPOS['rhva6']['name']).search(
        query={'organization_id': module_org.id}
    )
    if repo:
        repo = repo[0]
    else:
        repo_with_cves_id = enable_rhrepo_and_fetchid(
            basearch=DEFAULT_ARCHITECTURE,
            org_id=module_org.id,
            product=PRDS['rhel'],
            repo=REPOS['rhva6']['name'],
            reposet=REPOSET['rhva6'],
            releasever=DEFAULT_RELEASE_VERSION,
        )
        repo = entities.Repository(id=repo_with_cves_id)
    assert repo.sync()['result'] == 'success'
    erratum_list = entities.Errata(repository=repo).search(
        query={'order': 'cve DESC', 'per_page': '1000'}
    )
    # Most of Errata don't have any CVEs. Removing empty CVEs from results
    erratum_cves = [errata.cves for errata in erratum_list if errata.cves]
    # Verifying each errata have its CVEs sorted in DESC order
    for errata_cves in erratum_cves:
        cve_ids = [cve['cve_id'] for cve in errata_cves]
        assert cve_ids == sorted(cve_ids, reverse=True)


@pytest.mark.tier3
def test_positive_sort_by_issued_date(module_org):
    """Filter errata by issued date

    :id: 6b4a783a-a7b4-4af4-b9e6-eb2928b7f7c1

    :Setup: Errata synced on satellite server.

    :Steps: GET /katello/api/errata

    :expectedresults: Errata is sorted by issued date.

    :CaseLevel: System
    """
    repo = entities.Repository(name=REPOS['rhva6']['name']).search(
        query={'organization_id': module_org.id}
    )
    if repo:
        repo = repo[0]
    else:
        repo_with_cves_id = enable_rhrepo_and_fetchid(
            basearch=DEFAULT_ARCHITECTURE,
            org_id=module_org.id,
            product=PRDS['rhel'],
            repo=REPOS['rhva6']['name'],
            reposet=REPOSET['rhva6'],
            releasever=DEFAULT_RELEASE_VERSION,
        )
        repo = entities.Repository(id=repo_with_cves_id)
    assert repo.sync()['result'] == 'success'
    erratum_list = entities.Errata(repository=repo).search(
        query={'order': 'issued ASC', 'per_page': '1000'}
    )
    issued = [errata.issued for errata in erratum_list]
    assert issued == sorted(issued)


@pytest.mark.tier3
@pytest.mark.skip_if_open("BZ:1682940")
def test_positive_filter_by_envs(module_org):
    """Filter applicable errata for a content host by current and
    Library environments

    :id: f41bfcc2-39ee-4ae1-a71f-d2c9288875be

    :Setup:

        1. Make sure multiple environments are present.
        2. One of Content host's previous environment has additional
            errata.

    :Steps: GET /katello/api/errata

    :expectedresults: The errata for the content host is filtered by
        current and Library environments.

    :CaseLevel: System

    :BZ: 1682940
    """
    org = entities.Organization().create()
    env = entities.LifecycleEnvironment(organization=org).create()
    content_view = entities.ContentView(organization=org).create()
    activation_key = entities.ActivationKey(environment=env, organization=org).create()
    setup_org_for_a_rh_repo(
        {
            'product': PRDS['rhel'],
            'repository-set': REPOSET['rhst7'],
            'repository': REPOS['rhst7']['name'],
            'organization-id': org.id,
            'content-view-id': content_view.id,
            'lifecycle-environment-id': env.id,
            'activationkey-id': activation_key.id,
        }
    )
    new_cv = entities.ContentView(organization=org).create()
    new_repo = entities.Repository(
        product=entities.Product(organization=org).create(), url=CUSTOM_REPO_URL
    ).create()
    assert new_repo.sync()['result'] == 'success'
    new_cv = new_cv.read()
    new_cv.repository.append(new_repo)
    new_cv = new_cv.update(['repository'])
    new_cv.publish()
    library_env = entities.LifecycleEnvironment(name='Library', organization=org).search()[0]
    errata_library = entities.Errata(environment=library_env).search(query={'per_page': '1000'})
    errata_env = entities.Errata(environment=env).search(query={'per_page': '1000'})
    assert len(errata_library) > len(errata_env)


@pytest.mark.tier3
def test_positive_get_count_for_host(module_org):
    """Available errata count when retrieving Host

    :id: 2f35933f-8026-414e-8f75-7f4ec048faae

    :Setup:

        1. Errata synced on satellite server.
        2. Some Content hosts present.

    :Steps: GET /api/v2/hosts

    :expectedresults: The available errata count is retrieved.

    :CaseLevel: System
    """
    org = entities.Organization().create()
    env = entities.LifecycleEnvironment(organization=org).create()
    content_view = entities.ContentView(organization=org).create()
    activation_key = entities.ActivationKey(environment=env, organization=org).create()
    setup_org_for_a_rh_repo(
        {
            'product': PRDS['rhel'],
            'repository-set': REPOSET['rhst6'],
            'repository': REPOS['rhst6']['name'],
            'organization-id': org.id,
            'content-view-id': content_view.id,
            'lifecycle-environment-id': env.id,
            'activationkey-id': activation_key.id,
        },
        force_manifest_upload=True,
    )
    setup_org_for_a_custom_repo(
        {
            'url': CUSTOM_REPO_URL,
            'organization-id': org.id,
            'content-view-id': content_view.id,
            'lifecycle-environment-id': env.id,
            'activationkey-id': activation_key.id,
        }
    )
    repo_id = enable_rhrepo_and_fetchid(
        basearch=DEFAULT_ARCHITECTURE,
        org_id=org.id,
        product=PRDS['rhel'],
        repo=REPOS['rhva6']['name'],
        reposet=REPOSET['rhva6'],
        releasever=DEFAULT_RELEASE_VERSION,
    )
    repo = entities.Repository(id=repo_id)
    assert repo.sync()['result'] == 'success'
    content_view = content_view.read()
    content_view.repository.append(repo)
    content_view = content_view.update(['repository'])
    content_view.publish()
    versions = sorted(content_view.read().version, key=lambda ver: ver.id)
    cvv = versions[-1].read()
    promote(cvv, env.id)
    with VirtualMachine(distro=DISTRO_RHEL6) as client:
        client.install_katello_ca()
        client.register_contenthost(org.label, activation_key.name)
        assert client.subscribed
        client.enable_repo(REPOS['rhst6']['id'])
        client.enable_repo(REPOS['rhva6']['id'])
        client.install_katello_agent()
        host = entities.Host().search(query={'search': f'name={client.hostname}'})[0].read()
        for errata in ('security', 'bugfix', 'enhancement'):
            _validate_errata_counts(module_org, host, errata_type=errata, expected_value=0)
        client.run(f'yum install -y {FAKE_1_CUSTOM_PACKAGE}')
        _validate_errata_counts(module_org, host, errata_type='security', expected_value=1)
        client.run(f'yum install -y {REAL_0_RH_PACKAGE}')
        for errata in ('bugfix', 'enhancement'):
            _validate_errata_counts(module_org, host, errata_type=errata, expected_value=1)


@pytest.mark.upgrade
@pytest.mark.tier3
def test_positive_get_applicable_for_host(module_org):
    """Get applicable errata ids for a host

    :id: 51d44d51-eb3f-4ee4-a1df-869629d427ac

    :Setup:
        1. Errata synced on satellite server.
        2. Some Content hosts present.

    :Steps: GET /api/v2/hosts/:id/errata

    :expectedresults: The available errata is retrieved.

    :CaseLevel: System
    """
    org = entities.Organization().create()
    env = entities.LifecycleEnvironment(organization=org).create()
    content_view = entities.ContentView(organization=org).create()
    activation_key = entities.ActivationKey(environment=env, organization=org).create()
    setup_org_for_a_rh_repo(
        {
            'product': PRDS['rhel'],
            'repository-set': REPOSET['rhst6'],
            'repository': REPOS['rhst6']['name'],
            'organization-id': org.id,
            'content-view-id': content_view.id,
            'lifecycle-environment-id': env.id,
            'activationkey-id': activation_key.id,
        },
        force_manifest_upload=True,
    )
    setup_org_for_a_custom_repo(
        {
            'url': CUSTOM_REPO_URL,
            'organization-id': org.id,
            'content-view-id': content_view.id,
            'lifecycle-environment-id': env.id,
            'activationkey-id': activation_key.id,
        }
    )
    repo_id = enable_rhrepo_and_fetchid(
        basearch=DEFAULT_ARCHITECTURE,
        org_id=org.id,
        product=PRDS['rhel'],
        repo=REPOS['rhva6']['name'],
        reposet=REPOSET['rhva6'],
        releasever=DEFAULT_RELEASE_VERSION,
    )
    repo = entities.Repository(id=repo_id)
    assert repo.sync()['result'] == 'success'
    content_view = content_view.read()
    content_view.repository.append(repo)
    content_view = content_view.update(['repository'])
    content_view.publish()
    versions = sorted(content_view.read().version, key=lambda ver: ver.id)
    cvv = versions[-1].read()
    promote(cvv, env.id)
    with VirtualMachine(distro=DISTRO_RHEL6) as client:
        client.install_katello_ca()
        client.register_contenthost(org.label, activation_key.name)
        assert client.subscribed
        client.enable_repo(REPOS['rhst6']['id'])
        client.enable_repo(REPOS['rhva6']['id'])
        client.install_katello_agent()
        host = entities.Host().search(query={'search': f'name={client.hostname}'})[0].read()
        erratum = _fetch_available_errata(module_org, host, expected_amount=0)
        assert len(erratum) == 0
        client.run(f'yum install -y {FAKE_1_CUSTOM_PACKAGE}')
        erratum = _fetch_available_errata(module_org, host, 1)
        assert len(erratum) == 1
        assert CUSTOM_REPO_ERRATA_ID in [errata['errata_id'] for errata in erratum]
        client.run(f'yum install -y {REAL_0_RH_PACKAGE}')
        erratum = _fetch_available_errata(module_org, host, 3)
        assert len(erratum) == 3
        assert {REAL_1_ERRATA_ID, REAL_2_ERRATA_ID}.issubset(
            {errata['errata_id'] for errata in erratum}
        )


@pytest.mark.tier3
def test_positive_get_diff_for_cv_envs():
    """Generate a difference in errata between a set of environments
    for a content view

    :id: 96732506-4a89-408c-8d7e-f30c8d469769

    :Setup:

        1. Errata synced on satellite server.
        2. Multiple environments present.

    :Steps: GET /katello/api/compare

    :expectedresults: Difference in errata between a set of environments
        for a content view is retrieved.

    :CaseLevel: System
    """
    org = entities.Organization().create()
    env = entities.LifecycleEnvironment(organization=org).create()
    content_view = entities.ContentView(organization=org).create()
    activation_key = entities.ActivationKey(environment=env, organization=org).create()
    setup_org_for_a_rh_repo(
        {
            'product': PRDS['rhel'],
            'repository-set': REPOSET['rhst7'],
            'repository': REPOS['rhst7']['name'],
            'organization-id': org.id,
            'content-view-id': content_view.id,
            'lifecycle-environment-id': env.id,
            'activationkey-id': activation_key.id,
        },
        force_use_cdn=True,
    )
    setup_org_for_a_custom_repo(
        {
            'url': CUSTOM_REPO_URL,
            'organization-id': org.id,
            'content-view-id': content_view.id,
            'lifecycle-environment-id': env.id,
            'activationkey-id': activation_key.id,
        }
    )
    new_env = entities.LifecycleEnvironment(organization=org, prior=env).create()
    cvvs = content_view.read().version[-2:]
    promote(cvvs[-1], new_env.id)
    result = entities.Errata().compare(
        data={'content_view_version_ids': [cvv.id for cvv in cvvs], 'per_page': '9999'}
    )
    cvv2_only_errata = next(
        errata for errata in result['results'] if errata['errata_id'] == CUSTOM_REPO_ERRATA_ID
    )
    assert [cvvs[-1].id] == cvv2_only_errata['comparison']
    both_cvvs_errata = next(
        errata for errata in result['results'] if errata['errata_id'] == REAL_0_ERRATA_ID
    )
    assert {cvv.id for cvv in cvvs} == set(both_cvvs_errata['comparison'])


@pytest.mark.tier3
def test_positive_incremental_update_required(
    module_org, module_lce, activation_key, module_cv, custom_repo, rh_repo
):
    """Given a set of hosts and errata, check for content view version
    and environments that need updating."

    :id: 6dede920-ba6b-4c51-b782-c6db6ea3ee4f

    :Setup:
        1. Errata synced on satellite server

    :Steps:
        1. Create VM as Content Host, registering to CV with custom errata
        2. Install package in VM so it needs one erratum
        3. Check if incremental_updates required:
            POST /api/hosts/bulk/available_incremental_updates
        4. Assert empty [] result (no incremental update required)
        5. Apply a filter to the CV so errata will be applicable but not installable
        6. Publish the new version
        7. Promote the new version into the same LCE
        8. Check if incremental_updates required:
            POST /api/hosts/bulk/available_incremental_updates
        9. Assert incremental update is suggested


    :expectedresults: Incremental update requirement is detected.

    :CaseLevel: System
    """
    with VirtualMachine(distro=DISTRO_RHEL7) as client_vm:
        client_vm.install_katello_ca()
        client_vm.register_contenthost(module_org.label, activation_key.name)
        assert client_vm.subscribed
        client_vm.enable_repo(REPOS['rhst7']['id'])
        client_vm.install_katello_agent()
        host = entities.Host().search(query={'search': f'name={client_vm.hostname}'})[0]
        # install package to create demand for an Erratum
        _install_package(
            module_org,
            [client_vm],
            [host.id],
            FAKE_1_CUSTOM_PACKAGE,
            via_ssh=True,
            rpm_package_name=FAKE_1_CUSTOM_PACKAGE,
        )
        # Call nailgun to make the API POST to see if any incremental updates are required
        response = entities.Host().bulk_available_incremental_updates(
            data={
                'organization_id': module_org.id,
                'included': {'ids': [host.id]},
                'errata_ids': [FAKE_2_ERRATA_ID],
            },
        )
        assert not response, 'Incremental update should not be required at this point'
        # Add filter of type include but do not include anything
        # this will hide all RPMs from selected erratum before publishing
        entities.RPMContentViewFilter(
            content_view=module_cv, inclusion=True, name="Include Nothing"
        ).create()
        module_cv.publish()
        module_cv = module_cv.read()
        CV1V = module_cv.version[-1].read()
        # Must promote a CV version into a new Environment before we can add errata
        promote(CV1V, module_lce.id)
        module_cv = module_cv.read()
        # Call nailgun to make the API POST to ensure an incremental update is required
        response = entities.Host().bulk_available_incremental_updates(
            data={
                'organization_id': module_org.id,
                'included': {'ids': [host.id]},
                'errata_ids': [FAKE_2_ERRATA_ID],
            },
        )
        assert 'next_version' in response[0], 'Incremental update should be suggested'
        'at this point'


@pytest.fixture(scope='module')
def repos_collection(module_org, module_lce):
    repos_collection = RepositoryCollection(
        distro=DISTRO_RHEL8, repositories=[YumRepository(url=CUSTOM_SWID_TAG_REPO)]
    )
    repos_collection.setup_content(module_org.id, module_lce.id, upload_manifest=True)
    return repos_collection


def _run_remote_command_on_content_host(module_org, command, vm, return_result=False):
    result = vm.run(command)
    assert result.return_code == 0
    if return_result:
        return result.stdout


def _set_prerequisites_for_swid_repos(module_org, vm):
    _run_remote_command_on_content_host(
        module_org, f"wget --no-check-certificate {settings.swid_tools_repo}", vm
    )
    _run_remote_command_on_content_host(module_org, "mv *swid*.repo /etc/yum.repos.d", vm)
    _run_remote_command_on_content_host(module_org, "yum install -y swid-tools", vm)
    _run_remote_command_on_content_host(module_org, "dnf install -y dnf-plugin-swidtags", vm)


def _validate_swid_tags_installed(module_org, vm, module_name):
    result = _run_remote_command_on_content_host(
        module_org, f"swidq -i -n {module_name} | grep 'Name'", vm, return_result=True
    )
    assert module_name in result


@pytest.mark.tier3
@pytest.mark.upgrade
def test_errata_installation_with_swidtags(module_org, module_lce, repos_collection):
    """Verify errata installation with swid_tags and swid tags get updated after
    module stream update.

    :id: 43a59b9a-eb9b-4174-8b8e-73d923b1e51e

    :steps:

        1. create product and repository having swid tags
        2. create content view and published it with repository
        3. create activation key and register content host
        4. create rhel8, swid repos on content host
        5. install swid-tools, dnf-plugin-swidtags packages on content host
        6. install older module stream and generate errata, swid tag
        7. assert errata count, swid tags are generated
        8. install errata vis updating module stream
        9. assert errata count and swid tag after module update

    :expectedresults: swid tags should get updated after errata installation via
        module stream update

    :CaseAutomation: Automated

    :CaseImportance: Critical

    :CaseLevel: System
    """
    with VirtualMachine(distro=repos_collection.distro) as vm:
        module_name = 'kangaroo'
        version = '20180704111719'
        # setup rhel8 and sat_tools_repos
        vm.create_custom_repos(
            **{repo_name: settings.rhel8_os[repo_name] for repo_name in ('baseos', 'appstream')}
        )
        repos_collection.setup_virtual_machine(vm, install_katello_agent=False)

        # install older module stream
        add_remote_execution_ssh_key(vm.ip_addr)
        _set_prerequisites_for_swid_repos(module_org, vm=vm)
        _run_remote_command_on_content_host(
            module_org, f'dnf -y module install {module_name}:0:{version}', vm
        )

        # validate swid tags Installed
        before_errata_apply_result = _run_remote_command_on_content_host(
            module_org,
            f"swidq -i -n {module_name} | grep 'File' | grep -o 'rpm-.*.swidtag'",
            vm,
            return_result=True,
        )
        assert before_errata_apply_result != ''
        host = entities.Host().search(query={'search': f'name={vm.hostname}'})[0]
        host = host.read()
        applicable_errata_count = host.content_facet_attributes['errata_counts']['total']
        assert applicable_errata_count == 1

        # apply modular errata
        _run_remote_command_on_content_host(module_org, f'dnf -y module update {module_name}', vm)
        _run_remote_command_on_content_host(module_org, 'dnf -y upload-profile', vm)
        host = host.read()
        applicable_errata_count -= 1
        assert host.content_facet_attributes['errata_counts']['total'] == applicable_errata_count
        after_errata_apply_result = _run_remote_command_on_content_host(
            module_org,
            f"swidq -i -n {module_name} | grep 'File'| grep -o 'rpm-.*.swidtag'",
            vm,
            return_result=True,
        )

        # swidtags get updated based on package version
        assert before_errata_apply_result != after_errata_apply_result
