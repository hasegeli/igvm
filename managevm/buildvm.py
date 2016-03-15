import os

from fabric.api import env, execute, run
from fabric.network import disconnect_all

from managevm.hooks import load_hooks

from managevm.utils import raise_failure, fail_gracefully
from managevm.utils.config import (
        get_server,
        init_vm_config,
        import_vm_config_from_admintool,
        check_dsthv_vm,
        check_dsthv_memory,
        check_dsthv_cpu,
        check_vm_config,
    )
from managevm.utils.resources import get_ssh_keytypes, get_hw_model
from managevm.utils.storage import (
        create_storage,
        mount_storage,
        umount_temp,
        remove_temp,
        get_vm_block_dev,
    )
from managevm.utils.image import download_image, extract_image
from managevm.utils.network import get_network_config, get_vlan_info
from managevm.utils.preparevm import (
        prepare_vm,
        copy_postboot_script,
        run_puppet,
        block_autostart,
        unblock_autostart,
    )
from managevm.utils.hypervisor import VM
from managevm.utils.portping import wait_until
from managevm.utils.virtutils import (
        get_virtconn,
        close_virtconns,
    )
from managevm.signals import send_signal

run = fail_gracefully(run)

def buildvm(vm_hostname, image=None, nopuppet=False, postboot=None):
    load_hooks()

    config = {'vm_hostname': vm_hostname}
    if image != None:
        config['image'] = image
    config['runpuppet'] = not nopuppet
    if postboot != None:
        config['postboot_script'] = postboot
    config['vm'] = get_server(vm_hostname, 'vm')
    config['dsthv_hostname'] = config['vm']['xen_host']
    config['dsthv'] = get_server(config['dsthv_hostname'])
    config['network'] = get_network_config(config['vm'])
    # Override VLAN information
    config['network']['vlan'] = get_vlan_info(config['vm'], None, config['dsthv'], None)[0]
    config['vlan_tag'] = config['network']['vlan']

    init_vm_config(config)
    import_vm_config_from_admintool(config)

    check_vm_config(config)

    # Configuration of Fabric:
    env.disable_known_hosts = True
    env.use_ssh_config = True
    env.always_use_pty = False
    env.forward_agent = True
    env.user = 'root'
    env.shell = '/bin/bash -c'

    # Perform operations on Hypervisor
    execute(setup_dsthv, config, hosts=[config['dsthv_hostname']])

    # Perform operations on Virtual Machine
    execute(setup_vm, config, hosts=[config['vm_hostname']])

    close_virtconns()
    disconnect_all()

def setup_dsthv(config):
    if config['dsthv']['hypervisor'] == 'kvm':
        config['dsthv_conn'] = get_virtconn(config['dsthv']['hostname'], 'kvm')

    check_dsthv_vm(config)
    check_dsthv_cpu(config)
    check_dsthv_memory(config)

    config['vm_block_dev'] = get_vm_block_dev(config['dsthv']['hypervisor'])
    config['dsthv_hw_model'] = get_hw_model(config['dsthv'])

    send_signal('populate_config', config)

    # Config completely generated -> start doing stuff.
    send_signal('setup_hardware', config)
    config['device'] = create_storage(config['vm_hostname'], config['disk_size_gib'])
    mount_path = mount_storage(config['device'], config['vm_hostname'])

    download_image(config['image'])
    extract_image(config['image'], mount_path, config['dsthv']['os'])

    send_signal('prepare_vm', config, config['device'], mount_path)
    prepare_vm(mount_path,
            server=config['vm'],
            mailname=config['mailname'],
            dns_servers=config['dns_servers'],
            network_config=config['network'],
            swap_size=config['swap_size'],
            blk_dev=config['vm_block_dev'],
            ssh_keytypes=get_ssh_keytypes(config['os']))
    send_signal('prepared_vm', config, config['device'], mount_path)

    if config['runpuppet']:
        block_autostart(mount_path)
        run_puppet(mount_path, config['vm_hostname'], True)
        unblock_autostart(mount_path)

    if 'postboot_script' in config:
        copy_postboot_script(mount_path, config['postboot_script'])

    umount_temp(config['device'])
    remove_temp(mount_path)

    # Note: Extra values used to be separated from config, but since they're currently unused
    # this shouldn't matter.
    for extra in send_signal('hypervisor_extra', config, config['dsthv']['hypervisor']):
        config.update(extra)

    vm = VM.get(config['vm_hostname'], config['dsthv']['hypervisor'], config['dsthv']['hostname'])
    vm.create(config)

    send_signal('defined_vm', config, config['dsthv']['hypervisor'])

    vm.start()

    host_up = wait_until(str(config['vm']['intern_ip']),
            waitmsg='Waiting for guest to boot')

    if not host_up:
        raise_failure(Exception('Guest did not boot.'))

def setup_vm(config):
    send_signal('vm_booted', config)
    if 'postboot_script' in config:
        run('/buildvm-postboot')
        run('rm -f /buildvm-postboot')
        send_signal('postboot_executed', config)
