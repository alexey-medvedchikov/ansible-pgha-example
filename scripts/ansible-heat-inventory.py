#!/usr/bin/python

import getopt
import json
import os
import subprocess
import sys


def usage(exit_code=0):
    """Prints command usage to stdout and exits with exit_code

    :param exit_code: Exit code to return
    :type exit_code: numeric
    :rtype: None
    """
    opts = (('--list', 'List inventory.'),
            ('-h, --help', 'show this help and exit'),
            ('Environment variable: HEAT_STACK', 'OpenStack Heat stack name'))
    align = 0
    for optname, _ in opts:
        if len(optname) > align:
            align = len(optname) + 5
    print('Usage: {0} [OPTIONS]'.format(sys.argv[0]))
    for optname, description in opts:
        print('  {0}{1}'.format(optname.ljust(align), description))
    sys.exit(exit_code)


def get_inventory_from_stack(stack):
    """Getting inventory from HEAT stack and converts it from this

        "ansible_group_mygroup": {
          "inventory_host": ["hostname01", ...],
          "ansible_ssh_host": ["10.10.10.10", ...],
          ...
        }

    to this

        {
          "databases": {
            "hosts": ["hostname01", ...],
          },
          ...
          "_meta": {
            "hostvars" : {
              "hostname01": {
                "ansible_ssh_host": "10.10.10.10",
                ...
              },
              ...
            }
          }
        }

    :param stack: name of HEAT stack to operate on
    :type stack: str
    :rtype: mapping
    """
    process = subprocess.Popen(['heat', 'output-show', stack, '--all',
                                '--format', 'json'],
                               stdout=subprocess.PIPE)
    out, _ = process.communicate()
    try:
        outputs = json.loads(out.decode('utf8'))
    except ValueError as err:
        print(err)
        sys.exit(2)
    inventory = {}
    hostvars = {}
    for output in outputs:
        if not output['output_key'].startswith('ansible_group_'):
            continue
        ansible_group = output['output_key'][len('ansible_group_'):]
        inventory[ansible_group] = {}
        output_value = output['output_value']
        inventory_hostnames = output_value.pop('inventory_hostname')
        inventory[ansible_group]['hosts'] = inventory_hostnames
        for i, inventory_hostname in enumerate(inventory_hostnames, start=0):
            hostvars[inventory_hostname] = {}
            for var_name, var_values in output_value.items():
                hostvars[inventory_hostname][var_name] = var_values[i]
    inventory.update({'_meta': {'hostvars': hostvars}})
    return inventory


def parse_opts(args):
    try:
        optlist, args = getopt.getopt(args, 'h', ['help', 'list'])
    except getopt.GetoptError as err:
        print(err)
        usage(2)
    options = {'list': False, 'stack': os.environ.get('HEAT_STACK', None)}
    for optname, value in optlist:
        if optname in ('-h', '--help'):
            usage(0)
        elif optname == '--list':
            options['list'] = True
    if not options['list'] or options['stack'] is None:
        usage(2)
    return options, args


def main():
    options, args = parse_opts(sys.argv[1:])
    inventory = get_inventory_from_stack(options['stack'])
    print(json.dumps(inventory, sort_keys=True, indent=2,
                     separators=(',', ': ')))


if __name__ == '__main__':
    main()
