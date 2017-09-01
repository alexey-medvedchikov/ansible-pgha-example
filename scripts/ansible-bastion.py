#!/usr/bin/env python

import jinja2
import os
import subprocess
import sys

J2_SSH_CONFIG = """
Host {{ bastion_host }}
  User                   {{ bastion_user }}
  HostName               {{ bastion_host }}
  ProxyCommand           none
  BatchMode              yes
  PasswordAuthentication no
  ControlMaster          auto
  ControlPath            ~/.ssh/mux-%r@%h:%p
  ControlPersist         15m

Host *
  ServerAliveInterval 60
  TCPKeepAlive        yes
  ProxyCommand        ssh -q -A {{ bastion_user }}@{{ bastion_host }} nc %h %p
  ControlMaster       auto
  ControlPath         ~/.ssh/mux-%r@%h:%p
  ControlPersist      8h
  User                ansible
  StrictHostKeyChecking no
  Compression         yes
  CompressionLevel    9
"""
J2_SSH_CONFIG_PATH = 'bastion.ssh_config'

J2_ANSIBLE_CFG = """
[ssh_connection]
ssh_args = -o ControlPersist=15m -F {{ ssh_config }} -q
scp_if_ssh = True
control_path = ~/.ssh/mux-%%r@%%h:%%p
"""
J2_ANSIBLE_CFG_PATH = 'bastion.ansible.cfg'


def render_file(content, filepath, context):
    """Render Jinja2 template from string to temporary file. Return new file
    path.

    :param content: Jinja2 template string
    :param filepath: path to store file
    :param context: context for template
    :type content: str
    :type filepath: str
    :type context: mapping
    :rtype: None
    """
    template = jinja2.Template(content)
    with open(filepath, 'w') as fp:
        fp.write(template.render(context))


def main():
    bastion_user, bastion_host = sys.argv[1].split('@', 1)

    render_file(J2_SSH_CONFIG, J2_SSH_CONFIG_PATH, {
        'bastion_host': bastion_host,
        'bastion_user': bastion_user})

    try:
        with open('ansible.cfg', 'r') as fp:
            ansible_cfg_content = fp.read()
    except IOError:
        pass
    global J2_ANSIBLE_CFG
    J2_ANSIBLE_CFG = ansible_cfg_content + "\n\n" + J2_ANSIBLE_CFG

    render_file(J2_ANSIBLE_CFG, J2_ANSIBLE_CFG_PATH, {
        'bastion_host': bastion_host,
        'bastion_user': bastion_user,
        'ssh_config': J2_SSH_CONFIG_PATH})

    cmd = ['ansible-playbook'] + sys.argv[2:]
    env = dict(os.environ)
    env['ANSIBLE_CONFIG'] = J2_ANSIBLE_CFG_PATH
    print('Calling {0}'.format(' '.join(cmd)))
    process = subprocess.Popen(cmd, env=env, stdout=sys.stdout)
    returncode = process.wait()

    sys.exit(returncode)


if __name__ == '__main__':
    main()
