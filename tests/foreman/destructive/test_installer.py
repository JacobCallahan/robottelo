"""Smoke tests to check installation health

:Requirement: Installation

:CaseAutomation: Automated

:CaseLevel: Acceptance

:CaseComponent: Installation

:Team: Platform

:TestType: Functional

:CaseImportance: Critical

:Upstream: No
"""
import pytest

from robottelo.config import settings
from robottelo.constants import SATELLITE_ANSWER_FILE
from robottelo.utils.installer import InstallerCommand

pytestmark = pytest.mark.destructive


def test_installer_sat_pub_directory_accessibility(target_sat):
    """Verify the public directory accessibility from satellite url after disabling it from
    the custom-hiera

    :id: 2ef78840-098c-4be2-a9e5-db60f16bb803

    :steps:
        1. Check the public directory accessibility from http and https satellite url
        2. Add the foreman_proxy_content::pub_dir::pub_dir_options:"+FollowSymLinks -Indexes"
            in custom-hiera.yaml file.
        3. Run the satellite-installer.
        4. Check the public directory accessibility from http and https satellite url

    :expectedresults: Public directory accessibility from http and https satellite url.
        1. It should be accessible if accessibility is enabled(by default it is enabled).
        2. It should not be accessible if accessibility is disabled in custom_hiera.yaml file.

    :CaseImportance: High

    :CaseLevel: System

    :BZ: 1960801

    :customerscenario: true
    """
    custom_hiera_location = '/etc/foreman-installer/custom-hiera.yaml'
    custom_hiera_settings = (
        'foreman_proxy_content::pub_dir::pub_dir_options: "+FollowSymLinks -Indexes"'
    )
    http_curl_command = f'curl -i {target_sat.url.replace("https", "http")}/pub/ -k'
    https_curl_command = f'curl -i {target_sat.url}/pub/ -k'
    for command in [http_curl_command, https_curl_command]:
        accessibility_check = target_sat.execute(command)
        assert 'HTTP/1.1 200 OK' or 'HTTP/2 200 ' in accessibility_check.stdout.split('\r\n')
    target_sat.get(
        local_path='custom-hiera-satellite.yaml',
        remote_path=f'{custom_hiera_location}',
    )
    _ = target_sat.execute(f'echo {custom_hiera_settings} >> {custom_hiera_location}')
    command_output = target_sat.execute('satellite-installer', timeout='20m')
    assert 'Success!' in command_output.stdout
    for command in [http_curl_command, https_curl_command]:
        accessibility_check = target_sat.execute(command)
        assert 'HTTP/1.1 200 OK' or 'HTTP/2 200 ' not in accessibility_check.stdout.split('\r\n')
    target_sat.put(
        local_path='custom-hiera-satellite.yaml',
        remote_path=f'{custom_hiera_location}',
    )
    command_output = target_sat.execute('satellite-installer', timeout='20m')
    assert 'Success!' in command_output.stdout


def test_installer_inventory_plugin_update(target_sat):
    """DB consistency should not break after enabling the inventory plugin flags

    :id: a2b66d38-e819-428f-9529-23bed398c916

    :steps:
        1. Enable the cloud inventory plugin flag

    :expectedresults: inventory flag should be updated successfully without any db consistency
        error.

    :CaseImportance: High

    :CaseLevel: System

    :BZ: 1863597

    :customerscenario: true

    """
    target_sat.create_custom_repos(rhel7=settings.repos.rhel7_os)
    installer_cmd = target_sat.install(
        InstallerCommand(
            'enable-foreman-plugin-rh-cloud',
            foreman_proxy_plugin_remote_execution_script_install_key=['true'],
        )
    )
    assert 'Success!' in installer_cmd.stdout
    verify_rhcloud_flag = target_sat.install(
        InstallerCommand(help='|grep "\'foreman_plugin_rh_cloud\' puppet module (default: true)"')
    )
    assert 'true' in verify_rhcloud_flag.stdout
    verify_proxy_plugin_flag = target_sat.install(
        InstallerCommand(
            **{'full-help': '| grep -A1 foreman-proxy-plugin-remote-execution-script-install-key'}
        )
    )
    assert '(current: true)' in verify_proxy_plugin_flag.stdout


def test_positive_installer_certs_regenerate(target_sat):
    """Ensure "satellite-installer --certs-regenerate true" command correctly generates
    /etc/tomcat/cert-users.properties after editing answers file

    :id: db6152c3-4459-425b-998d-4a7992ca1f72

    :steps:
        1. Update /etc/foreman-installer/scenarios.d/satellite-answers.yaml
        2. Fill some empty strings in certs category for 'state'
        3. Run satellite-installer --certs-regenerate true
        4. hammer ping

    :expectedresults: Correct generation of /etc/tomcat/cert-users.properties

    :BZ: 1964037

    :customerscenario: true
    """
    target_sat.execute(f'sed -i "s/state: North Carolina/state: \'\'/g" {SATELLITE_ANSWER_FILE}')
    result = target_sat.execute(f'grep state: {SATELLITE_ANSWER_FILE}')
    assert "state: ''" in result.stdout
    result = target_sat.install(
        InstallerCommand(
            'certs-update-all',
            'certs-update-server',
            'certs-update-server-ca',
            certs_regenerate=['true'],
        )
    )
    assert result.status == 0
    assert 'FAIL' not in target_sat.cli.Base.ping()
